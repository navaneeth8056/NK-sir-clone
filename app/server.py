"""Web server: chat (Claude, streamed) + voice (ElevenLabs, streamed) + the test UI.

Run:  uvicorn app.server:app --reload --port 8000   then open http://localhost:8000
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from app.brain import Brain  # noqa: E402  (env must be loaded first)
from app import voice  # noqa: E402
from app import history  # noqa: E402

app = FastAPI(title="NKC Brain")
brain = Brain()


# ---------------------------------------------------------------- passcode lock (for hosting)
# Set APP_PASSCODE to require a passcode before anyone can use the app. Empty = open (fine on your own PC).
OPEN_PATHS = {"/login", "/api/login", "/favicon.ico"}
COOKIE = "nkc_auth"


def _token() -> str:
    return hmac.new(os.getenv("APP_PASSCODE", "").encode(), b"nkc-unlocked", hashlib.sha256).hexdigest()


@app.middleware("http")
async def require_passcode(request: Request, call_next):
    if not os.getenv("APP_PASSCODE") or request.url.path in OPEN_PATHS:
        return await call_next(request)
    if hmac.compare_digest(request.cookies.get(COOKIE, ""), _token()):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "locked: enter the passcode first"}, status_code=401)
    return RedirectResponse("/login")


class LoginIn(BaseModel):
    passcode: str = Field(max_length=200)


@app.get("/login")
def login_page():
    return FileResponse(ROOT / "web" / "login.html", headers={"Cache-Control": "no-store"})


@app.post("/api/login")
async def login(body: LoginIn, request: Request):
    code = os.getenv("APP_PASSCODE", "")
    if code and not hmac.compare_digest(body.passcode.strip(), code):
        await asyncio.sleep(1)                       # slow down guessing
        raise HTTPException(401, "Wrong passcode")
    resp = JSONResponse({"ok": True})
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(COOKIE, _token(), max_age=30 * 24 * 3600, httponly=True, samesite="lax", secure=https)
    return resp


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[dict] = []
    mode: str = "text"  # "voice" = live spoken conversation: shorter, conversational answers
    conversation_id: str | None = None  # groups messages for the History page; created if missing


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=2500)
    speed: float | None = None  # 0.7 (slow) .. 1.2 (fast); None = ELEVENLABS_SPEED from .env


@app.get("/")
def index():
    # no-store: always serve the latest UI (browsers were showing a cached copy)
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/history")
def history_page():
    return FileResponse(ROOT / "web" / "history.html", headers={"Cache-Control": "no-store"})


@app.get("/api/conversations")
def list_conversations():
    return history.summaries()


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str):
    try:
        conv = history.load(cid)
    except ValueError:
        raise HTTPException(400, "bad conversation id")
    if conv is None:
        raise HTTPException(404, "conversation not found")
    return conv


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str):
    try:
        return {"deleted": history.delete(cid)}
    except ValueError:
        raise HTTPException(400, "bad conversation id")


@app.get("/api/status")
async def status(recheck: bool = False):
    nk = await voice.health(force=recheck)
    return {
        "model": brain.model,
        "knowledge_sections": len(brain.sections),
        "mode": "full-context" if brain.use_full_context else "retrieval",
        "app_dir": str(ROOT),
        "history_storage": history.backend(),
        "voices": {
            "nk": {"label": "NK Chaudhary voice", **nk, "model": voice.voice_config()["model_id"]},
            "machine": {"label": "Machine voice", "available": True, "reason": "browser / OS text-to-speech"},
        },
        # kept for older UIs
        "voice": nk["available"],
        "voice_model": voice.voice_config()["model_id"],
    }


@app.post("/api/chat")
def chat(body: ChatIn):
    """Server-sent events: {"conversation_id"}, then {"delta": "..."} chunks, then {"done": true}.
    The question and the answer (even a partly spoken, interrupted one) are saved to history."""
    cid = body.conversation_id if history.valid(body.conversation_id) else history.new_id()
    mode = "voice" if body.mode == "voice" else "text"

    def events():
        yield f"data: {json.dumps({'conversation_id': cid})}\n\n"
        history.append(cid, "user", body.message, mode)
        answer, finished = "", False
        try:
            for piece in brain.stream(body.message, body.history, voice=mode == "voice"):
                answer += piece
                yield f"data: {json.dumps({'delta': piece})}\n\n"
            finished = True
        except Exception as e:  # surface API/key errors in the UI
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            history.append(cid, "error", str(e), mode)
            finished = True
        finally:
            # runs on normal end and when the browser disconnects (barge-in)
            history.append(cid, "assistant", answer, mode, interrupted=not finished)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def _fallback(status: int, reason: str) -> JSONResponse:
    """Tell the browser to speak this text with the machine voice instead."""
    return JSONResponse({"fallback": "machine", "detail": reason}, status_code=status)


@app.post("/api/stt")
async def stt(request: Request, lang: str | None = None):
    """Backup speech-to-text: the browser sends the recorded utterance when its own recogniser heard nothing."""
    audio = await request.body()
    if not audio or len(audio) > 10_000_000:
        raise HTTPException(400, "no audio or audio too large")
    language = {"hi-IN": "hi", "hi": "hi"}.get(lang or "")      # English/Hinglish: let Scribe auto-detect
    try:
        text = await voice.transcribe(audio, request.headers.get("content-type", "audio/webm"), language)
    except voice.VoiceUnavailable as e:
        return JSONResponse({"text": "", "detail": str(e)}, status_code=502)
    return {"text": text}


@app.post("/api/tts")
async def tts(body: TTSIn):
    """MP3 in the NK voice, or a JSON {"fallback": "machine"} response if ElevenLabs can't be used."""
    if not voice.voice_configured():
        return _fallback(503, "ElevenLabs key or voice ID not set in .env")
    gen = voice.synthesize(body.text, body.speed)
    try:
        first = await gen.__anext__()  # fail fast so the browser can fall back
    except StopAsyncIteration:
        return _fallback(502, "ElevenLabs returned no audio")
    except voice.VoiceUnavailable as e:
        return _fallback(502, str(e))

    async def audio():
        yield first
        try:
            async for chunk in gen:
                yield chunk
        except voice.VoiceUnavailable:
            return  # stream cut mid-sentence; browser plays what arrived

    return StreamingResponse(audio(), media_type="audio/mpeg")

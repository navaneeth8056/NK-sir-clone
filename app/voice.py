"""ElevenLabs text-to-speech for the cloned "NK Chaudhary" voice.

If ElevenLabs is not configured, not reachable, or returns an error, callers get a
VoiceUnavailable exception and the browser falls back to the machine voice
(the operating system's built-in text-to-speech).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncIterator

import httpx


class VoiceUnavailable(RuntimeError):
    """ElevenLabs can't be used right now; the UI should fall back to the machine voice."""


def voice_config() -> dict:
    return {
        "base_url": os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/"),
        "api_key": os.getenv("ELEVENLABS_API_KEY", "").strip(),
        "voice_id": os.getenv("ELEVENLABS_VOICE_ID", "").strip(),
        # eleven_multilingual_v2 = best quality, Hindi + English.
        # eleven_flash_v2_5     = much lower latency, for the live conversation stage.
        "model_id": os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip(),
        "output_format": os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128"),
        "timeout": float(os.getenv("ELEVENLABS_TIMEOUT", "8")),
        "voice_settings": {
            "stability": float(os.getenv("ELEVENLABS_STABILITY", "0.5")),
            "similarity_boost": float(os.getenv("ELEVENLABS_SIMILARITY", "0.8")),
            "style": float(os.getenv("ELEVENLABS_STYLE", "0.15")),
            "use_speaker_boost": True,
            # 1.0 = normal; ElevenLabs accepts 0.7 (slowest) to 1.2. Default slightly slow, like an elder speaking.
            "speed": clamp_speed(os.getenv("ELEVENLABS_SPEED", "0.88")),
        },
    }


def clamp_speed(v) -> float:
    try:
        return max(0.7, min(1.2, float(v)))
    except (TypeError, ValueError):
        return 1.0


def voice_configured() -> bool:
    c = voice_config()
    return bool(c["api_key"] and c["voice_id"])


# Back-compat name used by older code/tests
voice_enabled = voice_configured

_health_cache: dict = {"at": 0.0, "result": None}
HEALTH_TTL = float(os.getenv("ELEVENLABS_HEALTH_TTL", "60"))


async def health(force: bool = False) -> dict:
    """Is the NK voice usable right now? Checks key + voice ID against ElevenLabs (cached)."""
    if not voice_configured():
        return {"available": False, "reason": "ElevenLabs key or voice ID not set in .env"}
    now = time.monotonic()
    if not force and _health_cache["result"] and now - _health_cache["at"] < HEALTH_TTL:
        return _health_cache["result"]
    c = voice_config()
    try:
        async with httpx.AsyncClient(timeout=min(c["timeout"], 5)) as client:
            r = await client.get(f"{c['base_url']}/v1/voices/{c['voice_id']}",
                                 headers={"xi-api-key": c["api_key"]})
        detail = {}
        try:
            detail = (r.json() or {}).get("detail") or {}
            detail = detail if isinstance(detail, dict) else {"message": str(detail)}
        except ValueError:
            pass
        if r.status_code == 200:
            result = {"available": True, "reason": "ok", "name": r.json().get("name", "")}
        elif "voices_read" in str(detail.get("message", "")):
            # Key works but can't read voice details. Speech may still work (needs text_to_speech);
            # if it doesn't, each answer falls back to the machine voice anyway.
            result = {"available": True, "name": "",
                      "reason": "ok (voice not verified: API key lacks the Voices read permission)"}
        elif detail.get("type") == "authentication_error" or r.status_code in (401, 403):
            msg = detail.get("message") or "check the key and its permissions"
            result = {"available": False, "reason": f"ElevenLabs rejected the API key: {msg}"}
        elif r.status_code in (400, 404):
            result = {"available": False, "reason": "Voice ID not found in this ElevenLabs account"
                      + (f" ({detail.get('message')})" if detail.get("message") else "")}
        else:
            result = {"available": False, "reason": f"ElevenLabs returned {r.status_code}: {detail.get('message', '')}".rstrip(": ")}
    except httpx.HTTPError as e:
        result = {"available": False, "reason": f"ElevenLabs not reachable ({type(e).__name__})"}
    _health_cache.update(at=now, result=result)
    return result


# ElevenLabs plans cap how many requests may run at once (e.g. 5). Each spoken sentence is one
# request, so the server queues them here instead of firing them all in parallel.
MAX_CONCURRENT = max(1, int(os.getenv("ELEVENLABS_MAX_CONCURRENT", "2")))
RETRIES_ON_BUSY = 3
_sem: asyncio.Semaphore | None = None
_sem_loop = None


def _semaphore() -> asyncio.Semaphore:
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        _sem, _sem_loop = asyncio.Semaphore(MAX_CONCURRENT), loop
    return _sem


def _friendly(status: int, body: str) -> str:
    try:
        d = json.loads(body).get("detail") or {}
        d = d if isinstance(d, dict) else {"message": str(d)}
    except ValueError:
        d = {"message": body}
    if status == 429:
        if "concurrent" in str(d.get("code", "")) + str(d.get("message", "")):
            return "ElevenLabs is busy (too many requests at once)"
        return "ElevenLabs rate limit or quota reached"
    if status in (401, 403) and "quota" in str(d.get("message", "")).lower():
        return "ElevenLabs character quota used up"
    return f"ElevenLabs error {status}: {d.get('message') or d.get('status') or body[:200]}"


async def synthesize(text: str, speed: float | None = None) -> AsyncIterator[bytes]:
    """Yield MP3 bytes for `text`. Raises VoiceUnavailable on any setup/network/API problem.
    Requests are queued (MAX_CONCURRENT at a time) and retried when ElevenLabs says it is busy."""
    c = voice_config()
    if speed is not None:
        c["voice_settings"]["speed"] = clamp_speed(speed)
    if not (c["api_key"] and c["voice_id"]):
        raise VoiceUnavailable("ElevenLabs key or voice ID not set in .env")
    url = f"{c['base_url']}/v1/text-to-speech/{c['voice_id']}/stream"
    async with _semaphore():
        for attempt in range(RETRIES_ON_BUSY + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(c["timeout"], read=30)) as client:
                    async with client.stream(
                        "POST",
                        url,
                        params={"output_format": c["output_format"]},
                        headers={"xi-api-key": c["api_key"], "Content-Type": "application/json"},
                        json={"text": text, "model_id": c["model_id"], "voice_settings": c["voice_settings"]},
                    ) as r:
                        if r.status_code == 429 and attempt < RETRIES_ON_BUSY:
                            await r.aread()
                            await asyncio.sleep(0.4 * (attempt + 1))      # busy: wait a moment, try again
                            continue
                        if r.status_code != 200:
                            body = (await r.aread()).decode(errors="ignore")[:600]
                            if r.status_code != 429:
                                _health_cache.update(at=0.0, result=None)
                            raise VoiceUnavailable(_friendly(r.status_code, body))
                        async for chunk in r.aiter_bytes():
                            yield chunk
                        return
            except httpx.HTTPError as e:
                _health_cache.update(at=0.0, result=None)
                raise VoiceUnavailable(f"ElevenLabs not reachable ({type(e).__name__})") from e


async def transcribe(audio: bytes, content_type: str = "audio/webm", language: str | None = None) -> str:
    """Speech-to-text with ElevenLabs Scribe. Used when the browser's own recogniser misses an utterance."""
    c = voice_config()
    if not c["api_key"]:
        raise VoiceUnavailable("ElevenLabs key not set in .env")
    data = {"model_id": os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2"), "tag_audio_events": "false"}
    if language:
        data["language_code"] = language
    ext = "webm" if "webm" in content_type else "ogg" if "ogg" in content_type else "mp4" if "mp4" in content_type else "wav"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(c["timeout"], read=30)) as client:
            r = await client.post(f"{c['base_url']}/v1/speech-to-text", headers={"xi-api-key": c["api_key"]},
                                  data=data, files={"file": (f"speech.{ext}", audio, content_type)})
    except httpx.HTTPError as e:
        raise VoiceUnavailable(f"ElevenLabs not reachable ({type(e).__name__})") from e
    if r.status_code != 200:
        raise VoiceUnavailable(_friendly(r.status_code, r.text[:600]))
    return (r.json().get("text") or "").strip()

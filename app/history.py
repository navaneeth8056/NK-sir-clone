"""Conversation history.

Storage is chosen automatically:
- Upstash Redis (used when hosting, e.g. on Vercel, where files are not kept): set
  UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN (or KV_REST_API_URL + KV_REST_API_TOKEN,
  the names Vercel's Upstash integration creates).
- Otherwise one JSON file per conversation in ./conversations/ (running on your own computer).

A conversation starts when you press "Start conversation" (voice) or send the first typed
message after opening the page, and holds every question and answer in order.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger("nkc.history")
ROOT = Path(__file__).resolve().parent.parent
_default_dir = Path("/tmp/conversations") if os.getenv("VERCEL") else ROOT / "conversations"
DIR = Path(os.getenv("CONVERSATIONS_DIR", _default_dir))
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
_lock = threading.Lock()
INDEX_KEY = "nkc:conversations"          # sorted set: id -> start time
KEY = "nkc:conv:{}"                      # one JSON document per conversation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check(cid: str) -> str:
    if not _ID.match(cid or ""):
        raise ValueError("bad conversation id")
    return cid


def new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def valid(cid: str | None) -> bool:
    return bool(cid and _ID.match(cid))


# ---------------------------------------------------------------- storage backends
def _redis_conf():
    url = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("KV_REST_API_URL")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("KV_REST_API_TOKEN")
    return (url.rstrip("/"), token) if url and token else None


def backend() -> str:
    return "upstash-redis" if _redis_conf() else "files"


def _redis(*cmd):
    url, token = _redis_conf()
    r = httpx.post(url, headers={"Authorization": f"Bearer {token}"}, json=[str(c) for c in cmd], timeout=8)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body.get("result")


def _get(cid: str) -> dict | None:
    if _redis_conf():
        raw = _redis("GET", KEY.format(cid))
        return json.loads(raw) if raw else None
    p = DIR / f"{cid}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _put(conv: dict) -> None:
    data = json.dumps(conv, ensure_ascii=False)
    if _redis_conf():
        _redis("SET", KEY.format(conv["id"]), data)
        _redis("ZADD", INDEX_KEY, int(datetime.fromisoformat(conv["started_at"]).timestamp()), conv["id"])
        return
    DIR.mkdir(parents=True, exist_ok=True)
    p = DIR / f"{conv['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(conv, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _all() -> list[dict]:
    if _redis_conf():
        ids = _redis("ZRANGE", INDEX_KEY, 0, 499, "REV") or []
        if not ids:
            return []
        raws = _redis("MGET", *[KEY.format(i) for i in ids]) or []
        return [json.loads(r) for r in raws if r]
    out = []
    if DIR.exists():
        for p in DIR.glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return out


# ---------------------------------------------------------------- public API
def load(cid: str) -> dict | None:
    return _get(_check(cid))


def append(cid: str, role: str, text: str, mode: str = "text", **extra) -> None:
    """Add one message; creates the conversation on first use. Never breaks the chat if storage fails."""
    text = (text or "").strip()
    if not text:
        return
    try:
        with _lock:
            conv = _get(_check(cid)) or {"id": cid, "started_at": _now(), "messages": []}
            msg = {"role": role, "text": text, "at": _now(), "mode": mode}
            msg.update({k: v for k, v in extra.items() if v not in (None, "", False)})
            conv["messages"].append(msg)
            conv["updated_at"] = msg["at"]
            _put(conv)
    except Exception as e:  # storage problems must not stop him talking
        log.warning("could not save conversation %s: %s", cid, e)


def summaries() -> list[dict]:
    out = []
    for c in _all():
        msgs = c.get("messages", [])
        first_q = next((m["text"] for m in msgs if m.get("role") == "user"), "")
        modes = {m.get("mode", "text") for m in msgs}
        out.append({
            "id": c.get("id"),
            "started_at": c.get("started_at"),
            "updated_at": c.get("updated_at", c.get("started_at")),
            "title": (first_q[:80] + ("…" if len(first_q) > 80 else "")) or "(no questions)",
            "messages": len(msgs),
            "mode": "voice" if "voice" in modes else "text",
        })
    return sorted(out, key=lambda s: s["started_at"] or "", reverse=True)


def delete(cid: str) -> bool:
    _check(cid)
    with _lock:
        if _redis_conf():
            n = _redis("DEL", KEY.format(cid))
            _redis("ZREM", INDEX_KEY, cid)
            return bool(n)
        p = DIR / f"{cid}.json"
        if p.exists():
            p.unlink()
            return True
        return False

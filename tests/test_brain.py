"""Offline tests: knowledge loading, retrieval, prompt assembly, server wiring (no API keys needed)."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.brain import Brain, Retriever, load_knowledge


class FakeStream:
    def __init__(self, kw, log): log.append(kw); self.text_stream = iter(["I was ", "a weaver first. ", "Businessman later."])
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeClient:
    def __init__(self): self.calls = []; self.messages = SimpleNamespace(stream=lambda **kw: FakeStream(kw, self.calls))


def test_knowledge_loads_with_sources():
    secs = load_knowledge()
    assert len(secs) > 20
    assert any("Source notes" == s.heading for s in secs)
    assert any("two looms" in s.text for s in secs)


def test_retriever_finds_relevant_section():
    r = Retriever(load_knowledge())
    heads = [s.heading for s in r.search("Vahid Khan open jail loom", k=3)]
    assert any("Purpose is Timeless" in h or "Initiatives" in h for h in heads), heads
    heads = [s.heading for s in r.search("direction destination", k=3)]
    assert any("Direction" in h for h in heads), heads


def test_system_prompt_is_cached_and_complete():
    b = Brain(client=FakeClient())
    sys = b.system_blocks("hello")[0]
    assert b.use_full_context and sys.get("cache_control") == {"type": "ephemeral"}
    assert "Honesty rules" in sys["text"] and "Threads of My Life" in sys["text"]


def test_history_is_sanitised():
    h = [{"role": "assistant", "content": "x"}, {"role": "system", "content": "evil"},
         {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert Brain.clean_history(h) == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]


def test_stream_and_server(monkeypatch):
    from app import server
    fake = FakeClient(); server.brain._client = fake
    c = TestClient(server.app)
    assert c.get("/").status_code == 200
    st = c.get("/api/status").json(); assert st["mode"] == "full-context"
    r = c.post("/api/chat", json={"message": "Who are you?", "history": []})
    events = [json.loads(l[6:]) for l in r.text.split("\n\n") if l.startswith("data: ")]
    assert "".join(e.get("delta", "") for e in events) == "I was a weaver first. Businessman later."
    assert events[-1] == {"done": True}
    assert fake.calls[0]["messages"][-1] == {"role": "user", "content": "Who are you?"}
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert c.post("/api/tts", json={"text": "hello"}).status_code == 503


def test_tts_falls_back_when_elevenlabs_unreachable(monkeypatch):
    from app import server, voice
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    monkeypatch.setenv("ELEVENLABS_BASE_URL", "http://127.0.0.1:9")   # nothing listens here
    monkeypatch.setenv("ELEVENLABS_TIMEOUT", "1")
    voice._health_cache.update(at=0.0, result=None)
    c = TestClient(server.app)
    r = c.post("/api/tts", json={"text": "hello"})
    assert r.status_code == 502 and r.json()["fallback"] == "machine"
    st = c.get("/api/status?recheck=1").json()
    assert st["voices"]["nk"]["available"] is False and "reachable" in st["voices"]["nk"]["reason"]
    assert st["voices"]["machine"]["available"] is True


def test_tts_not_configured_returns_machine_fallback(monkeypatch):
    from app import server
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    r = TestClient(server.app).post("/api/tts", json={"text": "hello"})
    assert r.status_code == 503 and r.json()["fallback"] == "machine"


def test_voice_mode_adds_conversational_block():
    b = Brain(client=FakeClient())
    assert len(b.system_blocks("hi")) == 1
    blocks = b.system_blocks("hi", voice=True)
    assert len(blocks) == 2 and "LIVE VOICE CONVERSATION" in blocks[1]["text"]
    assert "cache_control" in blocks[0] and "cache_control" not in blocks[1]
    fake = FakeClient(); b._client = fake
    "".join(b.stream("hello", voice=True))
    assert fake.calls[-1]["max_tokens"] == b.voice_max_tokens


def test_health_reports_key_problems_not_voice_id(monkeypatch):
    import asyncio, httpx
    from app import voice
    monkeypatch.setenv("ELEVENLABS_API_KEY", "abc"); monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    body = {"detail": {"type": "authentication_error", "message": "API key ID used as API key"}}
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(lambda req: httpx.Response(400, json=body)), **kw))
    voice._health_cache.update(at=0.0, result=None)
    r = asyncio.run(voice.health(force=True))
    assert r["available"] is False and "rejected the API key" in r["reason"] and "key ID" in r["reason"]


def test_health_tolerates_missing_voices_read(monkeypatch):
    import asyncio, httpx
    from app import voice
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_x"); monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    body = {"detail": {"status": "missing_permissions", "message": "The API key you used is missing the permission voices_read to execute this operation."}}
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(lambda req: httpx.Response(401, json=body)), **kw))
    voice._health_cache.update(at=0.0, result=None)
    assert asyncio.run(voice.health(force=True))["available"] is True


def test_tts_sends_speed_to_elevenlabs(monkeypatch):
    import httpx, json as _j
    from app import server
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_x"); monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    seen = []
    def handler(req):
        seen.append(_j.loads(req.content)); return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    c = TestClient(server.app)
    assert c.post("/api/tts", json={"text": "hi", "speed": 0.8}).status_code == 200
    assert seen[-1]["voice_settings"]["speed"] == 0.8
    c.post("/api/tts", json={"text": "hi", "speed": 3})            # clamped
    assert seen[-1]["voice_settings"]["speed"] == 1.2
    monkeypatch.delenv("ELEVENLABS_SPEED", raising=False)
    c.post("/api/tts", json={"text": "hi"})                         # .env default
    assert seen[-1]["voice_settings"]["speed"] == 0.88


def test_conversations_are_saved_and_listed(tmp_path, monkeypatch):
    from app import server, history as h
    monkeypatch.setattr(h, "DIR", tmp_path)
    server.brain._client = FakeClient()
    c = TestClient(server.app)
    r = c.post("/api/chat", json={"message": "How did you start?", "mode": "voice"})
    events = [json.loads(l[6:]) for l in r.text.split("\n\n") if l.startswith("data: ")]
    cid = events[0]["conversation_id"]
    c.post("/api/chat", json={"message": "And then?", "conversation_id": cid, "mode": "voice"})
    lst = c.get("/api/conversations").json()
    assert len(lst) == 1 and lst[0]["id"] == cid and lst[0]["title"] == "How did you start?" and lst[0]["mode"] == "voice"
    conv = c.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"]
    assert conv["messages"][1]["text"] == "I was a weaver first. Businessman later."
    c.post("/api/chat", json={"message": "new chat"})                      # no id -> new conversation
    assert len(c.get("/api/conversations").json()) == 2
    assert c.get("/history").status_code == 200
    assert c.get("/api/conversations/../../etc").status_code in (400, 404)
    assert c.delete(f"/api/conversations/{cid}").json() == {"deleted": True}
    assert c.get(f"/api/conversations/{cid}").status_code == 404


def test_tts_queues_and_retries_when_elevenlabs_is_busy(monkeypatch):
    """8 sentences at once against a plan that allows only 2 parallel requests: all must succeed."""
    import asyncio, httpx
    from app import voice
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_x"); monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    state = {"now": 0, "peak": 0, "busy": 0}

    async def handler(req):
        if state["now"] >= 2:
            state["busy"] += 1
            return httpx.Response(429, json={"detail": {"code": "concurrent_limit_exceeded", "message": "Too many concurrent requests"}})
        state["now"] += 1; state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.05); state["now"] -= 1
        return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def one(i):
        return b"".join([c async for c in voice.synthesize(f"sentence {i}")])

    async def main():
        return await asyncio.gather(*(one(i) for i in range(8)))

    out = asyncio.run(main())
    assert all(o == b"ID3mp3" for o in out)
    assert state["peak"] <= voice.MAX_CONCURRENT and state["busy"] == 0


def test_busy_message_is_friendly():
    from app import voice
    body = '{"detail":{"type":"rate_limit_error","code":"concurrent_limit_exceeded","message":"Too many concurrent requests."}}'
    assert voice._friendly(429, body) == "ElevenLabs is busy (too many requests at once)"


def test_stt_proxies_to_scribe(monkeypatch):
    import httpx
    from app import server
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_x")
    seen = {}
    def handler(req):
        seen["url"] = str(req.url); seen["body"] = req.content
        return httpx.Response(200, json={"text": " tell me about the weavers ", "language_code": "eng"})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = TestClient(server.app).post("/api/stt?lang=hi-IN", content=b"\x1aE\xdf\xa3webm", headers={"content-type": "audio/webm"})
    assert r.json() == {"text": "tell me about the weavers"}
    assert seen["url"].endswith("/v1/speech-to-text") and b"scribe" in seen["body"] and b'name="language_code"' in seen["body"]
    assert TestClient(server.app).post("/api/stt", content=b"").status_code == 400


def test_passcode_lock(monkeypatch):
    from app import server
    monkeypatch.setenv("APP_PASSCODE", "weaver123")
    c = TestClient(server.app)
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    assert c.get("/api/status").status_code == 401
    assert c.get("/login").status_code == 200
    assert c.post("/api/login", json={"passcode": "nope"}).status_code == 401
    assert c.post("/api/login", json={"passcode": "weaver123"}).status_code == 200   # sets cookie
    assert c.get("/").status_code == 200 and c.get("/api/conversations").status_code == 200
    monkeypatch.setenv("APP_PASSCODE", "")
    assert TestClient(server.app).get("/").status_code == 200                     # open when not set


def test_history_uses_upstash_when_configured(monkeypatch):
    """Simulates Upstash's REST API with an in-memory dict."""
    import httpx
    from app import history as h
    store, zset = {}, {}
    def fake_post(url, headers=None, json=None, timeout=None):
        cmd, *a = json; res = None
        if cmd == "GET": res = store.get(a[0])
        elif cmd == "SET": store[a[0]] = a[1]; res = "OK"
        elif cmd == "ZADD": zset[a[2]] = float(a[1]); res = 1
        elif cmd == "ZRANGE": res = sorted(zset, key=zset.get, reverse=True)
        elif cmd == "MGET": res = [store.get(k) for k in a]
        elif cmd == "DEL": res = int(store.pop(a[0], None) is not None)
        elif cmd == "ZREM": res = int(zset.pop(a[1], None) is not None)
        return httpx.Response(200, json={"result": res}, request=httpx.Request("POST", url))
    monkeypatch.setenv("KV_REST_API_URL", "https://fake.upstash.io"); monkeypatch.setenv("KV_REST_API_TOKEN", "t")
    monkeypatch.setattr(h.httpx, "post", fake_post)
    assert h.backend() == "upstash-redis"
    cid = h.new_id()
    h.append(cid, "user", "hello", "voice"); h.append(cid, "assistant", "namaste", "voice")
    assert [m["text"] for m in h.load(cid)["messages"]] == ["hello", "namaste"]
    assert h.summaries()[0]["title"] == "hello"
    assert h.delete(cid) is True and h.load(cid) is None

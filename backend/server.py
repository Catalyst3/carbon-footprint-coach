"""FastAPI app exposing EcoLens as a chat endpoint + serving the web UI.

Statefulness: the API is stateless, so we keep per-session history in memory and
resend it each turn. An in-memory dict is the right scope for an MVP/demo; the
``SessionStore`` seam documents exactly where Redis/Postgres would slot in for
production (horizontal scaling, persistence).

Abuse control: a per-client token-bucket rate limiter caps request volume so a
public URL can't drain the Anthropic key — the dominant cost of the app.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import EcoLensAgent
from .config import AgentConfig

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
# Session IDs are client-generated; constrain them so they can't be used as
# injection vectors or to balloon memory with junk keys (Security).
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw and raw.strip() else default
    except ValueError:
        return default


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=8000)


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class SessionStore:
    """Thread-safe in-memory conversation store. Swap for Redis in production."""

    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, str]]] = {}
        self._lock = threading.Lock()

    def history(self, session_id: str) -> list[dict[str, str]]:
        with self._lock:
            return list(self._data.get(session_id, []))

    def append(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            self._data.setdefault(session_id, []).append({"role": role, "content": content})


class RateLimiter:
    """Per-key token bucket. Smooths bursts and caps sustained request rate."""

    def __init__(self, capacity: int, refill_per_minute: int) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = refill_per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True
            self._buckets[key] = (tokens, now)
            return False


# --- Dependency wiring -------------------------------------------------------
# Lazily constructed so importing the app (e.g. in tests) needs no API key.
# Tests override these via ``app.dependency_overrides``.
_agent: EcoLensAgent | None = None
_store = SessionStore()
_limiter = RateLimiter(
    capacity=_int_env("ECOLENS_RATE_BURST", 10),
    refill_per_minute=_int_env("ECOLENS_RATE_PER_MIN", 20),
)


def get_agent() -> EcoLensAgent:
    global _agent
    if _agent is None:
        import anthropic  # lazy import so tests need no key/SDK at import time

        _agent = EcoLensAgent(anthropic.Anthropic())
    return _agent


def get_store() -> SessionStore:
    return _store


def get_limiter() -> RateLimiter:
    return _limiter


def _client_key(request: Request) -> str:
    """Best-effort client identity for rate limiting. Behind a proxy (Render,
    Cloud Run) the real IP is the first hop in X-Forwarded-For."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _validate_and_record(
    request: Request, req: ChatRequest, agent: EcoLensAgent, store: SessionStore, limiter: RateLimiter
) -> str:
    """Shared guardrails for both chat endpoints. Returns the cleaned message."""
    if not limiter.allow(_client_key(request)):
        raise HTTPException(
            status_code=429,
            detail="Too many requests — please slow down.",
            headers={"Retry-After": "5"},
        )
    if not _SESSION_ID_RE.match(req.session_id):
        raise HTTPException(status_code=422, detail="Invalid session_id format.")

    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    if len(message) > agent.config.max_message_chars:
        raise HTTPException(
            status_code=413,
            detail=f"Message too long (max {agent.config.max_message_chars} characters).",
        )

    store.append(req.session_id, "user", message)
    return message


app = FastAPI(title="EcoLens", description="Conversational carbon-footprint coach")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    request: Request,
    req: ChatRequest,
    agent: EcoLensAgent = Depends(get_agent),
    store: SessionStore = Depends(get_store),
    limiter: RateLimiter = Depends(get_limiter),
) -> ChatResponse:
    """Non-streaming reply (used by API clients and tests)."""
    _validate_and_record(request, req, agent, store, limiter)
    reply = agent.reply(store.history(req.session_id))
    store.append(req.session_id, "assistant", reply)
    return ChatResponse(session_id=req.session_id, reply=reply)


@app.post("/api/chat/stream")
def chat_stream(
    request: Request,
    req: ChatRequest,
    agent: EcoLensAgent = Depends(get_agent),
    store: SessionStore = Depends(get_store),
    limiter: RateLimiter = Depends(get_limiter),
) -> StreamingResponse:
    """Token-streaming reply for snappy UX. Persists the full reply once done."""
    _validate_and_record(request, req, agent, store, limiter)

    def generate():
        chunks: list[str] = []
        for piece in agent.reply_stream(store.history(req.session_id)):
            chunks.append(piece)
            yield piece
        store.append(req.session_id, "assistant", "".join(chunks))

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

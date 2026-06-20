"""API-layer tests for rate limiting and the streaming endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.agent import EcoLensAgent
from backend.config import AgentConfig
from backend.server import (
    RateLimiter,
    SessionStore,
    app,
    get_agent,
    get_limiter,
    get_store,
)

from .conftest import FakeClient

SID = "test-session-123"


class StreamAgent(EcoLensAgent):
    def __init__(self) -> None:
        super().__init__(FakeClient(), config=AgentConfig(), system_prompt="test")

    def reply(self, history):  # type: ignore[override]
        return "ok"

    def reply_stream(self, history):  # type: ignore[override]
        yield from ["chunk-1 ", "chunk-2"]


def make_client(limiter: RateLimiter) -> TestClient:
    agent = StreamAgent()
    store = SessionStore()
    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_limiter] = lambda: limiter
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.clear()


def test_streaming_endpoint_returns_concatenated_chunks():
    client = make_client(RateLimiter(capacity=100, refill_per_minute=100))
    with client.stream("POST", "/api/chat/stream", json={"session_id": SID, "message": "hi"}) as res:
        assert res.status_code == 200
        body = "".join(res.iter_text())
    assert body == "chunk-1 chunk-2"


def test_streaming_persists_assistant_reply():
    """After streaming, the next turn must see the prior reply in history."""
    store = SessionStore()
    agent = StreamAgent()
    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_limiter] = lambda: RateLimiter(100, 100)
    client = TestClient(app)
    with client.stream("POST", "/api/chat/stream", json={"session_id": SID, "message": "hi"}) as res:
        list(res.iter_text())  # drain
    assert store.history(SID) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "chunk-1 chunk-2"},
    ]


def test_rate_limit_blocks_after_capacity():
    # Capacity 2, no meaningful refill within the test window.
    client = make_client(RateLimiter(capacity=2, refill_per_minute=1))
    ok1 = client.post("/api/chat", json={"session_id": SID, "message": "a"})
    ok2 = client.post("/api/chat", json={"session_id": SID, "message": "b"})
    blocked = client.post("/api/chat", json={"session_id": SID, "message": "c"})
    assert ok1.status_code == 200
    assert ok2.status_code == 200
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") == "5"


def test_rate_limit_applies_to_stream_endpoint_too():
    client = make_client(RateLimiter(capacity=1, refill_per_minute=1))
    first = client.post("/api/chat/stream", json={"session_id": SID, "message": "a"})
    assert first.status_code == 200
    blocked = client.post("/api/chat/stream", json={"session_id": SID, "message": "b"})
    assert blocked.status_code == 429

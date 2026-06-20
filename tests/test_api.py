"""API-layer tests: validation, session statefulness, error handling.

The agent is replaced with a fake via dependency override, so these exercise the
HTTP surface only — no model calls.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.agent import EcoLensAgent
from backend.config import AgentConfig
from backend.server import SessionStore, app, get_agent, get_store

from .conftest import FakeClient, FakeResponse, FakeTextBlock

SID = "test-session-123"


class EchoAgent(EcoLensAgent):
    """Replies with a fixed string and records the history it received."""

    def __init__(self) -> None:
        super().__init__(
            FakeClient(FakeResponse(content=[FakeTextBlock(text="ok")])),
            config=AgentConfig(max_message_chars=2000),
            system_prompt="test",
        )
        self.seen_histories: list[list[dict]] = []

    def reply(self, history):  # type: ignore[override]
        self.seen_histories.append([dict(m) for m in history])
        return "ok"


@pytest.fixture
def agent() -> EchoAgent:
    return EchoAgent()


@pytest.fixture
def client(agent: EchoAgent) -> TestClient:
    store = SessionStore()
    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}


def test_chat_happy_path(client: TestClient):
    res = client.post("/api/chat", json={"session_id": SID, "message": "took the metro"})
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "ok"
    assert body["session_id"] == SID


def test_session_is_stateful_across_requests(client: TestClient, agent: EchoAgent):
    client.post("/api/chat", json={"session_id": SID, "message": "drove to work"})
    client.post("/api/chat", json={"session_id": SID, "message": "and back"})
    # Second call must include the first exchange in the history it received.
    last_history = agent.seen_histories[-1]
    contents = [m["content"] for m in last_history]
    assert contents == ["drove to work", "ok", "and back"]


def test_sessions_are_isolated(client: TestClient, agent: EchoAgent):
    client.post("/api/chat", json={"session_id": "session-aaaa", "message": "mine"})
    client.post("/api/chat", json={"session_id": "session-bbbb", "message": "yours"})
    assert [m["content"] for m in agent.seen_histories[-1]] == ["yours"]


def test_rejects_bad_session_id(client: TestClient):
    res = client.post("/api/chat", json={"session_id": "bad id!", "message": "hi"})
    assert res.status_code == 422


def test_rejects_blank_message(client: TestClient):
    res = client.post("/api/chat", json={"session_id": SID, "message": "   "})
    assert res.status_code == 422


def test_rejects_oversized_message(client: TestClient):
    res = client.post("/api/chat", json={"session_id": SID, "message": "x" * 2001})
    assert res.status_code == 413

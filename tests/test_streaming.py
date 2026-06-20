"""Unit tests for EcoLensAgent.reply_stream — no network, no API key."""
from __future__ import annotations

from backend.agent import EcoLensAgent, _ERROR_REPLY, _REFUSAL_REPLY
from backend.config import AgentConfig

from .conftest import FakeClient


def make_agent(client: FakeClient, **cfg) -> EcoLensAgent:
    return EcoLensAgent(client, config=AgentConfig(**cfg), system_prompt="test")


def test_stream_yields_chunks_in_order():
    client = FakeClient()
    client.stream_chunks = ["a", "b", "c"]
    out = list(make_agent(client).reply_stream([{"role": "user", "content": "hi"}]))
    assert out == ["a", "b", "c"]
    assert "".join(out) == "abc"


def test_stream_uses_cacheable_system_prompt():
    client = FakeClient()
    list(make_agent(client).reply_stream([{"role": "user", "content": "hi"}]))
    sent_system = client.calls[0]["system"]
    assert sent_system[0]["cache_control"] == {"type": "ephemeral"}


def test_stream_refusal_emits_friendly_fallback():
    client = FakeClient()
    client.stream_chunks = []
    client.stream_stop_reason = "refusal"
    out = list(make_agent(client).reply_stream([{"role": "user", "content": "x"}]))
    assert out == [_REFUSAL_REPLY]


def test_stream_transport_error_emits_fallback():
    client = FakeClient()

    def explode(**_):
        raise RuntimeError("boom")

    client.messages.stream = explode  # type: ignore[assignment]
    out = list(make_agent(client).reply_stream([{"role": "user", "content": "x"}]))
    assert out == [_ERROR_REPLY]

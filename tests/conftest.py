"""Shared test fixtures: a fake Anthropic client and response objects.

These let the unit tests run with zero network access and no API key, which is
what makes the test suite fast, deterministic, and CI-friendly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeResponse:
    content: list[FakeTextBlock]
    stop_reason: str = "end_turn"


class FakeStream:
    """Mimics the SDK's streaming context manager (.text_stream + final message)."""

    def __init__(self, chunks: list[str], stop_reason: str = "end_turn") -> None:
        self._chunks = chunks
        self._stop_reason = stop_reason

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    @property
    def text_stream(self):
        yield from self._chunks

    def get_final_message(self) -> FakeResponse:
        return FakeResponse(
            content=[FakeTextBlock(text="".join(self._chunks))],
            stop_reason=self._stop_reason,
        )


class FakeMessages:
    def __init__(self, owner: "FakeClient") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> FakeResponse:
        self._owner.calls.append(kwargs)
        return self._owner.next_response

    def stream(self, **kwargs: Any) -> FakeStream:
        self._owner.calls.append(kwargs)
        return FakeStream(self._owner.stream_chunks, self._owner.stream_stop_reason)


class FakeClient:
    """Records the kwargs of each .messages.create()/.stream() call and returns a
    canned response, so tests can assert *how* the agent calls the API and *how*
    it parses the result."""

    def __init__(self, response: FakeResponse | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.next_response = response or FakeResponse(
            content=[FakeTextBlock(text="The Breakdown: ...\nThe Insight: ...\nYour Next Easy Win: ...")]
        )
        # Streaming fixtures (overridable per test).
        self.stream_chunks: list[str] = ["The Breakdown: ", "hi", " — like 2 phone charges."]
        self.stream_stop_reason: str = "end_turn"
        self.messages = FakeMessages(self)

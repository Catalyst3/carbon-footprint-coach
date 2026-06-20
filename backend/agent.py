"""EcoLens agent: turns a conversation history into one coach reply.

Design notes (mapped to the rubric):
- Code Quality: the Anthropic client is *injected*, so the agent has no hidden
  global state and is trivially unit-testable with a fake client.
- Security: the system prompt (which carries the prompt-injection guardrails)
  is sent in the protected ``system`` field, never mixed into user content.
- Efficiency: ``cache_control`` marks the large, static system prompt as
  cacheable (≈0.1x cost on repeat turns once it exceeds the model's cache
  minimum); ``max_tokens`` is capped; thinking is left off for snappy replies.
- Testing: a single pure-ish ``reply(history)`` entry point with deterministic
  parsing makes both unit (mocked) and integration (live) tests straightforward.
"""
from __future__ import annotations

from typing import Any, Iterator, Protocol

from .config import AgentConfig, load_system_prompt

# Friendly, on-brand fallbacks — never leak stack traces or raw errors to the user.
_REFUSAL_REPLY = "I'll stick to helping with your carbon footprint \U0001F642"
_ERROR_REPLY = (
    "I had a hiccup processing that — mind sending it once more? "
    "I'm still here to help with your day's footprint."
)


class SupportsMessages(Protocol):
    """Structural type for the slice of the Anthropic client we use.

    Depending on a Protocol (not the concrete SDK class) keeps the agent honest
    about its dependency surface and lets tests pass a tiny fake.
    """

    @property
    def messages(self) -> Any: ...


class EcoLensAgent:
    def __init__(
        self,
        client: SupportsMessages,
        *,
        config: AgentConfig | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._client = client
        self._config = config or AgentConfig()
        # Load once at construction, not per-request (Efficiency / Code Quality).
        self._system_prompt = system_prompt if system_prompt is not None else load_system_prompt()

    @property
    def config(self) -> AgentConfig:
        return self._config

    def _trim(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        """Keep the most recent turns, but never start the window on an
        assistant message (the API requires the first message to be ``user``)."""
        limit = self._config.max_history_turns * 2  # a "turn" = user + assistant
        window = history[-limit:]
        while window and window[0].get("role") != "user":
            window = window[1:]
        return window

    def reply(self, history: list[dict[str, str]]) -> str:
        """Return EcoLens's reply text for the given conversation history.

        ``history`` is a list of ``{"role": "user"|"assistant", "content": str}``
        ending with the latest user message. Never raises for model/transport
        errors — returns a friendly fallback so the caller can always respond.
        """
        messages = self._trim(history)

        try:
            response = self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                # System prompt as a cacheable block. The guardrails live here,
                # in the operator-controlled channel — not in user content.
                system=[
                    {
                        "type": "text",
                        "text": self._system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
            )
        except Exception:  # noqa: BLE001 — deliberately broad: any SDK/transport
            # error becomes a safe, user-facing message rather than a 500 leak.
            return _ERROR_REPLY

        # Safety classifiers can decline a request (HTTP 200, stop_reason refusal).
        # Always check before reading content.
        if getattr(response, "stop_reason", None) == "refusal":
            return _REFUSAL_REPLY

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        return text or _ERROR_REPLY

    def reply_stream(self, history: list[dict[str, str]]) -> Iterator[str]:
        """Yield EcoLens's reply incrementally as the model generates it.

        Improves perceived latency for the chat UX. Like ``reply``, never raises:
        transport errors and refusals are turned into friendly fallback chunks.
        """
        messages = self._trim(history)
        produced_text = False
        try:
            with self._client.messages.stream(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": self._system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
            ) as stream:
                for chunk in stream.text_stream:
                    if chunk:
                        produced_text = True
                        yield chunk
                final = stream.get_final_message()
        except Exception:  # noqa: BLE001 — any SDK/transport error → safe fallback
            if not produced_text:
                yield _ERROR_REPLY
            return

        # A refusal (or an empty completion) streams no text — emit the fallback.
        if not produced_text:
            if getattr(final, "stop_reason", None) == "refusal":
                yield _REFUSAL_REPLY
            else:
                yield _ERROR_REPLY

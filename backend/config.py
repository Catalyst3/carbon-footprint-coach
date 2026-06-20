"""Centralized, env-overridable configuration.

Keeping every tunable in one typed place (rather than scattered literals) is the
Code-Quality lever that also makes the app testable: tests can construct an
``AgentConfig`` with tiny limits instead of mutating globals.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The system prompt is the heart of the product — load it from the versioned file
# so the prompt and the code stay in lockstep and the prompt can be reviewed/diffed
# independently of the harness.
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "ecolens_system_prompt.md"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class AgentConfig:
    # Per the Claude API reference, default to the latest capable model. Model is
    # NOT downgraded for cost — efficiency is bought via max_tokens, prompt
    # caching, and history trimming instead (see agent.py).
    model: str = os.environ.get("ECOLENS_MODEL", "claude-opus-4-8")

    # The output contract caps responses at ~70 words; 1024 tokens is generous
    # headroom while keeping latency and cost low (Efficiency).
    max_tokens: int = _int_env("ECOLENS_MAX_TOKENS", 1024)

    # Conversation memory: the API is stateless, so we resend history each turn.
    # Trim to the last N turns to bound token growth on long chats (Efficiency)
    # while preserving enough context for the "stateful agent" behavior.
    max_history_turns: int = _int_env("ECOLENS_MAX_HISTORY_TURNS", 12)

    # Reject oversized inputs before they ever reach the model (Security: bounds
    # request cost and blast radius of a hostile payload).
    max_message_chars: int = _int_env("ECOLENS_MAX_MESSAGE_CHARS", 2000)


def load_system_prompt(path: Path = PROMPT_PATH) -> str:
    return path.read_text(encoding="utf-8")

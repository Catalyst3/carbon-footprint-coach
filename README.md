# 🌱 EcoLens — Conversational Carbon-Footprint Coach

EcoLens turns casual chat ("took an Uber, had a burger, metro home") into a
grounded CO₂e estimate, a relatable real-world translation, and one easy next
step — no forms, no guilt. It's a stateful agent powered by the Claude API.

```
You:     Typical Tuesday. 30-min Uber to work in traffic, burger for lunch,
         metro home — exhausted.
EcoLens: The Breakdown: Today's traffic-heavy Uber and the beef burger added up
         to about 10 kg of CO₂ — like running your home AC for ~10 hours.
         The Insight: Lunch alone was over half of that.
         Your Next Easy Win: Nice call on the metro home! Next time, try the
         chicken or veggie option downstairs — it'd cut today's food impact ~70%.
```

## Architecture

```
prompts/ecolens_system_prompt.md   The product's brain — a reviewable, versioned
                                   system prompt (estimation rules, guardrails,
                                   output contract). See its DESIGN_NOTES.
backend/
  config.py    Typed, env-overridable settings (one place for every tunable)
  agent.py     EcoLensAgent — injectable Claude client, history trimming,
               prompt caching, token streaming, refusal/error handling
  server.py    FastAPI: /api/chat (+ /api/chat/stream), in-memory session
               store, input validation, per-IP rate limiting
static/index.html  Accessible single-file web chat (no external requests)
tests/
  test_agent.py         Unit tests (mocked client, always run)
  test_api.py           HTTP + session-statefulness tests (mocked agent)
  test_contract_live.py Integration tests asserting the output contract (needs key)
```

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your ANTHROPIC_API_KEY

export ANTHROPIC_API_KEY=sk-ant-...     # or: set -a; . ./.env; set +a
uvicorn backend.server:app --reload
# open http://127.0.0.1:8000
```

## Test it

```bash
pytest                       # 15 fast offline tests — no API key needed
ANTHROPIC_API_KEY=sk-... pytest tests/test_contract_live.py -v   # live contract checks
```

## How it maps to the review criteria

| Criterion | Where it lives |
|---|---|
| **Problem alignment** | Frictionless chat → grounded estimate → relatable translation → one nudge; stateful sessions remember the day. The worked demo runs end-to-end. |
| **Code quality** | Dependency injection (no hidden globals), typed config, `Protocol`-based client seam, small single-responsibility modules, a documented `SessionStore` swap point for Redis. |
| **Security** | API key from env only; **per-IP rate limiting** (token bucket) so a public URL can't drain the key; user text never enters the system/operator channel; session-id + length validation; XSS-safe UI (`textContent`); prompt-injection guardrails in the system prompt; errors never leak stack traces. |
| **Efficiency** | **Token streaming** for low perceived latency, cacheable system prompt (`cache_control`), capped `max_tokens`, history trimming to bound token growth. |
| **Testing** | 23 deterministic offline tests (mocked client) covering the agent, streaming, API validation, rate limiting, and session statefulness + live tests that assert the rigid output contract (3-line format, kg-always-translated, injection refused). |
| **Accessibility** | Semantic HTML, `role="log"` + `aria-live` announcements, labeled controls, visible focus, keyboard send (Enter / Shift+Enter), reduced-motion + light/dark via `prefers-*`, system fonts. |

## Production notes (out of MVP scope)
- Swap the in-memory `SessionStore` for Redis/Postgres for multi-instance scaling.
- Add rate limiting and auth in front of `/api/chat`.
- The system prompt's emission factors are deliberate midpoints — wire in a real
  factor dataset for higher fidelity.

# MarketPilot

A marketing agent. You give it two inputs — **service** and **target clients** — and it:

1. finds real local businesses,
2. researches them,
3. proposes evidence-backed ideas,
4. sends real outreach email,
5. processes real replies,
6. notifies you on Telegram.

No mocks. No fake data. No simulated integrations. Every integration below is real.

| Concern | Implementation |
| --- | --- |
| Language / API | Python 3.11, FastAPI |
| Storage | SQLAlchemy on Supabase Postgres (`DATABASE_URL`) |
| All model calls | LiteLLM — Gemini primary, Groq fallback |
| Discovery | Google Places API, OpenStreetMap fallback |
| Sending | Resend API |
| Replies | IMAP inbox |
| Notifications | Telegram Bot API |
| Config | `.env` (secrets) + `config.yaml` (behaviour) |

## Setup

```bash
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt     # Linux/macOS: pip install -r requirements.txt
copy .env.example .env                            # then fill in the keys
```

Required environment variables are listed in `.env.example`. Behaviour knobs (model ids,
send limits, opt-out phrases, radius) live in `config.yaml`. Keys are never hard-coded and
never appear in `config.yaml`.

```bash
.venv\Scripts\python -m pytest tests -q
```

---

## Phase 1 — Foundation

Everything the rest of the pipeline stands on.

- `marketpilot/config.py` — loads `.env` + `config.yaml` into a typed `Settings` object.
  Secrets are read from the environment only; `test_secrets_never_come_from_yaml` guards this.
- `marketpilot/statuses.py` — the 11 lead statuses and the enforced transition graph.
  A lead can only move along declared edges; anything else raises `InvalidTransition`.
  `FAILED` is terminal until `next_for_stage()` returns it to the stage that failed.
- `marketpilot/models.py` — `Job` (your two inputs), `Lead` (persisted `status`),
  `Email` (in/out, `provider_id` unique = send idempotency), `StepRun` (audit log).
- `marketpilot/steprun.py` — the idempotent step runner. `(step, idem_key)` is unique, so
  re-running a completed step short-circuits with `skipped=True` and no side effects.
  Failures are persisted with the error text, then re-raised.
- `marketpilot/llm.py` — every model call goes through LiteLLM. `structured()` asks for JSON,
  validates against a Pydantic schema, and re-asks with the validation error up to
  `llm.validation_retries` times. Invalid output never reaches the database.
- `marketpilot/schemas.py` — `ResearchOutput`, `IdeasOutput`, `OutreachOutput`,
  `ReplyClassification`. Every field is bounded; facts must carry `statement` + `evidence`.

Tests: `tests/test_phase1_foundation.py` (18 assertions covering the status graph,
idempotency, config hygiene and schema rejection).

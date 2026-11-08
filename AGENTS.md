# AGENTS.md

Resolve: AI support for a subscription app on Adapty (FitBuddy, a fitness app, the same as `appy/`; it runs on the organisers' seeded dataset, so ids stay `lumen_443814_*`). Level 2 is the first-line agent inside the app (`POST /chat`): it reads the user's live Adapty profile, explains, gives store steps and grants free access within limits; everything risky goes to Level 1 as a ticket. Level 1 is the triage agent for the support person: it builds each ticket with tools and proposes 2–3 ranked options; a person accepts, edits or rejects; only then anything runs in Adapty. Built at the Adapty Hackathon Belgrade, 7–8 Nov 2026. `README.md` is the user documentation; `docs/agents.md` is the binding spec; `docs/openapi.json` the API contract.

Python 3.11+, FastAPI, SQLite, one `uvicorn` process. One tool-calling interface for two LLM providers (Anthropic, or any OpenAI-compatible API such as DeepSeek). Adapty server-side API v2 for profiles and grants. The web UI is static HTML/CSS/JS in `resolve/static/ui/` over the JSON API.

## Layout

| Module | Role |
|---|---|
| `resolve/config.py` | Settings from `.env`: keys, `LLM_PROVIDER` (auto/openai/anthropic), OpenAI-compatible extras, app id, `DEMO_MODE`, DB path |
| `resolve/adapty.py` | The only client of the Adapty server-side API: `get_profile`, `grant_access_level`, `update_profile`, `update_email`, `compensation_expiry` |
| `resolve/llm.py`, `llm_openai.py`, `llm_anthropic.py`, `llm_schema.py` | `LLM.step(system, messages, tools) -> Step`; providers only translate formats; strict tool schemas from pydantic; `FakeLLM` for tests |
| `resolve/models.py` | Shared contract: AgentConfig, TriageTicket, Decision, ChatResult, API response models |
| `resolve/jira.py` | The only client of Jira Cloud; `export`: one issue per ticket |
| `resolve/policy.py` | Grant limits as pure code (`check_grant`), config loading |
| `resolve/actions.py` | `execute(...)`: the only place an action writes to Adapty; grants recorded in `grants` |
| `resolve/frontline.py` | Level 2 chat loop: enabled tools only, every call checked and logged, handoff in code after a blocked grant, after-handoff mode, `cancel_handoff` |
| `resolve/triage.py` | Level 1: code prefetches user, profile, history and config (`prefetch`), then usually one `submit_ticket` call; `get_profile`, `find_user_by_email`, `get_user_history` stay for a second id/email, checks on the submitted ticket, insights |
| `resolve/workflow.py` | accept / reject / reopen, idempotent status transitions |
| `resolve/api.py`, `resolve/analytics.py` | JSON API (CORS for the phone apps), full-text search, analytics (timeline, open tasks) |
| `resolve/db.py` | SQLite schema + migrations (tickets, conversations, messages, l2_events, grants, agent_config, email_index) |
| `resolve/web.py` | App factory, redirects to the screens, `/healthz` |
| `resolve/static/ui/` | The web screens (`*.dc.html`, `support.js`, `api.js`), JSON API only; `?mock=1` for sample data |
| `scripts/` | `seed_demo_profiles.py` (copy the demo users into another Adapty app), `import_feedback.py`, `seed_emails.py`, `eval.py` (paid), `export_openapi.py`, `generate_tickets.py`, `snapshot.py` |
| `mobile-mock/` | Backup Android app: chat + debug buttons over `POST /chat` |
| `tests/` | pytest, no network, no keys; `tests/fakes_v2.py` has the scripted LLM steps and profiles |

## Commands

```bash
python -m resolve --port 8010            # http://127.0.0.1:8010/ui
python -m pytest
DEMO_MODE=1 python -m resolve --port 8010  # no keys, no model, no Adapty writes
python scripts/export_openapi.py         # after any API change
python scripts/eval.py --yes [--l2]      # paid: ask the owner first
```

## Hard limits (do not relax)

- Adapty writes allowed: `grantAccessLevel` and `updateProfile` on seeded demo users only (and `createProfile` / `setTransaction` only through `scripts/seed_demo_profiles.py` into a teammate's own app). Never Adapty Mail, placements, A/B start/stop.
- Level 2 may grant on its own only within the config limits, checked in code (`policy.check_grant`). Everything else that writes runs on a person's click in Level 1. The one exception the owner can switch on: Level 1 filing its own bug reports in Jira, capped per day (`jira.auto_create`).
- Refunds are never done by an agent: only the store refunds; Level 2 hands off, Level 1 offers `refund_to_human`.
- LLM calls cost money: no batch runs, no eval without asking the owner. Model calls per ticket and per chat message are capped in code.
- No real end-user data: tickets and chats are synthetic; App Store reviews are never ticket text.
- Nothing sends email or messages to users; "sent" is a status. The only outbound message is a Jira issue to the app's own teams (`resolve/jira.py`), with email addresses removed.
- `.env` is git-ignored. Never print, log or commit any key.

## Conventions

- The model never chooses the customer: Level 2 tools take the session's customer; Level 1 may read only the ticket's customer or one found by email in the same run.
- Model output is validated with pydantic; a bad tool call gets one corrective retry, then the run stops with an error on the ticket.
- Every Level 2 tool call is logged in `l2_events` (allowed / blocked reason / result).
- Spec first: behaviour changes go into `docs/agents.md` before the code; tests are written from the spec.

## AI log

Append to `docs/history/AI_LOG.md` as things happen: tool, what for, one thing it got wrong and how it was caught. Not at the end.

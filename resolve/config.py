"""Settings from the environment. `.env` next to the repo root is loaded once, here."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

APP_ID = os.environ.get("ADAPTY_APP_ID", "9c49efb6-f2ca-479a-8f57-4bb6c8ea2f41")
ACCESS_LEVEL = os.environ.get("ADAPTY_ACCESS_LEVEL", "premium")
COMPENSATION_DAYS = int(os.environ.get("COMPENSATION_DAYS", "7"))
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "")
# auto = openai when OPENAI_API_KEY is set, else anthropic when ANTHROPIC_API_KEY is set.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "auto").strip().lower() or "auto"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip()
# Provider-specific body fields as JSON, e.g. DeepSeek without thinking: {"thinking": {"type": "disabled"}}
OPENAI_EXTRA_BODY = os.environ.get("OPENAI_EXTRA_BODY", "").strip()
OPENAI_STRICT_TOOLS = os.environ.get("OPENAI_STRICT_TOOLS", "1").strip() != "0"
OPENAI_MAX_TOKENS = int(os.environ.get("OPENAI_MAX_TOKENS", "4000"))
DEMO_MODE = os.environ.get("DEMO_MODE", "0") == "1"
# Insights: re-run the hypothesis on its own when the grouping changes materially (docs/agents.md, "Insights").
INSIGHTS_AUTO = os.environ.get("INSIGHTS_AUTO", "1").strip() != "0"
# Jira Cloud: all three, or the app runs without it (docs/agents.md, "Jira"). The token is never printed or logged.
JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "").strip()
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "").strip()
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "").strip()
# Where this app is reachable, for the link from the issue back to the ticket. Optional.
RESOLVE_PUBLIC_URL = os.environ.get("RESOLVE_PUBLIC_URL", "").strip().rstrip("/")
DB_PATH = Path(os.environ.get("RESOLVE_DB", ROOT / "data" / "resolve.sqlite"))
DEMO_DB_PATH = ROOT / "data" / "demo.sqlite"
SEED_REPO = Path(os.environ.get("SEED_REPO", Path.home() / "hackathon" / "adapty-seed-data-hack"))


class MissingKey(RuntimeError):
    pass


def _key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingKey(f"{name} is not set; copy .env.example to .env and fill it in, or run with DEMO_MODE=1")
    return value


def adapty_secret() -> str:
    return _key("ADAPTY_SECRET_KEY")


def anthropic_key() -> str:
    return _key("ANTHROPIC_API_KEY")


def openai_key() -> str:
    return _key("OPENAI_API_KEY")


def openai_model() -> str:
    """No default on purpose: the model id comes with the tokens, it is never guessed in code."""
    return _key("OPENAI_MODEL")

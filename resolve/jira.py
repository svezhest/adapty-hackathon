"""Jira Cloud REST API v3 and the bug-report export: the only module that talks to Jira.

Docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/ (POST /issue, POST /search/jql).
Auth is HTTP basic with the account's email and an API token. See docs/agents.md, "Jira".

`export` files one issue per ticket: the ticket's `jira_json` and a `resolve-<ticket id>` label make
a second click, a triage rerun or a crash after the create safe. It never sends the user's email.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests

from resolve import config, db, policy
from resolve.models import JiraExport

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15
PRIORITIES = {"high": "High", "medium": "Medium", "low": "Low"}
OPTIONAL_FIELDS = ("priority", "labels")  # a project's screen may not have them: dropped, not fatal
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")

# One process serves the app; this keeps a click and the agent from filing the same ticket twice.
_export_lock = threading.Lock()


class JiraError(Exception):
    """A failed Jira call. `status` is None for a timeout or connection error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    def __str__(self) -> str:
        return f"Jira {self.status}: {self.args[0]}" if self.status else f"Jira: {self.args[0]}"


class NotConnected(Exception):
    """JIRA_BASE_URL, JIRA_EMAIL or JIRA_API_TOKEN is not set."""


class Client:
    def __init__(self, base_url: str, email: str, api_token: str, session: requests.Session | None = None):
        parts = urlsplit(base_url)
        if parts.scheme != "https" and parts.hostname not in LOCAL_HOSTS:
            raise ValueError("JIRA_BASE_URL must start with https://: the API token travels with every call")
        self.base_url = base_url.rstrip("/")
        self._auth = (email, api_token)
        self._session = session or requests.Session()

    def create_issue(self, fields: dict) -> dict:
        """Create an issue, returns `{id, key, self}`. A field the project refuses from OPTIONAL_FIELDS is dropped."""
        fields = dict(fields)
        while True:
            try:
                return self._request("POST", "/rest/api/3/issue", {"fields": fields})
            except _Rejected as exc:
                refused = [f for f in OPTIONAL_FIELDS if f in exc.fields and f in fields]
                if not refused:
                    raise JiraError(exc.args[0], exc.status) from None
                for name in refused:
                    del fields[name]

    def find_by_label(self, project_key: str, label: str) -> str | None:
        """The key of an issue with this label: an earlier run that created it and did not get to record it."""
        jql = f'project = "{project_key}" AND labels = "{label}"'
        found = self._request("POST", "/rest/api/3/search/jql", {"jql": jql, "maxResults": 1, "fields": ["summary"]})
        issues = found.get("issues") or []
        return issues[0]["key"] if issues else None

    def browse_url(self, key: str) -> str:
        return f"{self.base_url}/browse/{key}"

    def _request(self, method: str, path: str, body: dict) -> dict:
        try:
            response = self._session.request(
                method, self.base_url + path, json=body, auth=self._auth,
                headers={"Accept": "application/json"}, timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise JiraError(f"could not reach the site ({exc.__class__.__name__})") from None
        if response.status_code < 400:
            try:
                return response.json()
            except ValueError:
                raise JiraError("the answer is not JSON", response.status_code) from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        errors = data.get("errors") if isinstance(data.get("errors"), dict) else {}
        text = "; ".join([*(data.get("errorMessages") or []), *(f"{k}: {v}" for k, v in errors.items())])
        message = text or {401: "the email or API token is wrong", 403: "this account has no permission for that", 404: "not found, or no permission to see it"}.get(response.status_code, "request failed")
        if response.status_code == 400:
            raise _Rejected(message, response.status_code, errors)
        raise JiraError(message, response.status_code)


class _Rejected(JiraError):
    """A 400 with field errors: `create_issue` may retry without an optional field."""

    def __init__(self, message: str, status: int, fields: dict):
        super().__init__(message, status)
        self.fields = fields


def client_from_env() -> Client | None:
    """The client when all three settings are present, else None (the app works without Jira)."""
    if not (config.JIRA_BASE_URL and config.JIRA_EMAIL and config.JIRA_API_TOKEN):
        return None
    return Client(config.JIRA_BASE_URL, config.JIRA_EMAIL, config.JIRA_API_TOKEN)


def scrub(text: str) -> str:
    """No user's email leaves for Jira, whatever the user or the model wrote."""
    return EMAIL.sub("[email removed]", text)


def _paragraphs(text: str) -> list[dict]:
    return [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in text.splitlines() if line.strip()]


def _heading(text: str) -> dict:
    return {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": text}]}


def _bullets(lines: list[str]) -> dict:
    return {"type": "bulletList", "content": [{"type": "listItem", "content": _paragraphs(line)} for line in lines]}


def description(ticket: dict, details: str, by: str, ticket_url: str | None = None) -> dict:
    """The issue body in Atlassian Document Format. Everything is plain text nodes: no markup from a user runs."""
    triage = ticket.get("triage") or {}
    content = _paragraphs(scrub(details))
    if triage.get("root_cause"):
        content += [_heading("Why we think so"), *_paragraphs(scrub(triage["root_cause"]))]
    evidence = [scrub(line) for line in triage.get("evidence") or [] if line.strip()]
    if evidence:
        content += [_heading("Evidence"), _bullets(evidence)]
    who = ticket.get("customer_user_id") or "not identified"
    facts = f"Ticket {ticket['id']}, customer_user_id {who}."
    content += [_heading("Support ticket"), *_paragraphs(facts)]
    if ticket_url:
        content.append({"type": "paragraph", "content": [{"type": "text", "text": "Open the ticket", "marks": [{"type": "link", "attrs": {"href": ticket_url}}]}]})
    source = "the triage agent" if by == "agent" else "a support person, from the agent's report"
    content += _paragraphs(f"Filed by {source}. It rests on what a user wrote: check it before acting on it.")
    return {"type": "doc", "version": 1, "content": content}


def label_for(ticket_id: str) -> str:
    return "resolve-" + re.sub(r"[^A-Za-z0-9_-]", "-", ticket_id)


def _report(ticket: dict, edits: JiraExport, teams: list, by: str) -> tuple:
    """(team, title, details, priority): the agent's report with the person's edits on top."""
    triage = ticket.get("triage") or {}
    report = triage.get("bug_report") or {}
    if by == "agent" and not report:
        raise ValueError("the agent wrote no bug report for this ticket")
    name = edits.team or report.get("team") or (teams[0].name if len(teams) == 1 else None)
    if not name:
        raise ValueError("pick a team")
    team = next((t for t in teams if t.name == name), None)
    if team is None:
        raise ValueError(f"no team named {name!r} in Agent settings")
    title = edits.summary or report.get("summary") or triage.get("summary") or ticket["subject"]
    details = edits.details or report.get("details") or ticket["body"]
    priority = edits.priority or report.get("priority") or "medium"
    if not title.strip() or not details.strip():
        raise ValueError("the title and the details cannot be empty")
    return team, " ".join(scrub(title).split())[:255], details, priority


def export(services, ticket_id: str, by: str = "person", edits: JiraExport | None = None) -> dict:
    """File one issue for the ticket and record it. Returns the ticket.

    Raises NotConnected without Jira settings, ValueError when the report or the team makes no sense.
    A failure from Jira itself is recorded in `jira_error`, with nothing else changed, so it can be retried.
    Demo mode makes no call and records `{demo: true}`.
    """
    conn = services.conn
    with _export_lock:
        ticket = db.get_ticket(conn, ticket_id)
        if ticket["jira"] and not ticket["jira"].get("demo"):
            return ticket
        cfg = policy.load_config(conn).jira
        team, title, details, priority = _report(ticket, edits or JiraExport(), cfg.teams, by)
        link = {"team": team.name, "project_key": team.project_key, "title": title, "by": by, "created_at": db.now_iso()}
        if services.demo:
            db.update_ticket(conn, ticket_id, jira_json=json.dumps({**link, "demo": True}), jira_error=None)
            return db.get_ticket(conn, ticket_id)
        if services.jira is None:
            raise NotConnected("Jira is not connected: set JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN in .env")
        label = label_for(ticket_id)
        ticket_url = f"{config.RESOLVE_PUBLIC_URL}/static/ui/Ticket.dc.html?id={ticket_id}" if config.RESOLVE_PUBLIC_URL else None
        try:
            key = services.jira.find_by_label(team.project_key, label)
            if key is None:
                created = services.jira.create_issue({
                    "project": {"key": team.project_key},
                    "issuetype": {"name": team.issue_type},
                    "summary": title,
                    "description": description(ticket, details, by, ticket_url),
                    "labels": [label, "resolve"],
                    "priority": {"name": PRIORITIES[priority]},
                })
                key = created["key"]
        except JiraError as exc:
            db.update_ticket(conn, ticket_id, jira_error=str(exc))
            return db.get_ticket(conn, ticket_id)
        link.update(key=key, url=services.jira.browse_url(key))
        db.update_ticket(conn, ticket_id, jira_json=json.dumps(link), jira_error=None)
        return db.get_ticket(conn, ticket_id)


def export_with_reply(services, ticket_id: str, edits: JiraExport | None) -> None:
    """A reply went out on a ticket with a bug report: it told the user the team has it, so the team gets it.

    The ticket is already closed by then: a failure is recorded on it and never undoes the reply.
    """
    ticket = db.get_ticket(services.conn, ticket_id)
    if ticket["jira"] or not (ticket.get("triage") or {}).get("bug_report"):
        return
    try:
        export(services, ticket_id, "person", edits)
    except (ValueError, NotConnected) as exc:
        db.update_ticket(services.conn, ticket_id, jira_error=str(exc))
    except Exception:  # noqa: BLE001
        log.exception("ticket %s: Jira export failed", ticket_id)
        db.update_ticket(services.conn, ticket_id, jira_error="The Jira export failed: try it from the ticket.")


def auto_export(services, ticket_id: str) -> None:
    """After triage, when the owner turned it on: the agent files its own report. Never fails the triage."""
    cfg = policy.load_config(services.conn).jira
    if not cfg.auto_create or services.demo or services.jira is None:
        return
    ticket = db.get_ticket(services.conn, ticket_id)
    if ticket["jira"] or not (ticket.get("triage") or {}).get("bug_report"):
        return
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if db.count_agent_jira(services.conn, since) >= cfg.max_auto_per_day:
        db.update_ticket(services.conn, ticket_id, jira_error=f"The agent's limit of {cfg.max_auto_per_day} issues a day is reached: a person can still create this one.")
        return
    try:
        export(services, ticket_id, by="agent")
    except (ValueError, NotConnected) as exc:
        db.update_ticket(services.conn, ticket_id, jira_error=str(exc))
    except Exception:  # noqa: BLE001 - the ticket is already triaged: it must reach the person anyway
        log.exception("ticket %s: Jira export failed", ticket_id)
        db.update_ticket(services.conn, ticket_id, jira_error="The Jira export failed: try it from the ticket.")

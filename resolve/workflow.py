"""The ticket state machine: open (profile), propose (Level 1 triage), accept / reject (the person's call), dismiss.

Statuses: new → triaged (profile fetched) → proposed (triage ticket cached) → approved (the person
accepted an option; the action is in flight) → done (action succeeded, reply marked sent), plus
dismissed (rejected with nothing to send).

Accept is idempotent: the proposed → approved transition is a single conditional UPDATE, so a
second click, a double submit or a stale tab finds the ticket no longer `proposed` and does
nothing. An Adapty failure records the error and returns the ticket to `proposed`; nothing is
marked done unless Adapty said yes. The only Adapty writes go through `actions.execute`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from resolve import actions, db, jira, llm, policy, triage
from resolve.adapty import AdaptyError, Client
from resolve.jira import Client as JiraClient
from resolve.llm import LLM
from resolve.models import Decision, Rejection

OPEN_STATUSES = ("new", "triaged", "proposed")


@dataclass
class Services:
    conn: db.Database
    demo: bool
    adapty: Client | None = None
    llm: LLM | None = None
    jira: JiraClient | None = None
    frozen_now: datetime | None = None  # demo mode: Analyze reads the snapshot as of its last event


class OptionsChanged(Exception):
    pass


class NotFound(Exception):
    pass


class TranslationFailed(Exception):
    """The person's English could not be put into the user's language: nothing was sent or run."""


TRANSLATE_PROMPT = """You translate one support reply from English into {language}, for a user of a subscription app. Translate faithfully, sentence by sentence: add no promise, fact, greeting or apology, and drop nothing. Keep every amount, date, product name, URL and number exactly as written. Output only the translation."""


def translate_reply(services: Services, ticket_id: str, text: str, language: str) -> str:
    """One small model call on the person's click; counted in the ticket's cap. Raises TranslationFailed."""
    if services.demo or services.llm is None:
        raise TranslationFailed(f"Could not translate to {language}: no model is connected. Nothing was sent.")
    if not db.reserve_model_call(services.conn, ticket_id, triage.MAX_CALLS):
        raise TranslationFailed(f"Could not translate to {language}: the model call cap for this ticket is reached. Nothing was sent.")
    try:
        step = services.llm.step(TRANSLATE_PROMPT.format(language=language), [{"role": "user", "content": text}], [])
    except llm.LLMError:
        raise TranslationFailed(f"Could not translate to {language}: the model call failed. Nothing was sent.") from None
    out = (step.text or "").strip()
    if step.stop in ("max_tokens", "refusal") or not out or out.casefold() == text.strip().casefold():
        raise TranslationFailed(f"Could not translate to {language}: no usable translation came back. Nothing was sent.")
    missing = set(re.findall(r"\d+", text)) - set(re.findall(r"\d+", out))
    if missing:
        raise TranslationFailed(f"Could not translate to {language}: the translation lost the number {sorted(missing)[0]}. Nothing was sent.")
    return out


def user_language(ticket: dict) -> str | None:
    """The ticket's non-English user language, or None (English, unknown, not triaged)."""
    language = (ticket.get("triage") or {}).get("user_language")
    return None if triage.is_english(language) else language


def _ticket(services: Services, ticket_id: str) -> dict:
    ticket = db.get_ticket(services.conn, ticket_id)
    if ticket is None:
        raise NotFound(ticket_id)
    return ticket


def triage_of(ticket: dict) -> dict | None:
    """The ticket's triage (v2), or its v1 proposal as a one-option legacy ticket, or None."""
    if ticket.get("triage"):
        return ticket["triage"]
    if ticket.get("proposal"):
        return triage.legacy_to_ticket(ticket["proposal"])
    return None


def open_ticket(services: Services, ticket_id: str, refresh: bool = True) -> dict:
    """Fetch the live profile (live mode) and move a new ticket to triaged. Never raises on Adapty errors."""
    ticket = _ticket(services, ticket_id)
    if services.demo or not refresh or services.adapty is None or not ticket["customer_user_id"]:
        return ticket
    try:
        profile = services.adapty.get_profile(ticket["customer_user_id"])
        db.update_ticket(services.conn, ticket_id, profile_json=json.dumps(profile), profile_fetched_at=db.now_iso(), profile_error=None)
        db.set_status_if(services.conn, ticket_id, "new", "triaged")
    except AdaptyError as exc:
        db.update_ticket(services.conn, ticket_id, profile_error=str(exc))
    return _ticket(services, ticket_id)


def propose(services: Services, ticket_id: str, force: bool = False) -> dict:
    """Run Level 1 unless a ticket is cached. Stores the triage or `triage_error`; never raises on model errors."""
    _ticket(services, ticket_id)
    return triage.run(services, ticket_id, force=force)


def final_option(services: Services, option: dict, decision: Decision) -> dict:
    """The option with the person's edits applied; grant days default to the option's, else the config max."""
    edits = decision.edits
    params = dict(option["params"])
    for key in ("days", "kind", "store", "question"):
        if getattr(edits, key) is not None:
            params[key] = getattr(edits, key)
    final = {
        "action": edits.action or option["action"],
        "params": params,
        "reply": option["reply"] if edits.reply is None else edits.reply,
    }
    if final["action"] == "grant_access":
        days = params.get("days") or policy.load_config(services.conn).grant.max_days
        if not 1 <= days <= 365:
            raise ValueError(f"days must be between 1 and 365, got {days}")
        params["days"] = days
    return final


def accept(services: Services, ticket_id: str, decision: Decision) -> dict:
    """Run one option (with edits). Only a `proposed` ticket can be accepted, exactly once.

    Raises ValueError when the option index is out of range or the edits make no sense.
    """
    ticket = _ticket(services, ticket_id)
    if ticket["status"] != "proposed":
        return ticket
    proposed = triage_of(ticket)
    if proposed is None:
        raise ValueError("the ticket has no options to accept")
    if decision.option >= len(proposed["options"]):
        raise ValueError(f"option {decision.option} does not exist; the ticket has {len(proposed['options'])}")
    if decision.expected_action and proposed["options"][decision.option].get("action") != decision.expected_action:
        raise OptionsChanged("the options changed after a new message from the user; reload the ticket")
    final = final_option(services, proposed["options"][decision.option], decision)
    language = user_language(ticket)
    edits = decision.edits
    if language:
        # Before anything runs: a failed translation leaves the ticket proposed, with no grant and no message.
        final["language"] = language
        final["reply_en"] = None if edits.reply is not None else (edits.reply_en if edits.reply_en is not None else proposed["options"][decision.option].get("reply_en"))
        if edits.reply is None and edits.reply_en is not None and edits.reply_en.strip():
            final["reply"] = translate_reply(services, ticket_id, edits.reply_en, language)
    elif edits.reply is None and edits.reply_en is not None:
        final["reply"] = edits.reply_en
    customer = ticket["customer_user_id"]
    if final["action"] == "grant_access" and not customer:
        raise ValueError("grant_access needs an identified customer")
    if not db.set_status_if(services.conn, ticket_id, "proposed", "approved"):
        return _ticket(services, ticket_id)
    try:
        result = actions.execute(services, customer, final["action"], final["params"], source="l1", ref=ticket_id)
    except AdaptyError as exc:
        db.set_status_if(services.conn, ticket_id, "approved", "proposed")
        db.update_ticket(services.conn, ticket_id, action_error=str(exc))
        return _ticket(services, ticket_id)
    except Exception:
        # Unexpected failure before any grant: never leave the ticket stuck in `approved`.
        db.set_status_if(services.conn, ticket_id, "approved", "proposed")
        raise
    # From here on the action happened: the ticket is done and the action recorded, so a second
    # Accept is impossible.
    action = dict(result, option=decision.option, final=final, approved_at=db.now_iso())
    if action.get("demo"):
        action["note"] = "Demo mode: no Adapty call was made; only the local ticket changed."
    fields = {
        "action_json": json.dumps(action),
        "action_at": db.now_iso(),
        "action_error": None,
        "decision_json": json.dumps(decision.model_dump()),
        "status": "done",
    }
    if final["action"] != "no_reply" and final["reply"].strip():
        fields["reply_sent_at"] = db.now_iso()
        _post_reply(services, ticket, final["reply"])
    db.update_ticket(services.conn, ticket_id, **fields)
    if final["action"] != "no_reply" and final["reply"].strip():
        jira.export_with_reply(services, ticket_id, decision.jira)
    return _ticket(services, ticket_id)


def approve(services: Services, ticket_id: str) -> dict:
    """The old pages' Approve: accept the best option as is."""
    return accept(services, ticket_id, Decision(option=0))


def reject(services: Services, ticket_id: str, rejection: Rejection) -> dict:
    """Reject all options: with a reply → done (reply marked sent), without → dismissed. No Adapty call.

    Allowed while the ticket is open (new, triaged, proposed), so a manual reply works when triage
    failed; never while an accept is in flight, and never after done or dismissed.
    """
    ticket = _ticket(services, ticket_id)
    reply = (rejection.reply or "").strip()
    english = (rejection.reply_en or "").strip()
    language = user_language(ticket)
    if english and not reply:
        # An open ticket only: a closed one must not spend a call. A failure raises and nothing changes.
        if language and ticket["status"] in OPEN_STATUSES:
            reply = translate_reply(services, ticket_id, english, language)
        elif not language:
            reply = english
    decision = {"note": rejection.note, "reply": reply or rejection.reply, "rejected": True}
    if language and reply:
        decision.update(language=language, reply_en=english or None)
    fields = {"decision_json": json.dumps(decision), "status": "done" if reply else "dismissed"}
    if reply:
        fields["reply_sent_at"] = db.now_iso()
    if db.update_ticket(services.conn, ticket_id, only_status=OPEN_STATUSES, **fields) and reply:
        _post_reply(services, ticket, reply)
    return _ticket(services, ticket_id)


CLOSED_STATUSES = ("done", "dismissed")


def reopen(services: Services, ticket_id: str, note: str = "") -> dict:
    """Send a closed ticket back to Incoming: `proposed` when it has a triage (or a v1 proposal), else `new`.

    The earlier decision and action move to `history`; nothing is undone in Adapty (a grant stays).
    Only from done/dismissed, as one conditional update, so a double click reopens once.
    """
    ticket = _ticket(services, ticket_id)
    if ticket["status"] not in CLOSED_STATUSES:
        return ticket
    entry = {"at": db.now_iso(), "from_status": ticket["status"], "decision": ticket["decision"], "action": ticket["action"], "note": note}
    db.update_ticket(
        services.conn,
        ticket_id,
        only_status=CLOSED_STATUSES,
        status="proposed" if (ticket["triage"] or ticket["proposal"]) else "new",
        history_json=json.dumps([*ticket["history"], entry]),
        decision_json=None,
        action_json=None,
        action_at=None,
        action_error=None,
        reply_sent_at=None,
    )
    return _ticket(services, ticket_id)


def _post_reply(services: Services, ticket: dict, reply: str) -> None:
    """A ticket from a chat handoff: the reply shows up in that chat. Nothing is sent anywhere else."""
    if ticket.get("session_id"):
        db.append_message(services.conn, ticket["session_id"], "support", reply)


def dismiss(services: Services, ticket_id: str) -> dict:
    """Dismiss an open ticket. Not while an accept is in flight, and never after done or dismissed."""
    _ticket(services, ticket_id)
    db.update_ticket(services.conn, ticket_id, only_status=OPEN_STATUSES, status="dismissed")
    return _ticket(services, ticket_id)

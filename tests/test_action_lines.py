"""The line under a ticket's title in Incoming: its kind, its words and the part in the accent colour."""

from __future__ import annotations

from resolve.api import ticket_summary
from resolve.tickets import CANCELLED_HANDOFF, TOUGH_AT_MOST, TOUGH_LINE, action_accent, action_line, action_state


OPTION = {"action": "reply_only", "reply": "Hi", "risk": "low"}


def line(status="proposed", options=2, confidence=0.9, error=None, triaged=True):
    view = {"confidence": confidence, "options": [OPTION] * options} if triaged else None
    return action_line({"triage_error": error}, status, view, (view or {}).get("options") or [])


def test_a_waiting_ticket_invites_and_says_how_many_responses_there_are():
    assert line(options=3) == "We've generated 3 responses, pick one"
    assert line(options=2) == "We've generated 2 responses, pick one"
    assert line(options=1) == "We've generated 1 response, take a look"


def test_tough_means_no_option_is_sure():
    scored = lambda *scores: [dict(OPTION, confidence=c) for c in scores]
    state = lambda view_conf, *scores: action_state({}, "proposed", {"confidence": view_conf, "options": scored(*scores)}, scored(*scores))
    assert state(0.9, 0.7, 0.5) == ("tough", TOUGH_LINE)
    assert state(0.62, 0.71, 0.5)[0] == "generated", "one option the agent is fairly sure of, so the claim would be false"
    assert state(None, 0.2)[0] == "tough"


def test_without_option_scores_the_triage_confidence_decides_and_a_missing_one_is_not_invented():
    assert line(confidence=TOUGH_AT_MOST) == TOUGH_LINE
    assert line(confidence=0) == TOUGH_LINE
    assert line(confidence=TOUGH_AT_MOST + 0.01) == "We've generated 2 responses, pick one"
    assert line(confidence=None) == "We've generated 2 responses, pick one"


def test_the_kinds():
    assert action_state({}, "new", None, []) == ("generating", "Still generating, please wait")
    assert action_state({"triage_error": "no key"}, "new", None, [])[0] == "failed"
    assert action_state({"decision": {"option": 0, "edits": {}}}, "done", {"options": [OPTION]}, [OPTION])[0] == "finished"
    assert action_state({"decision": {"rejected": True, "reply": "Hi"}}, "done", None, [])[0] == "finished"
    withdrawn = {"decision": {"rejected": True, "reply": None, "note": f"{CANCELLED_HANDOFF}: changed my mind"}}
    assert action_state(withdrawn, "done", None, [])[0] == "closed"


def test_a_person_closing_without_a_reply_is_the_lighter_kind():
    assert action_state({"decision": {"rejected": True, "reply": None, "note": "spam"}}, "dismissed", None, [])[0] == "closed"


def test_the_accent_names_what_the_person_does():
    assert action_accent("We've generated 3 responses, pick one") == "pick"
    assert action_accent("We've generated 1 response, take a look") == "take a look"
    assert action_accent(TOUGH_LINE) == "your call"
    assert action_accent("You've chosen an option") == "option"
    assert action_accent("You've chosen an option and edited it") == "edited"
    assert action_accent("You've written your own reply") == "You've written your own reply"
    assert action_accent("You've closed it without a reply") == "closed"
    assert action_accent("Closed without a reply") is None
    assert action_accent("Still generating, please wait") is None
    assert action_accent(None) is None


def test_the_agent_is_at_work_only_until_a_result_or_an_error_exists():
    assert line("new", triaged=False) == "Still generating, please wait"
    assert line("new", triaged=False, error="LLM is not configured") == "Triage failed"
    assert line("new", options=3) == "We've generated 3 responses, pick one"
    assert line("new", options=3, error="the rerun failed") == "We've generated 3 responses, pick one"


def test_a_legacy_proposal_without_a_confidence_is_not_tough():
    ticket = {
        "id": "x", "status": "proposed", "source": "email", "category": "other", "customer_user_id": "", "email": None, "subject": "s", "body": "b",
        "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z", "triage": None, "triage_error": None,
        "proposal": {"action": "explain", "category": "other", "reply": "Hi", "root_cause": "s"},
    }
    summary = ticket_summary(ticket)
    assert summary["confidence"] is None and summary["action_title"] == "We've generated 1 response, take a look"


def closed(decision, status="done", action=None, options=(OPTION, OPTION)):
    return action_line({"decision": decision, "action": action, "status": status}, status, {"options": list(options)}, list(options))


def test_a_sent_option_says_whether_it_was_edited():
    assert closed({"option": 0, "edits": {}}) == "You've chosen an option"
    assert closed({"option": 0, "edits": {"reply": None, "days": None}}) == "You've chosen an option"
    assert closed({"option": 1, "edits": {"reply": "Changed by hand"}}) == "You've chosen an option and edited it"
    assert closed({"option": 1, "edits": {"days": 3}}) == "You've chosen an option and edited it"


def test_a_persons_own_reply():
    assert closed({"rejected": True, "reply": "Hi", "note": ""}) == "You've written your own reply"


def test_a_person_closing_without_a_reply():
    assert closed({"rejected": True, "reply": None, "note": "spam"}, "dismissed") == "You've closed it without a reply"
    quiet = {"action": "no_reply", "reply": "", "risk": "low"}
    assert closed({"option": 0, "edits": {}}, options=(quiet, OPTION)) == "You've closed it without a reply"
    assert closed({"option": 1, "edits": {}}, action={"final": quiet}) == "You've closed it without a reply"


def test_no_you_when_no_person_decided():
    withdrawn = {"rejected": True, "reply": None, "note": f"{CANCELLED_HANDOFF}: changed my mind"}
    assert closed(withdrawn, "dismissed") == "The user withdrew the request"
    assert closed(None, "dismissed") == "Closed without a reply"
    assert closed(None, "done") == "Closed"

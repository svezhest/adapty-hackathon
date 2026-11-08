"""The demo feed's long tail: deterministic, every ticket holds up on click, and no number counts it."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from resolve import analytics, config
from resolve.api import ticket_summary
from resolve.models import TriageTicket
from resolve.tickets import CANCELLED_HANDOFF, GENERATED, TOUGH_BELOW, action_line
from resolve.web import create_app
from scripts import generate_feed

MONEY = re.compile(r"(?:CA\$|A\$|R\$|MX\$|[$€£¥₹])\s?\d[\d,]*(?:\.\d\d)?(?<![,])|\d[\d.,]* (?:PLN|SEK|TRY)")
DATE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}(?:, \d{4})?")
STAMPS = ("purchased_at", "originally_purchased_at", "expires_at", "renewal_cancelled_at", "billing_issue_detected_at")


def rows(path=config.DEMO_DB_PATH, where="rule = 'generated'") -> list[dict]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out = [dict(r) for r in conn.execute(f"SELECT * FROM tickets WHERE {where} ORDER BY id")]
    conn.close()
    return out


GENERATED_ROWS = rows()


def english_texts(row: dict) -> list[str]:
    tri = json.loads(row["triage_json"])
    texts = [tri["summary"], tri["root_cause"]]
    if tri["user_language"]:
        texts += tri["messages_en"]
        texts += [o["reply_en"] for o in tri["options"]]
    else:
        texts += [row["subject"], row["body"]]
        texts += [o["reply"] for o in tri["options"]]
    return texts


def all_texts(row: dict) -> list[str]:
    tri = json.loads(row["triage_json"])
    return english_texts(row) + [row["subject"], row["body"], *[o["reply"] for o in tri["options"]]]


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "demo.sqlite"
    shutil.copyfile(config.DEMO_DB_PATH, path)
    return TestClient(create_app(path, demo=True, seed_file=tmp_path / "none.json"))


def test_the_committed_snapshot_is_what_the_script_makes_and_a_rerun_changes_nothing(tmp_path):
    path = tmp_path / "demo.sqlite"
    shutil.copyfile(config.DEMO_DB_PATH, path)
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("DELETE FROM tickets WHERE rule = ?", (GENERATED,))
    conn.close()
    generate_feed.apply(path)
    first = rows(path)
    generate_feed.apply(path)
    assert rows(path) == first == GENERATED_ROWS


def test_the_feed_is_long_mostly_open_and_marked():
    assert 350 <= len(GENERATED_ROWS) <= 450
    closed = [r for r in GENERATED_ROWS if r["status"] in ("done", "dismissed")]
    assert 0 < len(closed) < len(GENERATED_ROWS) * 0.3
    assert all(r["status"] in ("proposed", "done", "dismissed") for r in GENERATED_ROWS)


def test_the_real_tickets_stay_on_top_and_are_the_same_31():
    real = rows(where="rule != 'generated'")
    assert len(real) == 31
    assert all(json.loads(r["triage_json"])["options"] for r in real)
    oldest_real = min(r["created_at"] for r in real)
    newest = max(r["created_at"] for r in GENERATED_ROWS)
    oldest = min(r["created_at"] for r in GENERATED_ROWS)
    assert newest < oldest_real
    gap = datetime.fromisoformat(oldest_real.replace("Z", "+00:00")) - datetime.fromisoformat(oldest.replace("Z", "+00:00"))
    assert gap <= timedelta(days=41)


def test_every_ticket_has_two_or_three_different_options_within_the_contract():
    for row in GENERATED_ROWS:
        tri = json.loads(row["triage_json"])
        TriageTicket(**tri)
        options = tri["options"]
        assert 2 <= len(options) <= 3, row["id"]
        assert len({o["reply"] for o in options}) == len(options), row["id"]
        for o in options:
            assert o["risk"] in ("low", "medium", "high") and 0 <= o["confidence"] <= 1
            assert len(o["reply"].split()) <= 120, row["id"]
            assert o["action"] == "no_reply" or o["reply"].strip(), row["id"]
            assert "{" not in o["reply"] and "None" not in o["reply"], row["id"]
        assert "{" not in row["subject"] + row["body"], row["id"]


def test_amounts_agree_with_the_users_plan_price():
    for row in GENERATED_ROWS:
        price = json.loads(row["context_json"]).get("price")
        allowed = {price}
        if not row["customer_user_id"]:
            allowed = set(MONEY.findall(row["body"]))  # an unmatched sender: only what they claim themselves
        for text in all_texts(row):
            for found in MONEY.findall(text):
                assert found in allowed, (row["id"], found, allowed)


def test_dates_agree_with_the_profile_on_that_ticket():
    for row in GENERATED_ROWS:
        if not row["profile_json"]:
            assert not DATE.findall(" ".join(english_texts(row))), row["id"]
            continue
        profile = json.loads(row["profile_json"])
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        stamps = {created}
        for entry in profile["subscriptions"] + profile["access_levels"]:
            for field in STAMPS:
                if entry.get(field):
                    stamps.add(datetime.fromisoformat(entry[field]))
        sub = profile["subscriptions"][0] if profile["subscriptions"] else None
        if sub:
            orig = datetime.fromisoformat(sub["originally_purchased_at"])
            stamps |= {orig + timedelta(days=3), orig + timedelta(days=7)}  # the first charge after a trial
            ends = datetime.fromisoformat(sub["expires_at"])
            for days in (3, 7):
                stamps.add(max(created, ends) + timedelta(days=days))  # free days added to the plan
        allowed = set()
        for stamp in stamps:
            short = f"{stamp:%b} {stamp.day}"
            allowed |= {short, f"{short}, {stamp.year}"}
        for text in english_texts(row):
            for found in DATE.findall(text):
                assert found in allowed, (row["id"], found, sorted(allowed))


def test_plan_and_store_named_in_the_text_are_the_users_own():
    for row in GENERATED_ROWS:
        if not row["profile_json"]:
            continue
        profile = json.loads(row["profile_json"])
        if not profile["subscriptions"]:
            continue
        sub = profile["subscriptions"][0]
        replies = " ".join(english_texts(row))
        other = "Google Play" if sub["store"] == "app_store" else "the App Store"
        assert other not in replies, row["id"]
        product = sub["store_product_id"]
        for word in ("weekly plan", "monthly plan", "annual plan"):
            if word in replies:
                assert word.split()[0] in product or (word == "annual plan" and "annual" in product), (row["id"], word, product)


def test_users_come_from_the_seed_and_addresses_are_example_com():
    base = sqlite3.connect(f"file:{generate_feed.EVAL_BASE}?mode=ro", uri=True)
    seeded = {r[0] for r in base.execute("SELECT customer_user_id FROM tickets")}
    base.close()
    real_users = {r["customer_user_id"] for r in rows(where="rule != 'generated'")}
    for row in GENERATED_ROWS:
        if row["customer_user_id"]:
            assert row["customer_user_id"] in seeded and row["customer_user_id"] not in real_users, row["id"]
        if row["email"]:
            assert row["email"].endswith("@example.com"), row["id"]


def test_the_summary_carries_what_a_row_shows(client):
    tickets = {t["id"]: t for t in client.get("/api/tickets").json()}
    assert len(tickets) == 31 + len(GENERATED_ROWS)
    for row in GENERATED_ROWS:
        t = tickets[row["id"]]
        tri = json.loads(row["triage_json"])
        assert t["top_risk"] == tri["options"][0]["risk"]
        assert t["action_title"], row["id"]


OPTION = {"action": "reply_only", "reply": "Hi", "risk": "low"}


def line(status="proposed", options=2, confidence=0.9, error=None, triaged=True):
    view = {"confidence": confidence, "options": [OPTION] * options} if triaged else None
    return action_line({"triage_error": error}, status, view, (view or {}).get("options") or [])


def test_a_waiting_ticket_says_how_many_responses_there_are():
    assert line(options=3) == "We've generated 3 responses"
    assert line(options=2) == "We've generated 2 responses"
    assert line(options=1) == "We've generated 1 response"


def test_a_low_confidence_reads_as_tough_and_a_missing_one_is_not_invented():
    assert line(confidence=TOUGH_BELOW - 0.01) == "This one is looking tough"
    assert line(confidence=0) == "This one is looking tough"
    assert line(confidence=TOUGH_BELOW) == "We've generated 2 responses"
    assert line(confidence=None) == "We've generated 2 responses"


def test_the_agent_is_at_work_only_until_a_result_or_an_error_exists():
    assert line("new", triaged=False) == "Agent is looking into it"
    assert line("new", triaged=False, error="LLM is not configured") == "Triage failed"
    assert line("new", options=3) == "We've generated 3 responses"
    assert line("new", options=3, error="the rerun failed") == "We've generated 3 responses"


def test_a_legacy_proposal_without_a_confidence_is_not_tough():
    ticket = {
        "id": "x", "status": "proposed", "source": "email", "category": "other", "customer_user_id": "", "email": None, "subject": "s", "body": "b",
        "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z", "triage": None, "triage_error": None,
        "proposal": {"action": "explain", "category": "other", "reply": "Hi", "root_cause": "s"},
    }
    summary = ticket_summary(ticket)
    assert summary["confidence"] is None and summary["action_title"] == "We've generated 1 response"


def test_both_waiting_lines_occur_and_tough_stays_occasional(client):
    tickets = client.get("/api/tickets").json()
    waiting = [t for t in tickets if t["status"] == "proposed"]
    tough = [t for t in waiting if t["action_title"] == "This one is looking tough"]
    assert all(t["action_title"] == "This one is looking tough" or re.fullmatch(r"We've generated [23] responses", t["action_title"]) for t in waiting)
    assert 0.05 < len(tough) / len(waiting) < 0.2
    assert any(not t["id"].startswith("g-") for t in tough), "the real tickets at the top of the feed show it too"
    assert any(t["action_title"] == "We've generated 2 responses" for t in waiting) and any(t["action_title"] == "We've generated 3 responses" for t in waiting)
    assert not any(t["action_title"] == "Agent is looking into it" for t in tickets)


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


def test_every_closed_ticket_in_the_snapshot_reads_as_its_decision(client):
    tickets = client.get("/api/tickets").json()
    lines = {t["action_title"] for t in tickets if t["status"] in ("done", "dismissed")}
    assert {"You've chosen an option", "You've chosen an option and edited it", "You've written your own reply", "You've closed it without a reply"} == lines


def _numbers(path) -> dict:
    client = TestClient(create_app(path, demo=False, seed_file=path.parent / "none.json"))
    conn = client.app.state.services.conn
    now = analytics.last_event(conn)
    out = {name: analytics.compute(conn, name, 0, now=now) for name in ("today", "7d", "30d")}
    insights = client.get("/api/insights").json()
    out["insights"] = {k: insights[k] for k in ("total", "triaged", "categories", "top", "tested", "report")}
    out["users"] = client.get("/api/users").json()
    out["last_event"] = now
    return out


def test_no_number_counts_the_generated_tickets(tmp_path):
    with_tail = tmp_path / "with.sqlite"
    without = tmp_path / "without.sqlite"
    shutil.copyfile(config.DEMO_DB_PATH, with_tail)
    shutil.copyfile(config.DEMO_DB_PATH, without)
    conn = sqlite3.connect(without)
    with conn:
        conn.execute("DELETE FROM tickets WHERE rule = ?", (GENERATED,))
    conn.close()
    a, b = _numbers(with_tail), _numbers(without)
    for key in ("today", "7d", "30d"):
        a[key].pop("eval", None), b[key].pop("eval", None)
    assert a == b
    assert a["today"]["tickets"]["total"] == 31


def test_a_generated_ticket_opens_and_can_be_accepted_in_demo_mode(client):
    row = next(r for r in GENERATED_ROWS if r["status"] == "proposed" and r["customer_user_id"])
    ticket = client.get(f"/api/tickets/{row['id']}").json()
    assert ticket["profile"]["customer_user_id"] == row["customer_user_id"] and len(ticket["triage"]["options"]) >= 2
    done = client.post(f"/api/tickets/{row['id']}/accept", json={"option": 0}).json()
    assert done["status"] == "done" and done["action"]["demo"] is True
    assert client.get("/api/analytics").json()["tickets"]["total"] == 31

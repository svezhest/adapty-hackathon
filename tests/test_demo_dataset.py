"""The demo dataset: its shape, and a provenance record for every piece."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest

from resolve import analytics, config, db
from resolve.demo_clock import shift_to_now

DIR = Path(config.DEMO_DB_PATH).parent / "dataset"
SECRET = re.compile(r"sk-[A-Za-z0-9]{16,}|secret_live|Api-Key\s+\S{16,}")


def load(name):
    return json.loads((DIR / name).read_text())


@pytest.fixture(scope="module")
def conn():
    connection = sqlite3.connect(f"file:{config.DEMO_DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def test_two_hundred_tickets_in_three_languages_mostly_chats():
    cards = load("cards.json")
    assert Counter(c["lang"] for c in cards) == {"en": 160, "ru": 20, "sr": 20}
    sources = Counter(c["source"] for c in cards)
    assert sources["chat"] > sources["email"] > 0
    assert len(load("tickets.json")) == 200


def test_each_person_and_style_appears_once():
    cards = load("cards.json")
    assert len({c["face"]["name"] for c in cards}) == 200
    assert len({(c["style"]["habit"], c["style"]["tone"], c["style"]["opening"]) for c in cards}) == 200
    assert len({t["voice"] for t in load("tickets.json")}) == 200


def test_tickets_share_no_long_run_with_a_review():
    assert load("check.json")["review_overlap"]["at_least_8"] == []


def test_the_demo_opens_with_ten_answered_and_ninety_waiting(conn):
    statuses = Counter(r[0] for r in conn.execute("SELECT status FROM tickets"))
    assert sum(statuses.values()) == 100
    assert statuses["proposed"] == 90 and statuses["done"] + statuses["dismissed"] == 10
    demo = [c for c in load("cards.json") if c["in_demo"]]
    assert Counter(c["lang"] for c in demo) == {"en": 80, "ru": 10, "sr": 10}


def test_every_triage_came_from_a_recorded_model_call(conn):
    prov = load("provenance.json")
    assert prov["models"]["writers"] == "claude-sonnet-5-5"
    assert prov["models"]["triage_owner_name"] == "deepseek-v4.1-flash-1017"
    records = {p["ticket"]: p for p in prov["tickets"] if p["ticket"]}
    for row in conn.execute("SELECT id, triage_json FROM tickets"):
        assert row["triage_json"], row["id"]
        calls = records[row["id"]]["triage"]["calls"]
        assert [c for c in calls if not c.get("superseded")], row["id"]
        assert all(c["response_id"] and c["response_model"] == "deepseek-flash" for c in calls)
    assert prov["insights_calls"] and conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0] == 1


def test_every_ticket_names_its_sources():
    prov = load("provenance.json")
    assert len(prov["tickets"]) == 200
    for record in prov["tickets"]:
        assert record["written_by"]["model"] == "claude-sonnet-5-5"
        assert record["label_ref"] and record["case_source"] and record["face"] and record["account"]


def test_the_people_are_marked_as_simulated(conn):
    prov = load("provenance.json")
    people = [p for p in prov["tickets"] if p.get("person")]
    assert len(people) == 10
    assert all("simulated" in p["person"]["by"] for p in people)


def test_the_bot_solved_800_and_did_800_pieces_of_work(conn):
    chats = Counter(r[0] for r in conn.execute("SELECT outcome FROM conversations WHERE session_id LIKE 'bot-%'"))
    assert chats == {"resolved": 800}
    tools = dict(conn.execute("SELECT tool, COUNT(*) FROM l2_events WHERE session_id LIKE 'bot-%' GROUP BY tool").fetchall())
    assert tools.pop("mark_resolved") == 800
    assert sum(tools.values()) == 800
    assert len(load("provenance.json")["bot_chats"]["records"]) == 800


def test_the_file_ends_in_the_past_and_the_machines_gain(tmp_path):
    path = tmp_path / "demo.sqlite"
    path.write_bytes(Path(config.DEMO_DB_PATH).read_bytes())
    connection = db.connect(path)
    newest = analytics.last_event(connection)
    assert newest.isoformat() <= "2026-11-09T00:00:00+00:00"
    week = analytics.compute(connection, "7d", 0, now=newest)["period"]
    connection.close()
    assert week["current"]["machine_share"] > week["previous"]["machine_share"]


@pytest.mark.parametrize("hour", range(0, 24, 4))
def test_the_machines_gain_whatever_the_hour_the_demo_starts(tmp_path, hour):
    path = tmp_path / "demo.sqlite"
    path.write_bytes(Path(config.DEMO_DB_PATH).read_bytes())
    connection = db.connect(path)
    at = analytics.last_event(connection) + timedelta(days=3, hours=hour, minutes=30)
    shift_to_now(connection, at)
    week = analytics.compute(connection, "7d", 0, now=at)["period"]
    connection.close()
    assert week["current"]["machine_share"] > week["previous"]["machine_share"]


def test_no_country_on_the_accounts_real_profiles_have_none(conn):
    for row in conn.execute("SELECT profile_json FROM tickets WHERE profile_json IS NOT NULL"):
        attributes = (json.loads(row[0]) or {}).get("custom_attributes") or []
        keys = {a.get("key") for a in attributes} if isinstance(attributes, list) else set(attributes)
        assert "country" not in keys


def test_no_key_in_the_dataset():
    for path in DIR.iterdir():
        assert not SECRET.search(path.read_text()), path.name


def test_the_study_is_up_to_date():
    import subprocess
    import sys

    root = Path(config.DEMO_DB_PATH).parent.parent
    done = subprocess.run([sys.executable, "scripts/dataset_study.py", "--check"], cwd=root, capture_output=True, text=True, env={"PYTHONPATH": "."})
    assert done.returncode == 0, done.stdout + done.stderr

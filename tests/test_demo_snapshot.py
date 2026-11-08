"""data/demo.sqlite is what a visitor sees without keys: real triage, real chats, and always current."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from resolve import analytics, config, db
from resolve.demo_clock import shift_to_now
from resolve.web import create_app


def test_every_snapshot_ticket_has_a_real_triage_and_chats_are_there():
    conn = sqlite3.connect(f"file:{config.DEMO_DB_PATH}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM tickets WHERE triage_json IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tickets WHERE status = 'proposed'").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] > 0


LOOKS_LIKE_A_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}([T ].*)?")
A_MONTH_ON = timedelta(days=30, hours=5, minutes=17)


def copy(tmp_path, name="demo.sqlite"):
    path = tmp_path / name
    shutil.copyfile(config.DEMO_DB_PATH, path)
    return path


def dump(path) -> dict:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall():
        out[table] = [dict(r) for r in conn.execute(f"SELECT rowid AS rowid_, * FROM {table} ORDER BY rowid")]
    conn.close()
    return out


def shifted_copy(tmp_path, name="demo.sqlite", later=A_MONTH_ON):
    path = copy(tmp_path, name)
    conn = db.connect(path)
    now = analytics.last_event(conn) + later
    delta = shift_to_now(conn, now)
    return conn, path, now, delta


def stamps(value):
    """Every whole value in a JSON document that looks like a date or a timestamp."""
    if isinstance(value, dict):
        for v in value.values():
            yield from stamps(v)
    elif isinstance(value, list):
        for v in value:
            yield from stamps(v)
    elif isinstance(value, str) and LOOKS_LIKE_A_STAMP.fullmatch(value):
        yield value


def test_the_newest_event_lands_on_now_and_nothing_is_left_behind(tmp_path):
    before = dump(copy(tmp_path, "before.sqlite"))
    conn, path, now, delta = shifted_copy(tmp_path)
    assert delta >= A_MONTH_ON and analytics.last_event(conn) == now
    conn.close()
    after = dump(path)
    moved = 0
    for table, rows in before.items():
        for old, new in zip(rows, after[table], strict=True):
            for column, value in old.items():
                if isinstance(value, str) and column.endswith("_json"):
                    was, is_now = list(stamps(json.loads(value))), list(stamps(json.loads(new[column])))
                    assert len(was) == len(is_now)
                    assert all(a != b for a, b in zip(was, is_now)), (table, column)
                    moved += len(was)
                elif isinstance(value, str) and LOOKS_LIKE_A_STAMP.fullmatch(value):
                    start = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    assert datetime.fromisoformat(new[column].replace("Z", "+00:00")) == start + delta, (table, column)
                    moved += 1
                else:
                    assert new[column] == value, (table, column)
    assert moved > 5000


def test_the_format_of_each_stamp_survives(tmp_path):
    before = dump(copy(tmp_path, "before.sqlite"))
    conn, path, _, delta = shifted_copy(tmp_path)
    conn.close()
    after = dump(path)
    grant, moved = json.loads(before["grants"][0]["response_json"]), json.loads(after["grants"][0]["response_json"])
    assert moved["timestamp"] == grant["timestamp"] + int(delta.total_seconds() * 1000)
    old, new = grant["access_levels"][0]["purchased_at"], moved["access_levels"][0]["purchased_at"]
    assert re.fullmatch(r"\S+\.\d{6}\+00:00", old) and re.fullmatch(r"\S+\.\d{6}\+00:00", new)
    assert old[19:] == new[19:], "fractions and the zone stay as stored"
    assert after["tickets"][0]["created_at"].endswith("Z")


def test_words_are_not_rewritten(tmp_path):
    before = dump(copy(tmp_path, "before.sqlite"))
    conn, path, _, _ = shifted_copy(tmp_path)
    conn.close()
    after = dump(path)
    assert [m["content"] for m in before["messages"]] == [m["content"] for m in after["messages"]]
    for old, new in zip(before["tickets"], after["tickets"], strict=True):
        assert (old["subject"], old["body"]) == (new["subject"], new["body"])
        was, is_now = json.loads(old["triage_json"]), json.loads(new["triage_json"])
        assert was == is_now, "the triage has no timestamps of its own, only sentences"


def test_a_month_later_the_week_is_full_and_ages_count_up(tmp_path):
    original = db.connect(copy(tmp_path, "before.sqlite"))
    then = analytics.last_event(original)
    conn, _, now, _ = shifted_copy(tmp_path)
    week = analytics.compute(conn, "7d", 0, now=now)["period"]["current"]
    assert week["chats"]["total"] == analytics.compute(original, "7d", 0, now=then)["period"]["current"]["chats"]["total"] > 0
    tasks = analytics.open_tasks(conn, now)
    assert tasks and all(t["age_s"] >= 0 for t in tasks)
    assert [t["age_s"] for t in tasks] == [t["age_s"] for t in analytics.open_tasks(original, then)]
    assert [t["age_s"] + 60 for t in tasks] == [t["age_s"] for t in analytics.open_tasks(conn, now + timedelta(seconds=60))]


def test_a_chat_typed_now_starts_at_zero_and_grows(tmp_path):
    conn, _, now, _ = shifted_copy(tmp_path)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute("INSERT INTO conversations (session_id, customer_user_id, outcome, created_at, updated_at) VALUES ('typed', 'u', 'open', ?, ?)", (stamp, stamp))
    age = lambda at: next(t["age_s"] for t in analytics.open_tasks(conn, at) if t["id"] == "typed")  # noqa: E731
    assert age(now) == 0
    assert age(now + timedelta(seconds=90)) == 90


def test_a_restart_gives_the_same_picture_relative_to_its_new_now(tmp_path):
    first, _, now_a, _ = shifted_copy(tmp_path, "a.sqlite", A_MONTH_ON)
    second, _, now_b, _ = shifted_copy(tmp_path, "b.sqlite", A_MONTH_ON + timedelta(days=40, hours=3))
    assert analytics.open_tasks(first, now_a) != [] and [t["age_s"] for t in analytics.open_tasks(first, now_a)] == [t["age_s"] for t in analytics.open_tasks(second, now_b)]
    assert first.execute("SELECT MIN(created_at) FROM tickets").fetchone()[0] < second.execute("SELECT MIN(created_at) FROM tickets").fetchone()[0]


def test_the_generated_tail_moves_and_stays_older_than_the_real_tickets(tmp_path):
    conn, _, _, _ = shifted_copy(tmp_path)
    newest_tail = conn.execute("SELECT MAX(created_at) FROM tickets WHERE rule = 'generated'").fetchone()[0]
    oldest_real = conn.execute("SELECT MIN(created_at) FROM tickets WHERE rule != 'generated'").fetchone()[0]
    assert newest_tail < oldest_real


def test_it_never_moves_backwards(tmp_path):
    path = copy(tmp_path)
    before = dump(path)
    conn = db.connect(path)
    assert shift_to_now(conn, analytics.last_event(conn)) == timedelta(0)
    assert shift_to_now(conn, analytics.last_event(conn) - timedelta(days=9)) == timedelta(0)
    conn.close()
    assert dump(path) == before


def test_an_empty_database_stays_empty(tmp_path):
    assert shift_to_now(db.connect(tmp_path / "empty.sqlite")) == timedelta(0)


def test_demo_mode_shifts_at_startup_and_the_snapshot_file_stays(tmp_path):
    untouched = config.DEMO_DB_PATH.read_bytes()
    path = copy(tmp_path)
    client = TestClient(create_app(path, demo=True, seed_file=tmp_path / "none.json"))
    newest = analytics.last_event(client.app.state.services.conn)
    assert abs(datetime.now(timezone.utc) - newest) < timedelta(seconds=30)
    assert client.get("/api/analytics?range=7d").json()["period"]["current"]["chats"]["total"] > 0
    assert config.DEMO_DB_PATH.read_bytes() == untouched


def test_other_modes_run_no_shift(tmp_path):
    path = copy(tmp_path)
    before = dump(path)
    client = TestClient(create_app(path, demo=False, seed_file=tmp_path / "none.json"))
    client.app.state.services.conn.close()
    assert dump(path) == before

"""data/demo.sqlite is what a visitor sees without keys: real triage, real chats, and Analyze that doesn't age."""

from __future__ import annotations

import shutil
import sqlite3

from fastapi.testclient import TestClient

from resolve import config
from resolve.web import create_app


def test_every_snapshot_ticket_has_a_real_triage_and_chats_are_there():
    conn = sqlite3.connect(f"file:{config.DEMO_DB_PATH}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM tickets WHERE triage_json IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tickets WHERE status = 'proposed'").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] > 0


def test_analyze_reads_the_snapshot_as_of_its_last_event(tmp_path):
    db_path = tmp_path / "demo.sqlite"
    shutil.copyfile(config.DEMO_DB_PATH, db_path)
    client = TestClient(create_app(db_path, demo=True, seed_file=tmp_path / "none.json"))
    chats = client.get("/api/analytics?range=7d").json()["period"]["current"]["chats"]
    assert chats["total"] > 0, "the period is measured from the snapshot's last event, not from today"

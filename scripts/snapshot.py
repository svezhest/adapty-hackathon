"""Build data/demo.sqlite, the committed snapshot DEMO_MODE serves without keys.

    python scripts/snapshot.py [--db data/resolve.sqlite] [--out data/demo.sqlite]

A copy of the live DB with SQLite's backup API (WAL included): statuses, cached profiles, triage,
actions, insights and drafts exactly as they are. Nothing is rewound: a ticket closed live stays
`done` with its real grant, because its cached profile already shows that grant and a rewound status
would contradict it.

The copy is then curated, because the repo is public and a visitor clicks at random: developers'
throwaway chats (DROP_SESSIONS) and tickets without a Level 1 triage go, with everything that hangs
on them. Nothing is edited or rewritten. In demo mode the Analyze clock stops at the last event in
the snapshot (resolve/web.py), so its periods don't empty out as the file ages.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve import config  # noqa: E402


# Sessions typed while building the app: greetings, jokes, smoke tests. The data is synthetic either way;
# these just aren't support conversations.
DROP_SESSIONS = (
    "s-shayiuq9", "s-v9mgjciv", "s-7iii1q5c", "web-%", "brandcheck-%", "ctrl-%",
    "appy-lumen_443814_00014-mw75t9qz", "appy-lumen_443814_00014-mw7ux47p", "appy-lumen_443814_00014-mw7zmxmk1",
    "appy-lumen_443814_00076-mw7v89qt", "appy-lumen_443814_00772-mw7ve82g",
    "appy-lumen_443814_02373-mw7tqqk63", "live-1108-05",
)


def curate(conn: sqlite3.Connection) -> None:
    """Remove the throwaway sessions and untriaged tickets, and what belongs only to them."""
    with conn:
        for pattern in DROP_SESSIONS:
            conn.execute(
                "DELETE FROM tickets WHERE id IN (SELECT ticket_id FROM conversations WHERE session_id LIKE ?)"
                " OR session_id LIKE ?",
                (pattern, pattern),
            )
            for table in ("messages", "l2_events", "conversations"):
                conn.execute(f"DELETE FROM {table} WHERE session_id LIKE ?", (pattern,))
        gone = conn.execute("SELECT id, session_id FROM tickets WHERE triage_json IS NULL").fetchall()
        for ticket_id, session_id in gone:
            conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
            if session_id:
                for table in ("messages", "l2_events", "conversations"):
                    conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
    conn.execute("VACUUM")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--out", type=Path, default=config.DEMO_DB_PATH)
    args = parser.parse_args()
    if not args.db.exists():
        print(f"no DB at {args.db}; run the app first")
        return 1

    for suffix in ("", "-wal", "-shm"):
        Path(str(args.out) + suffix).unlink(missing_ok=True)
    source = sqlite3.connect(args.db)
    target = sqlite3.connect(args.out)
    source.backup(target)
    source.close()
    target.execute("PRAGMA journal_mode=DELETE")
    curate(target)
    if not target.execute("SELECT 1 FROM tickets WHERE status = 'proposed' LIMIT 1").fetchone():
        print("warning: no ticket is `proposed`; the keyless path has nothing to answer")
    counts = {r[0]: r[1] for r in target.execute("SELECT status, COUNT(*) FROM tickets GROUP BY status")}
    triaged = target.execute("SELECT COUNT(*) FROM tickets WHERE triage_json IS NOT NULL").fetchone()[0]
    chats = target.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    profiles = target.execute("SELECT COUNT(*) FROM tickets WHERE profile_json IS NOT NULL").fetchone()[0]
    insights = target.execute("SELECT COUNT(*) FROM insights").fetchone()[0]
    target.close()
    print(f"{args.out}: statuses {counts}, {profiles} profiles, {triaged} triaged, {chats} chats, insights cached: {bool(insights)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

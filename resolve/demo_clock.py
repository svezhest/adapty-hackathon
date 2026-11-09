"""Keep the demo current: the runtime copy of the snapshot moves forward so its newest event is now."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from resolve import analytics, db

# A whole value that is a date or a timestamp. Text that merely mentions a date is left alone.
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})(?:([T ]\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?")


def _shift_text(value: str, delta: timedelta) -> str:
    match = STAMP.fullmatch(value)
    if not match:
        return value
    day, clock, fraction, zone = match.groups()
    if clock is None:
        return (datetime.fromisoformat(day) + delta).strftime("%Y-%m-%d")
    moved = datetime.fromisoformat(day + clock.replace(" ", "T")) + delta
    return moved.strftime(f"%Y-%m-%d{clock[0]}%H:%M:%S") + (fraction or "") + (zone or "")


def _shift_json(value, delta: timedelta):
    if isinstance(value, dict):
        return {
            # Adapty's `timestamp` is epoch milliseconds
            k: v + int(delta.total_seconds() * 1000) if k == "timestamp" and type(v) is int else _shift_json(v, delta)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_shift_json(v, delta) for v in value]
    return _shift_text(value, delta) if isinstance(value, str) else value


def _shifted(column: str, value: str, delta: timedelta) -> str:
    if not column.endswith("_json"):
        return _shift_text(value, delta)
    parsed = json.loads(value)
    moved = _shift_json(parsed, delta)
    return value if moved == parsed else json.dumps(moved, ensure_ascii=False)


def shift_to_now(conn: db.Database, now: datetime | None = None) -> timedelta:
    """Move every stored timestamp forward by one delta so the newest event lands on `now`. Returns the delta.

    Only for demo mode's runtime copy. An empty DB, or one whose newest event is not in the past, stays as it is.
    """
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    newest = analytics.last_event(conn)
    if newest is None or newest >= now:
        return timedelta(0)
    delta = now - newest
    shift_by(conn, delta)
    return delta


def shift_by(conn: db.Database, delta: timedelta) -> None:
    """Move every stored timestamp by `delta`. Words stay as written."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    with conn:
        for table in tables:
            names = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            columns = [c for c in names if c == "ts" or c.endswith(("_at", "_json"))]
            if not columns:
                continue
            for row in conn.execute(f"SELECT rowid AS row_id, {', '.join(columns)} FROM {table}").fetchall():
                values = {c: _shifted(c, row[c], delta) for c in columns if row[c]}
                changed = {c: v for c, v in values.items() if v != row[c]}
                if changed:
                    assignments = ", ".join(f"{c} = ?" for c in changed)
                    conn.execute(f"UPDATE {table} SET {assignments} WHERE rowid = ?", (*changed.values(), row["row_id"]))

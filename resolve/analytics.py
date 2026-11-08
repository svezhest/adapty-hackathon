"""Numbers for the Analyze tab (docs/agents.md, "Analyze, first version" and the `GET /api/analytics` shape).

Readings declared here (the spec does not fix them):
- `tickets.by_status` uses API statuses (DB_TO_API_STATUS); a ticket without a category counts as "uncategorized".
- `level2.grants` / `days_granted` count only Level 2 grants (`grants.source = 'l2'`).
- `level2.handoff_reasons` = `args.reason` of logged `handoff` tool calls; `blocked` = blocked_reason of disallowed calls.
- `triage.*` reads only `triage_json` (v2 Level 1 tickets), not legacy v1 proposals.
- `accepted_option`: a dismissed ticket, or a decision with a truthy `rejected` or without `option`, is "rejected";
  a decision whose `edits` has any non-null field is "edited"; otherwise `option + 1` (0-based index in Decision).

Analyze v3: `period` (one date range), `agent_log`, `open_counts`, `open_tasks[].detail`. Every number is from stored
rows; a number that cannot be computed (nothing to divide by, no sample) is null and the UI shows '—'.

Range (`?range=today|7d|30d`, `?tz=<minutes east of UTC>`, applied as a fixed offset): today = from local midnight
to now, hourly buckets; 7d / 30d = from local midnight 6 / 29 days ago to now, daily buckets. `previous` is the
same number of whole days right before (today → all of yesterday). A row is in a range by one timestamp, named below.

Records the numbers are built from:
- Agent reply = a `messages` row with role agent. The user message it answers = the latest user message of that
  session not yet answered. latency_s = agent ts - that user message ts (null when there is none).
  The reply's turn = the session's l2_events with user ts <= event ts <= reply ts. outcome, first match wins:
  grant_blocked (a grant_access refused by a limit: not allowed, blocked_reason set), handed_off (a handoff),
  resolved (mark_resolved), granted (grant_access that granted days), with_teammate (the session was already
  handed off before the turn: its last handoff / cancel_handoff event before it is a handoff), else answered.
- Person reply = a reply sent from Incoming: a ticket with reply_sent_at (accept with a reply, or reject with an
  own reply), at = reply_sent_at; plus each earlier close in history_json whose action has approved_at and a
  final reply that is not no_reply, at = approved_at (a reopened own reply has no stored time and is left out).
  action = the final option's action, or own_reply. It "came back" when the ticket was reopened after it (a
  history entry) or the user wrote again in the ticket's chat after `at`.
- Chats in the range = conversations by created_at, with their current outcome.

`period.current` / `period.previous` (PeriodSummary):
- chats: total / resolved / handed_off / open of the chats in the range.
- machine_share (hero bar "MACHINES x%"): resolved / (resolved + handed_off) of those chats; null if both 0.
- machines (agent column of "reply by reply"):
  - replies: agent replies with ts in the range.
  - first_reply_median_s ("Time to first reply", KPI "Median first reply"): median over the chats in the range of
    the latency of the session's first agent reply.
  - closed_with_reply ("Closed with that reply"): resolved / chats.
  - came_back ("User came back unhappy"): of the chats in the range that had a mark_resolved, the share where the
    user wrote again after the first one. We cannot read mood: any message after the close counts.
  - passed_on ("Passed on to someone else"): handed_off / chats.
  - free_days ("Free days given away"): days of Level 2 grants (grants.source l2) by grants.ts.
- humans (people column), over person replies with `at` in the range:
  - replies: their number.
  - first_reply_median_s: median of (the ticket's first person reply at - ticket created_at), for tickets whose
    first person reply is in the range.
  - closed_with_reply: share that did not come back and whose action is not ask_user (a question does not close).
  - came_back: share that came back (see above).
  - passed_on: share with action refund_to_human (the user is sent to the store for the refund).
  - free_days: days of Level 1 grants (grants.source l1) by grants.ts.
- grants (KPI "Free days granted"): Level 2 grants by ts: count, days, days_each.
- handoff_reasons ("Why chats went to people"): handoffs by event ts, one per ticket created (first handoff
  event per result.ticket_id), counted by args.reason.
- blocked ("Blocked by limits"): grant_access events refused by a limit (blocked_reason set) by event ts, by reason.
- decisions ("Level 1 decisions"), over tickets with triage_json and triage_at in the range: triaged; decided =
  closed by a person (done/dismissed with a decision that is not a user-cancelled handoff); of those: first
  (accepted option 1), second_third (option 2 or 3), edited (accepted with any edit; overlaps first/second_third),
  own_reply (rejected with a reply), no_reply (rejected without a reply).

`period.points` (the big chart), one per local hour / day from the range start to now:
- agent / handed_off: chats in the range by created_at whose outcome is resolved / handed_off (sums = chats).
- closed_by_people: tickets closed by a person, by COALESCE(reply_sent_at, action_at, updated_at).
- feedback: tickets not from a chat handoff (form, email) by created_at.

`agent_log`: the latest 20 agent replies (all time), newest first.
`open_counts`: how many open chats / tickets waiting for triage (new, triaged) / waiting for a person
(proposed, approved). `open_tasks[].detail`: chat = the agent's last tool step ("checked the profile", "sent cancel
steps", "granted 3 days", "grant blocked", "took the chat back"), else "replied" / "new message" by the last
message; review = the agent's first option ("grant 3 days", "reply, no action", "Google Play cancel steps", ...).
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from resolve.adapty import format_datetime, parse_datetime
from resolve import config, db
from resolve.models import DB_TO_API_STATUS
from resolve.tickets import ticket_title

EVAL_RESULTS = config.ROOT / "data" / "eval_results.json"
BUCKETS = (("0-0.5", 0.5), ("0.5-0.7", 0.7), ("0.7-0.9", 0.9), ("0.9-1", None))


def _bucket(confidence: float) -> str:
    for name, upper in BUCKETS:
        if upper is None or confidence < upper:
            return name
    raise AssertionError("unreachable")


def _accepted(status: str, decision: dict | None) -> str | None:
    if status == "dismissed":
        return "rejected"
    if decision is None:
        return None
    if decision.get("rejected") or "option" not in decision:
        return "rejected"
    if any(v is not None for v in (decision.get("edits") or {}).values()):
        return "edited"
    return str(decision["option"] + 1)


def _eval() -> dict | None:
    if not EVAL_RESULTS.exists():
        return None
    summary = json.loads(EVAL_RESULTS.read_text()).get("summary") or {}
    return {k: summary.get(k) for k in ("model", "cases", "category_correct", "action_correct")}


def compute(conn: db.Database, range_id: str = "today", tz_offset_min: int = 0, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    tickets = conn.execute("SELECT status, source, category, created_at, triage_json, decision_json FROM tickets").fetchall()

    by_status = Counter(DB_TO_API_STATUS.get(t["status"], t["status"]) for t in tickets)
    per_day = Counter(t["created_at"][:10] for t in tickets)

    buckets = {name: 0 for name, _ in BUCKETS}
    accepted = {"1": 0, "2": 0, "3": 0, "edited": 0, "rejected": 0}
    root_causes: Counter = Counter()
    triaged = 0
    for t in tickets:
        if t["triage_json"]:
            tri = json.loads(t["triage_json"])
            triaged += 1
            if isinstance(tri.get("confidence"), (int, float)):
                buckets[_bucket(tri["confidence"])] += 1
            if tri.get("root_cause"):
                root_causes[tri["root_cause"]] += 1
        key = _accepted(t["status"], json.loads(t["decision_json"]) if t["decision_json"] else None)
        if key in accepted:
            accepted[key] += 1

    outcomes = Counter({r[0]: r[1] for r in conn.execute("SELECT outcome, COUNT(*) FROM conversations GROUP BY outcome")})
    events = db.list_events(conn)
    handoff_reasons = Counter(str(e["args"].get("reason")) for e in events if e["tool"] == "handoff" and e["allowed"])
    blocked = Counter(e["blocked_reason"] or "unknown" for e in events if not e["allowed"])
    grants, days = conn.execute("SELECT COUNT(*), COALESCE(SUM(days), 0) FROM grants WHERE source = 'l2'").fetchone()

    return {
        "tickets": {
            "total": len(tickets),
            "by_status": dict(by_status),
            "by_source": dict(Counter(t["source"] for t in tickets)),
            "by_category": dict(Counter(t["category"] or "uncategorized" for t in tickets)),
            "per_day": [{"day": day, "n": n} for day, n in sorted(per_day.items())],
        },
        "level2": {
            "sessions": sum(outcomes.values()),
            "resolved": outcomes["resolved"],
            "handed_off": outcomes["handed_off"],
            "open": outcomes["open"],
            "handoff_reasons": dict(handoff_reasons),
            "grants": grants,
            "days_granted": days,
            "blocked": dict(blocked),
        },
        "triage": {
            "triaged": triaged,
            "confidence_buckets": buckets,
            "accepted_option": accepted,
            "top_root_causes": [{"root_cause": rc, "n": n} for rc, n in root_causes.most_common(5)],
        },
        "eval": _eval(),
        "timeline": timeline(conn, now),
        "open_tasks": open_tasks(conn, now),
        "open_counts": open_counts(conn),
        **v3(conn, range_id, tz_offset_min, now),
    }


TIMELINE_DAYS = 14


def last_event(conn: db.Database) -> datetime | None:
    """The latest moment anything happened in this DB, or None when it is empty."""
    stamps = [
        conn.execute(f"SELECT MAX({column}) FROM {table}").fetchone()[0]
        for table, column in (("tickets", "updated_at"), ("conversations", "updated_at"), ("messages", "ts"), ("grants", "ts"))
    ]
    stamps = [s for s in stamps if s]
    return parse_datetime(max(stamps)) if stamps else None


def _bucket_key(ts: str, hourly: bool) -> str:
    return ts[:13] + ":00:00Z" if hourly else ts[:10]


def timeline(conn: db.Database, now: datetime | None = None) -> dict:
    """AI vs people over time (real rows only). Hourly when everything is within 48 h, else daily, last 14 days.

    per bucket: chats Level 2 kept (resolved or answered and not handed off), chats handed to a person,
    tickets people closed in Level 1, other feedback that came in, and free days granted.
    """
    now = now or datetime.now(timezone.utc)
    since = format_datetime(now - timedelta(days=TIMELINE_DAYS))
    chats = conn.execute("SELECT outcome, created_at FROM conversations WHERE created_at >= ?", (since,)).fetchall()
    closed = conn.execute(
        "SELECT updated_at AS ts FROM tickets WHERE status IN ('done', 'dismissed') AND decision_json IS NOT NULL AND updated_at >= ?", (since,)
    ).fetchall()
    feedback = conn.execute("SELECT created_at AS ts FROM tickets WHERE source != 'chat_handoff' AND created_at >= ?", (since,)).fetchall()
    grants = conn.execute("SELECT ts, days FROM grants WHERE ts >= ?", (since,)).fetchall()
    stamps = [c["created_at"] for c in chats] + [r["ts"] for r in closed] + [r["ts"] for r in feedback] + [g["ts"] for g in grants]
    if not stamps:
        return {"bucket": "hour", "points": [], "agent_share": None}
    hourly = (now - parse_datetime(min(stamps))) <= timedelta(hours=48)
    points: dict[str, dict] = {}

    def point(ts: str) -> dict:
        key = _bucket_key(ts, hourly)
        return points.setdefault(key, {"t": key, "agent": 0, "handed_off": 0, "closed_by_people": 0, "feedback": 0, "grant_days": 0})

    for c in chats:
        point(c["created_at"])["handed_off" if c["outcome"] == "handed_off" else "agent"] += 1
    for r in closed:
        point(r["ts"])["closed_by_people"] += 1
    for r in feedback:
        point(r["ts"])["feedback"] += 1
    for g in grants:
        point(g["ts"])["grant_days"] += g["days"]
    agent = sum(p["agent"] for p in points.values())
    total_chats = agent + sum(p["handed_off"] for p in points.values())
    return {
        "bucket": "hour" if hourly else "day",
        "points": [points[k] for k in sorted(points)],
        "agent_share": round(agent / total_chats, 3) if total_chats else None,
    }


def open_tasks(conn: db.Database, now: datetime | None = None) -> list[dict]:
    """What is in flight right now: open chats, tickets Level 1 is still on, tickets waiting for a person."""
    now = now or datetime.now(timezone.utc)
    age = lambda ts: int((now - parse_datetime(ts)).total_seconds()) if ts else None  # noqa: E731
    tasks = []
    for c in conn.execute("SELECT session_id, customer_user_id, updated_at FROM conversations WHERE outcome = 'open' ORDER BY updated_at DESC LIMIT 20"):
        first = conn.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id LIMIT 1", (c["session_id"],)).fetchone()
        tasks.append({"kind": "chat", "id": c["session_id"], "title": (first["content"] if first else "")[:80], "user": c["customer_user_id"], "since": c["updated_at"], "age_s": age(c["updated_at"]), "note": "Level 2 is talking", "detail": _chat_step(conn, c["session_id"])})
    for t in conn.execute("SELECT id, subject, body, customer_user_id, created_at, triage_error FROM tickets WHERE status IN ('new', 'triaged') ORDER BY created_at DESC LIMIT 20"):
        tasks.append({"kind": "triage", "id": t["id"], "title": ticket_title(t["subject"], t["body"], None), "user": t["customer_user_id"], "since": t["created_at"], "age_s": age(t["created_at"]), "note": "triage failed" if t["triage_error"] else "not triaged yet", "detail": None})
    for t in conn.execute("SELECT id, subject, body, customer_user_id, created_at, triage_at, triage_json, proposal_json FROM tickets WHERE status IN ('proposed', 'approved') ORDER BY COALESCE(triage_at, created_at) LIMIT 20"):
        since = t["triage_at"] or t["created_at"]
        tasks.append({"kind": "review", "id": t["id"], "title": ticket_title(t["subject"], t["body"], json.loads(t["triage_json"] or "{}").get("summary")), "user": t["customer_user_id"], "since": since, "age_s": age(since), "note": "waiting for a person", "detail": _top_option(t)})
    return tasks


def open_counts(conn: db.Database) -> dict:
    """True totals behind open_tasks (which lists at most 20 of each kind)."""
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "chat": one("SELECT COUNT(*) FROM conversations WHERE outcome = 'open'"),
        "triage": one("SELECT COUNT(*) FROM tickets WHERE status IN ('new', 'triaged')"),
        "review": one("SELECT COUNT(*) FROM tickets WHERE status IN ('proposed', 'approved')"),
    }


STORE_NAMES = {"app_store": "App Store", "play_store": "Google Play"}
KIND_NAMES = {"restore": "restore", "cancel": "cancel", "refund_request": "refund"}


def _chat_step(conn: db.Database, session_id: str) -> str:
    for e in reversed(db.list_events(conn, session_id)):
        result = e["result"] if isinstance(e["result"], dict) else {}
        if e["tool"] == "get_profile" and e["allowed"]:
            return "checked the profile"
        if e["tool"] == "store_instructions" and e["allowed"]:
            kind = e["args"].get("kind")
            return f"sent {KIND_NAMES.get(kind, kind)} steps"
        if e["tool"] == "grant_access" and e["allowed"] and result.get("days"):
            return f"granted {result['days']} days"
        if e["tool"] == "grant_access" and e["blocked_reason"]:
            return "grant blocked"
        if e["tool"] == "cancel_handoff" and e["allowed"]:
            return "took the chat back"
    last = conn.execute("SELECT role FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    return "replied" if last and last["role"] == "agent" else "new message"


def _top_option(t) -> str | None:
    """The agent's first option in a few words, as the design's live list shows it."""
    if t["triage_json"]:
        options = json.loads(t["triage_json"]).get("options") or []
    elif t["proposal_json"]:
        from resolve.triage import legacy_to_ticket

        options = legacy_to_ticket(json.loads(t["proposal_json"]))["options"]
    else:
        return None
    if not options:
        return None
    action, params = options[0]["action"], options[0].get("params") or {}
    store = STORE_NAMES.get(params.get("store"))
    if action == "grant_access":
        return f"grant {params.get('days')} days"
    if action == "store_instructions":
        return " ".join(x for x in (store, KIND_NAMES.get(params.get("kind")), "steps") if x)
    if action == "refund_to_human":
        return " ".join(x for x in (store, "refund steps") if x)
    return {"reply_only": "reply, no action", "ask_user": "ask the user", "no_reply": "close without a reply"}.get(action, action)


# --- v3: one date range ------------------------------------------------------------------------------

RANGE_DAYS = {"today": 1, "7d": 7, "30d": 30}
LOG_LIMIT = 20
CANCELLED_HANDOFF = "user cancelled the handoff"


def _secs(a: str, b: str) -> float:
    return (parse_datetime(b) - parse_datetime(a)).total_seconds()


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _median(values) -> float | None:
    values = list(values)
    return float(statistics.median(values)) if values else None


def _closed_by_person(t: dict) -> bool:
    if t["status"] not in ("done", "dismissed") or not t["decision_json"]:
        return False
    return not str(json.loads(t["decision_json"]).get("note") or "").startswith(CANCELLED_HANDOFF)


def _load(conn: db.Database) -> dict:
    rows = lambda sql: [dict(r) for r in conn.execute(sql)]  # noqa: E731
    data = {
        "convs": rows("SELECT session_id, customer_user_id, outcome, created_at FROM conversations"),
        "msgs": rows("SELECT id, session_id, role, content, ts FROM messages ORDER BY id"),
        "events": db.list_events(conn),
        "tickets": rows(
            "SELECT id, status, source, session_id, created_at, updated_at, triage_at, triage_json, decision_json, "
            "action_json, action_at, history_json, reply_sent_at FROM tickets"
        ),
        "grants": rows("SELECT source, days, ts FROM grants ORDER BY ts, id"),
    }
    data["user_ts"] = defaultdict(list)
    for m in data["msgs"]:
        if m["role"] == "user":
            data["user_ts"][m["session_id"]].append(m["ts"])
    data["agent"] = _agent_replies(data)
    data["people"] = _person_replies(data)
    return data


def _turn_outcome(turn: list[dict], handed_before: bool) -> tuple[str, int | None]:
    if any(e["tool"] == "grant_access" and not e["allowed"] and e["blocked_reason"] for e in turn):
        return "grant_blocked", None
    if any(e["tool"] == "handoff" and e["allowed"] for e in turn):
        return "handed_off", None
    if any(e["tool"] == "mark_resolved" and e["allowed"] for e in turn):
        return "resolved", None
    granted = [e["result"]["days"] for e in turn if e["tool"] == "grant_access" and e["allowed"] and isinstance(e["result"], dict) and e["result"].get("days")]
    if granted:
        return "granted", granted[-1]
    return ("with_teammate" if handed_before else "answered"), None


def _agent_replies(data: dict) -> list[dict]:
    """Every agent message with the user message it answers, its latency and its turn's outcome, oldest first."""
    users = {c["session_id"]: c["customer_user_id"] for c in data["convs"]}
    events = defaultdict(list)
    for e in data["events"]:
        events[e["session_id"]].append(e)
    pending: dict[str, dict] = {}
    out = []
    for m in data["msgs"]:
        s = m["session_id"]
        if m["role"] == "user":
            pending[s] = m
            continue
        if m["role"] != "agent":
            continue
        asked = pending.pop(s, None)
        lo = asked["ts"] if asked else m["ts"]
        turn = [e for e in events[s] if lo <= e["ts"] <= m["ts"]]
        switches = [e["tool"] for e in events[s] if e["ts"] < lo and e["allowed"] and e["tool"] in ("handoff", "cancel_handoff")]
        outcome, days = _turn_outcome(turn, bool(switches) and switches[-1] == "handoff")
        out.append({
            "ts": m["ts"], "session_id": s, "user": users.get(s, ""), "reply": m["content"],
            "latency_s": _secs(asked["ts"], m["ts"]) if asked else None, "outcome": outcome, "grant_days": days,
        })
    return sorted(out, key=lambda r: r["ts"])


def _person_replies(data: dict) -> list[dict]:
    """Replies sent from Incoming: {ticket_id, created_at, at, action, came_back}."""
    out = []

    def add(t: dict, at: str, action: str | None, reopened: bool) -> None:
        wrote_after = any(ts > at for ts in data["user_ts"].get(t["session_id"] or "", []))
        out.append({"ticket_id": t["id"], "created_at": t["created_at"], "at": at, "action": action, "came_back": reopened or wrote_after})

    for t in data["tickets"]:
        for h in json.loads(t["history_json"] or "[]"):
            action = h.get("action") or {}
            final = action.get("final") or {}
            if action.get("approved_at") and final.get("action") != "no_reply" and (final.get("reply") or "").strip():
                add(t, action["approved_at"], final.get("action"), True)
        if t["reply_sent_at"]:
            decision = json.loads(t["decision_json"] or "{}")
            final = (json.loads(t["action_json"] or "null") or {}).get("final") or {}
            add(t, t["reply_sent_at"], "own_reply" if decision.get("rejected") else final.get("action"), False)
    return out


def _summary(data: dict, lo: str, hi: str) -> dict:
    """The PeriodSummary of [lo, hi) (definitions in the module docstring)."""
    within = lambda ts: bool(ts) and lo <= ts < hi  # noqa: E731
    chats = [c for c in data["convs"] if within(c["created_at"])]
    ids = {c["session_id"] for c in chats}
    outcomes = Counter(c["outcome"] for c in chats)
    resolved, handed = outcomes["resolved"], outcomes["handed_off"]

    first_latency: dict[str, float | None] = {}
    for r in data["agent"]:
        if r["session_id"] in ids:
            first_latency.setdefault(r["session_id"], r["latency_s"])
    resolved_at: dict[str, str] = {}
    for e in data["events"]:
        if e["tool"] == "mark_resolved" and e["allowed"] and e["session_id"] in ids:
            resolved_at.setdefault(e["session_id"], e["ts"])
    came_back = sum(1 for s, ts in resolved_at.items() if any(u > ts for u in data["user_ts"][s]))
    grants = [g for g in data["grants"] if within(g["ts"])]
    l2 = [g["days"] for g in grants if g["source"] == "l2"]
    machines = {
        "replies": sum(1 for r in data["agent"] if within(r["ts"])),
        "first_reply_median_s": _median(v for v in first_latency.values() if v is not None),
        "closed_with_reply": _ratio(resolved, len(chats)),
        "came_back": _ratio(came_back, len(resolved_at)),
        "passed_on": _ratio(handed, len(chats)),
        "free_days": sum(l2),
    }

    replies = [r for r in data["people"] if within(r["at"])]
    first_reply: dict[str, dict] = {}
    for r in sorted(data["people"], key=lambda r: r["at"]):
        first_reply.setdefault(r["ticket_id"], r)
    humans = {
        "replies": len(replies),
        "first_reply_median_s": _median(_secs(r["created_at"], r["at"]) for r in first_reply.values() if within(r["at"])),
        "closed_with_reply": _ratio(sum(1 for r in replies if not r["came_back"] and r["action"] != "ask_user"), len(replies)),
        "came_back": _ratio(sum(1 for r in replies if r["came_back"]), len(replies)),
        "passed_on": _ratio(sum(1 for r in replies if r["action"] == "refund_to_human"), len(replies)),
        "free_days": sum(g["days"] for g in grants if g["source"] == "l1"),
    }

    reasons: Counter = Counter()
    seen = set()
    for e in data["events"]:
        if e["tool"] == "handoff" and e["allowed"] and within(e["ts"]):
            key = (e["result"] or {}).get("ticket_id") if isinstance(e["result"], dict) else None
            if key is not None and key in seen:
                continue
            seen.add(key)
            reasons[str(e["args"].get("reason"))] += 1
    blocked = Counter(e["blocked_reason"] for e in data["events"] if e["tool"] == "grant_access" and not e["allowed"] and e["blocked_reason"] and within(e["ts"]))

    decisions = dict.fromkeys(("triaged", "decided", "first", "second_third", "edited", "own_reply", "no_reply"), 0)
    for t in data["tickets"]:
        if not (t["triage_json"] and within(t["triage_at"])):
            continue
        decisions["triaged"] += 1
        if not _closed_by_person(t):
            continue
        decisions["decided"] += 1
        d = json.loads(t["decision_json"])
        if d.get("rejected"):
            decisions["own_reply" if (d.get("reply") or "").strip() else "no_reply"] += 1
            continue
        decisions["first" if d.get("option", 0) == 0 else "second_third"] += 1
        if any(v is not None for v in (d.get("edits") or {}).values()):
            decisions["edited"] += 1

    return {
        "chats": {"total": len(chats), "resolved": resolved, "handed_off": handed, "open": outcomes["open"]},
        "machine_share": _ratio(resolved, resolved + handed),
        "machines": machines,
        "humans": humans,
        "grants": {"count": len(l2), "days": sum(l2), "days_each": l2},
        "handoff_reasons": dict(reasons),
        "blocked": dict(blocked),
        "decisions": decisions,
    }


def _points(data: dict, start: datetime, now: datetime, hourly: bool) -> list[dict]:
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    n = int((now - start) / step) + 1
    points = [{"t": format_datetime(start + i * step), "agent": 0, "handed_off": 0, "closed_by_people": 0, "feedback": 0} for i in range(n)]

    def at(ts: str | None) -> dict | None:
        if not ts:
            return None
        when = parse_datetime(ts)
        i = math.floor((when - start) / step)  # floor: a row just before the start must not land in bucket 0
        return points[i] if 0 <= i < n and when <= now else None

    for c in data["convs"]:
        p = at(c["created_at"])
        if p is not None and c["outcome"] in ("resolved", "handed_off"):
            p["agent" if c["outcome"] == "resolved" else "handed_off"] += 1
    for t in data["tickets"]:
        p = at(t["reply_sent_at"] or t["action_at"] or t["updated_at"]) if _closed_by_person(t) else None
        if p is not None:
            p["closed_by_people"] += 1
        p = at(t["created_at"]) if t["source"] != "chat_handoff" else None
        if p is not None:
            p["feedback"] += 1
    return points


def v3(conn: db.Database, range_id: str = "today", tz_offset_min: int = 0, now: datetime | None = None) -> dict:
    """`period` (range, buckets, current and previous summaries) and `agent_log` for GET /api/analytics."""
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    days = RANGE_DAYS[range_id]
    local_now = now.astimezone(timezone(timedelta(minutes=tz_offset_min)))
    start = local_now.replace(hour=0, minute=0, second=0) - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)
    data = _load(conn)
    s, e, p = format_datetime(start), format_datetime(now), format_datetime(prev_start)
    return {
        "period": {
            "range": range_id,
            "tz_offset_min": tz_offset_min,
            "start": s,
            "end": e,
            "prev_start": p,
            "bucket": "hour" if range_id == "today" else "day",
            "points": _points(data, start.astimezone(timezone.utc), now, range_id == "today"),
            "current": _summary(data, s, format_datetime(now + timedelta(seconds=1))),
            "previous": _summary(data, p, s),
        },
        "agent_log": list(reversed(data["agent"][-LOG_LIMIT:])),
    }

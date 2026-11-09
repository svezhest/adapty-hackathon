"""Insights over the JSON API: the shaped view for the screen and the auto refresh of the hypothesis.

docs/agents.md, "Insights: JSON API and live screen". Grouping is `insights.group_tickets`, the model run
is `triage.run_insights`, the draft is `insights.draft_ab_test`; this module only shapes their output and
decides when a run starts. Auto runs are evaluated on `GET /api/insights` (the open screen polls it), so
nothing is spent while nobody looks.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timezone

from resolve import config, db, insights, triage
from resolve.tickets import CATEGORY_LABELS

AUTO_DEBOUNCE_S = 3.0  # the grouping must stay identical this long after a material change
AUTO_MIN_GAP_S = 20.0  # between two auto starts
AUTO_MAX_PER_HOUR = 10  # auto runs per rolling hour; each run is ≤2 model calls
SHARE_THRESHOLD = 0.05  # a category's share of triaged tickets moving this much is material

REPORT_KEYS = (
    "headline", "hypothesis", "caveat", "placement", "control_flow", "variant_source", "variant_flow", "variant",
    "expected_effect", "evidence", "ticket_ids", "cited_cells", "what_changed", "model_calls", "latency_s",
)
PREVIOUS_KEYS = REPORT_KEYS + ("created_at", "trigger", "reason")  # the replaced report as stored: one level, no `previous`


class Busy(Exception):
    """A run is already in flight."""


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _label(category: str | None) -> str:
    return CATEGORY_LABELS.get(category or "", (category or "none").replace("_", " "))


def _key(cell: dict) -> str:
    return f"{cell['category']}|{cell['flow']}"


def outgrown(groups: dict, main: dict | None) -> list[dict]:
    """Flow-known cells with more distinct users than the tested (main) cell has now, largest first."""
    if not main:
        return []
    now = next((c for c in groups.get("cells", []) if _key(c) == _key(main)), None)
    users = now["users"] if now else 0
    return [c for c in groups.get("cells", []) if c["flow"] != "unknown" and c["users"] > users]


def basis_of(groups: dict, main: dict | None = None) -> dict:
    """What a run saw: the raw top, the shares, the cell it tested and the cells already larger than that cell."""
    triaged = groups["triaged"] or 0
    return {
        "top_category": groups["top_category"],
        "top_flow": groups["top_flow"],
        "triaged": triaged,
        "shares": {row["category"]: round(row["count"] / triaged, 4) for row in groups["by_category"]} if triaged else {},
        "main_cell": {"category": main["category"], "flow": main["flow"]} if main else None,
        "outgrown": [_key(c) for c in outgrown(groups, main)],
    }


def material_change(basis: dict | None, groups: dict) -> str | None:
    """Why the hypothesis is out of date, or None.

    Order: a cell outgrew the tested cell (the rule `check_insight` holds the model to), then the raw top
    category, the raw top flow, the largest share move.
    """
    if not basis or not groups["triaged"]:
        return None
    main = basis.get("main_cell")
    if main:
        seen = set(basis.get("outgrown") or [])
        new = [c for c in outgrown(groups, main) if _key(c) not in seen]
        if new:
            tested = next((c for c in groups["cells"] if _key(c) == _key(main)), {**main, "users": 0})
            return f"{insights.cell_label(new[0])} ({new[0]['users']} users) outgrew the tested {insights.cell_label(tested)} ({tested['users']} users)"
    now = basis_of(groups)
    if now["top_category"] != basis.get("top_category"):
        return f"top issue changed: {_label(basis.get('top_category'))} → {_label(now['top_category'])}"
    if now["top_flow"] != basis.get("top_flow"):
        return f"top paywall changed: {insights.flow_title(basis.get('top_flow'))} → {insights.flow_title(now['top_flow'])}"
    old = basis.get("shares") or {}
    moves = [(abs(now["shares"].get(c, 0) - old.get(c, 0)), c) for c in set(old) | set(now["shares"])]
    move, category = max(moves, default=(0, None))
    if category and move >= SHARE_THRESHOLD - 1e-9:
        return f"{_label(category)} share {round(old.get(category, 0) * 100)}% → {round(now['shares'].get(category, 0) * 100)}%"
    return None


def _report_view(report: dict | None, with_previous: bool = True) -> dict | None:
    if not report or not report.get("headline"):
        return None
    out = {k: report.get(k) for k in REPORT_KEYS}
    for k in ("evidence", "ticket_ids", "cited_cells"):
        out[k] = list(out[k] or [])
    out["placement_title"] = insights.PLACEMENTS.get(report.get("placement") or "", {}).get("title", report.get("placement"))
    out.update(created_at=report.get("created_at"), trigger=report.get("trigger"), reason=report.get("reason"))
    if with_previous:
        out["previous"] = _report_view(report.get("previous"), with_previous=False)
    return out


def _draft_view(row: dict) -> dict:
    d = row["draft"]
    return {
        "ab_test_id": row["test_id"],
        "title": row["title"],
        "state": d.get("state") or "draft",
        "placement": d.get("placement"),
        "placement_id": d.get("placement_id") or row["placement_id"],
        "arms": [{"title": a.get("title", ""), "weight": a.get("weight", 0)} for a in d.get("arms", [])],
        "goal": d.get("goal", ""),
        "created_at": row["created_at"],
    }


class Live:
    """Per-app state: the run lock, the last run's outcome, the pending change and the auto-run budget."""

    def __init__(self, services, clock=time.time):
        self.services = services
        self.clock = clock
        self.auto_enabled = config.INSIGHTS_AUTO
        self._lock = threading.Lock()  # held for the whole model run (acquired and released in different threads)
        self._state = threading.Lock()  # guards the fields below
        self.run = {"state": "idle", "trigger": None, "reason": None, "started_at": None, "finished_at": None, "error": None}
        self.pending: dict | None = None  # {"key", "since", "reason"}
        self.auto_starts: deque[float] = deque()
        self.failed_key: str | None = None  # the grouping an auto run failed on: not retried on its own
        self._running_key: str | None = None

    # --- reads ---------------------------------------------------------------------------------------

    def groups(self) -> dict:
        return insights.group_tickets(db.list_tickets(self.services.conn))

    def auto_on(self) -> bool:
        return self.auto_enabled and not self.services.demo and self.services.llm is not None

    def view(self, groups: dict) -> dict:
        conn = self.services.conn
        triaged = groups["triaged"]
        categories = [
            {
                "category": row["category"],
                "count": row["count"],
                "share": round(row["count"] / triaged, 4) if triaged else 0.0,
                "products": [{"product": p, "count": n} for p, n in groups["by_category_product"][row["category"]].items()],
                "flows": [{"flow": f, "title": insights.flow_title(f), "count": n} for f, n in groups["by_category_flow"][row["category"]].items()],
            }
            for row in groups["by_category"]
        ]
        top = None
        if groups["top_category"]:
            top = {"category": groups["top_category"], "flow": groups["top_flow"], "title": insights.flow_title(groups["top_flow"]), "count": groups["top_flow_count"]}
        report = _report_view(db.get_insights(conn))
        tested = None
        if report and report["cited_cells"]:
            main = report["cited_cells"][0]
            count = groups["by_category_flow"].get(main["category"], {}).get(main["flow"], 0)
            tested = {"category": main["category"], "flow": main["flow"], "title": insights.flow_title(main["flow"]), "count": count}
        with self._state:
            run = dict(self.run)
            pending = None
            if self.pending:
                due = max(0.0, self.pending["since"] + AUTO_DEBOUNCE_S - self.clock())
                pending = {"reason": self.pending["reason"], "since": _iso(self.pending["since"]), "due_in_s": round(due, 1), "blocked": self._blocked()}
        return {
            "updated_at": _iso(self.clock()),
            "demo": self.services.demo,
            "llm": self.services.llm is not None,
            "auto": self.auto_on(),
            "total": groups["total"],
            "triaged": triaged,
            "untriaged": groups["untriaged"],
            "categories": categories,
            "top": top,
            "tested": tested,
            "report": {k: v for k, v in report.items() if k != "previous"} if report else None,
            "previous": report["previous"] if report else None,
            "run": run,
            "pending": pending,
            "placements": [
                {"key": k, "title": p["title"], "flows": [f["title"] for f in p["flows"].values()], "draftable": len(p["flows"]) >= 2}
                for k, p in insights.PLACEMENTS.items()
            ],
            "drafts": [_draft_view(r) for r in db.list_ab_drafts(conn)],
        }

    # --- the auto rule (called on every GET) ---------------------------------------------------------

    def _blocked(self) -> str | None:
        if self.pending and self.pending["key"] == self.failed_key:
            return "last auto run failed"
        now = self.clock()
        while self.auto_starts and now - self.auto_starts[0] > 3600:
            self.auto_starts.popleft()
        if len(self.auto_starts) >= AUTO_MAX_PER_HOUR:
            return "hourly cap"
        if self.auto_starts and now - self.auto_starts[-1] < AUTO_MIN_GAP_S:
            return "cooling down"
        return None

    def poll(self, groups: dict) -> str | None:
        """Update the pending change; returns the reason when an auto run should start now (the lock is then held)."""
        if not self.auto_on():
            with self._state:
                self.pending = None
            return None
        stored = db.get_insights(self.services.conn) or {}
        reason = material_change(stored.get("basis"), groups)
        key = json.dumps([groups["by_category_product"], groups["by_category_flow"]], sort_keys=True)
        now = self.clock()
        with self._state:
            if reason is None:
                self.pending = None
                return None
            if not self.pending or self.pending["key"] != key:
                self.pending = {"key": key, "since": now, "reason": reason}
                return None
            self.pending["reason"] = reason
            if now - self.pending["since"] < AUTO_DEBOUNCE_S or self._blocked():
                return None
            if not self._lock.acquire(blocking=False):
                return None
            self.auto_starts.append(now)
            self._running_key = key
            self._started("auto", reason)
            return reason

    # --- runs ----------------------------------------------------------------------------------------

    def _started(self, trigger: str, reason: str | None) -> None:
        self.run = {"state": "running", "trigger": trigger, "reason": reason, "started_at": _iso(self.clock()), "finished_at": None, "error": None}

    def run_manual(self) -> None:
        """A person's click: runs now, in this request. Raises Busy when a run is in flight."""
        if not self._lock.acquire(blocking=False):
            raise Busy("a hypothesis run is already in progress")
        with self._state:
            self._started("manual", None)
        self._execute("manual", None)

    def run_auto(self, reason: str) -> None:
        """The background task after `poll` returned a reason (the lock is already held)."""
        self._execute("auto", reason)

    def _execute(self, trigger: str, reason: str | None) -> None:
        conn = self.services.conn
        try:
            groups = self.groups()
            try:
                result = triage.run_insights(self.services, groups)
            except triage.TriageError as exc:
                with self._state:
                    self.run.update(state="error", error=str(exc), finished_at=_iso(self.clock()))
                    if trigger == "auto":
                        self.failed_key = self._running_key
                return
            old = db.get_insights(conn)
            previous = {k: old[k] for k in PREVIOUS_KEYS if k in old} if old and old.get("headline") else None
            main = (result.get("cited_cells") or [None])[0]
            db.save_insights(conn, {**result, "basis": basis_of(groups, main), "trigger": trigger, "reason": reason, "previous": previous})
            with self._state:
                self.run.update(state="idle", error=None, finished_at=_iso(self.clock()))
                self.pending = None
                self.failed_key = None
        except Exception as exc:  # never leave the lock or a "running" state behind
            with self._state:
                self.run.update(state="error", error=f"{type(exc).__name__}: {exc}", finished_at=_iso(self.clock()))
            raise
        finally:
            self._lock.release()

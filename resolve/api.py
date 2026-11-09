"""The JSON API (docs/agents.md, "JSON API" and "JSON shapes"). Every UI talks to the backend only through this."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from resolve import analytics, config, db, frontline, insights, insights_live, jira, llm, policy, triage, workflow
from resolve.adapty import access_level
from resolve.models import (
    API_STATUSES,
    DB_TO_API_STATUS,
    SOURCES,
    AbDraftRequest,
    AgentConfig,
    ChatRequest,
    ChatResult,
    ChatSession,
    Decision,
    FeedbackCreated,
    JiraExport,
    JiraInfo,
    FeedbackRequest,
    Analytics,
    InsightsView,
    ErrorDetail,
    LlmInfo,
    Rejection,
    Reopen,
    Ticket,
    TicketSummary,
    UserItem,
)

E404 = {404: {"model": ErrorDetail, "description": "Unknown id"}}
E409 = {409: {"model": ErrorDetail, "description": "Wrong ticket status for this call"}}
E409_RUN = {409: {"model": ErrorDetail, "description": "A hypothesis run is already in flight"}}
E409_DRAFT = {409: {"model": ErrorDetail, "description": "Demo mode or no Adapty CLI: nothing is created"}}
E502 = {502: {"model": ErrorDetail, "description": "The person's English could not be translated to the user's language; nothing was sent"}}
from resolve.tickets import CATEGORIES, GENERATED, action_accent, action_state, ticket_title

ACCEPT_STATUSES = ("proposed",)
REJECT_STATUSES = ("new", "triaged", "proposed")
MAX_USERS = 200


def _switch_llm(services, choice: str) -> None:
    """Rebuild the LLM client for both agents. Demo mode never builds one."""
    if services.demo:
        return
    try:
        services.llm = llm.make_llm(choice)
    except (config.MissingKey, ValueError) as exc:
        raise HTTPException(422, f"cannot use {choice}: {exc}") from None


def install(app: FastAPI, services) -> None:
    """CORS for any origin (the APK and other UIs), then the JSON API routes."""
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    live = insights_live.Live(services)
    app.state.insights_live = live
    app.include_router(build_router(services, live=live, ab_runner=lambda: getattr(app.state, "ab_runner", None)))
    choice = policy.load_config(services.conn).llm_provider
    if choice != "env" and not services.demo:
        try:
            _switch_llm(services, choice)
        except HTTPException as exc:
            print(f"warning: {exc.detail}; keeping the provider from .env")


def _triage_view(ticket: dict) -> tuple[dict | None, bool]:
    """The Level 1 ticket and whether it was built from a v1 proposal."""
    if ticket["triage"]:
        return ticket["triage"], False
    if ticket["proposal"]:
        return triage.legacy_to_ticket(ticket["proposal"]), True
    return None, False


def ticket_summary(ticket: dict) -> dict:
    view, _ = _triage_view(ticket)
    options = (view or {}).get("options") or []
    status = DB_TO_API_STATUS[ticket["status"]]
    kind, line = action_state(ticket, status, view, options)
    return {
        "id": ticket["id"],
        "status": status,
        "source": ticket["source"],
        "category": ticket["category"],
        "customer_user_id": ticket["customer_user_id"],
        "email": ticket["email"],
        "subject": ticket["subject"],
        "title": ticket_title(ticket["subject"], ticket["body"], (view or {}).get("summary")),
        "created_at": ticket["created_at"],
        "updated_at": ticket["updated_at"],
        "confidence": view.get("confidence") if view else None,
        "top_action": options[0]["action"] if options else None,
        "top_risk": options[0].get("risk") if options else None,
        "action_kind": kind,
        "action_title": line,
        "action_accent": action_accent(line),
        "triage_error": ticket["triage_error"],
    }


def search_snippet(conn: db.Database, ticket: dict, q: str) -> str | None:
    """Full-text match over everything a support person reads on the ticket; returns ~120 chars around the
    first hit, or None. Case-insensitive for any script (casefold), every word must appear somewhere."""
    view, _ = _triage_view(ticket)
    parts = [ticket["subject"], ticket["body"], ticket["customer_user_id"], ticket.get("email") or "", ticket.get("category") or ""]
    if view:
        parts += [view.get("summary", ""), view.get("root_cause", ""), *view.get("evidence", [])]
        parts += [o.get("reply", "") for o in view.get("options", [])]
    if ticket.get("decision"):
        parts.append(ticket["decision"].get("reply") or ticket["decision"].get("note") or "")
    if ticket.get("session_id"):
        parts += [m["content"] for m in db.list_messages(conn, ticket["session_id"])]
    words = [w for w in q.casefold().split() if w]
    haystack = [p for p in parts if p]
    if not all(any(w in p.casefold() for p in haystack) for w in words):
        return None
    for p in haystack:
        i = p.casefold().find(words[0])
        if i >= 0:
            start = max(0, i - 50)
            text = " ".join(p[start : i + 70].split())
            return ("…" if start else "") + text + ("…" if i + 70 < len(p) else "")
    return None


def ticket_full(conn: db.Database, ticket: dict) -> dict:
    view, legacy = _triage_view(ticket)
    conversation = db.list_messages(conn, ticket["session_id"]) if ticket["session_id"] else None
    return {
        **ticket_summary(ticket),
        "body": ticket["body"],
        "session_id": ticket["session_id"],
        "triage": view,
        "legacy": legacy,
        "profile": ticket["profile"],
        "decision": ticket["decision"],
        "action": ticket["action"],
        "action_error": ticket["action_error"],
        "jira": ticket["jira"],
        "jira_error": ticket["jira_error"],
        "conversation": conversation,
        "history": ticket["history"],
    }


def build_router(services, live: insights_live.Live | None = None, ab_runner=lambda: None) -> APIRouter:
    router = APIRouter()
    conn = services.conn
    live = live or insights_live.Live(services)

    def ticket_or_404(ticket_id: str) -> dict:
        ticket = db.get_ticket(conn, ticket_id)
        if ticket is None:
            raise HTTPException(404, f"no ticket {ticket_id}")
        return ticket

    # One Level 1 run per ticket at a time; a message that arrives during a run gets one more run after it.
    retriage_lock = threading.Lock()
    retriage_running: set[str] = set()
    retriage_again: set[str] = set()

    def retriage(ticket_id: str) -> None:
        with retriage_lock:
            if ticket_id in retriage_running:
                retriage_again.add(ticket_id)
                return
            retriage_running.add(ticket_id)
        try:
            while True:
                triage.run(services, ticket_id, force=True)
                with retriage_lock:
                    if ticket_id not in retriage_again:
                        retriage_running.discard(ticket_id)
                        return
                    retriage_again.discard(ticket_id)
        except Exception:
            with retriage_lock:
                retriage_running.discard(ticket_id)
                retriage_again.discard(ticket_id)
            raise

    @router.post("/chat", response_model=ChatResult, tags=["mobile"], summary="Send one user message to the Level 2 agent")
    def chat(body: ChatRequest, background: BackgroundTasks):
        before = db.get_conversation(conn, body.session_id)
        before_ticket = before["ticket_id"] if before else None
        result = frontline.chat(services, body.customer_user_id, body.session_id, body.message)
        if result.handed_off and result.ticket_id:
            background.add_task(retriage, result.ticket_id)
        return result.model_dump()

    @router.get("/api/chat/{session_id}", response_model=ChatSession, tags=["mobile"], summary="Conversation, tool-call log and outcome", responses=E404)
    def chat_history(session_id: str):
        conversation = db.get_conversation(conn, session_id)
        if conversation is None:
            raise HTTPException(404, f"no session {session_id}")
        events = [
            {k: e[k] for k in ("ts", "tool", "args", "allowed", "blocked_reason", "result")}
            for e in db.list_events(conn, session_id)
        ]
        return {
            "session_id": session_id,
            "customer_user_id": conversation["customer_user_id"],
            "outcome": conversation["outcome"],
            "ticket_id": conversation["ticket_id"],
            "messages": db.list_messages(conn, session_id),
            "events": events,
        }

    @router.post("/feedback", response_model=FeedbackCreated, tags=["mobile"], summary="Feedback outside the chat; triage runs in the background")
    def feedback(body: FeedbackRequest, background: BackgroundTasks):
        ticket_id = db.insert_feedback(
            conn, body.text, source=body.source, customer_user_id=body.customer_user_id, email=body.email, subject=body.subject
        )
        background.add_task(triage.run, services, ticket_id)
        return {"ticket_id": ticket_id}

    @router.get("/api/tickets", response_model=list[TicketSummary], tags=["inbox"])
    def tickets(
        status: Literal[*API_STATUSES] | None = None,
        source: Literal[*SOURCES] | None = None,
        category: Literal[*CATEGORIES] | None = None,
        q: str | None = None,
    ):
        rows = [
            t for t in db.list_tickets(conn)
            if (status is None or DB_TO_API_STATUS[t["status"]] == status)
            and (source is None or t["source"] == source)
            and (category is None or t["category"] == category)
        ]
        if not (q or "").strip():
            return [ticket_summary(t) for t in rows]
        out = []
        for t in rows:
            snippet = search_snippet(conn, t, q)
            if snippet is not None:
                out.append({**ticket_summary(t), "snippet": snippet})
        return out

    @router.get("/api/tickets/{ticket_id}", response_model=Ticket, tags=["inbox"], responses=E404)
    def ticket(ticket_id: str):
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.post("/api/tickets/{ticket_id}/triage", response_model=Ticket, tags=["inbox"], responses=E404, summary="Run Level 1 now (cached unless ?force=1)")
    def run_triage(ticket_id: str, force: bool = False):
        ticket_or_404(ticket_id)
        triage.run(services, ticket_id, force=force)
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.post("/api/tickets/{ticket_id}/jira", response_model=Ticket, tags=["inbox"], responses={
        **E404,
        409: {"model": ErrorDetail, "description": "Jira is not connected"},
        422: {"model": ErrorDetail, "description": "No such team, or an empty title or details"},
    }, summary="File the bug report as one Jira issue; a failure from Jira comes back as jira_error")
    def export_to_jira(ticket_id: str, body: JiraExport | None = None):
        ticket_or_404(ticket_id)
        try:
            jira.export(services, ticket_id, "person", body)
        except jira.NotConnected as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.post("/api/tickets/{ticket_id}/accept", response_model=Ticket, tags=["inbox"], responses={**E404, **E409, **E502})
    def accept(ticket_id: str, body: Decision):
        if ticket_or_404(ticket_id)["status"] not in ACCEPT_STATUSES:
            raise HTTPException(409, "only a proposed ticket can be accepted")
        try:
            workflow.accept(services, ticket_id, body)
        except workflow.OptionsChanged as exc:
            raise HTTPException(409, str(exc)) from None
        except workflow.NotFound:
            raise HTTPException(404, f"no ticket {ticket_id}") from None
        except workflow.TranslationFailed as exc:
            raise HTTPException(502, str(exc)) from None
        except ValueError as exc:  # raised before any write: bad option index, days, or no customer for a grant
            raise HTTPException(422, str(exc)) from None
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.post("/api/tickets/{ticket_id}/reject", response_model=Ticket, tags=["inbox"], responses={**E404, **E409, **E502})
    def reject(ticket_id: str, body: Rejection):
        if ticket_or_404(ticket_id)["status"] not in REJECT_STATUSES:
            raise HTTPException(409, "only an open ticket can be rejected")
        try:
            workflow.reject(services, ticket_id, body)
        except workflow.TranslationFailed as exc:
            raise HTTPException(502, str(exc)) from None
        except workflow.NotFound:
            raise HTTPException(404, f"no ticket {ticket_id}") from None
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.post("/api/tickets/{ticket_id}/reopen", response_model=Ticket, tags=["inbox"], responses={**E404, **E409},
                 summary="Send a closed ticket back to Incoming; Adapty changes are not undone")
    def reopen(ticket_id: str, body: Reopen | None = None):
        ticket = ticket_or_404(ticket_id)
        if ticket["status"] not in workflow.CLOSED_STATUSES:
            raise HTTPException(409, "only a closed ticket can be reopened")
        workflow.reopen(services, ticket_id, (body or Reopen()).note)
        return ticket_full(conn, ticket_or_404(ticket_id))

    @router.get("/api/config", response_model=AgentConfig, tags=["settings"])
    def get_config():
        return policy.load_config(conn).model_dump()

    @router.put("/api/config", response_model=AgentConfig, tags=["settings"], summary="Save; a provider without keys → 422")
    def put_config(body: AgentConfig):
        if body.llm_provider != policy.load_config(conn).llm_provider:
            _switch_llm(services, body.llm_provider)  # 422 when that provider has no keys; nothing saved
        db.save_config(conn, body.model_dump())
        return body.model_dump()

    @router.get("/api/jira", response_model=JiraInfo, tags=["settings"], summary="Whether Jira is connected (never the token)")
    def get_jira():
        client = services.jira
        return {"connected": client is not None, "site": client.base_url if client else None, "demo": services.demo}

    @router.get("/api/llm", response_model=LlmInfo, tags=["settings"])
    def get_llm():
        active = services.llm
        return {
            "selected": policy.load_config(conn).llm_provider,
            "env_default": config.LLM_PROVIDER,
            "active": getattr(active, "provider", None),
            # AnthropicLLM resolves its model lazily over the network; never trigger that from here.
            "model": None if active is None else (config.ANTHROPIC_MODEL or "newest Opus") if active.provider == "anthropic" else active.model,
            "available": llm.available_providers(),
            "demo": services.demo,
        }

    @router.get("/api/analytics", response_model=Analytics, tags=["analyze"])
    def get_analytics(
        range_: Literal["today", "7d", "30d"] = Query("today", alias="range", description="period: today (hourly) / 7d / 30d (daily)"),
        tz: int = Query(0, ge=-840, le=840, description="the viewer's offset from UTC in minutes (JS: -getTimezoneOffset()); sets local midnight and hours"),
    ):
        return analytics.compute(conn, range_, tz)

    @router.get("/api/users", response_model=list[UserItem], tags=["mobile"], summary="Seeded demo users for a user picker")
    def users():
        ids = {r[0] for r in conn.execute("SELECT customer_user_id FROM tickets WHERE customer_user_id != '' AND rule != ?", (GENERATED,))}
        ids |= {r[0] for r in conn.execute("SELECT customer_user_id FROM conversations WHERE customer_user_id != ''")}
        profiles = {}  # latest cached profile per user (later rows overwrite earlier ones)
        for r in conn.execute(
            "SELECT customer_user_id, profile_json FROM tickets WHERE profile_json IS NOT NULL AND rule != ? ORDER BY profile_fetched_at, updated_at", (GENERATED,)
        ):
            profiles[r[0]] = json.loads(r[1])
        now = datetime.now(timezone.utc)
        out = []
        for uid in sorted(ids)[:MAX_USERS]:
            profile = profiles.get(uid)
            if profile is None:
                out.append({"customer_user_id": uid, "badge": "unknown", "product": None})
                continue
            level = access_level(profile, config.ACCESS_LEVEL)
            out.append(
                {
                    "customer_user_id": uid,
                    "badge": triage.level_badge(level, now),
                    "product": level.get("store_product_id") if level else None,
                }
            )
        return out

    @router.get("/api/insights", response_model=InsightsView, tags=["insights"],
                summary="Grouping, the cached hypothesis and drafts; no model call (may schedule an auto run)")
    def get_insights(background: BackgroundTasks):
        groups = live.groups()
        reason = live.poll(groups)
        view = live.view(groups)
        if reason:
            background.add_task(live.run_auto, reason)
        return view

    @router.post("/api/insights/run", response_model=InsightsView, tags=["insights"], responses=E409_RUN,
                 summary="Run the hypothesis now (one model call, ≤2 with the retry); errors come back in run.error")
    def run_insights():
        try:
            live.run_manual()
        except insights_live.Busy as exc:
            raise HTTPException(409, str(exc)) from None
        return live.view(live.groups())

    @router.post("/api/insights/ab-draft", response_model=InsightsView, tags=["insights"],
                 responses={**E409_DRAFT, 502: {"model": ErrorDetail, "description": "The Adapty CLI failed"}},
                 summary="Create a draft A/B test in Adapty (never started)")
    def ab_draft(body: AbDraftRequest):
        runner = ab_runner()
        if services.demo or runner is None:
            raise HTTPException(409, "Demo mode: no A/B test is created; live mode runs `adapty ab-tests create` (draft only)")
        if len(insights.PLACEMENTS[body.placement]["flows"]) < 2:
            raise HTTPException(422, f"{insights.PLACEMENTS[body.placement]['title']} has one flow; an A/B test needs two arms")
        try:
            draft = insights.draft_ab_test(runner, body.placement, body.goal)
        except insights.DraftError as exc:
            raise HTTPException(502, str(exc)) from None
        db.save_ab_draft(conn, body.placement, draft["ab_test_id"], draft["title"], draft)
        return live.view(live.groups())

    return router

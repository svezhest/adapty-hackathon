"""Shared contract models: agent config, the Level 1 ticket, decisions, Level 2 chat results.

Every local copy keeps these shapes (see docs/agents.md). Tool-argument models used with `llm.ToolSpec`
must stay strict-compatible: no free-form dicts; Optional fields are sent as required + nullable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from resolve.insights import PLACEMENT_IDS
from resolve.tickets import CATEGORIES

# Level 1 option actions (what the support person can accept or switch to).
ACTIONS = ("grant_access", "reply_only", "store_instructions", "refund_to_human", "ask_user", "no_reply")
StoreKind = Literal["restore", "cancel", "refund_request"]
Store = Literal["app_store", "play_store"]

# API statuses. The DB also has `triaged` (shown as new) and `approved` (action in flight, shown as proposed).
API_STATUSES = ("new", "proposed", "done", "dismissed")
DB_TO_API_STATUS = {"new": "new", "triaged": "new", "proposed": "proposed", "approved": "proposed", "done": "done", "dismissed": "dismissed"}

SOURCES = ("chat_handoff", "email", "form")
OUTCOMES = ("open", "resolved", "handed_off")
HANDOFF_RULES = ("refund", "chargeback", "threat", "asks_for_human", "profile_mismatch", "repeat_after_grant")


# --- Agent settings (Level 2 config) ---------------------------------------------------------------


class GrantLimits(BaseModel):
    access_level: str = "premium"
    max_days: int = Field(7, ge=1, le=365)
    max_grants_per_user_30d: int = Field(1, ge=0, le=10)
    require_paid: bool = True


class JiraTeam(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    project_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$", description="The Jira project this team works in, e.g. FIT")
    handles: str = Field("", max_length=300, description="What this team fixes: the agent routes by it")
    issue_type: str = Field("Bug", min_length=1, description="The issue type in that project")


class JiraConfig(BaseModel):
    auto_create: bool = Field(False, description="The agent files its bug reports on its own; off = a person's click")
    max_auto_per_day: int = Field(20, ge=0, le=500, description="The most issues the agent files on its own in 24 hours")
    teams: list[JiraTeam] = Field(default_factory=list)

    @field_validator("teams")
    @classmethod
    def unique_names(cls, teams: list[JiraTeam]) -> list[JiraTeam]:
        names = [t.name for t in teams]
        if len(set(names)) != len(names):
            raise ValueError("team names must be different")
        return teams


class AgentConfig(BaseModel):
    app_name: str = "FitBuddy"
    tone: str = "warm, short, plain English"
    tools: dict[str, bool] = Field(default_factory=lambda: {"grant_access": True})
    grant: GrantLimits = Field(default_factory=GrantLimits)
    always_handoff: list[Literal[*HANDOFF_RULES]] = Field(default_factory=lambda: list(HANDOFF_RULES))
    max_turns: int = Field(8, ge=1, le=50)
    # Which LLM runs both agents: env = LLM_PROVIDER from .env (auto picks the one with keys).
    llm_provider: Literal["env", "openai", "anthropic"] = "env"
    jira: JiraConfig = Field(default_factory=JiraConfig)


# --- Level 1 ticket ------------------------------------------------------------------------------


class OptionParams(BaseModel):
    days: int | None = Field(None, description="grant_access only: days of the access level")
    kind: StoreKind | None = Field(None, description="store_instructions only")
    store: Store | None = Field(None, description="store_instructions / refund_to_human: which store")
    question: str | None = Field(None, description="ask_user only: the question to the user")


class TriageOption(BaseModel):
    action: Literal[*ACTIONS]
    params: OptionParams
    reply: str = Field(description="Draft reply to the user, ready to send, under 120 words. Empty for no_reply.")
    risk: Literal["low", "medium", "high"]
    risk_reason: str = Field(description="One line: why this risk level (money, abuse, wrong diagnosis).")
    emoji: str = Field("", max_length=8, description="One emoji, sincere, that says what this answer means for the user (👍 we give something, ❌ we say no or end something, 🙏 we apologise, ❓ we ask, 🧾 we explain, 🔁 restore).")
    confidence: float | None = Field(None, ge=0, le=1, description="How sure you are that this answer is right for this user, 0 to 1, judged for this option on its own.")
    reply_en: str | None = Field(None, description="Faithful English translation of reply for the support person. Null when user_language is English.")


class BugReport(BaseModel):
    team: str = Field(description="The team from config.teams whose `handles` fits")
    summary: str = Field(max_length=200, description="What breaks, one line an engineer understands")
    details: str = Field(max_length=4000, description="Where it breaks, the version and device if given, what the user did, expected and actual. No names or emails.")
    priority: Literal["high", "medium", "low"] = Field(description="high: paying users lose what they paid for or the app is unusable; medium: a feature is broken; low: cosmetic")


class TriageTicket(BaseModel):
    """The arguments of the `submit_ticket` tool: what Level 1 hands to the support person."""

    customer_user_id: str | None = Field(description="The Adapty customer user id, or null when not found")
    lookup: str = Field(description="How the user was identified or why not (e.g. 'id from chat', 'email not in index')")
    summary: str = Field(description="One sentence: what the user wants")
    category: Literal[*CATEGORIES]
    evidence: list[str] = Field(min_length=1, description="Facts used, each 'field: value' from the profile or history")
    root_cause: str = Field(description="One sentence hypothesis of why this happened")
    confidence: float = Field(ge=0, le=1)
    options: list[TriageOption] = Field(min_length=2, max_length=3, description="Ranked, best first")
    bug_report: BugReport | None = Field(None, description="For the engineering team, when the cause is a defect in the product; null otherwise")
    user_language: str | None = Field(None, description="The language the user writes in, as an English name (Russian, Spanish, Serbian (Latin)); English for English users. Mixed: the language of most of their latest message.")
    messages_en: list[str] | None = Field(None, description="Faithful English translation of each numbered message [1], [2], ... in order, one entry each. Null when user_language is English.")


class OptionEdits(BaseModel):
    """What the support person may change before accepting. Any field left None keeps the option's value."""

    action: Literal[*ACTIONS] | None = None
    reply: str | None = None  # the text the user gets, as written
    reply_en: str | None = None  # the person's English: translated to the user's language on send (reply wins if both)
    days: int | None = Field(None, ge=1, le=365)
    kind: StoreKind | None = None
    store: Store | None = None
    question: str | None = None


class JiraExport(BaseModel):
    """What the support person may change before creating the issue. None keeps the agent's value."""

    team: str | None = None
    summary: str | None = Field(None, max_length=255)
    details: str | None = Field(None, max_length=8000)
    priority: Literal["high", "medium", "low"] | None = None


class Decision(BaseModel):
    option: int = Field(ge=0, le=2)
    edits: OptionEdits = Field(default_factory=OptionEdits)
    jira: JiraExport | None = Field(None, exclude=True, description="The person's edits of the bug report, filed with this reply; not part of the stored decision")
    expected_action: str | None = Field(None, exclude=True)  # the action the person saw; a Level 1 rerun may have replaced the options


class Reopen(BaseModel):
    note: str = ""


class Rejection(BaseModel):
    note: str = ""
    reply: str | None = None  # a manual reply → ticket done; none → dismissed
    reply_en: str | None = None  # the person's English: translated to the user's language on send (reply wins if both)


# --- Level 2 chat --------------------------------------------------------------------------------


class ChatRequest(BaseModel):
    customer_user_id: str
    session_id: str
    message: str


class ActionLog(BaseModel):
    tool: str
    args: dict
    allowed: bool
    blocked_reason: str | None = None
    result: dict | str | None = None


class ChatResult(BaseModel):
    reply: str
    actions: list[ActionLog] = Field(default_factory=list)
    handed_off: bool = False
    ticket_id: str | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "agent", "support"]
    content: str
    ts: str


class ChatEvent(BaseModel):
    ts: str
    tool: str
    args: dict
    allowed: bool
    blocked_reason: str | None = None
    result: dict | str | None = None


class ChatSession(BaseModel):
    """GET /api/chat/{session_id}: poll this to show a support reply that arrives after a handoff."""

    session_id: str
    customer_user_id: str
    outcome: Literal[*OUTCOMES]
    ticket_id: str | None = None
    messages: list[ChatMessage]
    events: list[ChatEvent]


class UserItem(BaseModel):
    customer_user_id: str
    badge: Literal["active", "expired", "lifetime", "none", "unknown"]
    product: str | None = None


# --- API response shapes (docs/openapi.json is generated from these) ---------------------------------


class ErrorDetail(BaseModel):
    detail: str


class TriageView(TriageTicket):
    """A Level 1 ticket as shown by the API: a v1 legacy proposal has a single option."""

    options: list[TriageOption] = Field(min_length=1, max_length=3, description="Ranked, best first")


class TicketSummary(BaseModel):
    id: str
    status: Literal[*API_STATUSES]
    source: Literal[*SOURCES]
    category: str | None = None
    customer_user_id: str = Field(description="Empty string when the user is not identified")
    email: str | None = None
    subject: str
    title: str = Field(description="What a person reads: the subject they wrote, else from the triage summary, else the handoff reason")
    created_at: str
    updated_at: str | None = None
    confidence: float | None = None
    top_action: Literal[*ACTIONS] | None = None
    top_risk: Literal["low", "medium", "high"] | None = Field(None, description="The risk of the agent's first option")
    action_kind: Literal["generating", "generated", "tough", "finished", "failed", "closed"] | None = Field(None, description="The category of action_title; it sets the line's tone on screen")
    action_title: str | None = Field(None, description="The line under the title in Incoming: 'Still generating, please wait' until a triage result or error exists, 'We've generated 3 responses, pick one' while it waits for a person, a warning that the agent isn't sure of any response when the best option's confidence is low, on a closed ticket the person's stored decision ('You've chosen an option'); null while new")
    triage_error: str | None = Field(None, description="Why Level 1 triage failed; the ticket stays new")
    snippet: str | None = Field(None, description="Only with ?q=: the text around the first match")


class JiraLink(BaseModel):
    team: str
    project_key: str
    title: str
    by: Literal["agent", "person"]
    created_at: str
    key: str | None = Field(None, description="Null in demo mode: no issue was created")
    url: str | None = None
    demo: bool = False


class JiraInfo(BaseModel):
    connected: bool = Field(description="JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN are all set")
    site: str | None = None
    demo: bool


class Ticket(TicketSummary):
    body: str
    session_id: str | None = None
    triage: TriageView | None = None
    legacy: bool = Field(description="True when triage was built from a v1 proposal")
    profile: dict | None = Field(None, description="Raw Adapty profile snapshot")
    decision: dict | None = Field(None, description="Decision on accept; {rejected: true, note, reply} on reject")
    action: dict | None = Field(None, description="Result of the executed action")
    action_error: str | None = None
    jira: JiraLink | None = Field(None, description="The Jira issue filed for this ticket")
    jira_error: str | None = Field(None, description="Why the last export failed")
    conversation: list[ChatMessage] | None = None
    history: list[dict] = Field(default_factory=list, description="Earlier closes of a reopened ticket: {at, from_status, decision, action, note}")


class LlmInfo(BaseModel):
    selected: Literal["env", "openai", "anthropic"]
    env_default: str
    active: str | None = None
    model: str | None = None
    available: dict[str, bool]
    demo: bool


class DayCount(BaseModel):
    day: str
    n: int


class TicketStats(BaseModel):
    total: int
    by_status: dict[str, int]
    by_source: dict[str, int]
    by_category: dict[str, int]
    per_day: list[DayCount]


class Level2Stats(BaseModel):
    sessions: int
    resolved: int
    handed_off: int
    open: int
    handoff_reasons: dict[str, int]
    grants: int
    days_granted: int
    blocked: dict[str, int]


class RootCauseCount(BaseModel):
    root_cause: str
    n: int


class TriageStats(BaseModel):
    triaged: int
    confidence_buckets: dict[str, int]
    accepted_option: dict[str, int]
    top_root_causes: list[RootCauseCount]


class EvalSummary(BaseModel):
    model: str | None = None
    cases: int | None = None
    category_correct: int | None = None
    action_correct: int | None = None


class TimelinePoint(BaseModel):
    t: str = Field(description="Bucket start: 'YYYY-MM-DDTHH:00:00Z' (hourly) or 'YYYY-MM-DD' (daily)")
    agent: int = Field(description="Chats Level 2 kept (not handed off)")
    handed_off: int = Field(description="Chats handed to a person")
    closed_by_people: int = Field(description="Tickets a person closed in Level 1")
    feedback: int = Field(description="Other feedback that came in (form, email)")
    grant_days: int


class Timeline(BaseModel):
    bucket: Literal["hour", "day"]
    points: list[TimelinePoint]
    agent_share: float | None = Field(None, description="Share of chats Level 2 kept, 0..1")


class OpenTask(BaseModel):
    kind: Literal["chat", "triage", "review"]
    id: str
    title: str
    user: str
    since: str | None = None
    age_s: int | None = None
    note: str
    detail: str | None = Field(None, description="For the live list: chat = the agent's last tool step, review = the agent's first option; null for triage")


class OpenCounts(BaseModel):
    """True totals of the open_tasks kinds (open_tasks lists at most 20 of each)."""

    chat: int
    triage: int
    review: int


# Analyze v3: one date range (every number is defined in resolve/analytics.py).


class ChatCounts(BaseModel):
    total: int = Field(description="Chats started in the range")
    resolved: int
    handed_off: int
    open: int


class ReplySide(BaseModel):
    """One column of 'reply by reply': machines (agent messages) or humans (replies sent from Incoming). Shares are 0..1."""

    replies: int
    first_reply_median_s: float | None = None
    closed_with_reply: float | None = None
    came_back: float | None = None
    passed_on: float | None = None
    free_days: int


class GrantTotals(BaseModel):
    count: int = Field(description="Level 2 grants in the range")
    days: int
    days_each: list[int] = Field(description="Days of each Level 2 grant, oldest first")


class Decisions(BaseModel):
    triaged: int
    decided: int
    first: int
    second_third: int
    edited: int
    own_reply: int
    no_reply: int


class PeriodSummary(BaseModel):
    chats: ChatCounts
    machine_share: float | None = Field(None, description="resolved / (resolved + handed_off), 0..1")
    machines: ReplySide
    humans: ReplySide
    grants: GrantTotals
    handoff_reasons: dict[str, int]
    blocked: dict[str, int]
    decisions: Decisions


class PeriodPoint(BaseModel):
    t: str = Field(description="Bucket start, UTC 'YYYY-MM-DDTHH:MM:SSZ' (a local hour or a local day)")
    agent: int
    handed_off: int
    closed_by_people: int
    feedback: int


class Period(BaseModel):
    range: Literal["today", "7d", "30d"]
    tz_offset_min: int
    start: str
    end: str
    prev_start: str = Field(description="previous = [prev_start, start)")
    bucket: Literal["hour", "day"]
    points: list[PeriodPoint]
    current: PeriodSummary
    previous: PeriodSummary


class AgentReply(BaseModel):
    ts: str
    session_id: str
    user: str
    reply: str
    latency_s: float | None = None
    outcome: Literal["resolved", "handed_off", "grant_blocked", "granted", "with_teammate", "answered"]
    grant_days: int | None = None


class Analytics(BaseModel):
    tickets: TicketStats
    level2: Level2Stats
    triage: TriageStats
    eval: EvalSummary | None = None
    timeline: Timeline
    open_tasks: list[OpenTask]
    open_counts: OpenCounts
    period: Period
    agent_log: list[AgentReply] = Field(description="Latest agent replies, newest first (all time, at most 20)")


class Health(BaseModel):
    ok: bool
    demo: bool


class FeedbackCreated(BaseModel):
    ticket_id: str


class FeedbackRequest(BaseModel):
    text: str
    source: Literal[*SOURCES] = "form"
    customer_user_id: str | None = None
    email: str | None = None
    subject: str | None = None


# --- Insights (docs/agents.md, "Insights: JSON API and live screen") --------------------------------


class InsightCount(BaseModel):
    product: str
    count: int


class InsightFlowCount(BaseModel):
    flow: str = Field(description="Flow key from the offline dataset, or 'unknown'")
    title: str
    count: int


class InsightCategory(BaseModel):
    category: str
    count: int
    share: float = Field(description="count / triaged")
    products: list[InsightCount]
    flows: list[InsightFlowCount]


class InsightTop(BaseModel):
    category: str
    flow: str | None
    title: str
    count: int


class InsightCell(BaseModel):
    category: str
    flow: str = Field(description="Flow key from the offline dataset")
    tickets: int
    users: int = Field(description="Distinct customers in the cell")


class InsightReport(BaseModel):
    headline: str
    hypothesis: str
    caveat: str | None = Field(None, description="What the data cannot tell, or the part a paywall cannot fix")
    placement: str
    placement_title: str | None
    control_flow: str
    variant_source: Literal["existing_flow", "edited_copy"] | None = Field(None, description="The variant arm is another existing flow, or an edited copy of the control")
    variant_flow: str | None = Field(None, description="The existing flow used as the variant arm (variant_source = existing_flow)")
    variant: str
    expected_effect: str
    evidence: list[str]
    ticket_ids: list[str] = Field(default_factory=list, description="Tickets the hypothesis was read from")
    cited_cells: list[InsightCell] = Field(default_factory=list, description="The first is the tested cell")
    what_changed: str | None = Field(None, description="Computed from counts against the replaced report: the test kept or moved")
    model_calls: int | None = Field(None, description="Model calls the run took (2 = one corrective retry)")
    latency_s: float | None = Field(None, description="Wall time of the model calls")
    created_at: str | None
    trigger: Literal["manual", "auto"] | None = None
    reason: str | None = Field(None, description="What started an auto run")


class InsightRun(BaseModel):
    state: Literal["idle", "running", "error"]
    trigger: Literal["manual", "auto"] | None
    reason: str | None
    started_at: str | None
    finished_at: str | None
    error: str | None


class InsightPending(BaseModel):
    reason: str = Field(description="What changed, e.g. 'top issue changed: Refund request → Charged after trial'")
    since: str | None
    due_in_s: float = Field(description="Seconds until the debounce ends")
    blocked: str | None = Field(description="Why it will not start: 'hourly cap', 'cooling down', 'last auto run failed'")


class InsightPlacement(BaseModel):
    key: str
    title: str
    flows: list[str]
    draftable: bool = Field(description="Two or more flows: can be an A/B test")


class DraftArm(BaseModel):
    title: str
    weight: int


class AbDraft(BaseModel):
    ab_test_id: str
    title: str
    state: str
    placement: str | None
    placement_id: str
    arms: list[DraftArm]
    goal: str
    created_at: str


class InsightsView(BaseModel):
    updated_at: str
    demo: bool
    llm: bool = Field(description="A model client is configured")
    auto: bool = Field(description="The hypothesis re-runs on its own after a material change")
    total: int
    triaged: int
    untriaged: int
    categories: list[InsightCategory]
    top: InsightTop | None = Field(description="Raw top: the category with most tickets and its most common flow")
    tested: InsightTop | None = Field(None, description="The cell the current report tests (its cited_cells[0]), with its ticket count now")
    report: InsightReport | None
    previous: InsightReport | None = Field(description="The report the current one replaced")
    run: InsightRun
    pending: InsightPending | None
    placements: list[InsightPlacement]
    drafts: list[AbDraft]


class AbDraftRequest(BaseModel):
    placement: Literal[*PLACEMENT_IDS] = Field(description="insights.PLACEMENTS key")
    goal: str = Field("", max_length=500)

"""Eval v2: Level 1 triage on data/eval.json, and with --l2 the Level 2 chats in data/l2_scenarios.json.

    python scripts/eval.py                    # prints how many model calls it may make, runs nothing
    python scripts/eval.py --yes              # Level 1: 20 cases + hard cases (paid)
    python scripts/eval.py --yes --l2         # + Level 2 scenarios (paid)
    python scripts/eval.py --yes --only t-017,x-004,l2-repeat

Runs on a scratch copy of --db (default data/eval-base.sqlite, which has the profiles the labels were written
against); the source DB is never written. The hard-case feedback (data/feedback_extra.json) and the
email index (scripts/seed_emails.py EMAILS) are added to the copy. Profiles come from that cache (a
customer id not in it = profile not found); --live-profiles reads them from Adapty instead. Adapty
writes are never made: the client is wrapped so grant_access_level / update_profile raise AdaptyError,
and a grant the code tried to make is counted as "allowed" (would grant).

Level 1: triage.run(force=True) per case; scores category and options[0].action, and reports the
quality signals the score misses: a `forbidden_actions` option anywhere or a `forbidden_first` action
first, reply length in words, pronoun slips, evidence not in the facts. Cases with `feedback` (and
`chat`, for a handed-off chat) are inserted into the scratch DB; `profiles` in data/eval.json override
the cached profile of those customers. Level 2: each scenario's user turns through frontline.chat with
a fresh session id, the clock frozen at the scenario's `as_of`; scores the grant outcome
(allowed / blocked:<reason> / none), the handoff reason, the store steps that ran, the log (a call that
was not allowed carries a blocked_reason) and, unless --no-judge, the scenario's `reply` checks (one
model call per run judges the transcript). In Level 2 a grant the code allows is simulated: nothing
reaches Adapty, the agent sees a success.

    python scripts/eval.py --yes --l2-only --repeat 2     # Level 2 only, each scenario twice

Writes data/eval_results.json (Level 2 only: data/eval_l2_results.json); the scratch DB is kept
(path printed) so the transcripts can be inspected. Prints the token usage of the run.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from resolve import config, db  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from resolve.adapty import AdaptyError  # noqa: E402

import import_feedback  # noqa: E402
import seed_emails  # noqa: E402

L1_CALL_CAP = 8  # triage: db.reserve_model_call cap per ticket (docs/agents.md)
L2_CALLS_PER_MESSAGE = 4  # frontline: ≤4 model calls per user message
READ_ONLY = "eval_read_only"


class ReadOnlyAdapty:
    """Looks like adapty.Client for reads; every write raises AdaptyError, grants are remembered as attempts."""

    def __init__(self, read_profile):
        self._read_profile = read_profile
        self.attempts: list[dict] = []
        self.simulate_grants = False

    def get_profile(self, customer_user_id: str) -> dict:
        return self._read_profile(customer_user_id)

    def grant_access_level(self, customer_user_id, access_level_id, expires_at, starts_at=None):
        self.attempts.append({"customer_user_id": customer_user_id, "access_level_id": access_level_id, "expires_at": str(expires_at)})
        if self.simulate_grants:  # Level 2: the agent sees what a live grant looks like; Adapty is not called
            return {}
        raise AdaptyError("Adapty writes are disabled during eval", code=READ_ONLY)

    def update_profile(self, customer_user_id, custom_attributes):
        if self.simulate_grants:  # the stamp after a simulated grant: not sent either
            return {}
        raise AdaptyError("Adapty writes are disabled during eval", code=READ_ONLY)

    def update_email(self, customer_user_id, email):
        raise AdaptyError("Adapty writes are disabled during eval", code=READ_ONLY)


def cached_profiles(conn: db.Database) -> dict[str, dict]:
    """customer_user_id → the latest profile cached on any of its tickets (snapshot taken before the run)."""
    rows = conn.execute("SELECT customer_user_id, profile_json FROM tickets WHERE profile_json IS NOT NULL ORDER BY profile_fetched_at")
    return {r["customer_user_id"]: json.loads(r["profile_json"]) for r in rows}


def profile_reader(profiles: dict[str, dict], overrides: dict[str, dict] | None = None):
    """Reads the cache; `overrides` (filled per Level 2 scenario from setup.profile) wins."""
    overrides = {} if overrides is None else overrides

    def read(customer_user_id: str) -> dict:
        if customer_user_id in overrides:
            return json.loads(json.dumps(overrides[customer_user_id]))
        if customer_user_id not in profiles:
            # STUB: the real Adapty answer for an unknown customer id is assumed to be a 404; code not verified
            raise AdaptyError("profile not found (eval cache)", status=404, code="profile_does_not_exist")
        return json.loads(json.dumps(profiles[customer_user_id]))  # a fresh copy per call

    return read


def scratch_copy(source: Path) -> Path:
    target = Path(tempfile.mkdtemp(prefix="resolve-eval-")) / "eval.sqlite"
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    src.backup(dst)  # consistent copy, WAL included; the source is only read
    src.close()
    dst.close()
    return target


def matches(expected, got) -> bool:
    if expected == "any":
        return True
    if isinstance(expected, list):
        return got in expected
    return got == expected


def insert_cases(conn: db.Database, cases: list[dict], access_level: str) -> int:
    """Hard cases that carry their own feedback (data/eval.json `feedback`, `chat`, `setup`) → tickets in the scratch DB."""
    inserted = 0
    for case in cases:
        fb = case.get("feedback")
        if not fb or db.get_ticket(conn, case["ticket_id"]) is not None:
            continue
        text, session_id = fb.get("text", ""), None
        if case.get("chat"):
            chat = case["chat"]
            session_id = chat["session_id"]
            db.ensure_conversation(conn, session_id, fb["customer_user_id"])
            for role, content in chat["messages"]:
                db.append_message(conn, session_id, role, content)
            transcript = "\n".join(f"{role}: {content}" for role, content in chat["messages"])
            text = f"Handoff reason: {chat['reason']}\nSummary: {chat['summary']}\n\nConversation:\n{transcript}\n\nTools tried:\n- none"
        db.insert_feedback(
            conn,
            text,
            source=fb["source"],
            customer_user_id=fb.get("customer_user_id"),
            email=fb.get("email"),
            session_id=session_id,
            subject=fb.get("subject") or (f"Chat: {case['chat']['reason']}" if case.get("chat") else None),
            ticket_id=case["ticket_id"],
        )
        if session_id:
            db.set_outcome(conn, session_id, "handed_off", case["ticket_id"])
        prior = case.get("setup", {}).get("prior_grant")
        if prior:
            db.record_grant(conn, fb["customer_user_id"], access_level, prior["days"], prior.get("expires_at"), prior["source"], prior["ref"], {"eval_setup": True})
        inserted += 1
    return inserted


PRONOUN = re.compile(r"\b(he|she|him|his|her|hers|himself|herself)\b", re.I)


def pronoun_slips(result: dict) -> list[str]:
    """Gendered pronouns in what the support person reads (summary, root cause, evidence); the spec says 'the user'."""
    texts = [result.get("summary") or "", result.get("root_cause") or "", *(result.get("evidence") or [])]
    return [m.group(0) for t in texts for m in PRONOUN.finditer(t)]


def run_l1(services, conn: db.Database, ro: ReadOnlyAdapty, cases: list[dict]) -> list[dict]:
    from resolve import triage

    rows = []
    for case in cases:
        tid = case["ticket_id"]
        if db.get_ticket(conn, tid) is None:
            print(f"   {tid}: not in the DB, skipped")
            rows.append({"ticket_id": tid, "error": "not in the DB"})
            continue
        # fresh start on the scratch copy: no cached triage, the full call budget
        db.update_ticket(conn, tid, status="new", model_calls=0, triage_json=None, triage_at=None, triage_error=None)
        attempts_before = len(ro.attempts)
        error = None
        try:
            triage.run(services, tid, force=True)
        except Exception as exc:  # noqa: BLE001 — one failed case must not stop the eval
            error = f"{exc.__class__.__name__}: {exc}"
        ticket = db.get_ticket(conn, tid)
        result = ticket["triage"]
        row = {"ticket_id": tid, "model_calls": ticket["model_calls"], "adapty_write_attempts": len(ro.attempts) - attempts_before}
        if not result or not result.get("options"):
            row["error"] = error or ticket["triage_error"] or "no triage stored"
            print(f"✗✗ {tid:7s} error: {row['error']}")
            rows.append(row)
            continue
        categories = case.get("acceptable_categories", [case["category"]])
        actions = case.get("acceptable_actions", [case["expected_action"]])
        got_category, got_action = result["category"], result["options"][0]["action"]
        all_actions = [o["action"] for o in result["options"]]
        forbidden = [a for a in all_actions if a in case.get("forbidden_actions", [])]
        if got_action in case.get("forbidden_first", []):
            forbidden.append(f"{got_action} first")
        words = [len(o["reply"].split()) for o in result["options"] if o["action"] != "no_reply"]
        row.update(
            expected_category=case["category"],
            got_category=got_category,
            category_ok=got_category in categories,
            expected_action=case["expected_action"],
            got_action=got_action,
            action_ok=got_action in actions,
            confidence=result.get("confidence"),
            customer_user_id=result.get("customer_user_id"),
            evidence=len(result.get("evidence") or []),
            evidence_ungrounded=ungrounded(services, conn, ticket, result),
            actions=all_actions,
            forbidden=forbidden,
            reply_words=words,
            style=pronoun_slips(result),
        )
        rows.append(row)
        mark = ("✓" if row["category_ok"] else "✗") + ("✓" if row["action_ok"] else "✗")
        flags = (f"  FORBIDDEN {forbidden}" if forbidden else "") + (f"  style {len(row['style'])}" if row["style"] else "")
        print(f"{mark} {tid:7s} {case['category']:26s}→ {got_category:26s} {case['expected_action']:18s}→ {' / '.join(all_actions):52s} {row['confidence']}  words {max(words, default=0)}{flags}")
    return rows


def ungrounded(services, conn: db.Database, ticket: dict, result: dict) -> list[str]:
    """Evidence lines whose value is not in the facts code loads for this ticket (v3 prefetch). Rough: no LLM."""
    from datetime import datetime, timezone

    from resolve import policy, triage
    from resolve.models import TriageTicket

    facts = triage.prefetch(services, ticket, set(), {}, policy.load_config(conn), datetime.now(timezone.utc))
    return triage.ungrounded_evidence(TriageTicket.model_validate(result), json.dumps(facts) + ticket["body"] + ticket["subject"])


def grant_outcome(actions: list, attempted: bool) -> tuple[str, list]:
    """allowed (the code let a grant through, or a write reached Adapty) | blocked:<first reason> | none."""
    grants = [a for a in actions if a.tool == "grant_access"]
    days = [a.args.get("days") for a in grants if a.allowed]
    if attempted or any(a.allowed for a in grants):
        return "allowed", days
    if grants:
        return f"blocked:{grants[0].blocked_reason}", days
    return "none", days


class FrozenClock:
    """`datetime` stand-in for the modules that read the clock, so a cached profile's dates keep their meaning."""

    MODULES = ("resolve.frontline", "resolve.policy", "resolve.actions")

    def __init__(self):
        import importlib

        self._modules = [importlib.import_module(m) for m in self.MODULES]
        self._real = {m: m.datetime for m in self._modules}

    def set(self, iso: str | None) -> None:
        from datetime import datetime

        if iso is None:
            for m in self._modules:
                m.datetime = self._real[m]
            return
        fixed = datetime.fromisoformat(iso.replace("Z", "+00:00"))

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed if tz is None else fixed.astimezone(tz)

        for m in self._modules:
            m.datetime = Frozen


def steps_ran(actions: list) -> list[str]:
    """'kind:store' of every store_instructions call that ran."""
    return [f"{a.args.get('kind')}:{a.args.get('store')}" for a in actions if a.tool == "store_instructions" and a.allowed]


def log_gaps(conn: db.Database, session_id: str) -> list[str]:
    """Logged calls that were not allowed but say no reason: the log cannot tell why they did not run."""
    return [e["tool"] for e in db.list_events(conn, session_id) if not e["allowed"] and not e["blocked_reason"]]


class VerdictCheck(BaseModel):
    n: int = Field(description="The check's number")
    passed: bool
    why: str = Field(description="One short sentence, quoting the reply when it fails")


class Verdict(BaseModel):
    checks: list[VerdictCheck]


JUDGE_SYSTEM = """You review one support chat between a user and a support assistant of a subscription app. You get the account the assistant could see (raw), the chat with every tool call the assistant made and the tool's result, and numbered checks. Judge each check strictly from the assistant's replies: a check passes only when every reply satisfies it. Tool results count as sources the assistant had; the raw account counts too; anything else the assistant states as fact (how the app works, menus, policies, causes) is not backed by a source. The system also told the assistant, as fact: premium belongs to the account and works on every device signed in to it, iPhone or Android, whichever store it was bought in; the user writes from the app signed in to this account. A reply saying a teammate will reply is backed only when a handoff tool call ran. Call verdict once with one entry per check."""


def transcript_text(turns: list[tuple[str, object]]) -> str:
    """(user message, ChatResult) per turn: the message, every tool call with its result, the reply."""
    lines = []
    for message, result in turns:
        lines.append(f"USER: {message}")
        for a in result.actions:
            out = a.result if isinstance(a.result, str) else json.dumps(a.result, ensure_ascii=False)
            reason = f" blocked={a.blocked_reason}" if a.blocked_reason else ""
            lines.append(f"  [tool {a.tool} {json.dumps(a.args, ensure_ascii=False)} allowed={str(a.allowed).lower()}{reason}] → {out[:600]}")
        lines.append(f"ASSISTANT: {result.reply}")
    return "\n".join(lines)


def judge(model, checks: list[str], turns: list, profile: dict | None) -> list[dict]:
    from resolve import llm

    account = json.dumps({k: profile.get(k) for k in ("total_revenue_usd", "access_levels", "subscriptions", "non_subscriptions")}, ensure_ascii=False) if profile else "not found"
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(checks, 1))
    content = f"Account (raw):\n{account}\n\nChat:\n{transcript_text(turns)}\n\nChecks:\n{numbered}"
    spec = llm.ToolSpec("verdict", "Your verdict on every check.", Verdict)
    messages = [{"role": "user", "content": content}]
    for _ in range(2):
        step = model.step(JUDGE_SYSTEM, messages, [spec])
        call = next((c for c in step.tool_calls if c.name == "verdict"), None)
        if call is not None:
            try:
                verdict = Verdict.model_validate(call.args)
            except Exception:  # noqa: BLE001
                verdict = None
            if verdict is not None:
                by_n = {c.n: c for c in verdict.checks}
                return [{"check": text, "passed": by_n[i].passed if i in by_n else False, "why": by_n[i].why if i in by_n else "no verdict"} for i, text in enumerate(checks, 1)]
        messages += [llm.assistant_message(step), {"role": "user", "content": "Call verdict once with one entry per check."}]
    return [{"check": text, "passed": False, "why": "judge failed"} for text in checks]


def run_l2(services, conn: db.Database, ro: ReadOnlyAdapty, overrides: dict, scenarios: list[dict], access_level: str,
           default_as_of: str | None, repeat: int = 1, use_judge: bool = True) -> list[dict]:
    from resolve import frontline

    clock = FrozenClock()
    ro.simulate_grants = True
    rows = []
    try:
        for sc in scenarios:
            for run in range(1, repeat + 1):
                rows.append(run_scenario(services, conn, ro, overrides, sc, access_level, clock, sc.get("as_of", default_as_of), run, use_judge, frontline))
    finally:
        clock.set(None)
        ro.simulate_grants = False
    return rows


def run_scenario(services, conn, ro, overrides, sc, access_level, clock, as_of, run, use_judge, frontline) -> dict:
    session_id = f"eval-{sc['id']}-{uuid.uuid4().hex[:8]}"
    setup = sc.get("setup", {})
    cuid = sc["customer_user_id"]
    if "profile" in setup:
        overrides[cuid] = setup["profile"]
    clock.set(as_of)
    last_grant = conn.execute("SELECT COALESCE(MAX(id), 0) FROM grants").fetchone()[0]
    try:
        prior = setup.get("prior_grant")
        if prior:
            db.record_grant(conn, cuid, access_level, prior["days"], None, prior["source"], prior["ref"], {"eval_setup": True})
        attempts_before = len(ro.attempts)
        actions, handed_off, error, sent, turns = [], False, None, 0, []
        for message in sc["messages"]:
            sent += 1
            try:
                result = frontline.chat(services, cuid, session_id, message)
            except Exception as exc:  # noqa: BLE001
                error = f"{exc.__class__.__name__}: {exc}"
                break
            actions.extend(result.actions)
            turns.append((message, result))
            if result.handed_off:
                handed_off = True
                break
        try:
            profile = ro.get_profile(cuid)
        except AdaptyError:
            profile = None
    finally:
        clock.set(None)
        overrides.pop(cuid, None)
    expect = sc["expect"]
    with conn:  # runs are independent: a grant simulated in this run must not block the next one
        conn.execute("DELETE FROM grants WHERE id > ?", (last_grant,))
    grant, days = grant_outcome(actions, len(ro.attempts) > attempts_before)
    handoff = None
    if handed_off:  # "auto" = handed off without a handoff tool call (max_turns, LLM failure)
        handoff = next((a.args.get("reason") for a in actions if a.tool == "handoff"), None) or "auto"
    ran = steps_ran(actions)
    gaps = log_gaps(conn, session_id)
    problems = []
    if error:
        problems.append(error)
    if not matches(expect["grant"], grant):
        problems.append(f"grant {grant}")
    if not matches(expect["handoff"], handoff):
        problems.append(f"handoff {handoff}")
    problems += [f"steps ran {s}" for s in expect.get("steps_forbid", []) if s in ran]
    problems += [f"steps missing {s}" for s in expect.get("steps_require", []) if s not in ran]
    if gaps:
        problems.append(f"log: not allowed without a reason: {', '.join(gaps)}")
    verdicts = judge(services.llm, expect["reply"], turns, profile) if use_judge and expect.get("reply") and not error else []
    problems += [f"reply: {v['check'][:60]}… ({v['why']})" for v in verdicts if not v["passed"]]
    row = {
        "id": sc["id"],
        "run": run,
        "session_id": session_id,
        "messages_sent": sent,
        "expected": expect,
        "grant": grant,
        "grant_days": days,
        "handoff": handoff,
        "steps_ran": ran,
        "log_gaps": gaps,
        "tools": [{"tool": a.tool, "args": a.args, "allowed": a.allowed, "blocked_reason": a.blocked_reason} for a in actions],
        "replies": [r.reply for _, r in turns],
        "reply_checks": verdicts,
        "code_ok": not [p for p in problems if not p.startswith("reply:")],
        "reply_ok": all(v["passed"] for v in verdicts),
        "correct": not problems,
    }
    if error:
        row["error"] = error
    print(f"{'✓' if row['correct'] else '✗'} {sc['id']:28s} #{run} grant {grant:22s} handoff {str(handoff):18s} steps {','.join(ran) or '-'}")
    for p in problems:
        print(f"      {p}")
    return row


class Usage:
    """Token counts of every model call (OpenAI-compatible responses; DeepSeek adds cache hits)."""

    def __init__(self, model):
        self.calls = 0
        self.prompt = 0
        self.completion = 0
        self.cache_hit = 0
        client = getattr(model, "client", None)
        completions = getattr(getattr(client, "chat", None), "completions", None)
        if completions is None:
            return
        create = completions.create

        def counted(*args, **kwargs):
            response = create(*args, **kwargs)
            usage = getattr(response, "usage", None)
            self.calls += 1
            if usage is not None:
                self.prompt += usage.prompt_tokens or 0
                self.completion += usage.completion_tokens or 0
                self.cache_hit += getattr(usage, "prompt_cache_hit_tokens", 0) or 0
            return response

        completions.create = counted

    def summary(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": self.prompt, "prompt_cache_hit_tokens": self.cache_hit, "completion_tokens": self.completion}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="run it (paid model calls)")
    parser.add_argument("--l2", action="store_true", help="also run the Level 2 scenarios")
    parser.add_argument("--l2-only", action="store_true", help="only the Level 2 scenarios")
    parser.add_argument("--repeat", type=int, default=1, help="Level 2: runs per scenario")
    parser.add_argument("--no-judge", action="store_true", help="Level 2: skip the reply checks (no judge calls)")
    parser.add_argument("--only", default="", help="comma-separated case / scenario ids")
    parser.add_argument("--db", type=Path, default=config.EVAL_DB_PATH, help="source DB, copied; never written")
    parser.add_argument("--live-profiles", action="store_true", help="read profiles from Adapty instead of the DB cache")
    parser.add_argument("--out", type=Path, default=None, help="default data/eval_results.json (Level 2 only: data/eval_l2_results.json)")
    args = parser.parse_args()
    args.l2 = args.l2 or args.l2_only
    if args.out is None:
        args.out = config.ROOT / "data" / ("eval_l2_results.json" if args.l2_only else "eval_results.json")

    spec = json.loads((config.ROOT / "data" / "eval.json").read_text())
    cases = spec["cases"] + spec.get("hard_cases", [])
    l2_spec = json.loads((config.ROOT / "data" / "l2_scenarios.json").read_text()) if args.l2 else {}
    scenarios = l2_spec.get("scenarios", [])
    if args.l2_only:
        cases = []
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if only:
        cases = [c for c in cases if c["ticket_id"] in only]
        scenarios = [s for s in scenarios if s["id"] in only]
    judge_calls = 0 if args.no_judge else sum(1 for s in scenarios if s["expect"].get("reply"))
    max_calls = len(cases) * L1_CALL_CAP + args.repeat * (sum(len(s["messages"]) for s in scenarios) * L2_CALLS_PER_MESSAGE + judge_calls)
    print(f"{len(cases)} Level 1 cases (≤{L1_CALL_CAP} model calls each), {len(scenarios)} Level 2 scenarios × {args.repeat} (≤{L2_CALLS_PER_MESSAGE} per user message, +{judge_calls} judge calls per round)")
    print(f"up to {max_calls} model calls; profiles {'live from Adapty (reads only)' if args.live_profiles else 'from the DB cache'}; no Adapty writes")
    if not args.yes:
        print("paid: pass --yes to run")
        return 1

    from resolve import llm, workflow
    from resolve.models import AgentConfig

    scratch = scratch_copy(args.db)
    conn = db.connect(scratch)
    db.seed(conn, config.ROOT / "data" / "tickets.json")
    import_feedback.import_items(conn, import_feedback.load())
    seed_emails.index_only(conn)
    insert_cases(conn, cases, AgentConfig(**(db.get_config(conn) or {})).grant.access_level)
    overrides: dict[str, dict] = {}
    if args.live_profiles:
        from resolve.adapty import Client

        live = Client(config.adapty_secret()).get_profile
        ro = ReadOnlyAdapty(lambda cuid: json.loads(json.dumps(overrides[cuid])) if cuid in overrides else live(cuid))
    else:
        ro = ReadOnlyAdapty(profile_reader({**cached_profiles(conn), **spec.get("profiles", {})}, overrides))
    model = llm.make_llm()
    usage = Usage(model)
    services = workflow.Services(conn=conn, demo=False, adapty=ro, llm=model)
    print(f"model {model.provider}/{model.model}, scratch DB {scratch}\n")

    l1_rows = run_l1(services, conn, ro, cases)
    l2_rows = []
    if scenarios:
        print()
        access_level = AgentConfig(**(db.get_config(conn) or {})).grant.access_level
        l2_rows = run_l2(services, conn, ro, overrides, scenarios, access_level, l2_spec.get("as_of"), args.repeat, not args.no_judge)

    scored = [r for r in l1_rows if "got_category" in r]
    summary = {
        "model": model.model,
        "cases": len(cases),
        "scored": len(scored),
        "category_correct": sum(r["category_ok"] for r in scored),
        "action_correct": sum(r["action_ok"] for r in scored),
        "both_correct": sum(r["category_ok"] and r["action_ok"] for r in scored),
        "l2_cases": len(scenarios) if args.l2 else None,
        "l2_correct": sum(r["correct"] for r in l2_rows) if args.l2 else None,
        "l2_runs": len(l2_rows) if args.l2 else None,
        "l2_code_ok": sum(r["code_ok"] for r in l2_rows) if args.l2 else None,
        "l2_by_scenario": {sc["id"]: f"{sum(r['correct'] for r in l2_rows if r['id'] == sc['id'])}/{sum(1 for r in l2_rows if r['id'] == sc['id'])}" for sc in scenarios} if args.l2 else None,
        "usage": usage.summary(),
        "model_calls_l1": sum(r.get("model_calls") or 0 for r in l1_rows),
        "evidence_lines": sum(r.get("evidence") or 0 for r in scored),
        "evidence_ungrounded": sum(len(r.get("evidence_ungrounded") or []) for r in scored),
        "forbidden": sum(len(r["forbidden"]) for r in scored),
        "reply_words_mean": round(sum(sum(r["reply_words"]) for r in scored) / max(1, sum(len(r["reply_words"]) for r in scored)), 1),
        "reply_words_max": max((max(r["reply_words"], default=0) for r in scored), default=0),
        "options_mean": round(sum(len(r["actions"]) for r in scored) / max(1, len(scored)), 2),
        "style_issues": sum(len(r["style"]) for r in scored),
        "profiles": "live" if args.live_profiles else "cached",
        "ran_at": db.now_iso(),
    }
    n = len(scored)
    line = f"\ncategory {summary['category_correct']}/{n}  action {summary['action_correct']}/{n}  both {summary['both_correct']}/{n}"
    line += f"  evidence grounded {summary['evidence_lines'] - summary['evidence_ungrounded']}/{summary['evidence_lines']}"
    line += f"\nforbidden {summary['forbidden']}  reply words mean {summary['reply_words_mean']} max {summary['reply_words_max']}  options {summary['options_mean']}  style {summary['style_issues']}  model calls {summary['model_calls_l1']}"
    if args.l2:
        line += f"  level 2 {summary['l2_correct']}/{len(l2_rows)} runs (code checks {summary['l2_code_ok']}/{len(l2_rows)})"
    print(line)
    print(f"usage: {json.dumps(summary['usage'])}")
    args.out.write_text(json.dumps({"summary": summary, "rows": l1_rows, "l2_rows": l2_rows}, indent=2, ensure_ascii=False) + "\n")
    print(f"→ {args.out}  (scratch DB kept: {scratch})")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

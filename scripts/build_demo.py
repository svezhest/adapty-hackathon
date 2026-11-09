"""Build data/demo.sqlite from the written dataset. Replaces the file; the two model stages are paid.

    python scripts/build_demo.py base      --db build.sqlite   # the 100 tickets, no model, no network
    python scripts/build_demo.py bots      --db build.sqlite   # the 800 bot chats and 800 work records, no model
    python scripts/build_demo.py triage    --db build.sqlite   # real Level 1 over the 100 tickets (paid, resumable)
    python scripts/build_demo.py decide    --db build.sqlite   # the 10 people's decisions from data/dataset/decisions.json
    python scripts/build_demo.py insights  --db build.sqlite   # the real analysis over those rows (paid)
    python scripts/build_demo.py publish   --db build.sqlite   # copy to data/demo.sqlite, write the provenance file

Provenance, in data/dataset/provenance.json, one record per ticket and per bot chat, says who produced what:
- what a user says, and what the assistant said before a handoff: the writers (model WRITER_MODEL, data/dataset/writer_brief.md);
- the case behind it: labels of a real complaint and a real case (cards.json, `provenance`);
- the account: synthetic, served by a local stand-in for Adapty, so no Adapty call is made and no real user is touched;
- the triage: the real Level 1 code and model; every call's returned model id, fingerprint, tokens and time is recorded;
- the people's decisions: written by hand into decisions.json, run through the real accept and reject code;
- the bot chats: dialogues from WRITER_MODEL, picked and dated by this script with a fixed seed, never run through a model.

Time is real for the run and made up for the row: a run sets the clock to the ticket's own day, so the model sees the
account as it stood then, and every row is stamped with that day.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime as real_datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from resolve import actions, analytics, config, db, demo_clock, insights_live, llm, triage, workflow  # noqa: E402
from resolve.adapty import AdaptyError  # noqa: E402
from resolve.models import Decision, Rejection  # noqa: E402

DIR = ROOT / "data" / "dataset"
SEED = 20261109
NOW = real_datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
WRITER_MODEL = "claude-sonnet-5-5"
# What the project owner calls the model. The API only accepts `deepseek-flash` and does not confirm this name.
OWNER_MODEL_NAME = "deepseek-v4.1-flash-1017"
BOT_CHATS = 800
WORK_EVENTS = 800
DAYS = 30
BOT_GROWTH = 1.15  # per day
BOT_END = NOW + timedelta(hours=14)  # the newest chat, and with it the demo's "now": the newest ticket is that much older
WORKERS = 5
CLOSERS_REWRITTEN = 307
CALLS_FILE = "triage_calls.json"
STORE_NAME = {"app_store": "App Store", "play_store": "Google Play"}
PLAN_NAME = {"weekly": "weekly plan", "monthly": "monthly plan", "annual": "annual plan", "pro_annual": "Pro annual plan"}
PRICE = {"weekly": "$6.99", "monthly": "$12.99", "annual": "$49.99", "pro_annual": "$69.99"}
PERIOD = {"weekly": 7, "monthly": 30, "annual": 365, "pro_annual": 365}
STEPS_IN_CHAT = {"cant_cancel", "cant_cancel_other_store", "D1", "A5", "A7", "A1", "A8", "A2", "A4"}
RESOLVED_NOTE = {
    "restore_ok": "Restore purchases brought premium back.", "cancel_app_store": "Cancelled in iPhone Settings.", "cancel_play": "Cancelled in Google Play.",
    "renewal_date": "Told the next charge date.", "trial_end_date": "Told when the trial ends.", "already_cancelled_check": "Confirmed the cancellation.",
    "grant_sync_delay": "Added free days while the purchase syncs.", "grant_billing_delay": "Added free days while the store catches up.",
    "update_payment": "Gave the steps to change the card.", "receipt_where": "Showed where the store keeps receipts.", "plan_question": "Explained the plan from the account.",
    "switch_plan": "Explained how to switch plan in the store.",
}
FLOW_WEIGHT = {
    "restore_ok": 12, "cancel_app_store": 10, "cancel_play": 9, "renewal_date": 11, "trial_end_date": 9, "already_cancelled_check": 8,
    "grant_sync_delay": 7, "grant_billing_delay": 5, "update_payment": 8, "receipt_where": 7, "plan_question": 8, "switch_plan": 6,
}
STEP_KIND = {"restore_ok": ("restore", None), "cancel_app_store": ("cancel", "app_store"), "cancel_play": ("cancel", "play_store")}
COME_BACK = {
    "en": [("wait, one more thing, does this work on my ipad too?", "Yes, premium belongs to your account, so it works on every device signed in to it.")],
    "ru": [("а на планшете тоже заработает?", "Да, премиум привязан к аккаунту, так что он работает на всех устройствах, где вы вошли.")],
    "sr": [("a da li radi i na tabletu?", "Da, premijum je vezan za nalog, pa radi na svim uređajima na kojima ste prijavljeni.")],
}


# --- The clock: per thread, so the code under test sees the ticket's own day -------------------------------------------


_clock = threading.local()


class TicketTime(real_datetime):
    """datetime whose now() is the thread's ticket time, when one is set."""

    @classmethod
    def now(cls, tz=None):
        frozen = getattr(_clock, "at", None)
        return frozen if frozen is not None else real_datetime.now(tz)


def at(moment: real_datetime) -> None:
    _clock.at = moment


_real_now_iso = db.now_iso


def install_clock() -> None:
    triage.datetime = TicketTime
    actions.datetime = TicketTime
    db.now_iso = lambda: _clock.at.strftime("%Y-%m-%dT%H:%M:%SZ") if getattr(_clock, "at", None) is not None else _real_now_iso()


def parse(text: str) -> real_datetime:
    return real_datetime.fromisoformat(text.replace("Z", "+00:00"))


class LocalAccounts:
    """Stands in for Adapty with the cards' synthetic accounts. Nothing here reaches the network."""

    def __init__(self, cards: list[dict]):
        self.profiles = {c["customer_user_id"]: c["profile"] for c in cards if c["customer_user_id"] and c["profile"]}

    def get_profile(self, customer_user_id: str) -> dict:
        if customer_user_id not in self.profiles:
            raise AdaptyError(f"profile {customer_user_id} not found", code="not_found")
        return self.profiles[customer_user_id]

    def grant_access_level(self, customer_user_id, access_level, expires_at):
        return {"profile_id": self.profiles[customer_user_id]["profile_id"], "local_stand_in": True}

    def update_profile(self, customer_user_id, attributes):
        return {"local_stand_in": True}


def load() -> tuple[list[dict], dict[str, dict]]:
    cards = json.loads((DIR / "cards.json").read_text())
    tickets = {t["id"]: t for t in json.loads((DIR / "tickets.json").read_text())}
    return cards, tickets


# --- base: tickets ------------------------------------------------------------------------------------------------------


def add_chat_ticket(conn, card, written, ticket_id, rng) -> None:
    end = parse(card["created_at"])
    messages = written["messages"]
    # walking backwards from the handoff: the assistant in seconds, the person in a minute or three
    gaps = []
    for m in messages[1:]:
        gaps.append(rng.randint(2, 9) if m["role"] == "agent" else rng.randint(35, 240))
    start = end - timedelta(seconds=sum(gaps) + 1)
    session = f"chat-{card['id']}"
    at(start)
    db.ensure_conversation(conn, session, card["customer_user_id"])
    moment = start
    tried = []
    users = sum(1 for m in messages if m["role"] == "user")
    steps = card["real_case"] in STEP_KIND or (card["real_case"] in STEPS_IN_CHAT and users >= 2)
    for i, m in enumerate(messages):
        if i:
            moment += timedelta(seconds=gaps[i - 1])
        at(moment)
        if m["role"] == "agent" and i == 1 and steps:
            kind = "restore" if card["real_case"] in ("A5", "A7", "A1", "A8", "A2", "A4") else "cancel"
            args = {"kind": kind, "store": card["store"] if card["store"] in STORE_NAME else "app_store"}
            db.log_event(conn, session, "store_instructions", args, True, None, "steps sent")
            tried.append("store_instructions")
        if m["role"] == "agent" and i == len(messages) - 1:
            at(end)
            transcript = "\n".join(f"{x['role']}: {x['text']}" for x in messages[:i])
            ran = "\n".join(f"- {tool}: ran" for tool in tried)
            text = f"Handoff reason: {written['handoff_reason']}\nSummary: {written['summary']}\n\nConversation:\n{transcript}\n\nTools tried:\n{ran or '- none'}"
            db.insert_feedback(conn, text=text, source="chat_handoff", customer_user_id=card["customer_user_id"], session_id=session, subject=f"Chat: {written['handoff_reason']}", context=card["context"], ticket_id=ticket_id)
            db.set_outcome(conn, session, "handed_off", ticket_id)
            db.log_event(conn, session, "handoff", {"reason": written["handoff_reason"], "summary": written["summary"]}, True, None, {"ticket_id": ticket_id})
        db.append_message(conn, session, m["role"], m["text"])


def add_email_ticket(conn, card, written, ticket_id) -> None:
    at(parse(card["created_at"]))
    db.insert_feedback(conn, text=written["body"], source="email", customer_user_id=card["customer_user_id"], email=card["email"], subject=written["subject"] or "(no subject)", context=card["context"], ticket_id=ticket_id)
    if card["customer_user_id"] and card["email"]:
        with conn:
            conn.execute("INSERT OR REPLACE INTO email_index (email, customer_user_id) VALUES (?, ?)", (card["email"], card["customer_user_id"]))


# --- base: the bot's own work -------------------------------------------------------------------------------------------


def daily_counts(rng) -> list[int]:
    """24-hour windows, oldest first: a rollout that keeps growing, with a weekly rhythm, and exactly BOT_CHATS in all."""
    raw = []
    for i in range(DAYS):
        day = BOT_END - timedelta(days=DAYS - i)
        week = {5: 0.88, 6: 0.84}.get(day.weekday(), 1.0)
        raw.append(BOT_GROWTH ** i * week * rng.uniform(0.97, 1.03))
    scale = BOT_CHATS / sum(raw)
    counts = [round(x * scale) for x in raw]
    counts[-1] += BOT_CHATS - sum(counts)
    return counts


def moment_in_window(rng, window_start: real_datetime) -> real_datetime:
    """Busier in the daytime, yet never empty: the demo's clock can land on any hour."""
    while True:
        moment = window_start + timedelta(seconds=rng.randint(0, 86399))
        if rng.random() < 0.55 + 0.45 * (1 - abs(moment.hour - 14) / 14):
            return moment


def fill(text: str, slots: dict) -> str:
    for key, value in slots.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def add_bot_chats(conn, rng) -> list[dict]:
    library = json.loads((DIR / "bot_library.json").read_text())
    flows = list(FLOW_WEIGHT)
    assert set(library) == set(flows), set(library) ^ set(flows)
    # every dialogue once before any dialogue twice
    decks = {f: rng.sample(range(len(library[f])), len(library[f])) for f in flows}
    used = {f: 0 for f in flows}
    starts: list[real_datetime] = []
    for i, count in enumerate(daily_counts(rng)):
        window = BOT_END - timedelta(days=DAYS - i)
        starts += [moment_in_window(rng, window) for _ in range(count)]
    starts.sort()

    plans = []
    for n, start in enumerate(starts):
        flow = rng.choices(flows, [FLOW_WEIGHT[f] for f in flows])[0]
        index = decks[flow][used[flow] % len(decks[flow])]
        used[flow] += 1
        plans.append({"n": n, "start": start, "flow": flow, "index": index})
    # exactly WORK_EVENTS records of work: the steps and grants each flow needs, then profile checks up to that number
    needed = 0
    for p in plans:
        p["tools"] = []
        if p["flow"] in STEP_KIND:
            p["tools"].append("store_instructions")
        if p["flow"].startswith("grant_"):
            p["blocked"] = rng.random() < 0.12
            p["tools"] += (["grant_blocked"] if p["blocked"] else []) + ["grant_access"]
        needed += len(p["tools"])
    checks = WORK_EVENTS - needed
    assert 0 <= checks <= BOT_CHATS, checks
    for p in rng.sample(plans, checks):
        p["tools"].insert(0, "get_profile")

    records = []
    for p in plans:
        flow, dialogue = p["flow"], library[p["flow"]][p["index"]]
        session = f"bot-{p['n'] + 1:04d}"
        customer = f"lumen_443814_2{p['n']:04d}"
        store = {"cancel_app_store": "app_store", "cancel_play": "play_store"}.get(flow) or rng.choices(["app_store", "play_store"], [6, 4])[0]
        plan = rng.choices(list(PLAN_NAME), [4, 3, 5, 1])[0]
        days = rng.choices([3, 5, 7], [6, 2, 2])[0]
        next_date = p["start"] + timedelta(days=rng.randint(2, min(PERIOD[plan], 27)))
        slots = {"plan": PLAN_NAME[plan], "price": PRICE[plan], "date": next_date.strftime("%b %-d"), "store": STORE_NAME[store], "days": days}
        moment = p["start"]
        at(moment)
        db.ensure_conversation(conn, session, customer)
        messages = dialogue["messages"]
        for i, m in enumerate(messages):
            if i:
                moment += timedelta(seconds=rng.randint(2, 8) if m["role"] == "agent" else rng.randint(20, 200))
            at(moment)
            if m["role"] == "agent" and i == 1:
                for tool in p["tools"]:
                    if tool == "get_profile":
                        db.log_event(conn, session, "get_profile", {}, True, None, {"store": store, "product": f"lumen_{plan}", "active": True})
                    elif tool == "store_instructions":
                        kind, forced = STEP_KIND[flow]
                        db.log_event(conn, session, "store_instructions", {"kind": kind, "store": forced or store}, True, None, "steps sent")
                    elif tool == "grant_blocked":
                        db.log_event(conn, session, "grant_access", {"days": 14, "reason": RESOLVED_NOTE[flow]}, False, "over_max_days", None)
                    elif tool == "grant_access":
                        until = (moment + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
                        result = {"type": "grant_access", "access_level": "premium", "days": days, "expires_at": until, "local_stand_in": True}
                        db.log_event(conn, session, "grant_access", {"days": days, "reason": RESOLVED_NOTE[flow]}, True, None, result)
                        db.record_grant(conn, customer, "premium", days, until, "l2", session, {"local_stand_in": True})
            if i == len(messages) - 1:
                db.log_event(conn, session, "mark_resolved", {"note": RESOLVED_NOTE[flow]}, True, None, "resolved")
                db.set_outcome(conn, session, "resolved")
            db.append_message(conn, session, m["role"], fill(m["text"], slots))
        came_back = rng.random() < 0.03
        if came_back:
            line, answer = rng.choice(COME_BACK[dialogue["lang"]])
            moment += timedelta(minutes=rng.randint(30, 300))
            if moment < BOT_END:
                at(moment)
                db.append_message(conn, session, "user", line)
                moment += timedelta(seconds=rng.randint(2, 7))
                at(moment)
                db.append_message(conn, session, "agent", answer)
            else:
                came_back = False
        records.append({"session_id": session, "flow": flow, "library_index": p["index"], "lang": dialogue["lang"], "came_back": came_back, "slots": slots, "work_events": p["tools"]})
    return records


def stage_base(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    install_clock()
    conn = db.connect(path)
    cards, written = load()
    rng = random.Random(SEED)
    demo = sorted((c for c in cards if c["in_demo"]), key=lambda c: c["created_at"])
    assert len(demo) == 100
    order = {}
    for n, card in enumerate(demo, 1):
        ticket_id = f"f-{n:04d}"
        order[card["id"]] = ticket_id
        if card["source"] == "chat":
            add_chat_ticket(conn, card, written[card["id"]], ticket_id, rng)
        else:
            add_email_ticket(conn, card, written[card["id"]], ticket_id)
    (path.parent / "order.json").write_text(json.dumps({"tickets": order, "bot_chats": []}, ensure_ascii=False, indent=0))
    print(f"{path}: {len(order)} tickets, {conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]} messages")


def stage_bots(path: Path) -> None:
    install_clock()
    conn = db.connect(path)
    built = json.loads((path.parent / "order.json").read_text())
    with conn:  # a rerun replaces its own rows
        for table in ("messages", "l2_events"):
            conn.execute(f"DELETE FROM {table} WHERE session_id LIKE 'bot-%'")
        conn.execute("DELETE FROM conversations WHERE session_id LIKE 'bot-%'")
        conn.execute("DELETE FROM grants WHERE source = 'l2'")
    built["bot_chats"] = add_bot_chats(conn, random.Random(SEED + 1))
    (path.parent / "order.json").write_text(json.dumps(built, ensure_ascii=False, indent=0))
    resolved = conn.execute("SELECT COUNT(*) FROM conversations WHERE outcome = 'resolved'").fetchone()[0]
    print(f"{path}: {resolved} resolved chats, {conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]} messages, {[tuple(r) for r in conn.execute('SELECT tool, COUNT(*), SUM(allowed) FROM l2_events GROUP BY tool')]}, {tuple(conn.execute('SELECT COUNT(*), SUM(days) FROM grants').fetchone())}")


# --- triage: real model, every call recorded ----------------------------------------------------------------------------


class Recorded:
    """Wraps the real client's completions call: what the API returned about itself, per ticket."""

    def __init__(self, inner, calls: dict, lock: threading.Lock):
        self.inner, self.calls, self.lock = inner, calls, lock
        original = inner.client.chat.completions.create

        def create(**kwargs):
            started = time.time()
            response = original(**kwargs)
            usage = response.usage
            details = getattr(usage, "prompt_tokens_details", None)
            record = {
                "requested_model": kwargs.get("model"), "response_model": response.model, "system_fingerprint": response.system_fingerprint, "response_id": response.id,
                "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "cached_tokens": getattr(details, "cached_tokens", None),
                "latency_s": round(time.time() - started, 2), "run_at": real_datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            with self.lock:
                self.calls.setdefault(getattr(_clock, "ticket", "?"), []).append(record)
            return response

        inner.client.chat.completions.create = create

    def __getattr__(self, name):
        return getattr(self.inner, name)


def calls_path(path: Path) -> Path:
    return path.parent / CALLS_FILE


def load_calls(path: Path) -> dict:
    file = calls_path(path)
    return json.loads(file.read_text()) if file.exists() else {}


def forget_triage(path: Path, ticket_ids: list[str]) -> None:
    """Run these again: their earlier calls stay on record, marked superseded, with the language they chose."""
    conn = db.connect(path)
    calls = load_calls(path)
    for ticket_id in ticket_ids:
        before = db.get_ticket(conn, ticket_id)["triage"] or {}
        for call in calls.get(ticket_id, []):
            call["superseded"] = True
            call.setdefault("superseded_user_language", before.get("user_language"))
        with conn:
            conn.execute("UPDATE tickets SET triage_json = NULL, triage_at = NULL, triage_error = NULL, model_calls = 0, profile_json = NULL, profile_fetched_at = NULL, status = 'new' WHERE id = ?", (ticket_id,))
    calls_path(path).write_text(json.dumps(calls, indent=0))


def stage_triage(path: Path, limit: int | None, workers: int) -> None:
    install_clock()
    cards, _ = load()
    order = json.loads((path.parent / "order.json").read_text())["tickets"]
    by_ticket = {order[c["id"]]: c for c in cards if c["id"] in order}
    accounts = LocalAccounts(cards)
    calls = load_calls(path)
    lock = threading.Lock()
    client = Recorded(llm.make_llm(), calls, lock)
    conn = db.connect(path)
    todo = [t["id"] for t in db.list_tickets(conn) if not t["triage"] and t["status"] in workflow.OPEN_STATUSES]
    todo.sort()
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} tickets, model {client.inner.model}, {workers} at a time", flush=True)

    def one(ticket_id: str) -> str:
        card = by_ticket[ticket_id]
        local = db.connect(path)
        _clock.ticket = ticket_id
        at(parse(card["created_at"]) + timedelta(seconds=random.Random(ticket_id).randint(6, 40)))
        services = workflow.Services(conn=local, demo=False, adapty=accounts, llm=client)
        workflow.open_ticket(services, ticket_id)
        result = workflow.propose(services, ticket_id)
        tri = result["triage"]
        if tri:
            return f"{ticket_id} {card['lang']} {tri['category']:26s} {tri['options'][0]['action']:18s} {result['model_calls']} calls"
        return f"{ticket_id} {card['lang']} ERROR {result['triage_error']}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, t): t for t in todo}
        for future in as_completed(futures):
            try:
                print(future.result(), flush=True)
            except Exception as exc:  # noqa: BLE001 - one ticket must not stop a paid run
                print(f"{futures[future]} CRASH {exc.__class__.__name__}: {str(exc)[:200]}", flush=True)
            with lock:
                calls_path(path).write_text(json.dumps(calls, indent=0))
    total = [c for rows in calls.values() for c in rows]
    print(f"\n{len(total)} model calls, {sum(c['prompt_tokens'] for c in total)} prompt tokens, {sum(c['completion_tokens'] for c in total)} completion tokens")


# --- decide: the ten closed tickets -------------------------------------------------------------------------------------


def stage_decide(path: Path) -> None:
    install_clock()
    cards, _ = load()
    accounts = LocalAccounts(cards)
    conn = db.connect(path)
    services = workflow.Services(conn=conn, demo=False, adapty=accounts, llm=None)
    for item in json.loads((DIR / "decisions.json").read_text()):
        ticket = db.get_ticket(conn, item["ticket_id"])
        at(parse(ticket["created_at"]) + timedelta(minutes=item["after_minutes"]))
        if item["kind"] == "reject":
            workflow.reject(services, item["ticket_id"], Rejection(reply=item.get("reply"), note=item.get("note")))
        else:
            option = (ticket["triage"] or {})["options"][item["option"]]
            edits = {"reply": option["reply"].rstrip() + " " + item["edit_append"]} if item.get("edit_append") else {}
            workflow.accept(services, item["ticket_id"], Decision(option=item["option"], edits=edits))
        after = db.get_ticket(conn, item["ticket_id"])
        print(item["ticket_id"], item["kind"], after["status"], after.get("action_error"))


# --- insights: the real analysis over those rows --------------------------------------------------------------------------


def stage_insights(path: Path) -> None:
    install_clock()
    cards, _ = load()
    calls = load_calls(path)
    client = Recorded(llm.make_llm(), calls, threading.Lock())
    conn = db.connect(path)
    _clock.ticket = "insights"
    at(NOW)
    services = workflow.Services(conn=conn, demo=False, adapty=LocalAccounts(cards), llm=client)
    live = insights_live.Live(services)
    live.auto_enabled = False
    live.run_manual()
    print(json.dumps(live.run, ensure_ascii=False))
    stored = db.get_insights(conn)
    print(json.dumps({k: stored.get(k) for k in ("headline", "hypothesis", "control_flow", "variant_flow")}, ensure_ascii=False, indent=1) if stored else "no insight stored")
    calls_path(path).write_text(json.dumps(calls, indent=0))


# --- publish -------------------------------------------------------------------------------------------------------------


def provenance(path: Path) -> dict:
    cards, written = load()
    built = json.loads((path.parent / "order.json").read_text())
    order = built["tickets"]
    calls = load_calls(path)
    decisions = {d["ticket_id"]: d for d in json.loads((DIR / "decisions.json").read_text())}
    edits = json.loads((DIR / "edits.json").read_text())
    edited = {e["id"] for e in edits["edits"]}
    brief = hashlib.sha256((DIR / "writer_brief.md").read_bytes()).hexdigest()[:16]
    tickets = []
    for card in cards:
        ticket_id = order.get(card["id"])
        record = {
            "card": card["id"], "ticket": ticket_id, "lang": card["lang"], "source": card["source"], "in_demo": card["in_demo"],
            "real_case": card["real_case"], "pain": card["real_labels"]["pain"], **card["provenance"],
            "written_by": {"model": WRITER_MODEL, "batch": card["batch"], "brief_sha256_16": brief, "voice": written[card["id"]].get("voice")},
            "edited_after_writing_by": edits["edited_by"] if card["id"] in edited else None,
        }
        if ticket_id:
            rows = calls.get(ticket_id, [])
            record["triage"] = {
                "code": "resolve/triage.py", "owner_model_name": OWNER_MODEL_NAME, "calls": rows,
                "response_models": sorted({r["response_model"] for r in rows}), "fingerprints": sorted({r["system_fingerprint"] or "" for r in rows}),
            }
            d = decisions.get(ticket_id)
            record["person"] = None if not d else {"by": "a person's decision simulated: chosen and worded by claude-opus-5-5, run through resolve/workflow.py", "kind": d["kind"], "edited": bool(d.get("edit_append"))}
        tickets.append(record)
    return {
        "models": {
            "writers": WRITER_MODEL, "triage_requested": "deepseek-flash", "triage_owner_name": OWNER_MODEL_NAME,
            "note": "The API accepts `deepseek-flash` and rejects `deepseek-v4.1-flash-1017` by name. Each call records the id and fingerprint the API returned. The owner's name is theirs; the API does not confirm it.",
        },
        "insights_calls": calls.get("insights", []),
        "tickets": tickets,
        "bot_chats": {"written_by": WRITER_MODEL, "closing_lines_of_two_words_or_fewer_rewritten_by": "claude-opus-5-5 (%d dialogues)" % CLOSERS_REWRITTEN, "assembled_by": "scripts/build_demo.py, fixed seed; never run through a model", "accounts": "invented", "records": built["bot_chats"]},
    }


def stage_publish(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    target = config.DEMO_DB_PATH
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    shutil.copyfile(path, target)
    # The chats run past the newest ticket, and the demo's clock only moves forward: the file has to end in the past.
    conn = db.connect(target)
    demo_clock.shift_by(conn, NOW - analytics.last_event(conn))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("VACUUM")
    counts = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM tickets GROUP BY status")}
    conn.close()
    (DIR / "provenance.json").write_text(json.dumps(provenance(path), ensure_ascii=False, indent=1) + "\n")
    print(f"{target}: {counts}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("base", "bots", "triage", "decide", "insights", "publish"))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--again", default="", help="triage: comma-separated tickets to run again")
    args = parser.parse_args()
    if args.stage == "base":
        stage_base(args.db)
    elif args.stage == "bots":
        stage_bots(args.db)
    elif args.stage == "triage":
        if args.again:
            forget_triage(args.db, args.again.split(","))
        stage_triage(args.db, args.limit, args.workers)
    elif args.stage == "decide":
        stage_decide(args.db)
    elif args.stage == "insights":
        stage_insights(args.db)
    else:
        stage_publish(args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())

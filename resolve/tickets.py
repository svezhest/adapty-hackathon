"""Tickets: the fixed enums, the ticket record and the synthetic ticket generator.

Tickets are synthetic. The generator reads the organisers' offline dataset (first 5,000 users, the
ones that exist as live Adapty profiles), picks users whose transaction history motivates a
complaint (a trial that converted, a billing issue, a refund, a lifetime purchase, ...) and writes
the complaint in the App Store review register. Dates in the text are shifted to the live
profile's clock (`LIVE_SHIFT`), so what the ticket says agrees with what the agent sees in Adapty.
"""

from __future__ import annotations

import csv
import gzip
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

CATEGORIES = (
    "charged_after_trial",
    "no_access_after_purchase",
    "billing_issue",
    "refund_request",
    "cancel_how",
    "price_complaint",
    "other",
)
# One line per category: the Level 1 prompt shows them to label tickets, the insights facts to read the counts.
CATEGORY_MEANING = {
    "charged_after_trial": "the user did not expect the charge when a free trial converted.",
    "no_access_after_purchase": "the user paid (or says so) and premium features are locked.",
    "billing_issue": "the store could not charge the user (billing issue, grace period or lapsed) and the user lost access.",
    "refund_request": "the user wants money back.",
    "cancel_how": "the user asks how to cancel or asks you to cancel.",
    "price_complaint": "the user objects to the price or how it was shown.",
    "other": "anything else (bugs, feature requests, questions).",
}
ACTIONS = ("grant_access", "explain", "escalate_refund", "no_action")
STATUSES = ("new", "triaged", "proposed", "approved", "done", "dismissed")

# Live profiles are the offline rows replayed with every timestamp moved forward by this constant
# (the seeder ends the history on seed day). Verified on three profiles: 00549, 03073, 02138.
LIVE_SHIFT = timedelta(seconds=325_859)
CUSTOMER_PREFIX = "lumen_443814_"
LIVE_USERS = 5_000

# How many generated tickets per rule. The three hand-written demo tickets come on top and take
# one slot each from charged_after_trial, no_access_after_purchase and billing_issue.
MIX = {
    "charged_after_trial": 22,
    "no_access_after_purchase": 8,
    "billing_issue": 9,
    "refund_request": 8,
    "cancel_how": 6,
    "price_complaint": 4,
    "other": 3,
}
assert sum(MIX.values()) == 60

PRODUCT_NAMES = {
    "weekly": "weekly plan",
    "monthly": "monthly plan",
    "annual": "annual plan",
    "annual_trial": "annual plan",
    "pro_annual": "Pro annual plan",
    "lifetime": "lifetime plan",
}
TRIAL_DAYS = {"weekly": 3, "annual_trial": 7}
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "BRL": "R$", "INR": "₹", "JPY": "¥", "MXN": "MX$"}


@dataclass
class Ticket:
    id: str
    customer_user_id: str
    subject: str
    body: str
    created_at: str  # ISO 8601, UTC
    demo: bool
    rule: str  # the generator rule (one of CATEGORIES); for the eval and sanity checks, never shown to the agent
    context: dict = field(default_factory=dict)  # offline CSV columns: placement, flow, ab_test, country, platform, acquisition_channel

    def as_dict(self) -> dict:
        return asdict(self)


CONTEXT_COLUMNS = ("placement", "flow", "ab_test", "country", "platform", "acquisition_channel")


def user_context(user: dict) -> dict:
    return {column: user[column] for column in CONTEXT_COLUMNS}


def customer_user_id(offline_user_id: str) -> str:
    return CUSTOMER_PREFIX + offline_user_id[1:]


def offline_user_id(customer_id: str) -> str:
    return "u" + customer_id.rsplit("_", 1)[1]


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def live(value: str) -> datetime | None:
    parsed = parse_ts(value)
    return parsed + LIVE_SHIFT if parsed else None


def money(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency)
    if currency == "JPY":
        return f"¥{amount:,.0f}"
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def day(value: datetime) -> str:
    return value.strftime("%b %-d")


def load_users(path: Path) -> dict[str, dict]:
    with gzip.open(path, "rt", newline="") as handle:
        rows = (row for row in csv.DictReader(handle) if int(row["user_id"][1:]) < LIVE_USERS)
        return {row["user_id"]: row for row in rows}


def load_transactions(path: Path, users: dict[str, dict]) -> dict[str, list[dict]]:
    by_user: dict[str, list[dict]] = {}
    with gzip.open(path, "rt", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["user_id"] in users:
                by_user.setdefault(row["user_id"], []).append(row)
    for rows in by_user.values():
        rows.sort(key=lambda r: int(r["seq"]))
    return by_user


# --- Candidate events per rule -----------------------------------------------------------------
# Each candidate is a dict with the slots the templates need plus `event_at` (live clock).


def candidates(rule: str, users: dict[str, dict], transactions: dict[str, list[dict]], now: datetime) -> list[dict]:
    found: list[dict] = []
    for user_id, user in users.items():
        rows = transactions.get(user_id, [])
        event = _event(rule, user, rows, now)
        if event:
            event["user_id"] = user_id
            event["user"] = user
            found.append(event)
    return found


def _price(row: dict) -> str:
    return money(float(row["price_local"]), row["currency"])


def _store(user: dict) -> str:
    return "Google Play" if user["platform"] == "Android" else "the App Store"


def _event(rule: str, user: dict, rows: list[dict], now: datetime) -> dict | None:
    last = rows[-1] if rows else None
    if rule == "charged_after_trial":
        trial = next((r for r in rows if r["offer"] == "free_trial"), None)
        if not trial:
            return None
        paid = next((r for r in rows if r["product"] == trial["product"] and float(r["price_usd"]) > 0), None)
        if not paid or paid["refunded_at"] or paid["billing_issue_detected_at"]:
            return None
        return {
            "event_at": live(paid["purchased_at"]),
            "price": _price(paid),
            "product": PRODUCT_NAMES[paid["product"]],
            "trial_days": TRIAL_DAYS.get(trial["product"], 7),
            "trial_start": day(live(trial["purchased_at"])),
            "weight": 3 if (paid["product"] == "weekly" and user["flow"] == "onb_weekly" and user["acquisition_channel"] in ("meta_ads", "tiktok_ads")) else 1,
        }
    if rule == "no_access_after_purchase":
        if not last or float(last["price_usd"]) <= 0 or last["refunded_at"] or last["billing_issue_detected_at"]:
            return None
        expires = parse_ts(last["expires_at"])
        if expires is not None and expires + LIVE_SHIFT < now:
            return None
        return {"event_at": live(last["purchased_at"]), "price": _price(last), "product": PRODUCT_NAMES[last["product"]], "store": _store(user)}
    if rule == "billing_issue":
        row = next((r for r in rows if r["billing_issue_detected_at"]), None)
        if not row:
            return None
        return {"event_at": live(row["billing_issue_detected_at"]), "product": PRODUCT_NAMES[row["product"]], "store": _store(user)}
    if rule == "refund_request":
        paid = [r for r in rows if float(r["price_usd"]) > 0 and r["product"] in ("annual", "annual_trial", "pro_annual", "lifetime", "monthly")]
        if not paid:
            return None
        row = paid[-1]
        return {"event_at": live(row["purchased_at"]), "price": _price(row), "product": PRODUCT_NAMES[row["product"]], "store": _store(user), "refunded": bool(row["refunded_at"])}
    if rule == "cancel_how":
        if not last or last["renew_status"].lower() != "true" or last["purchase_type"] != "subscription":
            return None
        expires = parse_ts(last["expires_at"])
        if expires is None or expires + LIVE_SHIFT < now:
            return None
        return {"event_at": live(last["purchased_at"]), "product": PRODUCT_NAMES[last["product"]], "store": _store(user), "renews": day(expires + LIVE_SHIFT)}
    if rule == "price_complaint":
        if not last or last["product"] != "weekly" or float(last["price_usd"]) <= 0:
            return None
        return {"event_at": live(last["purchased_at"]), "price": _price(last)}
    if rule == "other":
        if rows:
            return None
        return {"event_at": parse_ts(user["install_at"]) + LIVE_SHIFT, "goal": user["onboarding_goal"].replace("_", " "), "version": user["app_version"]}
    raise ValueError(rule)


# --- Wording -------------------------------------------------------------------------------------

TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "charged_after_trial": [
        ("Charged after free trial", "I started the {trial_days}-day free trial on {trial_start} and on {date} you charged me {price}. Nowhere did it say the trial turns into a {product}. I want this reversed."),
        ("SCAM - trial was not free", "Downloaded this for the \"free\" trial and got hit with {price} {ago}. I used the app twice. This is a scam, cancel it and give me my money back."),
        ("Unexpected charge {price}", "Why was I charged {price} on {date}? I only signed up for the trial. I did not agree to a {product}. Please refund."),
        ("you charged me without asking", "trial ended and boom {price} gone from my account on {date}. no reminder, no email, nothing. not ok"),
        ("Trial converted without warning", "The paywall said \"try free\". {trial_days} days later I'm paying {price}. I would have cancelled if I had known the date. Can you at least refund this one?"),
        ("Didn't know the trial was only {trial_days} days", "I thought the trial was a week. Charged {price} on {date}. Honestly I like the app but I feel tricked. What can you do?"),
    ],
    "no_access_after_purchase": [
        ("Paid but still locked", "I paid {price} for the {product} on {date} and the app still shows me the subscribe screen. Restore purchases does nothing. I have the receipt from {store}."),
        ("Premium not working after purchase", "Bought the {product} ({price}) {ago}. Every premium workout still says upgrade. Fix this or refund me."),
        ("No access after paying {price}", "Receipt says paid on {date}. App says free. I reinstalled, logged out, nothing. What now?"),
        ("Charged but features locked", "{store} charged me {price} on {date} and FitBuddy still treats me as a free user. This is unacceptable, I needed my training plan today."),
    ],
    "billing_issue": [
        ("Payment problem message", "Since {date} the app says there is a problem with my payment and I lost access to premium. My card works everywhere else. What is going on?"),
        ("Lost premium - billing error", "Got a notification about a billing issue on {date} and now the {product} is gone. I didn't change anything. How do I get it back?"),
        ("Subscription stopped working", "My {product} just stopped {ago}, {store} shows some payment error. I don't want to lose my workout history. Help."),
        ("billing issue??", "app says 'billing issue detected' since {date}. i have money on the card. fix your app"),
    ],
    "refund_request": [
        ("Refund request", "I was charged {price} for the {product} on {date}. I don't use the app anymore and want a refund. Please process it."),
        ("Want my money back", "{price} for this?? Charged on {date}. I want a full refund, I am not using it."),
        ("Refund for accidental purchase", "My kid bought the {product} ({price}) on {date} by accident. Please refund, I already removed the app."),
        ("Where is my refund", "I requested a refund through {store} for the {price} charge on {date}. Still nothing. Can you check on your side?"),
        ("Refund please - not what I expected", "Paid {price} on {date} expecting the AI coach to build a plan that fits me. The plans are bad. I'd like a refund."),
    ],
    "cancel_how": [
        ("How do I cancel?", "I can't find where to cancel the {product}. It renews on {renews} and I don't want to be charged again. Please cancel it for me."),
        ("Cancel my subscription", "Please cancel my {product} before {renews}. There is no cancel button anywhere in the app."),
        ("Stop renewal", "I don't need the {product} anymore. I deleted the app, does that cancel it? I don't want another charge on {renews}."),
    ],
    "price_complaint": [
        ("{price} a WEEK?", "{price} per week for a fitness app is insane. I didn't notice it was weekly until the second charge. Do you have a cheaper plan?"),
        ("Pricing is misleading", "The price on the paywall looked like a monthly price. It's {price} every week. That's more than Netflix. Not happy."),
        ("Too expensive", "Charged {price} again on {date}. For what, three workouts? Lower the price or I'm out."),
    ],
    "other": [
        ("App crashes when saving a workout", "FitBuddy {version} crashes every time I save a finished workout. Android, latest update."),
        ("Can I use it on two phones?", "Can I use my account on my tablet too? Didn't find it in the settings."),
        ("Feature request: workout queue", "Would love to queue several workouts in a row with the same rest timer."),
    ],
}


def write_ticket(rule: str, event: dict, rng: random.Random, now: datetime) -> Ticket:
    subject, body = rng.choice(TEMPLATES[rule])
    latest = now - timedelta(minutes=5)
    window_hours = min(96.0, max(0.5, (latest - event["event_at"]).total_seconds() / 3600))
    created = event["event_at"] + timedelta(hours=rng.uniform(min(3.0, window_hours), window_hours))
    days_ago = (created - event["event_at"]).days
    slots = dict(event, date=day(event["event_at"]), ago="yesterday" if days_ago <= 1 else f"{days_ago} days ago")
    return Ticket(
        id="",
        customer_user_id=customer_user_id(event["user_id"]),
        subject=subject.format(**slots),
        body=body.format(**slots),
        created_at=created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        demo=False,
        rule=rule,
    )


def generate(
    users_csv: Path,
    transactions_csv: Path,
    demo: list[Ticket],
    now: datetime | None = None,
    seed: int = 7,
    recent_days: int = 28,
) -> list[Ticket]:
    """60 tickets: the hand-written `demo` ones plus generated ones to fill `MIX`, one user each."""
    now = now or datetime.now(timezone.utc)
    rng = random.Random(seed)
    users = load_users(users_csv)
    transactions = load_transactions(transactions_csv, users)
    used = {t.customer_user_id for t in demo}
    tickets = list(demo)
    for rule, total in MIX.items():
        wanted = total - sum(1 for t in demo if t.rule == rule)
        pool = [c for c in candidates(rule, users, transactions, now) if customer_user_id(c["user_id"]) not in used]
        recent = [c for c in pool if c["event_at"] >= now - timedelta(days=recent_days)]
        pool = recent if len(recent) >= wanted else pool
        if len(pool) < wanted:
            raise ValueError(f"{rule}: only {len(pool)} candidates for {wanted} tickets")
        for event in _weighted_sample(pool, wanted, rng):
            used.add(customer_user_id(event["user_id"]))
            tickets.append(write_ticket(rule, event, rng, now))
    tickets.sort(key=lambda t: t.created_at)
    for index, ticket in enumerate((t for t in tickets if not t.demo), 1):
        ticket.id = f"t-{index:03d}"
    for ticket in tickets:
        ticket.context = user_context(users[offline_user_id(ticket.customer_user_id)])
    return tickets


def _weighted_sample(pool: list[dict], count: int, rng: random.Random) -> list[dict]:
    remaining = list(pool)
    picked = []
    while len(picked) < count:
        choice = rng.choices(remaining, weights=[c.get("weight", 1) for c in remaining])[0]
        remaining.remove(choice)
        picked.append(choice)
    return picked

TOUGH_AT_MOST = 0.7  # the best option's confidence at or under which Incoming says the agent isn't sure of any response
TOUGH_LINE = "A tough one, your call"
NO_REPLY_LINE = "You've closed it without a reply"
CANCELLED_HANDOFF = "user cancelled the handoff"  # the start of the note on a ticket the user withdrew in chat

CATEGORY_LABELS = {
    "charged_after_trial": "Charged after trial",
    "no_access_after_purchase": "No access after purchase",
    "billing_issue": "Billing issue",
    "refund_request": "Refund request",
    "cancel_how": "How to cancel",
    "price_complaint": "Price complaint",
    "other": "Other",
}


# --- The title a person reads (docs/agents.md, "Ticket title") ---------------------------------------

TITLE_MAX = 90
CHAT_REASON_TITLE = {
    "refund": "Refund request from chat",
    "chargeback": "Chargeback mentioned in chat",
    "threat": "Escalation from chat",
    "asks_for_human": "Asked for a person in chat",
    "profile_mismatch": "Account does not match what the user says",
    "repeat_after_grant": "Complaint again after free days",
    "other": "Handed off from chat",
}


def short_title(summary: str) -> str:
    """The triage summary as a headline: without "The user", its first clause, at most TITLE_MAX characters."""
    text = re.sub(r"^(?:The user|User) (?=[a-z])", "", " ".join(summary.split())).rstrip(".")
    text = text[:1].upper() + text[1:]
    for m in re.finditer(r",(?!\s*\d)| — | – |; ", text):
        head = text[: m.start()]
        dangling = re.search(r"\b(?:and|or|but|then|so|because|while|when|after|before|that|who)$", head)
        if 30 <= len(head) <= TITLE_MAX and head.count("(") == head.count(")") and not dangling:
            return head
    if len(text) <= TITLE_MAX:
        return text
    return text[:TITLE_MAX].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def ticket_title(subject: str, body: str, summary: str | None) -> str:
    """A subject a person wrote stays. A machine subject ("Chat: refund") or a cut of the body gives way to the
    triage summary, else to a readable line for the handoff reason."""
    chat = re.fullmatch(r"Chat: (\w+)", subject or "")
    if not chat and subject and subject != (body or "")[:60]:
        return subject
    if summary and summary.strip():
        return short_title(summary)
    if chat:
        return CHAT_REASON_TITLE.get(chat.group(1), CHAT_REASON_TITLE["other"])
    return subject or "(no text)"


# --- The line under the title in Incoming (docs/agents.md, "Ticket summary") ---------------------------

def best_confidence(view: dict, options: list[dict]) -> float | None:
    """How sure the agent is of its strongest option; the triage's own number when options carry none."""
    scores = [o["confidence"] for o in options if o.get("confidence") is not None]
    return max(scores) if scores else view.get("confidence")


def action_state(ticket: dict, status: str, view: dict | None, options: list[dict]) -> tuple[str | None, str | None]:
    """(kind, line) under the title in Incoming, from what the triage really produced."""
    if status in ("done", "dismissed"):
        line = closed_line(ticket, options)
        return ("finished" if line.startswith("You've") and line != NO_REPLY_LINE else "closed"), line
    if options:
        best = best_confidence(view, options)
        if best is not None and best <= TOUGH_AT_MOST:
            return "tough", TOUGH_LINE
        count = len(options)
        return "generated", "We've generated 1 response, take a look" if count == 1 else f"We've generated {count} responses, pick one"
    if ticket.get("triage_error"):
        return "failed", "Triage failed"
    return (None, None) if view else ("generating", "Still generating, please wait")


def action_line(ticket: dict, status: str, view: dict | None, options: list[dict]) -> str | None:
    return action_state(ticket, status, view, options)[1]


def action_accent(line: str | None) -> str | None:
    """The part of the line that names what the person does: the screen draws it in the accent colour."""
    if not line:
        return None
    if line.endswith("and edited it"):
        return "edited"
    if line.endswith("an option"):
        return "option"
    if line.endswith("your own reply"):
        return line
    if line.endswith("pick one"):
        return "pick"
    if line.endswith("take a look"):
        return "take a look"
    if line == NO_REPLY_LINE:
        return "closed"
    return "your call" if line == TOUGH_LINE else None


def closed_line(ticket: dict, options: list[dict]) -> str:
    """A closed ticket's line, from its stored decision. "You've" only when a person decided."""
    decision = ticket.get("decision") or {}
    if decision.get("rejected"):
        if str(decision.get("note") or "").startswith(CANCELLED_HANDOFF):
            return "The user withdrew the request"
        return "You've written your own reply" if decision.get("reply") else NO_REPLY_LINE
    index = decision.get("option")
    if index is None:
        return "Closed without a reply" if ticket.get("status") == "dismissed" else "Closed"
    final = (ticket.get("action") or {}).get("final") or (options[index] if index < len(options) else {})
    if final.get("action") == "no_reply":
        return NO_REPLY_LINE
    edited = any(v is not None for v in (decision.get("edits") or {}).values())
    return "You've chosen an option and edited it" if edited else "You've chosen an option"

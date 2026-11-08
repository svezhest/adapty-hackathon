"""Add the demo feed's long tail to data/demo.sqlite: about 400 tickets assembled from templates by code.

    python scripts/snapshot.py          # the 31 real triaged tickets from the live DB (this replaces the file)
    python scripts/generate_feed.py     # then this, on top of the snapshot; run it again any time, same result

No model call, no network, no key. The same seed gives the same rows, and a rerun replaces its own rows only.

What it does:
- Users and profiles: the seeded demo users cached in data/eval-base.sqlite. Users who also have a real ticket in the
  snapshot are left out, so the invented history never contradicts a real one (that leaves 37). About 34 tickets come
  from made-up @example.com addresses that match no account.
- Words: scripts/feed_scenes.py, invented by hand per category and in six more languages. App Store reviews and
  docs/market-research are not used. Regional prices come from the text cached with the same user; where it names no
  price, from the REGIONAL table below.
- Dates: every ticket is older than every real one (at most 40 days before them). A profile cached on Nov 7 would show
  purchases later than a ticket from September, so each user's profile is moved back by a whole number of days, and a
  plan that renews is renewed up to the ticket's day. All text is then written from that profile, so plan, store,
  amount and dates agree on the ticket page. No Adapty call is made and no `grants` row is written.
- `rule` = generated on every row: resolve/analytics.py, /api/insights and /api/users leave these out, and the newest
  thing they touch is older than the snapshot's last event, so they don't change where the demo's clock shift lands.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import re
import sqlite3
import zlib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resolve import config  # noqa: E402
from resolve.models import TriageTicket  # noqa: E402
from resolve.tickets import GENERATED, PRODUCT_NAMES  # noqa: E402
from scripts.feed_scenes import (  # noqa: E402
    ACKS, CAUSES, LOCAL_SCENES, NAMES, OWN_REPLIES, REPLIES_EN, REPLIES_LOCAL, SCENES, SUMMARIES, UNKNOWN_SCENES,
)

SEED = 20261108
EVAL_BASE = config.ROOT / "data" / "eval-base.sqlite"
WINDOW_START = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)  # the oldest real ticket is Oct 13, 11:54
KNOWN_TICKETS = 366
UNKNOWN_TICKETS = 34
CLOSED_SHARE = 0.15

REGIONAL = {
    ("US", "weekly"): "$6.99", ("US", "annual"): "$49.99", ("FR", "weekly"): "€6.99", ("MX", "weekly"): "MX$99.99",
    ("GB", "annual"): "£39.99", ("PL", "monthly"): "35.99 PLN", ("TR", "annual"): "399.99 TRY",
}
LANGUAGE_BY_COUNTRY = {"DE": "German", "MX": "Spanish", "BR": "Portuguese", "RS": "Serbian (Latin)", "FR": "French", "PL": "Russian", "SE": "Russian", "TR": "Russian"}
LANGUAGE_BY_USER = {"00936": "Spanish", "02138": "Spanish", "00990": "French"}
MONEY = re.compile(r"(?:CA\$|A\$|R\$|MX\$|[$€£¥₹])\s?[\d,]+(?:\.\d\d)?|[\d.,]+ (?:PLN|SEK|TRY)")
PERIOD_DAYS = {"weekly": 7, "monthly": 30, "annual": 365}
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS = {
    "German": ("Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."),
    "Spanish": ("ene.", "feb.", "mar.", "abr.", "may.", "jun.", "jul.", "ago.", "sep.", "oct.", "nov.", "dic."),
    "French": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
    "Serbian (Latin)": ("januara", "februara", "marta", "aprila", "maja", "juna", "jula", "avgusta", "septembra", "oktobra", "novembra", "decembra"),
    "Russian": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"),
}
STAMP_FIELDS = ("purchased_at", "originally_purchased_at", "expires_at", "renewal_cancelled_at", "billing_issue_detected_at", "starts_at")
PATHS = {
    "app_store": {
        "store": "the App Store", "name": "App Store",
        "cancel": "Settings → your name → Subscriptions → FitBuddy → Cancel Subscription.",
        "refund": "Go to reportaproblem.apple.com, pick the FitBuddy purchase and choose Request a refund.",
        "cancel_url": "apps.apple.com/account/subscriptions", "refund_url": "reportaproblem.apple.com",
    },
    "play_store": {
        "store": "Google Play", "name": "Google Play",
        "cancel": "Play Store → your profile picture → Payments & subscriptions → Subscriptions → FitBuddy → Cancel subscription.",
        "refund": "Open play.google.com/store/account/orderhistory, find FitBuddy and choose Request a refund.",
        "cancel_url": "play.google.com/store/account/subscriptions", "refund_url": "play.google.com/store/account/orderhistory",
    },
}


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def day_en(when: datetime, ref: datetime) -> str:
    text = f"{MONTHS_EN[when.month - 1]} {when.day}"
    return f"{text}, {when.year}" if when.year != ref.year else text


def day_local(when: datetime, ref: datetime, language: str) -> str:
    year = f" {when.year}" if when.year != ref.year else ""
    if language == "Portuguese":
        return f"{when.day:02d}/{when.month:02d}{year}"
    month = MONTHS[language][when.month - 1]
    if language in ("German", "Serbian (Latin)"):
        return f"{when.day}. {month}{year}"
    if language == "Spanish":
        return f"{when.day} de {month}{year}"
    return f"{when.day} {month}{year}"


# --- The user: their profile, moved back in time -------------------------------------------------------


@dataclass
class User:
    uid: str
    tail: str
    country: str
    context: dict
    profile: dict  # as cached in eval-base
    price: str | None
    kind: str | None  # weekly / monthly / annual; None = no purchases
    plan: str | None
    store: str | None
    trial_days: int
    shift: timedelta
    start: datetime  # the earliest day this user can write in
    end: datetime
    unit_usd: float
    charges: int
    language: str | None
    name: str

    @property
    def sub(self) -> dict | None:
        return (self.profile.get("subscriptions") or [None])[0]


def load_users(demo: sqlite3.Connection, eval_base: sqlite3.Connection, rng: random.Random) -> list[User]:
    real = {r[0] for r in demo.execute("SELECT DISTINCT customer_user_id FROM tickets WHERE rule != ?", (GENERATED,))}
    users = []
    for row in eval_base.execute("SELECT customer_user_id, subject, body, profile_json, context_json FROM tickets ORDER BY customer_user_id"):
        uid, subject, body, profile_json, context_json = row
        if uid in real:
            continue
        profile, context = json.loads(profile_json), json.loads(context_json)
        levels = profile.get("access_levels") or []
        sub = (profile.get("subscriptions") or [None])[0]
        if any(level["store"] == "adapty" for level in levels):
            continue  # already carries a live grant: its dates would need the grant's history too
        if sub and sub.get("offer") and not sub.get("billing_issue_detected_at"):
            continue  # still inside a trial: nothing in the window would hold for long
        users.append(_user(uid, profile, context, subject + " " + body, sub, rng))
    return users


def _user(uid: str, profile: dict, context: dict, text: str, sub: dict | None, rng: random.Random) -> User:
    tail = uid.rsplit("_", 1)[1]
    name = f"{NAMES[int(tail) % len(NAMES)]}+{tail}@example.com"
    language = LANGUAGE_BY_USER.get(tail) or LANGUAGE_BY_COUNTRY.get(context["country"])
    if not sub:
        return User(uid, tail, context["country"], context, profile, None, None, None, None, 0, timedelta(days=60), WINDOW_START, WINDOW_END, 0.0, 0, language, name)
    product = sub["store_product_id"].replace("lumen.", "").replace("lumen_", "")
    kind = "weekly" if product == "weekly" else "monthly" if product == "monthly" else "annual"
    found = MONEY.search(text)
    price = found.group(0) if found else REGIONAL[(context["country"], kind)]
    purchased, orig = parse(sub["purchased_at"]), parse(sub["originally_purchased_at"])
    billing = parse(sub["billing_issue_detected_at"])
    trial_days = 7 if product == "annual_trial" else 3 if kind == "weekly" and (purchased - orig).days % 7 == 3 else 0
    revenue = profile.get("total_revenue_usd") or 0.0
    first = orig + timedelta(days=trial_days)
    charges = 0 if revenue == 0 else ((purchased - first).days // PERIOD_DAYS[kind]) + 1
    if billing:
        anchor, earliest, latest = billing, 0, 25
    elif sub["renewal_cancelled_at"]:
        anchor, earliest, latest = purchased, 0, 30
    else:
        anchor, earliest, latest = purchased, -6, 24
    target = WINDOW_START + timedelta(days=rng.randint(earliest, latest))
    shift = timedelta(days=(anchor.date() - target.date()).days)
    latest_fact = max(x for x in (purchased, billing, parse(sub["renewal_cancelled_at"])) if x) - shift
    start = max(WINDOW_START, latest_fact + timedelta(hours=2))
    return User(uid, tail, context["country"], context, profile, price, kind, PRODUCT_NAMES[product], sub["store"], trial_days, shift, start, WINDOW_END, revenue / charges if charges else 0.0, charges, language, name)


@dataclass
class Facts:
    tags: set
    profile: dict | None
    purchased: datetime | None = None
    first: datetime | None = None
    orig: datetime | None = None
    expires: datetime | None = None
    billing: datetime | None = None
    revenue: float = 0.0


def facts_on(user: User, when: datetime, fetched: datetime) -> Facts:
    """The user's profile as it would have read on that day, and what is true of them then."""
    if user.profile is None:
        return Facts(set(), None)
    profile = copy.deepcopy(user.profile)
    for entry in (profile.get("access_levels") or []) + (profile.get("subscriptions") or []) + (profile.get("non_subscriptions") or []):
        for field in STAMP_FIELDS:
            if entry.get(field):
                entry[field] = (parse(entry[field]) - user.shift).isoformat()
    attrs = []
    for attr in profile.get("custom_attributes") or []:
        if attr["key"] == "last_support_ticket":
            continue
        if attr["key"] == "simulated_install_date":
            attr = dict(attr, value=(datetime.fromisoformat(attr["value"]) - user.shift).date().isoformat())
        attrs.append(attr)
    profile["custom_attributes"] = attrs
    profile["timestamp"] = int(fetched.timestamp() * 1000)
    sub = (profile.get("subscriptions") or [None])[0]
    if not sub:
        profile["total_revenue_usd"] = 0.0
        return Facts({"free"}, profile)
    billing = parse(sub["billing_issue_detected_at"])
    cancelled = bool(sub["renewal_cancelled_at"])
    expires = parse(sub["expires_at"])
    purchased = parse(sub["purchased_at"])
    renewals = 0
    if not billing and not cancelled and expires and expires <= when:
        renewals = (when - expires).days // PERIOD_DAYS[user.kind] + 1
        step = timedelta(days=renewals * PERIOD_DAYS[user.kind])
        expires, purchased = expires + step, purchased + step
        for entry in profile["access_levels"] + profile["subscriptions"]:
            entry["expires_at"] = (parse(entry["expires_at"]) + step).isoformat()
            entry["purchased_at"] = (parse(entry["purchased_at"]) + step).isoformat()
    charges = user.charges + renewals
    profile["total_revenue_usd"] = round(user.unit_usd * charges, 2)
    orig = parse(sub["originally_purchased_at"])
    first = orig + timedelta(days=user.trial_days)
    tags = {"sub", user.kind}
    if billing:
        tags.add("bi")
        if charges == 0:
            tags.add("neverpaid")
    elif expires > when:
        tags |= {"active", "cancelled" if cancelled else "renewing"}
    else:
        tags.add("lapsed")
    if charges:
        tags.add("paid")
        if user.trial_days and (when - first).days <= 30:
            tags.add("trial")
        if (when - first).days >= 35:
            tags.add("long")
    return Facts(tags, profile, purchased, first, orig, expires, billing, profile["total_revenue_usd"])


def slots_en(user: User, facts: Facts, when: datetime, grant: int) -> dict:
    if not facts.purchased:
        return {"store": "the store"}
    path = PATHS[user.store]
    until = max(when, facts.expires) + timedelta(days=grant)
    return {
        "price": user.price, "plan": user.plan, "store": path["store"], "days": user.trial_days,
        "paid": day_en(facts.purchased, when), "first": day_en(facts.first, when), "orig": day_en(facts.orig, when),
        "ends": day_en(facts.expires, when), "bi": day_en(facts.billing, when) if facts.billing else "",
        "cancel_path": path["cancel"], "refund_path": path["refund"], "grant": grant, "grant_until": day_en(until, when),
    }


def slots_local(user: User, facts: Facts, when: datetime, grant: int, language: str) -> dict:
    if not facts.purchased:
        return {}
    path = PATHS[user.store]
    until = max(when, facts.expires) + timedelta(days=grant)
    local = lambda d: day_local(d, when, language)  # noqa: E731
    return {
        "price": user.price, "store": path["name"], "days": user.trial_days, "paid": local(facts.purchased), "first": local(facts.first),
        "orig": local(facts.orig), "ends": local(facts.expires), "bi": local(facts.billing) if facts.billing else "",
        "cancel_path": path["cancel_url"], "refund_path": path["refund_url"], "grant": grant, "grant_until": local(until),
    }


# --- The agent's options ---------------------------------------------------------------------------


def option(action, reply, risk, reason, emoji, days=None, kind=None, store=None, question=None, reply_en=None) -> dict:
    out = {
        "action": action, "params": {"days": days, "kind": kind, "store": store, "question": question},
        "reply": reply, "risk": risk, "risk_reason": reason, "emoji": emoji, "confidence": None,
    }
    if reply_en is not None:
        out["reply_en"] = reply_en
    return out


def ranked(options: list[dict], rng: random.Random, confidence: float) -> list[dict]:
    """Best first, the first option about as certain as the ticket as a whole."""
    options = options[:3]
    top = min(0.97, confidence + rng.uniform(-0.05, 0.06))
    for i, o in enumerate(options):
        o["confidence"] = round(max(0.15, top - i * rng.uniform(0.14, 0.26)), 2)
    return options


class Blank(dict):
    """A user with no purchases has no dates: the options that would need them are built, then never offered."""

    def __missing__(self, key: str) -> str:
        return ""


def en_options(cat: str, scene: dict, user: User, f: Facts, s: dict, rng: random.Random) -> list[dict]:
    ack = rng.choice(ACKS[cat]) + " " if cat in ACKS else ""
    if cat == "charged_after_trial":
        s = dict(s, paid=s["first"])  # the complaint is about the first charge, even if the plan has renewed since
    fill = lambda text: ack + text.format_map(Blank(s))  # noqa: E731
    store = user.store
    tags = f.tags
    grant = option("grant_access", fill("As a one-off I've added {grant} free days of premium, so you're covered to {grant_until}. If you'd rather stop, renewal is managed in {store}."), "medium", "Gives away {} days; within the limit, but a cost the user did not strictly ask for.".format(s.get("grant")), "👍", days=s.get("grant"))
    refund_steps = option("store_instructions", fill("Refunds are decided by {store}, not by us. {refund_path} Mention the {price} from {paid}."), "medium" if scene.get("heat") or (cat == "refund_request" and rng.random() < 0.5) else "low", "The money leaves through the store, and the user may come back if the store says no.", "🧾", kind="refund_request", store=store)
    to_person = option("refund_to_human", fill("I've passed this to a person on our team, who will look at the {price} from {paid}. The refund itself goes through {store}: {refund_path}"), "high" if scene.get("heat") else "medium", "The user mentions a dispute or a complaint: money and reputation are at stake." if scene.get("heat") else "Money leaves through the store, and a person should decide what to say about it.", "💸", store=store)
    cancel = option("store_instructions", fill("Renewal lives in {store}, not in the app: {cancel_path} You keep premium until {ends} and nothing more is charged."), "low", "Standard path; may lose the user, but it is what they ask.", "❌", kind="cancel", store=store)

    if cat == "charged_after_trial":
        explain = option("reply_only", fill("Here's what happened: the free trial started on {orig} and ran for {days} days, and then {store} charged {price} on {first} for the {plan}."), "low", "Only explains; no money or days.", "🧾")
        cancel_if = [cancel] if "renewing" in tags else []
        if scene.get("heat"):
            return [to_person, refund_steps, *cancel_if]
        if "lapsed" in tags:
            return [refund_steps, explain]
        return rng.choice(([refund_steps, grant, *cancel_if], [grant, refund_steps, explain], [grant, refund_steps, *cancel_if], [explain, refund_steps, grant], [refund_steps, explain, *cancel_if]))
    if cat == "no_access_after_purchase":
        if "lapsed" in tags:
            ended = option("reply_only", fill("Your {plan} ended on {ends} because renewal was off, so the app has gone back to the free version. To pick it up again, subscribe from the app with the same store account."), "low", "Explains the profile; nothing is given.", "🧾")
            return [ended, option("ask_user", fill("Your {plan} ended on {ends}. Did you mean to stop it, or did it end by surprise?"), "low", "Only a question.", "❓", question="Did you turn off renewal yourself?")]
        restore = option("store_instructions", fill("Your plan is active until {ends}, so let's bring it back: open Settings → Restore purchases in FitBuddy with the same store account you paid with, then restart the app."), "low", "Restore is free and fixes the common case.", "🔁", kind="restore", store=store)
        ask = option("ask_user", fill("I can see the {plan} active until {ends}. Which store account are you signed in to on this phone? If it differs from the one you paid with, that's the likely reason."), "low", "Only a question; the diagnosis may need it.", "❓", question="Which store account is signed in on this phone?")
        short_grant = dict(grant, params=dict(grant["params"], days=3), reply=fill("Please try Settings → Restore purchases first. While we check, I've added 3 free days on top of your plan.") , risk_reason="Days on top of a working plan; only useful as an apology.")
        return rng.choice(([restore, ask, short_grant], [ask, restore, short_grant], [restore, short_grant, ask], [short_grant, restore, ask], [restore, ask]))
    if cat == "billing_issue":
        steps = option("reply_only", fill("{store} could not take the payment on {bi}, which is why premium stopped. Update the payment method there ({cancel_path}), and your plan comes back by itself with your history intact."), "low", "Explains the failed renewal; no money or days.", "🧾")
        ask = option("ask_user", fill("{store} flagged the payment on {bi}. Did you change your card or bank around then? That tells me what to check next."), "low", "Only a question.", "❓", question="Did you change your card or bank around then?")
        return rng.choice(([steps, ask, grant], [grant, steps, ask])) if "paid" in tags else [steps, ask]
    if cat == "refund_request":
        cancel_if = [cancel] if "renewing" in tags else []
        if scene.get("heat"):
            return [to_person, refund_steps, *cancel_if]
        if "annual" in tags:
            return rng.choice(([to_person, refund_steps, *cancel_if], [refund_steps, to_person, *cancel_if]))
        return rng.choice(([refund_steps, *cancel_if, to_person], [refund_steps, to_person, grant], [to_person, refund_steps]))
    if cat == "cancel_how":
        if "renewing" in tags:
            what_happens = option("reply_only", fill("Deleting the app does not stop payments; only {store} can. Your {plan} stays on until {ends} either way, and your workouts stay in your account if you come back."), "low", "Pure explanation.", "🙏")
            return rng.choice(([cancel, what_happens], [cancel, what_happens, grant], [what_happens, cancel]))
        if "cancelled" in tags:
            return [option("reply_only", fill("Renewal is off on your {plan}. You keep premium until {ends} and nothing more is charged after that."), "low", "Confirms what the profile shows.", "🧾"), option("ask_user", fill("Renewal is off and premium runs to {ends}. Do you see anything in {store} that says otherwise?"), "low", "Only a question.", "❓", question="Do you see anything in the store that says otherwise?")]
        if "bi" in tags:
            return [option("reply_only", fill("After the failed payment on {bi} nothing more is being charged. To end it for good, go to {store}: {cancel_path}"), "low", "Explains the state; nothing changes on our side.", "🧾"), option("ask_user", fill("Nothing has been charged since {bi}. Would you rather end the plan, or fix the payment and keep going?"), "low", "Only a question.", "❓", question="End the plan, or fix the payment?")]
        return [option("reply_only", fill("Your {plan} ended on {ends}, so nothing more will be charged. You can use the free version as long as you like."), "low", "Confirms what the profile shows.", "🧾"), option("ask_user", fill("I see the plan ended on {ends}. Is there a newer charge on your statement? If so, send me the date."), "low", "Only a question.", "❓", question="Is there a newer charge on your statement?")]
    if cat == "price_complaint":
        if "free" in tags:
            return [option("reply_only", "Thanks for writing. Basic workouts are free and always will be; premium adds full programs and the coach. You don't need to pay anything to keep going with the free ones.", "low", "Only explains what is free.", "🙏"), option("ask_user", "Thanks for writing. What would you most want from FitBuddy? Then I can tell you if the free part covers it.", "low", "Only a question.", "❓", question="What would you most want from the app?")]
        tail = " Whatever you do, what you've paid for stays until {ends}." if "active" in tags else ""
        plans = option("reply_only", fill("You can move to another plan from Subscriptions in {store}; the monthly and annual ones cost much less per week." + tail), "low", "Plan guidance; no money or days.", "🙏")
        if "renewing" in tags:
            return rng.choice(([plans, cancel, grant], [plans, grant], [grant, plans, cancel], [cancel, plans]))
        return [plans, grant]
    # other
    what = scene.get("what", "")
    answer = scene.get("answer")
    if scene.get("noise"):
        return [option("no_reply", "", "low", "Not a support request.", "❌"), option("reply_only", "Hi, it looks like this message wasn't meant for us. If you do need help with FitBuddy, just reply here.", "low", "Harmless, but probably not needed.", "🙏")]
    if answer:
        return [option("reply_only", answer, "low", "Only answers the question.", "🙏"), option("ask_user", "Thanks for writing. So I point you the right way, which phone and app version are you on?", "low", "Only a question.", "❓", question="Which phone and app version are you on?")]
    if scene.get("feature"):
        return [option("reply_only", "Thanks, that's useful to hear. I've passed it to the team that plans the app. I can't promise a date, but requests like this do count.", "low", "Only a thank you; promises nothing.", "🙏"), option("no_reply", "", "low", "Nothing to do for the user right now.", "❌")]
    report = option("ask_user", f"Thanks for reporting this: {what} isn't how it should work. Which phone and app version are you on, and when did it start? Then the team can reproduce it.", "low", "Only a question; no diagnosis yet.", "❓", question="Which phone and app version, and since when?")
    logged = option("reply_only", f"Thanks for telling me that {what}. That's not how it should work, so I've passed it to the team. Updating to the latest version is worth a try in the meantime.", "low", "Promises no date or compensation.", "🙏")
    if "active" in tags and "paid" in tags:
        extra = dict(grant, params=dict(grant["params"], days=3), reply=f"Sorry, {what} isn't good enough on a paid plan. I've passed it to the team and added 3 free days to make up for the trouble.", risk_reason="Days as an apology for a bug; a small cost, and not asked for.")
        return rng.choice(([report, logged, extra], [logged, report, extra], [extra, report], [report, extra]))
    return rng.choice(([report, logged], [logged, report]))


LOCAL_PLAN = {
    "charged_after_trial": ("refund", "grant", "cancel"), "no_access_after_purchase": ("restore", "grant", "ask"), "billing_issue": ("billing", "ask"),
    "refund_request": ("refund", "cancel", "explain"), "cancel_how": ("cancel", "explain"), "price_complaint": ("price", "cancel"), "other": ("ask", "explain"),
}
LOCAL_ACTION = {
    "refund": ("store_instructions", "refund_request", "low", "🧾", "Standard store route; no days or money from our side."),
    "grant": ("grant_access", None, "medium", "👍", "Gives away days; within the limit, but not strictly asked for."),
    "cancel": ("store_instructions", "cancel", "low", "❌", "Standard path; may lose the user, but it is what they ask."),
    "restore": ("store_instructions", "restore", "low", "🔁", "Restore is free and fixes the common case."),
    "billing": ("reply_only", None, "low", "🧾", "Explains the failed renewal; no money or days."),
    "price": ("reply_only", None, "low", "🙏", "Plan guidance; no money or days."),
    "ask": ("ask_user", None, "low", "❓", "Only a question."),
    "explain": ("reply_only", None, "low", "🙏", "Only acknowledges; promises nothing."),
}


def local_options(cat: str, language: str, user: User, f: Facts, en: dict, local: dict) -> list[dict]:
    out = []
    tags = f.tags
    for key in (*LOCAL_PLAN[cat], "explain", "ask"):  # the last two only top up a plan that came out too short
        if key not in REPLIES_LOCAL[language] or (len(out) >= 2 and key not in LOCAL_PLAN[cat]) or len(out) == 3:
            continue
        if any(o["key"] == key for o in out):
            continue
        if key == "cancel" and "renewing" not in tags and cat != "cancel_how":
            continue
        if key == "grant" and "paid" not in tags:
            continue
        if key == "refund" and "paid" not in tags:
            continue
        action, kind, risk, emoji, reason = LOCAL_ACTION[key]
        store = user.store if action == "store_instructions" else None
        if key == "cancel" and cat == "cancel_how" and "renewing" not in tags:
            action, kind, store = "reply_only", None, None
        out.append(option(
            action, REPLIES_LOCAL[language][key].format(**local).replace("..", "."), risk, reason, emoji, days=en.get("grant") if action == "grant_access" else None,
            kind=kind, store=store, question="Which phone and app version, and what happened right before?" if action == "ask_user" else None,
            reply_en=REPLIES_EN[key].format(**en),
        ) | {"key": key})
    return [{k: v for k, v in o.items() if k != "key"} for o in out]


def evidence(user: User, f: Facts, earlier: int) -> list[str]:
    if not f.purchased:
        return ["access_levels: none", "subscriptions: none", "total_revenue_usd: 0.00", f"history.tickets: {earlier} earlier"]
    sub = f.profile["subscriptions"][0]
    ends = sub["expires_at"]
    state = "billing issue" if f.billing else "active" if "active" in f.tags else "expired"
    lines = [
        f"access_levels: premium {state}, {user.store}, expires_at {ends}",
        f"subscriptions: {sub['store_product_id']}, purchased_at {sub['purchased_at'][:10]}, originally_purchased_at {sub['originally_purchased_at'][:10]}, renewal_cancelled_at {(sub['renewal_cancelled_at'] or 'null')[:10]}",
        f"total_revenue_usd: {f.revenue:.2f}",
    ]
    if f.billing:
        lines.append(f"subscriptions.billing_issue_detected_at: {f.billing.date().isoformat()}")
    lines.append(f"history.tickets: {earlier} earlier" if earlier else "history.tickets: none earlier")
    return lines


def other_words(scene: dict) -> tuple[str, str]:
    what = scene.get("what", "")
    if scene.get("noise"):
        return f"This asks for {what}.", "Not a support request."
    if scene.get("feature"):
        return f"The user would like {what}.", "A product request; nothing is broken."
    if scene.get("answer") or what.startswith("whether"):
        return f"The user asks {what}.", "A product question; nothing is wrong with the account."
    return f"The user reports that {what}.", "Looks like a defect in the app rather than a problem with the account."


# --- One ticket ----------------------------------------------------------------------------------------


def known_ticket(user: User, scene: dict, cat: str, language: str | None, when: datetime, earlier: int, rng: random.Random) -> dict:
    fetched = when + timedelta(seconds=rng.randint(40, 300))
    f = facts_on(user, when, fetched)
    grant = rng.choice((3, 7, 7))
    en = slots_en(user, f, when, grant)
    if language:
        local = slots_local(user, f, when, grant, language)
        both = dict(local, **{k + "_en": v for k, v in en.items()})
        subject, body = scene["subject"].format(**both), scene["body"].format(**both)
        options = local_options(cat, language, user, f, en, local)
        messages_en = [scene["en"].format(**both)]
    else:
        subject, body = scene["subject"].format(**en), scene["body"].format(**en)
        options = en_options(cat, scene, user, f, en, rng)
        messages_en = None
    if cat == "other":
        summary, cause = other_words(scene)
    elif "free" in f.tags:
        summary, cause = "The user, still on the free version, thinks the paywall asks too much.", "No purchase on this account; a question about value, not about a charge."
    elif scene.get("heat"):
        summary, cause = "The user demands the {price} back and says they will escalate.".format(**en), rng.choice(CAUSES.get(cat)).format(**en)
    elif cat == "no_access_after_purchase" and "lapsed" in f.tags:
        summary, cause = "The user's {plan} ended on {ends}, and they want to know why premium is gone.".format(**en), "Renewal was turned off, so the plan ran out on {ends}; the app is right to show the free version.".format(**en)
    elif cat == "cancel_how" and "renewing" not in f.tags:
        summary, cause = "The user wants to be sure nothing more will be charged for the {plan}.".format(**en), rng.choice(CAUSES[cat]).format(**en)
    else:
        summary, cause = rng.choice(SUMMARIES[cat]).format(**en), rng.choice(CAUSES[cat]).format(**en)
    sure = "heat" not in scene and rng.random() > 0.18
    confidence = round(rng.uniform(0.74, 0.96) if sure else rng.uniform(0.58, 0.8), 2)
    tri = {
        "customer_user_id": user.uid, "lookup": "by customer_user_id", "summary": summary, "category": cat, "evidence": evidence(user, f, earlier),
        "root_cause": cause, "confidence": confidence,
        "options": ranked(options, rng, confidence), "bug_report": None, "user_language": language, "messages_en": messages_en,
    }
    source = "email" if rng.random() < 0.35 else "form"
    return {
        "customer_user_id": user.uid, "subject": subject, "body": body, "created_at": when, "category": cat, "source": source,
        "email": user.name if source == "email" else None, "context": dict(user.context, price=user.price), "profile": f.profile, "fetched": fetched, "triage": tri,
    }


def unknown_ticket(scene: dict, cat: str, when: datetime, index: int, rng: random.Random) -> dict:
    email = f"{NAMES[index % len(NAMES)]}.{index}@example.com"
    fetched = when + timedelta(seconds=rng.randint(40, 300))
    ask = option("ask_user", "Thanks for getting in touch. We couldn't find an account under this email, so we can't see your plan yet. Could you send the address you used in the App Store or Google Play, or the receipt they emailed you?", "low", "Only asks for the identifier before any claim is made.", "❓", question="Which store email did you use, or can you forward the receipt?")
    look = option("reply_only", "Thanks for writing. We couldn't match this email to an account, so the store is the quickest place to look: the App Store and Google Play both list what you pay for under Subscriptions, with the date and amount.", "low" if cat != "refund_request" else "medium", "Explains where to look, but leaves the question open.", "🧾")
    to_person = option("refund_to_human", "Thanks for writing. We can't issue refunds ourselves: payments and refunds go through the store. A person on our team will follow up once a receipt ties the payment to an account.", "medium", "Sends the user to the store without being able to verify the charge is ours.", "💸")
    passed = option("reply_only", "Thanks for writing. I've passed this to the right person on the team, who will reply to this address.", "low", "Nothing is promised.", "🙏")
    if cat == "other":
        options = [passed, option("no_reply", "", "low", "Not a request about an account.", "❌")]
    elif cat == "refund_request":
        options = [ask, to_person, look]
    else:
        options = rng.choice(([ask, look], [ask, look, to_person], [look, ask]))
    summary = {
        "charged_after_trial": "Someone writing from an unmatched address asks about a charge they don't recognise.",
        "no_access_after_purchase": "Someone says they paid and can't get in, and we can't match the address to an account.",
        "refund_request": "Someone asks for money back; the address matches no account.",
        "cancel_how": "Someone wants a subscription stopped; the address matches no account.",
        "price_complaint": "Someone asks about the price; the address matches no account.",
    }.get(cat, "A message that isn't about an account: " + scene["subject"].lower() + ".")
    confidence = round(rng.uniform(0.45, 0.78), 2)
    tri = {
        "customer_user_id": None, "lookup": f"Email {email} not in our index; the ticket has no customer id", "summary": summary, "category": cat,
        "evidence": ["lookup: email not in our index", "customer_user_id: null"], "root_cause": "We can't confirm a plan, date or amount until the sender is tied to an account." if cat != "other" else "Not a support request about an account.",
        "confidence": confidence, "options": ranked(options, rng, confidence), "bug_report": None, "user_language": None, "messages_en": None,
    }
    return {
        "customer_user_id": "", "subject": scene["subject"], "body": scene["body"], "created_at": when, "category": cat, "source": "email", "email": email,
        "context": {}, "profile": None, "fetched": fetched, "triage": tri,
    }


EDITED_ENDINGS = ("If anything still looks off, just reply here.", "Sorry for the trouble, and thanks for your patience.", "Thanks for bearing with us.")


def close(ticket: dict, rng: random.Random) -> dict:
    """Some tickets are already closed, in the shape workflow.accept / reject leave behind; demo mode: no Adapty call."""
    tri = ticket["triage"]
    closed_at = min(ticket["created_at"] + timedelta(minutes=rng.randint(40, 3400)), WINDOW_END + timedelta(hours=2))
    closed_at = max(closed_at, ticket["fetched"] + timedelta(minutes=5))
    roll = rng.random()
    out = {"status": "done", "action_at": None, "reply_sent_at": None, "action": None, "updated": closed_at}
    if roll < 0.08:
        return dict(out, status="dismissed", decision={"note": "", "reply": None, "rejected": True})
    if roll < 0.16 and not tri["user_language"]:
        return dict(out, decision={"note": "", "reply": rng.choice(OWN_REPLIES), "rejected": True}, reply_sent_at=closed_at)
    index = 0 if roll < 0.8 or len(tri["options"]) < 2 else 1
    chosen = tri["options"][index]
    final = {"action": chosen["action"], "params": chosen["params"], "reply": chosen["reply"]}
    # A hash, not rng: edited rows must not shift the random numbers of the rows after them.
    pick = zlib.crc32(chosen["reply"].encode())
    touched = None
    if chosen["reply"].strip() and not tri["user_language"] and pick % 4 == 0:
        touched = chosen["reply"] + " " + EDITED_ENDINGS[pick % len(EDITED_ENDINGS)]
        final["reply"] = touched
    if tri["user_language"]:
        final.update(language=tri["user_language"], reply_en=chosen.get("reply_en"))
    action = {"type": chosen["action"], "demo": True, "note": "Demo mode: no Adapty call was made; only the local ticket changed.", "option": index, "final": final, "approved_at": iso(closed_at)}
    if chosen["action"] == "grant_access":
        action.update(access_level="premium", days=chosen["params"]["days"])
    edits = dict.fromkeys(("action", "reply", "reply_en", "days", "kind", "store", "question"))
    edits["reply"] = touched
    return dict(out, decision={"option": index, "edits": edits}, action=action, action_at=closed_at, reply_sent_at=closed_at if chosen["reply"].strip() else None)


# --- The feed --------------------------------------------------------------------------------------------


def day_time(day: datetime, rng: random.Random) -> datetime:
    hour = rng.choices(range(24), weights=[2, 1, 1, 1, 1, 2, 3, 5, 7, 8, 8, 7, 7, 7, 8, 8, 8, 9, 10, 10, 9, 7, 5, 3])[0]
    return day.replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59))


def slots_when(user: User, rng: random.Random) -> datetime:
    span = (user.end - user.start).total_seconds()
    for _ in range(20):
        when = day_time(user.start + timedelta(seconds=rng.uniform(0, span)), rng)
        if user.start <= when <= user.end:
            return when
    return user.start + timedelta(seconds=rng.uniform(0, span))


def pick_scene(pool: list[tuple[str, dict]], used: dict, recent: list, per_user: set, cats: dict) -> tuple[str, dict]:
    def allowed(item, cooled=True, capped=True):
        cat, scene = item
        if id(scene) in per_user:
            return False
        if cooled and scene["subject"] in recent:
            return False
        return not capped or cats.get(cat, 0) < (5 if cat == "other" else 3)

    for cooled, capped in ((True, True), (False, True), (False, False)):
        fit = [item for item in pool if allowed(item, cooled, capped)]
        if fit:
            low = min(used.get(id(s), 0) for _, s in fit)
            return [item for item in fit if used.get(id(item[1]), 0) == low]
    return []


def generate(demo: sqlite3.Connection, eval_base: sqlite3.Connection, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    users = load_users(demo, eval_base, rng)
    weights = [(u.end - u.start).total_seconds() for u in users]
    plan: list[tuple[datetime, User | None]] = []
    for user, weight in zip(users, weights):
        for _ in range(max(5, round(KNOWN_TICKETS * weight / sum(weights)))):
            plan.append((slots_when(user, rng), user))
    while sum(1 for _, u in plan if u) > KNOWN_TICKETS:
        del plan[rng.randrange(len(plan))]
    for _ in range(UNKNOWN_TICKETS):
        plan.append((day_time(WINDOW_START + timedelta(days=rng.randint(0, 39)), rng), None))
    plan.sort(key=lambda item: (item[0], item[1].uid if item[1] else ""))

    used: dict[int, int] = {}
    recent: list[str] = []
    per_user: dict[str, set] = {}
    per_cats: dict[str, dict] = {}
    earlier: dict[str, int] = {}
    tickets = []
    unknown_pool = list(UNKNOWN_SCENES)
    for when, user in plan:
        if user is None:
            low = min(used.get(id(s), 0) for s in unknown_pool)
            scene = rng.choice([s for s in unknown_pool if used.get(id(s), 0) == low and s["subject"] not in recent] or unknown_pool)
            used[id(scene)] = used.get(id(scene), 0) + 1
            ticket = unknown_ticket(scene, scene["cat"], when, len(tickets), rng)
            subject = scene["subject"]
        else:
            tags = facts_on(user, when, when).tags
            language = user.language if rng.random() < 0.55 else None
            pool = [(s["cat"], s) for s in LOCAL_SCENES.get(language, []) if set(s["needs"]) <= tags] if language else []
            choices = pick_scene(pool, used, recent, per_user.setdefault(user.uid, set()), per_cats.setdefault(user.uid, {})) if pool else []
            if not choices:
                language = None
                pool = [(cat, s) for cat, scenes in SCENES.items() for s in scenes if set(s["needs"]) <= tags]
                choices = pick_scene(pool, used, recent, per_user[user.uid] if user.uid in per_user else per_user.setdefault(user.uid, set()), per_cats.setdefault(user.uid, {}))
            cat, scene = rng.choice(choices)
            used[id(scene)] = used.get(id(scene), 0) + 1
            per_user[user.uid].add(id(scene))
            per_cats[user.uid][cat] = per_cats[user.uid].get(cat, 0) + 1
            ticket = known_ticket(user, scene, cat, language, when, earlier.get(user.uid, 0), rng)
            earlier[user.uid] = earlier.get(user.uid, 0) + 1
            subject = scene["subject"]
        recent = (recent + [subject])[-40:]
        TriageTicket(**ticket["triage"])  # the same contract as a real Level 1 ticket
        ticket["closed"] = close(ticket, rng) if rng.random() < CLOSED_SHARE else None
        ticket["id"] = f"g-{len(tickets) + 1:04d}"
        tickets.append(ticket)
    return tickets


def row(ticket: dict) -> dict:
    closed = ticket["closed"] or {}
    triage_at = ticket["fetched"]
    return {
        "id": ticket["id"], "customer_user_id": ticket["customer_user_id"], "subject": ticket["subject"], "body": ticket["body"],
        "created_at": iso(ticket["created_at"]), "demo": 0, "rule": GENERATED, "context_json": json.dumps(ticket["context"], ensure_ascii=False),
        "status": closed.get("status", "proposed"), "category": ticket["category"],
        "profile_json": json.dumps(ticket["profile"], ensure_ascii=False) if ticket["profile"] else None,
        "profile_fetched_at": iso(ticket["fetched"]) if ticket["profile"] else None,
        "action_json": json.dumps(closed["action"], ensure_ascii=False) if closed.get("action") else None,
        "action_at": iso(closed["action_at"]) if closed.get("action_at") else None,
        "reply_sent_at": iso(closed["reply_sent_at"]) if closed.get("reply_sent_at") else None,
        "updated_at": iso(closed.get("updated") or triage_at), "source": ticket["source"], "email": ticket["email"],
        "triage_json": json.dumps(ticket["triage"], ensure_ascii=False), "triage_at": iso(triage_at),
        "decision_json": json.dumps(closed["decision"], ensure_ascii=False) if closed.get("decision") else None,
    }


def apply(db_path: Path, base_path: Path = EVAL_BASE, seed: int = SEED) -> list[dict]:
    """Replace the generated rows in `db_path`; returns them."""
    demo = sqlite3.connect(db_path)
    eval_base = sqlite3.connect(f"file:{base_path}?mode=ro", uri=True)
    with demo:
        demo.execute("DELETE FROM tickets WHERE rule = ?", (GENERATED,))
    tickets = generate(demo, eval_base, seed)
    rows = [row(t) for t in tickets]
    columns = list(rows[0])
    with demo:
        demo.executemany(f"INSERT INTO tickets ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})", rows)
    demo.execute("VACUUM")
    demo.close()
    eval_base.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=config.DEMO_DB_PATH)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    if not args.db.exists():
        print(f"no DB at {args.db}; run scripts/snapshot.py first")
        return 1
    rows = apply(args.db, seed=args.seed)
    states: dict[str, int] = {}
    for r in rows:
        states[r["status"]] = states.get(r["status"], 0) + 1
    print(f"{args.db}: {len(rows)} generated tickets, {states}, {rows[0]['created_at'][:10]} to {rows[-1]['created_at'][:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

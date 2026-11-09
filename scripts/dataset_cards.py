"""Build the 200 case cards the demo's tickets are written from: data/dataset/cards.json.

    python scripts/dataset_cards.py --labels <folder with the labelled complaints>

Each card joins three things:
- A real complaint's labels: pain, what the user wants, what support did, escalation, plan. Sampled per pain in the
  proportion the 1,517 labelled purchase complaints have (docs/market-research). Labels only: the review's words and
  its id never enter a card, and the writers never see a review. `provenance.label_ref` is a one-way hash of the
  review id: whoever holds the research data can trace a card back to its complaint, nobody else can.
- A real case: how that pain looks in an Adapty account (kb/E-real-cases.md, public AdaptySDK issues), which fixes the
  profile, so what the user says and what the account shows relate the way they do in real support.
- A face: one invented person (scripts/dataset_faces.py) with a job, a city, something going on, and a way of writing.

Same seed, same cards. No model, no network, no key.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import random
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.dataset_faces import EN, HABITS, RU, SHAPES, SR, TONES, parse  # noqa: E402

SEED = 20261109
NOW = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
OUT = ROOT / "data" / "dataset" / "cards.json"
BATCHES = 5
CHAT_SHARE = 0.75

# Scaled from stats_v2.json `pain` (1,517 purchase complaints): 200 cards, largest remainder.
PAINS = {
    "cant_cancel": 38, "charged_after_cancel": 33, "unaware_subscribed": 22, "paid_still_limited": 20, "trial_no_reminder": 20,
    "restore_fails": 15, "refund_refused": 11, "refund_wanted": 10, "price_surprise": 9, "not_delivered": 8,
    "double_wrong_charge": 7, "lost_after_reinstall": 4, "payment_fails": 3,
}
TAG_PAINS = {
    "trial": ("trial_no_reminder", "price_surprise"), "cancel": ("cant_cancel", "charged_after_cancel"),
    "access": ("paid_still_limited", "restore_fails", "lost_after_reinstall"), "double": ("double_wrong_charge",),
    "refund": ("refund_refused", "refund_wanted"), "billing": ("payment_fails",), "unaware": ("unaware_subscribed",), "credits": ("not_delivered",),
}

PRICES = {
    "USD": {"weekly": "$6.99", "monthly": "$12.99", "annual": "$49.99", "pro_annual": "$69.99", "lifetime": "$99.99", "credits": "$4.99"},
    "EUR": {"weekly": "€6.99", "monthly": "€12.99", "annual": "€49.99", "pro_annual": "€69.99", "lifetime": "€99.99", "credits": "€4.99"},
    "GBP": {"weekly": "£5.99", "monthly": "£10.99", "annual": "£39.99", "pro_annual": "£59.99", "lifetime": "£89.99", "credits": "£3.99"},
    "CAD": {"weekly": "CA$8.99", "monthly": "CA$16.99", "annual": "CA$64.99", "pro_annual": "CA$89.99", "lifetime": "CA$129.99", "credits": "CA$6.99"},
    "AUD": {"weekly": "A$10.99", "monthly": "A$18.99", "annual": "A$74.99", "pro_annual": "A$99.99", "lifetime": "A$149.99", "credits": "A$7.99"},
    "NZD": {"weekly": "NZ$11.99", "monthly": "NZ$20.99", "annual": "NZ$79.99", "pro_annual": "NZ$109.99", "lifetime": "NZ$159.99", "credits": "NZ$8.99"},
    "INR": {"weekly": "₹199", "monthly": "₹449", "annual": "₹2,499", "pro_annual": "₹3,499", "lifetime": "₹4,999", "credits": "₹199"},
    "BRL": {"weekly": "R$27.90", "monthly": "R$49.90", "annual": "R$229.90", "pro_annual": "R$319.90", "lifetime": "R$499.90", "credits": "R$19.90"},
    "MXN": {"weekly": "MX$99.99", "monthly": "MX$199.00", "annual": "MX$799.00", "pro_annual": "MX$1,099", "lifetime": "MX$1,699", "credits": "MX$79.00"},
    "ZAR": {"weekly": "R99.99", "monthly": "R199.99", "annual": "R849.99", "pro_annual": "R1,199", "lifetime": "R1,799", "credits": "R79.99"},
    "PLN": {"weekly": "19.99 PLN", "monthly": "35.99 PLN", "annual": "199.99 PLN", "pro_annual": "279.99 PLN", "lifetime": "399.99 PLN", "credits": "19.99 PLN"},
    "SEK": {"weekly": "69 SEK", "monthly": "129 SEK", "annual": "549 SEK", "pro_annual": "749 SEK", "lifetime": "1,090 SEK", "credits": "49 SEK"},
    "TRY": {"weekly": "149.99 TRY", "monthly": "279.99 TRY", "annual": "1,299 TRY", "pro_annual": "1,799 TRY", "lifetime": "2,599 TRY", "credits": "99.99 TRY"},
    "JPY": {"weekly": "¥900", "monthly": "¥1,800", "annual": "¥7,500", "pro_annual": "¥10,000", "lifetime": "¥15,000", "credits": "¥700"},
    "KRW": {"weekly": "₩8,900", "monthly": "₩16,000", "annual": "₩65,000", "pro_annual": "₩89,000", "lifetime": "₩139,000", "credits": "₩6,500"},
    "NOK": {"weekly": "79 NOK", "monthly": "149 NOK", "annual": "599 NOK", "pro_annual": "799 NOK", "lifetime": "1,190 NOK", "credits": "59 NOK"},
    "SGD": {"weekly": "S$8.98", "monthly": "S$16.98", "annual": "S$68.98", "pro_annual": "S$94.98", "lifetime": "S$138.98", "credits": "S$6.98"},
    "AED": {"weekly": "AED 24.99", "monthly": "AED 49.99", "annual": "AED 189.99", "pro_annual": "AED 259.99", "lifetime": "AED 379.99", "credits": "AED 18.99"},
    "CZK": {"weekly": "149 CZK", "monthly": "299 CZK", "annual": "1,190 CZK", "pro_annual": "1,690 CZK", "lifetime": "2,490 CZK", "credits": "119 CZK"},
    "RSD": {"weekly": "€6.99", "monthly": "€12.99", "annual": "€49.99", "pro_annual": "€69.99", "lifetime": "€99.99", "credits": "€4.99"},
}
CURRENCY = {
    "US": "USD", "GB": "GBP", "CA": "CAD", "NZ": "NZD", "IE": "EUR", "AU": "AUD", "DE": "EUR", "NL": "EUR", "FR": "EUR", "ES": "EUR", "IT": "EUR",
    "SE": "SEK", "PL": "PLN", "IN": "INR", "BR": "BRL", "MX": "MXN", "ZA": "ZAR", "NG": "USD", "PH": "USD", "SG": "SGD", "AE": "AED", "TR": "TRY",
    "JP": "JPY", "KR": "KRW", "NO": "NOK", "PT": "EUR", "CZ": "CZK", "RS": "RSD", "GE": "USD", "KZ": "USD", "AM": "USD", "LV": "EUR", "IL": "USD",
    "CY": "EUR", "UZ": "USD", "ME": "EUR", "FI": "EUR", "TH": "USD", "BA": "EUR",
}
ANDROID_LEAN = {"IN", "BR", "MX", "ZA", "NG", "PH", "TR", "RS", "BA", "ME", "UZ", "KZ", "AM", "GE", "TH"}
PERIOD_DAYS = {"weekly": 7, "monthly": 30, "annual": 365, "pro_annual": 365}
PRODUCT = {"weekly": "lumen_weekly", "monthly": "lumen_monthly", "annual": "lumen_annual", "pro_annual": "lumen_pro_annual", "lifetime": "lumen_lifetime", "credits": "lumen_ai_credits_50"}
# The story the numbers should be able to tell, from the organisers' dataset: trial complaints cluster on weekly-first.
FLOW_BY_PAIN = {
    "trial_no_reminder": (("onb_weekly", 13), ("onb_trial", 5), ("main", 2)),
    "unaware_subscribed": (("onb_weekly", 12), ("onb_trial", 5), ("main", 3), ("ai_limit", 2)),
    "price_surprise": (("main", 4), ("onb_weekly", 3), ("main_lifetime", 2)),
    "cant_cancel": (("onb_weekly", 12), ("onb_trial", 8), ("main", 8), ("settings", 6), ("main_lifetime", 4)),
    "charged_after_cancel": (("onb_weekly", 12), ("onb_trial", 7), ("main", 8), ("settings", 6)),
    "paid_still_limited": (("ai_limit", 10), ("main", 5), ("onb_trial", 5)),
    "restore_fails": (("main", 6), ("onb_trial", 5), ("settings", 4)),
    "lost_after_reinstall": (("main_lifetime", 2), ("main", 2)),
    "refund_refused": (("onb_weekly", 5), ("main", 4), ("main_lifetime", 2)),
    "refund_wanted": (("onb_weekly", 4), ("main", 3), ("onb_trial", 3)),
    "not_delivered": (("ai_limit", 8),),
    "double_wrong_charge": (("main_lifetime", 4), ("main", 3)),
    "payment_fails": (("settings", 2), ("main", 1)),
}
PLACEMENT = {"onb_weekly": "onboarding", "onb_trial": "onboarding", "main": "main", "main_lifetime": "main", "ai_limit": "ai_limit", "settings": "settings"}
CHANNELS = (("meta_ads", 4), ("tiktok_ads", 3), ("organic", 3), ("google_uac", 2), ("referral", 1))

# Variant per pain: (real case, what happened, what they believe, how the account is set up).
VARIANTS = {
    "cant_cancel": [
        ("cant_cancel", "The subscription is active and renewing. It can only be stopped in the store it was bought in.", "Looked for a cancel button in the app and found none.", "renewing"),
        ("D1", "The subscription is active and renewing. Deleting the app or the account never cancels a store subscription.", "Deleted the app or the account and thinks that ended it, but is worried about the next charge.", "renewing"),
        ("cant_cancel_other_store", "Bought on one platform, now uses another, so the settings they are looking at do not show it.", "Looks in the store on their current phone and the subscription is not listed.", "renewing"),
    ],
    "charged_after_cancel": [
        ("D1", "Never actually cancelled in the store: deleted the app or the account. Renewal is still on and it charged.", "Says they cancelled.", "renewing_charged"),
        ("D3_after", "They cancelled, but only after the renewal had already charged. Access runs until the period ends.", "Believes they cancelled in time.", "cancelled_after_charge"),
        ("D3_before", "They really cancelled before the renewal date and were charged anyway: an unexplained case for a person to look at.", "Cancelled before the date, has a screenshot or remembers the day.", "cancelled_before_charge"),
        ("D2", "Has both a web subscription and a store subscription; cancelled one, the other kept charging.", "Cancelled and was charged again by the other one.", "web_and_store"),
    ],
    "unaware_subscribed": [
        ("unaware_onboarding", "Tapped through an onboarding paywall; the trial or weekly plan started and later charged.", "Never meant to subscribe, does not remember agreeing.", "renewing_charged"),
        ("unaware_household", "Someone else in the household, a child or an older relative, tapped the offer on this phone.", "Found a charge they cannot explain, or someone found it for them.", "renewing_charged"),
    ],
    "paid_still_limited": [
        ("A7", "The account has an active access level, but the app still locks features (paywall callback vs access level).", "Paid, the app said thank you, features are still locked.", "active"),
        ("A5", "Active on the server; the app shows locked after a reinstall or update (a known SDK version bug).", "Was premium, then after an update it shows locked.", "active"),
        ("A6", "Android renewal is in flight: the period just ended, renewal still on, access will come back shortly.", "Renewal was charged, but access is off right now.", "just_expired_renewing"),
        ("ai_limit", "Active plan; they hit the AI coach limit and think premium should not have one.", "Paying, still gets told they are out of AI plans.", "active"),
    ],
    "trial_no_reminder": [
        ("trial_converted", "A free trial converted to paid on time. No reminder was sent. Everything worked as designed.", "Thought it was free, got no warning.", "trial_converted"),
    ],
    "restore_fails": [
        ("A1", "This account has no purchase at all: the purchase sits on another profile (anonymous or another login).", "Paid with this Apple or Google account, Restore does nothing.", "empty"),
        ("A8", "A duplicate profile after login: the purchase is on the other profile, this one is empty.", "Logged in with email or Google, premium disappeared.", "empty"),
        ("A5", "Active on the server, the app shows locked after restore.", "Restore says done, the app is still free.", "active"),
    ],
    "refund_refused": [
        ("refund_store_refused", "A store purchase. They already asked the store for a refund and were turned down. Only the store can refund it.", "Support or the store said no; wants someone to actually help.", "renewing_charged"),
        ("refund_no_reply", "A store purchase. They wrote earlier and nobody answered.", "Wants money back and is annoyed about the silence.", "cancelled_after_charge"),
    ],
    "refund_wanted": [
        ("refund_plain", "A recent paid store purchase, not used much. A refund is only possible through the store.", "Wants their money back.", "renewing_charged"),
        ("refund_done", "The store has already refunded this purchase; the account shows the refund.", "Does not see the money yet and asks where it is.", "refunded"),
    ],
    "price_surprise": [
        ("E1", "The offer was pay-up-front at a low price, not free, then the normal price.", "The screen said free or very cheap, and it took more.", "pay_up_front"),
        ("E2", "A discounted first year ended; year two renewed at full price.", "Charged much more than last time.", "renewed_full_price"),
    ],
    "not_delivered": [
        ("C1", "Bought an AI credits pack (a one-off purchase in the account). A generation failed and the credits did not come back. The account shows the purchase, not the balance.", "Paid for credits and got nothing for them.", "credits"),
    ],
    "double_wrong_charge": [
        ("B1", "Has an active weekly plan and a lifetime purchase: the weekly goes on until cancelled in the store.", "Bought lifetime and is still charged weekly.", "weekly_and_lifetime"),
        ("B2", "Paying twice: a web subscription and a store subscription for the same access.", "Two charges every period.", "web_and_store"),
        ("A2", "On a new device the app asked again and they bought the same plan a second time: two real purchases.", "Bought it twice because the app forgot.", "two_store"),
    ],
    "lost_after_reinstall": [
        ("A4", "Lifetime on an old account; the new account is empty.", "Bought lifetime, re-created the account, it is gone.", "empty"),
        ("A2", "New device, new anonymous profile: this account is empty.", "New phone, the subscription is gone.", "empty"),
    ],
    "payment_fails": [
        ("billing_grace", "The store could not charge the card: billing issue, grace period, access still on for now.", "Card changed or ran out, asks what will happen.", "grace"),
        ("billing_lapsed", "The store could not charge the card, the grace period ended, access is off.", "Access is gone mid-plan.", "lapsed"),
    ],
}


# Where each case comes from. Issues are public; A–E are sections of kb/E-real-cases.md; the rest is the complaint's own label.
KB = "kb/E-real-cases.md"
CASE_SOURCES = {
    "A1": f"{KB}#A1", "A2": f"{KB}#A2", "A4": f"{KB}#A4",
    "A5": "https://github.com/adaptyteam/AdaptySDK-iOS/issues/136",
    "A6": "https://github.com/adaptyteam/AdaptySDK-Flutter/issues/112",
    "A7": "https://github.com/adaptyteam/AdaptySDK-Flutter/issues/206",
    "A8": "https://github.com/adaptyteam/AdaptySDK-iOS/issues/101",
    "B1": f"{KB}#B1", "B2": f"{KB}#B2", "C1": f"{KB}#C1", "D1": f"{KB}#D1", "D2": f"{KB}#D2",
    "D3_after": f"{KB}#D3", "D3_before": f"{KB}#D3", "E1": f"{KB}#E1", "E2": f"{KB}#E2",
}


def stamp(day: datetime) -> str:
    return day.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def pretty(day: datetime) -> str:
    return day.strftime("%b %-d, %Y")


def weighted(rng: random.Random, pairs) -> str:
    return rng.choices([p for p, _ in pairs], [w for _, w in pairs])[0]


def slug(name: str) -> str:
    table = str.maketrans({"đ": "dj", "Đ": "dj", "ł": "l", "'": ""})
    translit = str.maketrans(dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"])))
    text = name.lower().translate(table).translate(translit)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]+", ".", text).strip(".")


def load_labels(folder: Path) -> dict[str, list[dict]]:
    """Real complaints' labels per pain. Only the label columns are read; review ids are dropped here."""
    fields = ("pain", "wants", "support", "escalation", "plan", "amount", "solver")
    by_pain: dict[str, list[dict]] = {pain: [] for pain in PAINS}
    for name in sorted(glob.glob(str(folder / "*.tsv"))):
        with open(name, newline="") as handle:
            for row in csv.reader(handle, delimiter="\t"):
                if len(row) == 8 and row[1] in by_pain:
                    item = dict(zip(fields, row[1:]))
                    item["label_ref"] = hashlib.sha256(row[0].encode()).hexdigest()[:12]
                    by_pain[row[1]].append(item)
    return by_pain


def sub(store, plan, bought, expires, renew_off=None, billing=None, grace=False, reason=None, first=None, offer=None, serial=0):
    code = {"app_store": "2000000", "play_store": "GPA.4438-", "stripe": "sub_1Q"}[store]
    tx = f"{code}{serial:07d}"
    base = {
        "store": store, "store_product_id": PRODUCT[plan], "store_base_plan_id": None, "store_transaction_id": tx, "store_original_transaction_id": tx,
        "offer": offer, "purchased_at": stamp(bought), "originally_purchased_at": stamp(first or bought), "expires_at": stamp(expires) if expires else None,
        "renewal_cancelled_at": stamp(renew_off) if renew_off else None, "billing_issue_detected_at": stamp(billing) if billing else None,
        "is_in_grace_period": grace, "cancellation_reason": reason,
    }
    return base


FREE_TRIAL = {"category": "introductory", "type": "free_trial", "id": None}
PAY_UP_FRONT = {"category": "introductory", "type": "pay_up_front", "id": "intro_first_period"}


def build_account(rng, setup, plan, store, ticket_at, serial):
    """Subscriptions, one-offs and the one-line truth, all as of the ticket's day."""
    subs, ones, refund_on = [], [], None
    period = timedelta(days=PERIOD_DAYS.get(plan, 365))
    charged = ticket_at - timedelta(days=rng.randint(1, min(6, max(1, period.days - 1))), hours=rng.randint(0, 20))
    first = charged - timedelta(days=rng.choice([0, 0, 7, 30])) if plan in ("weekly", "monthly") else charged
    expires = charged + period
    facts = {"last_charge": pretty(charged), "renews_or_ends": pretty(expires)}
    add = lambda *a, **k: subs.append(sub(*a, serial=serial + len(subs), **k))
    if setup == "renewing":
        charged = ticket_at - timedelta(days=rng.randint(2, period.days - 1 if period.days < 30 else 25))
        facts = {"last_charge": pretty(charged), "next_charge": pretty(charged + period)}
        add(store, plan, charged, charged + period, first=charged - timedelta(days=rng.choice([0, 7, 30, 60])))
    elif setup == "renewing_charged":
        add(store, plan, charged, expires, first=first)
    elif setup == "cancelled_after_charge":
        off = charged + timedelta(hours=rng.randint(3, 30))
        off = min(off, ticket_at - timedelta(hours=1))
        add(store, plan, charged, expires, renew_off=off, first=first, reason="voluntarily_cancelled")
        facts["cancelled_at"] = pretty(off)
    elif setup == "cancelled_before_charge":
        off = charged - timedelta(days=rng.randint(1, 3))
        add(store, plan, charged, expires, renew_off=off, first=first, reason="voluntarily_cancelled")
        facts["cancelled_at"] = pretty(off)
    elif setup == "web_and_store":
        add("stripe", plan, charged - timedelta(days=2), expires - timedelta(days=2), first=first)
        add(store, plan, charged, expires, first=first)
        facts["two_subscriptions"] = "stripe (web) and " + store
    elif setup == "two_store":
        add(store, plan, charged, expires, first=charged)
        add(store, plan, charged - timedelta(days=rng.randint(8, 40)), expires - timedelta(days=rng.randint(8, 40)), first=charged - timedelta(days=60))
        facts["two_purchases"] = "the same plan bought twice"
    elif setup == "active":
        add(store, plan, charged, expires, first=first)
    elif setup == "just_expired_renewing":
        ended = ticket_at - timedelta(minutes=rng.randint(10, 300))
        add(store, plan, ended - period, ended, first=ended - period)
        facts = {"last_charge": pretty(ended - period), "period_ended": stamp(ended)}
    elif setup == "trial_converted":
        trial = {"weekly": 3, "annual": 7, "monthly": 7}.get(plan, 7)
        start = charged - timedelta(days=trial)
        add(store, plan, charged, expires, first=start)
        facts = {"trial_started": pretty(start), "trial_days": trial, "first_charge": pretty(charged), "renews": pretty(expires)}
    elif setup == "refunded":
        refund_on = charged + timedelta(days=rng.randint(1, 3))
        add(store, plan, charged, refund_on, renew_off=refund_on, first=first, reason="refund")
        facts["refunded_by_store_on"] = pretty(refund_on)
    elif setup == "pay_up_front":
        intro = charged - timedelta(days=PERIOD_DAYS.get(plan, 30))
        add(store, plan, charged, expires, first=intro)
        subs[-1]["offer"] = PAY_UP_FRONT
        facts = {"intro_paid": pretty(intro), "full_price_charged": pretty(charged)}
    elif setup == "renewed_full_price":
        add(store, plan, charged, expires, first=charged - timedelta(days=365))
        facts["second_year_at_full_price"] = True
    elif setup == "credits":
        bought = ticket_at - timedelta(days=rng.randint(0, 4), hours=rng.randint(1, 12))
        add(store, "monthly", ticket_at - timedelta(days=rng.randint(3, 20)), ticket_at + timedelta(days=rng.randint(9, 25)))
        ones.append({"store": store, "store_product_id": PRODUCT["credits"], "store_transaction_id": f"CR{serial:08d}", "purchased_at": stamp(bought), "is_consumable": True, "is_refund": False, "is_sandbox": False})
        facts = {"credits_pack_bought": pretty(bought), "plan": "monthly"}
    elif setup == "weekly_and_lifetime":
        bought = ticket_at - timedelta(days=rng.randint(8, 25))
        wk = ticket_at - timedelta(days=rng.randint(1, 6))
        add(store, "weekly", wk, wk + timedelta(days=7), first=bought - timedelta(days=30))
        ones.append({"store": store, "store_product_id": PRODUCT["lifetime"], "store_transaction_id": f"LT{serial:08d}", "purchased_at": stamp(bought), "is_consumable": False, "is_refund": False, "is_sandbox": False})
        facts = {"lifetime_bought": pretty(bought), "weekly_last_charge": pretty(wk)}
    elif setup == "grace":
        billed = ticket_at - timedelta(days=rng.randint(1, 4))
        add(store, plan, billed - period, billed + timedelta(days=6), billing=billed, grace=True, reason="billing_error", first=billed - period)
        facts = {"billing_issue_since": pretty(billed), "grace_until": pretty(billed + timedelta(days=6))}
    elif setup == "lapsed":
        billed = ticket_at - timedelta(days=rng.randint(9, 20))
        add(store, plan, billed - period, billed, billing=billed, reason="billing_error", first=billed - period)
        facts = {"billing_issue_since": pretty(billed), "access_ended": pretty(billed)}
    elif setup == "empty":
        facts = {"account_shows": "no purchases on this account"}
    else:
        raise ValueError(setup)
    return subs, ones, facts


def profile(cid, subs, ones, country, channel, version, usd):
    levels = []
    lifetime = next((o for o in ones if o["store_product_id"] == PRODUCT["lifetime"]), None)
    if subs:
        latest = max(subs, key=lambda s: s["purchased_at"])
        levels.append({**{k: latest[k] for k in latest if k != "commitment"}, "access_level_id": "premium", "starts_at": None})
    if lifetime:
        levels = [{"store": lifetime["store"], "store_product_id": lifetime["store_product_id"], "store_base_plan_id": None, "store_transaction_id": lifetime["store_transaction_id"],
                   "store_original_transaction_id": lifetime["store_transaction_id"], "offer": None, "purchased_at": lifetime["purchased_at"], "originally_purchased_at": lifetime["purchased_at"],
                   "expires_at": None, "renewal_cancelled_at": None, "billing_issue_detected_at": None, "is_in_grace_period": False, "cancellation_reason": None, "access_level_id": "premium", "starts_at": None}]
    return {
        "app_id": "demo", "profile_id": f"demo-{cid}", "customer_user_id": cid, "total_revenue_usd": usd,
        "custom_attributes": [{"key": "acquisition_channel", "value": channel}, {"key": "app_version", "value": version}, {"key": "seed_source", "value": "resolve_demo_dataset"}],
        "access_levels": levels,
        "subscriptions": [{**s, "environment": "Production", "commitment": None} for s in subs],
        "non_subscriptions": ones,
    }


def assign_pains(rng, people):
    """Each person gets a pain, the ones with a fitting circumstance first."""
    left = dict(PAINS)
    order = sorted(people, key=lambda p: (p["tag"] is None, rng.random()))
    for person in order:
        fits = [p for p in TAG_PAINS.get(person["tag"], ()) if left.get(p)]
        pool = fits or [p for p, n in left.items() if n]
        weights = [left[p] for p in pool]
        pain = rng.choices(pool, weights)[0]
        person["pain"] = pain
        left[pain] -= 1
    assert not any(left.values()), left


def build(labels_dir: Path) -> list[dict]:
    rng = random.Random(SEED)
    labels = load_labels(labels_dir)
    for pain, need in PAINS.items():
        assert len(labels[pain]) >= need, (pain, len(labels[pain]))
    groups = [("en", parse(EN)), ("ru", parse(RU)), ("sr", parse(SR))]
    assert [len(p) for _, p in groups] == [160, 20, 20], [len(p) for _, p in groups]
    for lang, people in groups:
        for person in people:
            person["lang"] = lang
    people = [p for _, group in groups for p in group]
    assert len({p["name"] for p in people}) == 200
    assign_pains(rng, people)

    # real complaints' labels, no complaint used twice; plan labels too sparse to use "unknown" as is
    samples = {pain: rng.sample(rows, PAINS[pain]) for pain, rows in labels.items()}
    usage = {pain: 0 for pain in PAINS}
    styles = [(h, t, s) for h in HABITS for t in TONES for s in SHAPES]
    rng.shuffle(styles)
    habit_use: dict[str, int] = {}
    picked_styles = []
    for style in styles:  # as even as the habit list allows
        if habit_use.get(style[0], 0) < 7:
            habit_use[style[0]] = habit_use.get(style[0], 0) + 1
            picked_styles.append(style)
        if len(picked_styles) == 200:
            break

    rng.shuffle(people)
    # 5 batches, each 32 en + 4 ru + 4 sr; half of every language is in the demo's first 100
    by_lang = {lang: [p for p in people if p["lang"] == lang] for lang in ("en", "ru", "sr")}
    ordered, batch_of = [], {}
    for batch in range(BATCHES):
        for lang, n in (("en", 32), ("ru", 4), ("sr", 4)):
            for _ in range(n):
                person = by_lang[lang].pop()
                batch_of[person["name"]] = batch
                ordered.append(person)
    cards = []
    in_demo_by_lang = {"en": 0, "ru": 0, "sr": 0}
    quota = {"en": 80, "ru": 10, "sr": 10}
    for index, person in enumerate(ordered):
        pain = person["pain"]
        real = samples[pain][usage[pain]]
        usage[pain] += 1
        variant = rng.choice(VARIANTS[pain])
        case, truth, belief, setup = variant
        channel = weighted(rng, CHANNELS)
        flow = weighted(rng, FLOW_BY_PAIN[pain])
        android = person["country"] in ANDROID_LEAN
        store = rng.choices(["play_store", "app_store"], [7, 3] if android else [3, 7])[0]
        plan = real["plan"] if real["plan"] in ("weekly", "monthly", "annual") else None
        if plan is None:
            plan = {"onb_weekly": "weekly", "onb_trial": "annual"}.get(flow) or rng.choice(["monthly", "annual", "weekly", "pro_annual"])
        if setup == "renewed_full_price" or (setup == "pay_up_front" and plan == "weekly"):
            plan = "annual" if setup == "renewed_full_price" else "monthly"
        in_demo = in_demo_by_lang[person["lang"]] < quota[person["lang"]] and index % 2 == 0 or (quota[person["lang"]] - in_demo_by_lang[person["lang"]] >= (len([p for p in ordered[index:] if p["lang"] == person["lang"]])))
        if in_demo:
            in_demo_by_lang[person["lang"]] += 1
        source = "chat" if rng.random() < CHAT_SHARE else "email"
        # age in days of the ticket: recent ones denser
        age = min(29.5, rng.expovariate(1 / 9.0)) + rng.random() * 0.2
        ticket_at = NOW - timedelta(days=age)
        serial = 1000 + index * 10
        subs, ones, facts = build_account(rng, setup, plan, store, ticket_at, serial)
        plan = {"credits": "monthly", "weekly_and_lifetime": "weekly"}.get(setup, plan)  # the plan the account really has
        cid = "" if source == "email" and setup == "empty" and rng.random() < 0.5 else f"lumen_443814_1{index:04d}"
        currency = CURRENCY[person["country"]]
        price = PRICES[currency]
        amounts = {k: price[k] for k in ("weekly", "monthly", "annual", "pro_annual", "lifetime")}
        habit, tone, shape = picked_styles[index]
        usd = round(sum({"weekly": 6.99, "monthly": 12.99, "annual": 49.99, "pro_annual": 69.99}.get(next((k for k, v in PRODUCT.items() if v == s["store_product_id"]), "monthly"), 12.99) for s in subs) + sum(99.99 if "lifetime" in o["store_product_id"] else 4.99 for o in ones), 2)
        version = rng.choice(["3.4.2", "3.5.0", "3.5.0", "3.5.1", "3.5.1", "3.6.0"])
        email = f"{slug(person['name'])}@example.com" if source == "email" else None
        cards.append({
            "id": f"c-{index + 1:03d}",
            "batch": batch_of[person["name"]],
            "in_demo": in_demo,
            "lang": person["lang"],
            "source": source,
            "created_at": ticket_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "customer_user_id": cid,
            "email": email,
            "face": {k: person[k] for k in ("name", "country", "city", "age", "job", "life")},
            "style": {"habit": habit, "tone": tone, "opening": shape},
            "real_labels": {k: real[k] for k in ("pain", "wants", "support", "escalation", "solver")},
            "provenance": {
                "labels": "a labelled App Store purchase complaint; labels only, its words were never used",
                "label_ref": real["label_ref"],
                "case_source": CASE_SOURCES.get(case, "the complaint's own label"),
                "face": "invented, scripts/dataset_faces.py",
                "account": "synthetic, built by scripts/dataset_cards.py; not in Adapty",
            },
            "real_case": case,
            "truth": truth,
            "belief": belief,
            "plan": plan,
            "store": store,
            "price": price[plan] if plan in price else price["monthly"],
            "credits_price": price["credits"],
            "prices": amounts,
            "dates": facts,
            "context": {"placement": PLACEMENT[flow], "flow": flow, "ab_test": "", "country": person["country"], "platform": "iOS" if store == "app_store" else "Android", "acquisition_channel": channel},
            "profile": profile(cid, subs, ones, person["country"], channel, version, usd) if cid else None,
        })
    assert sum(c["in_demo"] for c in cards) == 100, sum(c["in_demo"] for c in cards)
    assert in_demo_by_lang == quota, in_demo_by_lang
    assert len({(c["style"]["habit"], c["style"]["tone"], c["style"]["opening"]) for c in cards}) == 200
    return cards


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args()
    cards = build(args.labels)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cards, ensure_ascii=False, indent=1) + "\n")
    print(f"{OUT}: {len(cards)} cards")
    return 0


if __name__ == "__main__":
    sys.exit(main())

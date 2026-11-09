"""Check the written tickets against their cards, each other and the review corpus, then merge them.

    python scripts/dataset_check.py --out <writers' folder> [--reviews <reviews json>] [--merge]

What it looks for: a ticket for every card in the card's format and language; openings that repeat; pairs of tickets
that share phrasing; whole phrases shared with a real review (the writers never saw one, this proves it); amounts the
card does not hold; words a user could not know. `--merge` writes data/dataset/tickets.json and study numbers to
data/dataset/check.json. The check.json holds counts and ticket ids only, never review text.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "dataset"
REASONS = {"refund", "chargeback", "threat", "asks_for_human", "profile_mismatch", "repeat_after_grant", "other"}
INSIDE_WORDS = re.compile(r"\b(access level|sdk|paywall flow|customer_user_id|profile id|adapty|webhook)\b", re.I)
MONEY = re.compile(r"(?:CA\$|A\$|NZ\$|S\$|R\$|MX\$|AED\s?|[$€£¥₹₩]|\bR(?=\d))\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:PLN|SEK|TRY|NOK|CZK|USD|EUR|GBP|INR)\b")
WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.U)
CYRILLIC = re.compile(r"[Ѐ-ӿ]")
LATIN = re.compile(r"[A-Za-zÀ-ž]")


def words(text: str) -> list[str]:
    return [w.casefold() for w in WORD.findall(text)]


def user_text(ticket: dict) -> str:
    if "messages" in ticket:
        return "\n".join(m["text"] for m in ticket["messages"] if m["role"] == "user")
    return ticket.get("body", "")


def shingles(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def review_overlap(tickets: dict[str, dict], path: Path, n: int = 6) -> dict:
    """Longest run of words shared with any review, per ticket. Only counts leave this function."""
    reviews = json.loads(path.read_text())
    seen: set[int] = set()
    for row in reviews:
        tokens = words(f"{row.get('title', '')} {row.get('text', '')}")
        seen.update(hash(s) for s in shingles(tokens, n))
    runs = {}
    for tid, ticket in tickets.items():
        tokens = words(user_text(ticket))
        best = current = 0
        for i in range(len(tokens) - n + 1):
            current = current + 1 if hash(tuple(tokens[i:i + n])) in seen else 0
            if current:
                best = max(best, current + n - 1)
        runs[tid] = best
    return {
        "reviews": len(reviews), "min_run": n, "tickets_with_run": sum(1 for v in runs.values() if v),
        "longest": max(runs.values()), "at_least_8": sorted(t for t, v in runs.items() if v >= 8),
        "run_histogram": dict(sorted(Counter(v for v in runs.values() if v).items())),
    }


def check(out: Path, reviews: Path | None) -> tuple[list[str], dict, dict]:
    cards = {c["id"]: c for c in json.loads((DIR / "cards.json").read_text())}
    tickets: dict[str, dict] = {}
    problems: list[str] = []
    for path in sorted(out.glob("batch-*.json")):
        batch = int(path.stem.split("-")[1])
        rows = json.loads(path.read_text())
        expected = [c for c in cards.values() if c["batch"] == batch]
        if [r["id"] for r in rows] != [c["id"] for c in expected]:
            problems.append(f"{path.name}: ids differ from the cards")
        for row in rows:
            tickets[row["id"]] = row
    edits = DIR / "edits.json"
    if edits.exists():
        for edit in json.loads(edits.read_text())["edits"]:
            before = json.dumps(tickets[edit["id"]], ensure_ascii=False)
            if edit["old"] not in before:
                problems.append(f"{edit['id']}: the edit does not apply: {edit['old']!r}")
            tickets[edit["id"]] = json.loads(before.replace(edit["old"], edit["new"]))

    for tid, card in cards.items():
        t = tickets.get(tid)
        if t is None:
            problems.append(f"{tid}: missing")
            continue
        text = user_text(t)
        if not t.get("voice"):
            problems.append(f"{tid}: no voice")
        if card["source"] == "email":
            if "body" not in t or "subject" not in t or not t["body"].strip():
                problems.append(f"{tid}: email needs subject and body")
        else:
            ms = t.get("messages") or []
            if not ms or ms[0]["role"] != "user" or ms[-1]["role"] != "agent":
                problems.append(f"{tid}: a chat starts with the user and ends with the agent")
            if any(a["role"] == b["role"] == "agent" for a, b in zip(ms, ms[1:])):
                problems.append(f"{tid}: two agent messages in a row")
            if t.get("handoff_reason") not in REASONS:
                problems.append(f"{tid}: handoff_reason {t.get('handoff_reason')!r}")
            if not (t.get("summary") or "").strip() or CYRILLIC.search(t.get("summary") or ""):
                problems.append(f"{tid}: summary must be in English")
        cyr, lat = len(CYRILLIC.findall(text)), len(LATIN.findall(text))
        if card["lang"] == "ru" and cyr < lat * 2:
            problems.append(f"{tid}: ru, but cyrillic {cyr} vs latin {lat}")
        if card["lang"] == "en" and cyr > 0:
            problems.append(f"{tid}: en with cyrillic")
        if "—" in text:
            problems.append(f"{tid}: em dash in the user's words")
        hit = INSIDE_WORDS.search(text)
        if hit:
            problems.append(f"{tid}: user says {hit.group(0)!r}")
        known = {re.sub(r"\D", "", re.sub(r"\.00$", "", v)) for v in [*card["prices"].values(), card["credits_price"]]}
        for amount in MONEY.findall(text):
            digits = re.sub(r"\D", "", re.sub(r"\.00$", "", amount.strip()))
            if digits and digits not in known and len(digits) >= 3 and card["lang"] == "en":
                problems.append(f"{tid}: amount {amount.strip()!r} is not on the card ({card['price']})")

    openings = Counter(" ".join(words(user_text(t))[:3]) for t in tickets.values())
    repeats = {k: v for k, v in openings.items() if v > 1 and k}
    grams = {tid: shingles(words(user_text(t)), 5) for tid, t in tickets.items()}
    pairs = []
    for a, b in combinations(sorted(grams), 2):
        shared = grams[a] & grams[b]
        if len(shared) >= 2:
            pairs.append((len(shared), a, b, " ".join(sorted(shared)[0])))
    pairs.sort(reverse=True)
    lengths = [len(words(user_text(t))) for t in tickets.values()]
    stats = {
        "tickets": len(tickets),
        "languages": dict(Counter(cards[t]["lang"] for t in tickets)),
        "sources": dict(Counter(cards[t]["source"] for t in tickets)),
        "user_words": {"min": min(lengths), "median": statistics.median(lengths), "max": max(lengths)} if lengths else {},
        "repeated_openings": repeats,
        "pairs_sharing_two_or_more_5grams": len(pairs),
        "worst_pairs": [{"shared_5grams": n, "a": a, "b": b, "example": e} for n, a, b, e in pairs[:12]],
        "handoff_reasons": dict(Counter(t.get("handoff_reason") for t in tickets.values() if "messages" in t)),
    }
    if reviews and tickets:
        stats["review_overlap"] = review_overlap(tickets, reviews)
    return problems, stats, tickets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()
    problems, stats, tickets = check(args.out, args.reviews)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print(f"\n{len(problems)} problems")
    for p in problems:
        print(" ", p)
    if args.merge:
        (DIR / "tickets.json").write_text(json.dumps([tickets[k] for k in sorted(tickets)], ensure_ascii=False, indent=1) + "\n")
        (DIR / "check.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

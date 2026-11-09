"""Write docs/dataset-study.md from the dataset's own records, or trace one ticket back to its sources.

    python scripts/dataset_study.py              # rewrite the study
    python scripts/dataset_study.py --check      # fail if the study is out of date
    python scripts/dataset_study.py --trace f-0018   # or a card id, c-001

No model calls, no network. Every number in the study is computed here.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import statistics
import sys
import tempfile
from collections import Counter
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from resolve import analytics, config, db  # noqa: E402
from resolve.demo_clock import shift_to_now  # noqa: E402

DIR = ROOT / "data" / "dataset"
STUDY = ROOT / "docs" / "dataset-study.md"
WRITERS = "claude-sonnet-5-5"
ANALYST = "deepseek-v4.1-flash-1017"
PAINS = ("cant_cancel", "charged_after_cancel", "unaware_subscribed", "paid_still_limited", "trial_no_reminder", "restore_fails",
         "refund_refused", "refund_wanted", "price_surprise", "not_delivered", "double_wrong_charge", "lost_after_reinstall", "payment_fails")
CATEGORIES = ("refund_request", "cancel_how", "no_access_after_purchase", "charged_after_trial", "billing_issue", "price_complaint", "other")


def load(name: str):
    return json.loads((DIR / name).read_text())


def table(head: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(" --- " for _ in head) + "|"]
    return "\n".join(lines + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows])


def rejections(log: str) -> Counter:
    kinds = Counter()
    for line in log.splitlines():
        if "submit_ticket rejected:" not in line:
            continue
        reason = line.split("rejected:", 1)[1]
        if "messages_en" in reason:
            kinds["`messages_en` has the wrong number of entries"] += 1
        elif "not valid JSON" in reason:
            kinds["the arguments are not valid JSON"] += 1
        elif "repeats the action" in reason:
            kinds["two options with the same action"] += 1
        elif re.search(r"\b(she|he|her|his|him|pronoun)\b", reason, re.I):
            kinds["a gendered pronoun for the user"] += 1
        elif "words" in reason:
            kinds["a reply over the length limit"] += 1
        else:
            kinds["other schema errors"] += 1
    return kinds


def gather() -> dict:
    with tempfile.TemporaryDirectory() as tmp:  # opening a database can touch it; the published file stays as it is
        copy = Path(tmp) / "demo.sqlite"
        shutil.copyfile(config.DEMO_DB_PATH, copy)
        conn = db.connect(copy)
        now = analytics.last_event(conn)
        week = analytics.compute(conn, "7d", 0, now=now)["period"]
        rows = [dict(r) for r in conn.execute("SELECT id, status, triage_json, decision_json FROM tickets ORDER BY id")]
        insight = json.loads(conn.execute("SELECT insights_json FROM insights").fetchone()[0])
        tools = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT tool, COUNT(*), SUM(allowed) FROM l2_events GROUP BY tool")}
        bot_grants = conn.execute("SELECT COUNT(*), SUM(days) FROM grants WHERE source = 'l2'").fetchone()
        bot_tools = {r[0]: r[1] for r in conn.execute("SELECT tool, COUNT(*) FROM l2_events WHERE session_id LIKE 'bot-%' GROUP BY tool")}
        conn.close()
        gains = []
        for hour in range(0, 24, 2):  # the demo's clock can start at any hour, and the week's edges are calendar days
            shutil.copyfile(config.DEMO_DB_PATH, copy)
            conn = db.connect(copy)
            at = now + timedelta(days=3, hours=hour, minutes=30)
            shift_to_now(conn, at)
            period = analytics.compute(conn, "7d", 0, now=at)["period"]
            gains.append(period["current"]["machine_share"] - period["previous"]["machine_share"])
            conn.close()
    prov = load("provenance.json")
    return {
        "cards": load("cards.json"), "check": load("check.json"), "prov": prov, "tickets": rows, "insight": insight, "week": week, "gains": gains,
        "tools": tools, "bot_tools": bot_tools, "bot_grants": tuple(bot_grants), "edits": load("edits.json"), "read": load("read_sample.json"),
        "measured": load("measured.json"), "log": (DIR / "triage_first_run.log").read_text(),
    }


def render(d: dict) -> str:
    cards = d["cards"]
    prov = d["prov"]
    by_ticket = {p["ticket"]: p for p in prov["tickets"] if p["ticket"]}
    card_of = {c["id"]: c for c in cards}
    triages = {r["id"]: json.loads(r["triage_json"]) for r in d["tickets"]}
    check = d["check"]
    overlap = check["review_overlap"]
    demo = [c for c in cards if c["in_demo"]]
    words = check["user_words"]

    calls = [c for p in by_ticket.values() for c in p["triage"]["calls"]]
    final = [c for c in calls if not c.get("superseded")]
    fingerprints = sorted({c["system_fingerprint"] for c in calls + prov["insights_calls"]})
    response_models = sorted({c["response_model"] for c in calls + prov["insights_calls"]})
    prompt = sum(c["prompt_tokens"] for c in calls)
    completion = sum(c["completion_tokens"] for c in calls)
    cached = sum(c["cached_tokens"] for c in calls)
    latency = statistics.median(c["latency_s"] for c in calls)
    balance = d["measured"]["deepseek_balance_cny"]

    per_pain = {p: Counter() for p in PAINS}
    first = Counter()
    every = Counter()
    grant_risk = Counter()
    confidence = []
    language = Counter()
    wrong_language = []
    for tid, t in triages.items():
        card = card_of[by_ticket[tid]["card"]]
        per_pain[card["real_labels"]["pain"]][t["category"]] += 1
        first[t["options"][0]["action"]] += 1
        for option in t["options"]:
            every[option["action"]] += 1
            if option["action"] == "grant_access":
                grant_risk[option["risk"]] += 1
        confidence.append(t["confidence"])
        language[card["lang"]] += 1
        said = (t.get("user_language") or "").lower()
        expected = {"en": "english", "ru": "russian", "sr": "serbian"}[card["lang"]]
        if not said.startswith(expected):
            wrong_language.append(f"{tid} ({t['user_language']})")

    kinds = rejections(d["log"])
    failures = len(re.findall(r"triage failed:", d["log"]))
    run_tickets = int(re.match(r"(\d+) tickets", d["log"]).group(1))
    verdicts = Counter(d["read"]["verdicts"].values())
    statuses = Counter(r["status"] for r in d["tickets"])
    closed = statuses["done"] + statuses["dismissed"]
    case_sources = Counter(c["provenance"]["case_source"] for c in cards)
    named = sum(v for k, v in case_sources.items() if k != "the complaint's own label")
    public = sum(v for k, v in case_sources.items() if k.startswith("http"))
    current, previous = d["week"]["current"], d["week"]["previous"]
    bot_records = prov["bot_chats"]["records"]
    flows = Counter(r["flow"] for r in bot_records)
    bot_lang = Counter(r["lang"] for r in bot_records)
    edited = sum(1 for p in prov["tickets"] if p["edited_after_writing_by"])
    work = sum(d["bot_tools"].get(k, 0) for k in ("get_profile", "grant_access", "store_instructions"))
    insight = d["insight"]
    source = (ROOT / "scripts" / "build_demo.py").read_text()
    growth = round((float(re.search(r"^BOT_GROWTH = ([\d.]+)", source, re.M).group(1)) - 1) * 100)
    hours = int(re.search(r"^BOT_END = NOW \+ timedelta\(hours=(\d+)\)", source, re.M).group(1))

    crosstab = [[p, sum(per_pain[p].values()), *[per_pain[p][c] or "" for c in CATEGORIES]] for p in PAINS if sum(per_pain[p].values())]
    trace_example = prov["tickets"][0]["ticket"]

    return f"""# What a model makes of 100 believable support tickets

A small study that came out of building the demo data. Generated by `scripts/dataset_study.py`; every number below is computed from the records in `data/dataset/` and `data/demo.sqlite`.

**In short.** {len(cards)} support tickets were written by `{WRITERS}` from invented people and real complaint labels. {len(demo)} of them went through the real triage agent on `{ANALYST}`. In a sample read by `{d['read']['reader']}`, the agent's root cause matched the hidden truth of the case in {verdicts['hit']} of {sum(verdicts.values())} tickets and stopped short in {verdicts['partial']}. It answered {len(wrong_language)} of {len(demo)} tickets in the wrong language and offered free days in {every['grant_access']} of {len(demo)}, whether or not they would help. Its own checks refused a submission {sum(kinds.values())} times, and {failures} of {run_tickets} tickets failed on the first pass. No person judged any of this, and the data is synthetic throughout.

## Where each piece comes from

| Piece | Made by | Made from | Real or synthetic | Record |
| --- | --- | --- | --- | --- |
| The problem behind each ticket | `scripts/dataset_cards.py`, sampling | Labels of real App Store complaints, in the proportions of 1517 labelled complaints. Labels only: `pain`, `wants`, `support`, `escalation`, `solver` | Real labels | `label_ref`: first 12 characters of a hash of the review id |
| The case | `scripts/dataset_cards.py` | {public} tickets: public AdaptySDK GitHub issues. {named - public} tickets: `kb/E-real-cases.md` (not in this repo). {case_sources["the complaint's own label"]} tickets: the complaint label alone | Real cases, retold | `case_source` |
| The person | Hand-written table, `scripts/dataset_faces.py` | Nothing: name, city, age, job and life are invented. One of {len(set((c['style']['habit'], c['style']['tone'], c['style']['opening']) for c in cards))} distinct writing styles each | Synthetic | `face`, `style` |
| The account | `scripts/dataset_cards.py` | Plans and prices of the demo app. Not in Adapty | Synthetic | `account` |
| The words | `{WRITERS}`, 5 agents with 40 tickets each | The card and `data/dataset/writer_brief.md`. The writers never saw a review | Synthetic | `written_by` with batch, brief hash and voice |
| Later edits | `claude-opus-5-5` | {len(d['edits']['edits'])} edits on {edited} tickets: repeated openings, gender of the assistant in Russian and Serbian, odd phrases | Synthetic | `data/dataset/edits.json` |
| The triage of {len(demo)} tickets | The real agent, `resolve/triage.py`, on `{ANALYST}` | The ticket and its synthetic account | Real run on synthetic input | Per call: response id, model, fingerprint, tokens, latency, time |
| The answers on {closed} closed tickets | Chosen and worded by `claude-opus-5-5`, run through `resolve/workflow.py` | The triage's options | **Simulated person** | `person` |
| The analyst's finding | The real agent, `resolve/insights_live.py`, on `{ANALYST}` | The {len(demo)} triaged tickets | Real run on synthetic input | `insights_calls` |
| {len(bot_records)} chats the bot solved and {work} work records | `scripts/build_demo.py` with a fixed seed, from 12 flows of 40 dialogues by `{WRITERS}` | The library in `data/dataset/bot_library.json` | **Synthetic, never run through a model** | `bot_chats.records` |

To follow one ticket back to its sources: `python scripts/dataset_study.py --trace {trace_example}`.

### About the model's name

The name given by the project's owner is `{ANALYST}`. The API rejects that name with an error 400, so the run asked for `deepseek-flash`, and every response came back as {', '.join(f'`{m}`' for m in response_models)}, with system fingerprint {', '.join(f'`{f}`' for f in fingerprints)}. Both names are in the record. The API does not confirm that they are the same model.

## The tickets

{table(['', 'English', 'Russian', 'Serbian', 'All'], [
    ['Written', check['languages']['en'], check['languages']['ru'], check['languages']['sr'], check['tickets']],
    ['In the demo', sum(c['lang'] == 'en' for c in demo), sum(c['lang'] == 'ru' for c in demo), sum(c['lang'] == 'sr' for c in demo), len(demo)],
])}

{check['sources']['chat']} are chats with the in-app assistant that ended in a handoff, {check['sources']['email']} are emails. A ticket's author writes between {words['min']} and {words['max']} words, with a median of {words['median']:.0f}.

**Are they all different?** All {len({p['written_by']['voice'] for p in prov['tickets']})} `voice` notes differ. {len(check['repeated_openings'])} three-word openings occur more than once, none more than {max(check['repeated_openings'].values())} times. {check['pairs_sharing_two_or_more_5grams']} of 19,900 pairs share two or more five-word phrases, the worst pair {check['worst_pairs'][0]['shared_5grams']}, mostly plain phrases such as "{check['worst_pairs'][0]['example']}". This measures repeated wording, not whether the tickets feel like different people.

**Are they copied from reviews?** The writers saw labels, never text. Checked against {overlap['reviews']:,} reviews: {overlap['tickets_with_run']} tickets share a run of {overlap['min_run']} or more words with some review, the longest is {overlap['longest']} words, none reach 8. Only these counts leave the check, and no review text is stored here.

Handoff reasons written by the authors: {', '.join(f'{k} {v}' for k, v in sorted(check['handoff_reasons'].items(), key=lambda x: -x[1]))}. No chat ended in `threat`: the one card with a threat label became an email. The brief did not offer `repeat_after_grant`. So the data does not exercise either reason.

## What the triage agent did

{len(calls)} model calls for {len(triages)} tickets: {prompt:,} prompt tokens, {completion:,} completion tokens, {cached / prompt:.0%} of the prompt served from cache, median call {latency:.1f} s. The DeepSeek balance fell from {balance['before']} to {balance['after']} CNY for the whole job. {len(calls) - len(final)} of these calls were replaced by a second run.

**The first pass failed on {failures} of {run_tickets} tickets** after one correction each; the other {len(triages) - run_tickets} ran earlier as a smoke test. All {failures} passed on a second try. The agent's own checks refused a submission {sum(kinds.values())} times:

{table(['Refused because', 'Times'], kinds.most_common())}

**Its first choice** is what a person sees on top. Refunds are handed to a person most often, which fits a dataset where refunds are the largest handoff reason:

{table(['Action', 'As first option', 'Among all options'], [[a, first[a], every[a]] for a, _ in every.most_common()])}

**Free days as filler.** `grant_access` is among the options in {every['grant_access']} of {len(triages)} tickets, also where it cannot help: f-0012 offers 7 days that start after October 2027. It rates {grant_risk['high']} of these as high risk, {grant_risk['medium']} as medium and {grant_risk['low']} as low.

**Language.** {len(wrong_language)} tickets by authors writing English got replies in another language: {', '.join(wrong_language)}. The other {len(triages) - len(wrong_language)} were right, including all {language['ru']} in Russian and {language['sr']} in Serbian. f-0030 quotes a Spanish bank alert, and f-0068 has a German subject and a "Danke schön" in an English message. These words may be the cause. That is untested.

**Confidence** runs from {min(confidence)} to {max(confidence)}, median {statistics.median(confidence)}. It is not calibrated here: there are no labels to calibrate against.

### Real complaint type against the agent's category

Rows are the label behind the card, which the agent never sees. Columns are the category it chose.

{table(['Label', 'Tickets', *CATEGORIES], crosstab)}

The agent's categories name the request, the labels name the complaint. So `charged_after_cancel` mostly lands on `refund_request`: both are fair. The table shows how the categories are used. It is not a score.

### Reading a sample

{d['read']['how']}

Result: {verdicts['hit']} hit, {verdicts['partial']} partial, 0 wrong.

The partial ones: {'; '.join(f"{k}: {d['read']['notes'][k]}" for k, v in d['read']['verdicts'].items() if v == 'partial')}

Noticed on the way: f-0028: {d['read']['notes']['f-0028']}

**How much to trust this.** The reader is a model, `{d['read']['reader']}`, and it read with the truth in hand. The chats were written by a model that knew the truth, and the first-line assistant in them states facts about the account, so part of each answer is already in the input. The result shows that the agent can use what it is given. It does not show how it does on real tickets.

## What the analyst found

One real run over the {len(triages)} triaged tickets. Finding, in its own words:

> {insight['headline']}

Its proposal: {insight['variant']}

The counts come from code, and the choice of cluster and the proposal come from the model. The flows were assigned on the cards, so this shows the agent working and says nothing about any real app.

## The numbers on the Analyze screen

{statuses['done']} tickets answered, {statuses.get('dismissed', 0)} closed without a reply, {statuses['proposed']} waiting. They are what the screen counts from the real triage. The rest of its numbers come from the synthetic chats, and **their shape was chosen to look good in a demo**:

- {len(bot_records)} chats, all solved, with {work} work records: {d['bot_tools'].get('get_profile', 0)} profile reads, {d['bot_tools'].get('store_instructions', 0)} store instructions, {d['bot_tools'].get('grant_access', 0)} grant attempts ({d['bot_grants'][0]} allowed, {d['bot_grants'][1]} free days).
- Languages: {', '.join(f'{k} {v}' for k, v in bot_lang.most_common())}. Largest flows: {', '.join(f'{k} {v}' for k, v in flows.most_common(3))}.
- Daily volume grows by {growth}% a day, and the chats run on for about {hours} hours after the newest ticket.
- That is why the week shows machines at {current['machine_share']:.1%} against {previous['machine_share']:.1%} the week before. The gain depends on the hour the demo starts: {min(d['gains']) * 100:.1f} to {max(d['gains']) * 100:.1f} points over a day. **The rise was tuned**, not measured. With a gentler curve the same tickets showed a fall.

Read the screen as a demonstration of what it can show, not as a result.

## What this does not show, and what was not tried

- No person judged whether the tickets sound real. No native speaker read the Russian or the Serbian: a model did, and the writing agents said themselves that their checks were partial.
- Only {sum(verdicts.values())} of {len(triages)} triages were read, against the root cause only. The wording of the replies and the store steps inside them were not checked against the stores' current help pages.
- The chats' first-line assistant was written by `{WRITERS}`. The real first-line agent, `resolve/frontline.py`, did not produce these chats.
- The other {len(cards) - len(demo)} tickets were written and checked, and never triaged. They are only in `data/dataset/tickets.json`.
- Each ticket was triaged once, apart from the second tries, so there is no measure of how much answers vary between runs. Only one model was tried.
- {len(triages) - 6} of the tickets were triaged while their accounts held a `country` field that real profiles do not have, removed afterwards. It may have nudged the language. The 6 tickets with the wrong language were run again without it: 3 became correct and 2 did not. The sixth, c-005, was the writer's error: a Spanish ticket on an English card, rewritten in English and run again. The effect on the other tickets is not known.
- No bug reports: the build has no Jira teams configured, so the agent had nowhere to send one and the code drops them. The screen's Jira flow is not shown by this data.
- The accounts are synthetic and their dates are fixed around the tickets, so nothing here tests lookups that fail for real reasons.
- The answers of the {closed} closed tickets were written by a model standing in for a person.

## To repeat it

`scripts/build_demo.py` runs the stages `base`, `bots`, `triage`, `decide`, `insights`, `publish`. Only `triage` and `insights` call a model. `scripts/dataset_check.py` holds the checks on the tickets. Card generation needs the label files, which are not in this repo. See `data/dataset/README.md`.
"""


def trace(key: str) -> int:
    prov = load("provenance.json")
    record = next((p for p in prov["tickets"] if key in (p["card"], p["ticket"])), None)
    if record is None:
        print(f"{key}: no such ticket or card")
        return 1
    card = next(c for c in load("cards.json") if c["id"] == record["card"])
    print(json.dumps({"models": prov["models"], **record, "person_on_the_card": card["face"], "style": card["style"], "truth": card["truth"], "belief": card["belief"]}, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--trace")
    args = parser.parse_args()
    if args.trace:
        return trace(args.trace)
    text = render(gather())
    if args.check:
        if STUDY.read_text() != text:
            print("docs/dataset-study.md is out of date")
            return 1
        return 0
    STUDY.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

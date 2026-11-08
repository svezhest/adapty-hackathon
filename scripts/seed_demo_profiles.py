"""Copy the demo users into the Adapty app of ADAPTY_SECRET_KEY, with the same ids and dates as the team's seeded app.

Every teammate seeded their own Lumen app, and the organisers' seeder puts a random run id into each
customer id (`lumen_<run>_<n>`). Our tickets, hard cases and Level 2 scenarios use the run 443814
(Milos's app). This script replays exactly those users into another app with the organisers' own
code (`seed_adapty.build_users` + `push_user`): same customer ids, same transactions, same dates
(the clock is taken from the cached profiles in data/eval-base.sqlite), plus the server-side grants that
show as `store=adapty` in the cache. Only these ~70 users are written; nothing else in the app changes.

    python scripts/seed_demo_profiles.py              # dry run: what would be written
    python scripts/seed_demo_profiles.py --one        # write one user and read it back
    python scripts/seed_demo_profiles.py --yes        # write all of them
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from resolve import adapty, config  # noqa: E402

RUN_ID = "443814"
USERS_IN_TEAM_SEED = 5000  # the team's seed run used the first 5,000 dataset users


def load_seeder(seed_repo: Path):
    spec = importlib.util.spec_from_file_location("seed_adapty", seed_repo / "seed_adapty.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["seed_adapty"] = module  # dataclasses look the module up while it loads
    spec.loader.exec_module(module)
    return module


def wanted_ids() -> set[str]:
    ids = {t["customer_user_id"] for t in json.loads((ROOT / "data" / "tickets.json").read_text())}
    extra = json.loads((ROOT / "data" / "feedback_extra.json").read_text())
    ids |= {i["customer_user_id"] for i in (extra if isinstance(extra, list) else extra["items"]) if i.get("customer_user_id")}
    scenarios = json.loads((ROOT / "data" / "l2_scenarios.json").read_text())
    ids |= {s["customer_user_id"] for s in (scenarios if isinstance(scenarios, list) else scenarios["scenarios"])}
    return {i for i in ids if i.startswith(f"lumen_{RUN_ID}_") and not i.endswith("99999")}  # 99999 = the "unknown user" case


def cached_profiles() -> dict[str, dict]:
    conn = sqlite3.connect(config.EVAL_DB_PATH)
    return {cid: json.loads(p) for cid, p in conn.execute("SELECT customer_user_id, profile_json FROM tickets WHERE profile_json IS NOT NULL")}


def team_clock(seeder, data: dict, cached: dict[str, dict]) -> datetime:
    """The `now` of the team's seed run, from one cached profile: cached purchased_at minus the dataset offset."""
    probe = seeder.build_users(data, USERS_IN_TEAM_SEED, RUN_ID, datetime.fromtimestamp(0, tz=timezone.utc))
    by_id = {u.cuid: u for u in probe}
    for cid, profile in cached.items():
        for sub in profile.get("subscriptions", []):
            user = by_id.get(cid)
            tx = next((t for t in (user.transactions if user else []) if t["store_transaction_id"] == sub.get("store_transaction_id")), None)
            if tx and sub.get("purchased_at"):
                offset = adapty.parse_datetime(tx["purchased_at"]) - datetime.fromtimestamp(0, tz=timezone.utc)
                return adapty.parse_datetime(sub["purchased_at"]) - offset
    raise SystemExit("could not align with the cached profiles (no matching transaction id)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-repo", type=Path, default=Path.home() / "Projects" / "adapty-hack-seed")
    parser.add_argument("--yes", action="store_true", help="write all users")
    parser.add_argument("--one", action="store_true", help="write one user and read it back")
    args = parser.parse_args()

    seeder = load_seeder(args.seed_repo)
    data = seeder.load_dataset()
    cached = cached_profiles()
    now0 = team_clock(seeder, data, cached)
    ids = wanted_ids()
    users = [u for u in seeder.build_users(data, USERS_IN_TEAM_SEED, RUN_ID, now0) if u.cuid in ids]
    grants = [
        (cid, level)
        for cid, profile in cached.items()
        if cid in ids
        for level in profile.get("access_levels", [])
        # server-side grants, and lifetime levels: an app whose lifetime product is not linked to the
        # access level gets the purchase but no level, so the level is granted (no expiry) instead
        if level.get("store") == "adapty" or level.get("expires_at") is None
    ]
    print(f"team clock {now0:%Y-%m-%d %H:%M:%S}Z; {len(users)} users, {sum(len(u.transactions) for u in users)} transactions, {len(grants)} server-side grants")
    missing = ids - {u.cuid for u in users}
    if missing:
        print(f"not in the dataset (skipped): {sorted(missing)}")

    if args.one:
        users = [next(u for u in users if u.cuid in cached)]
        grants = []
    elif not args.yes:
        print("dry run: pass --one to try one user, --yes to write all")
        return

    key = config.adapty_secret()
    api = seeder.Api(key, dry_run=False)
    for n, user in enumerate(users, 1):
        seeder.push_user(api, None, user)
        print(f"  {n}/{len(users)} {user.cuid}: {len(user.transactions)} tx", flush=True)
    client = adapty.Client(key)
    for cid, level in grants:
        client.grant_access_level(cid, level["access_level_id"], adapty.parse_datetime(level.get("expires_at")))
        print(f"  grant {cid}: {level['access_level_id']} until {level.get('expires_at')}")
    print("stats:", dict(api.stats), "errors:", dict(api.errors))

    if args.one:
        user = users[0]
        live = client.get_profile(user.cuid)
        want = cached[user.cuid]
        pick = lambda p: [(a["access_level_id"], a.get("store_product_id"), a.get("expires_at")) for a in p.get("access_levels", [])]  # noqa: E731
        print("live  :", pick(live))
        print("cached:", pick(want))


if __name__ == "__main__":
    main()

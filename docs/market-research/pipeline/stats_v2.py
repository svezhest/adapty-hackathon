"""Merge haiku labels (pass 1 coarse, pass 2 detailed) with review metadata and compute v2 report stats."""
import json, glob, collections
U = {f'{r["app_id"]}:{r["rid"]}': r for r in json.load(open("reviews_clean.json"))}
lab = {}
for fn in glob.glob("hk/out/*.tsv"):
    for line in open(fn):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2: lab[p[0]] = (p[1], p[2] if len(p) > 2 else "-")
det = {}
F = ["pain", "wants", "support", "escalation", "plan", "amount", "solver"]
for fn in glob.glob("hk2/out/*.tsv"):
    for line in open(fn):
        p = line.rstrip("\n").split("\t")
        if len(p) == 8: det[p[0]] = dict(zip(F, p[1:]))
rows = list(U.values())
for k, r in U.items():
    r["c"] = lab.get(k, ("O", "-"))[0] if r["rating"] <= 3 else "+"
    r["d"] = det.get(k)
    if r["d"] and r["d"]["pain"] == "not_purchase": r["c"] = "O"; r["d"] = None
PUR = set("ABCD")
pur = [r for r in rows if r["c"] in PUR]
neg = [r for r in rows if r["rating"] <= 3]
def share(rs): return round(100 * sum(r["c"] in PUR for r in rs) / max(1, len(rs)), 1)
out = {"n": len(rows), "neg": len(neg), "pur": len(pur), "pur_all_pct": share(rows), "pur_neg_pct": share(neg),
       "coarse_neg": collections.Counter(r["c"] for r in neg)}
out["tier"] = {t: share([r for r in rows if r["tier"] == t]) for t in ("top", "mid")}
G = sorted({r["genre"] for r in rows})
apps = collections.defaultdict(list)
for r in rows: apps[r["app"]].append(r)
appshare = {a: (share(rs), rs[0]["genre"], rs[0]["tier"]) for a, rs in apps.items()}
out["genre"] = {g: dict(share=share([r for r in rows if r["genre"] == g]),
                        apps=sum(1 for v in appshare.values() if v[1] == g),
                        apps3=sum(1 for v in appshare.values() if v[1] == g and v[0] >= 3)) for g in G}
out["apps_ge"] = {th: sum(v[0] >= th for v in appshare.values()) for th in (1, 3, 5, 10)}
out["n_apps"] = len(appshare)
out["top_apps"] = sorted(((v[0], a, v[1], v[2]) for a, v in appshare.items()), reverse=True)[:8]
# #1 complaint among 1-3* (coarse, excluding O)
first = 0
for a, rs in apps.items():
    c = collections.Counter(r["c"] for r in rs if r["rating"] <= 3 and r["c"] not in ("O", "+"))
    if c:
        if sum(c[x] for x in PUR) >= c["P"]: first += 1
out["purchase_beats_price"] = first
D = [r["d"] for r in pur if r["d"]]
out["detailed_n"] = len(D)
for f in ("pain", "wants", "support", "escalation", "plan", "solver"):
    out[f] = collections.Counter(d[f] for d in D).most_common()
am = [float(d["amount"]) for d in D if d["amount"] not in ("-", "") and d["amount"].replace(".", "", 1).isdigit()]
am = sorted(a for a in am if 0 < a < 2000)
out["amount"] = dict(n=len(am), median=am[len(am)//2] if am else None, mean=round(sum(am)/len(am), 2) if am else None)
out["flag_S"] = sum("S" in lab.get(k, ("", ""))[1] for k in U)
json.dump(out, open("stats_v2.json", "w"), indent=1, ensure_ascii=False)
for k, v in out.items(): print(k, v)

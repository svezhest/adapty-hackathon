#!/bin/bash
# Pass 1: coarse labels for all 1-3* reviews; pass 2: detailed pain fields for purchase-problem reviews (A-D)
cd "$(dirname "$0")/.."
ls hk/in/*.jsonl | xargs -P 6 -n 1 hk/run_one.sh
echo "PASS1 $(ls hk/out/*.tsv | wc -l) files, $(cat hk/out/*.tsv | wc -l) lines"
python3 -I - <<'PY'
import json, glob
lab = {}
for fn in glob.glob("hk/out/*.tsv"):
    for line in open(fn):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2: lab[p[0]] = p[1]
rows = [json.loads(l) for fn in sorted(glob.glob("hk/in/*.jsonl")) for l in open(fn)]
sel = [r for r in rows if lab.get(r["i"]) in ("A", "B", "C", "D")]
for k in range(0, len(sel), 100):
    open(f"hk2/in/{k//100:03d}.jsonl", "w").write("\n".join(json.dumps(r, ensure_ascii=False) for r in sel[k:k+100]) + "\n")
print("PASS2 input", len(sel))
PY
ls hk2/in/*.jsonl | xargs -P 6 -n 1 hk2/run_one.sh
echo "PASS2 $(cat hk2/out/*.tsv | wc -l) lines"
echo ALLDONE

#!/bin/bash
# Label one batch with haiku; retry once if the output line count doesn't match the input
f="$1"; b=$(basename "$f" .jsonl); out="hk2/out/$b.tsv"
for attempt in 1 2; do
  [ -s "$out" ] && [ "$(wc -l < "$out")" -eq "$(wc -l < "$f")" ] && exit 0
  claude -p --model haiku --system-prompt "$(cat hk2/system.txt)" --no-session-persistence \
    --disallowedTools "Bash Read Write Edit Glob Grep WebFetch WebSearch Task" < "$f" 2>/dev/null | awk -F'\t' 'NF==8' > "$out.tmp" && mv "$out.tmp" "$out"
done
echo "MISMATCH $b $(wc -l < "$out") vs $(wc -l < "$f")"

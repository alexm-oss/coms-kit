#!/usr/bin/env bash
# coms-kit hook: coms Stop hook for Claude Code.
#
# In a repo marked by `coms guard`, a window may not go idle while coms messages
# it has not seen are waiting. On Stop with unseen messages it prints them to
# stderr, marks them seen (so the next Stop passes: it blocks once per batch),
# and exits 2, which hands the messages back to the agent. If it cannot mark
# them seen, it lets the stop through rather than refuse every time. Silent
# elsewhere.

input=$(cat 2>/dev/null)
[ -z "$input" ] && exit 0

cwd=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
[ -z "$cwd" ] || [ -z "$sid" ] && exit 0

common=$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
common=${common%/}
if [ ! -f "$common/coms-enabled" ]; then
  hooks_dir=$(git -C "$cwd" rev-parse --path-format=absolute --git-path hooks 2>/dev/null) || exit 0
  grep -q "coms guard hook" "$hooks_dir/pre-commit" 2>/dev/null || exit 0
fi

holder="claude-${sid:0:8}"
state="${COMS_DIR:-$HOME/.claude/coms}"
inbox="$state/inbox/$holder.jsonl"
[ -f "$inbox" ] || exit 0

seen_dir="$state/inbox-seen"
mkdir -p "$seen_dir" 2>/dev/null
seen_file="$seen_dir/$holder"
total=$(wc -l < "$inbox" 2>/dev/null | tr -d ' ')
seen=$(cat "$seen_file" 2>/dev/null | tr -d ' ')
case "$seen" in ''|*[!0-9]*) seen=0 ;; esac
[ "${total:-0}" -le "$seen" ] && exit 0

new=$((total - seen))
show=$new; [ "$show" -gt 5 ] && show=5
# Mark this batch seen before refusing. If that cannot be recorded, let the
# stop through: refusing without a watermark would refuse on every stop.
{ printf '%s' "$total" > "$seen_file"; } 2>/dev/null || exit 0
{
  echo "[coms] STOP REFUSED ONCE: $new unread message(s) for $holder. Read them below, act on anything actionable (a hold, a go, a change of plan), answer the sender if asked, then finish. This will not block again for these messages."
  [ "$new" -gt "$show" ] && echo "[coms] ($((new - show)) older unread not shown, run coms inbox)"
  tail -n "$show" "$inbox" | python3 -c '
import sys, json, time
for line in sys.stdin:
    try:
        m = json.loads(line)
        ts = time.strftime("%H:%M", time.localtime(m.get("ts", 0)))
        txt = m.get("text", "")
        if len(txt) > 900: txt = txt[:900] + " ...[truncated, coms inbox has it all]"
        print("  [%s] %s: %s" % (ts, m.get("from", "?"), txt))
    except Exception:
        pass
' 2>/dev/null
} >&2
exit 2

"""Compare an operator-supplied weekly Codex usage percentage with a pace line."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

# Keep installed skill imports from creating bytecode beside the scripts.
sys.dont_write_bytecode = True

from operator_state import number, positive, read_json, reset_time, watch_dir, write_json  # noqa: E402


def reading(source):
    data = json.load(sys.stdin) if source == "-" else read_json(Path(source))
    if not isinstance(data, dict) or not number(data.get("used_percent"), 0, 100):
        raise ValueError("expected a numeric used_percent from 0 to 100")
    return data["used_percent"], reset_time(data["resets_at"])


def prior_state(path):
    try:
        state = read_json(path)
    except FileNotFoundError:
        return {}
    except (UnicodeError, ValueError, RecursionError):
        return {}
    if (isinstance(state, dict) and number(state.get("started_at"), 0, 253402300799)
            and number(state.get("start_used"), 0, 100)
            and number(state.get("resets_at"), 0, 253402300799)
            and state.get("verdict") in ("ON PACE", "OVER PACE", "PLAN USED", "READING EXPIRED")):
        return state
    return {}


def evaluate(state, used, reset, now, args):
    expired = now >= reset
    renewal = bool(state and state["resets_at"] != reset and not expired)
    if not state or renewal:
        state = {"started_at": now, "start_used": used, "resets_at": reset}
    line = min(100, state["start_used"] + args.rate * max(0, now - state["started_at"]) / 3600)
    if expired:
        verdict = "READING EXPIRED"
    elif used >= 100:
        verdict = "PLAN USED"
    elif used - line >= args.over_by:
        verdict = "OVER PACE"
    else:
        verdict = "ON PACE"
    previous = state.get("verdict")
    state["verdict"] = verdict
    return state, verdict, line, renewal, verdict != previous


def main():
    parser = argparse.ArgumentParser(prog="codex-pace-watch.sh", description=__doc__)
    parser.add_argument("--usage-file", default=os.environ.get("CODEX_WEEKLY_USAGE_FILE"),
                        help='JSON file with "used_percent" and "resets_at"; use - with --once for stdin')
    parser.add_argument("--state", type=Path, default=watch_dir() / "codex-pace-state.json",
                        help="override the local baseline/state file")
    parser.add_argument("--rate", type=positive, default="1", help="pace-line percentage points/hour (default 1)")
    parser.add_argument("--over-by", type=positive, default="4", help="over-pace threshold in points (default 4)")
    parser.add_argument("--interval", type=positive, default="300", help="poll interval in seconds (default 300)")
    parser.add_argument("--once", action="store_true", help="read one sample, print the verdict and exit")
    args = parser.parse_args()
    if args.interval > 86400 or args.rate > 100 or args.over_by > 100:
        parser.error("interval must be at most 86400 and rate and threshold at most 100")
    if not args.usage_file:
        parser.error("supply --usage-file or set CODEX_WEEKLY_USAGE_FILE")
    if args.usage_file == "-" and not args.once:
        parser.error("reading from stdin requires --once")
    if args.usage_file != "-" and Path(args.usage_file).resolve() == args.state.resolve():
        parser.error("the input and state paths must differ")

    try:
        state = prior_state(args.state)
    except OSError:
        print("codex-pace-watch.sh: baseline unavailable; starting fresh", file=sys.stderr)
        state = {}
    unavailable = False
    while True:
        try:
            used, reset = reading(args.usage_file)
        except (KeyError, OSError, UnicodeError, ValueError, OverflowError, RecursionError):
            if not unavailable:
                print("codex-pace-watch.sh: usage sample unavailable or invalid", file=sys.stderr, flush=True)
            unavailable = True
            if args.once:
                return 2
        else:
            state, verdict, line, renewal, changed = evaluate(state, used, reset, time.time(), args)
            if args.once or renewal or (changed and verdict != "ON PACE"):
                prefix = "NEW WINDOW | " if renewal else ""
                print(f"{prefix}{verdict} | used {used:g}% | pace line {line:.1f}%", flush=True)
            if changed or renewal:
                try:
                    write_json(args.state, state)
                except OSError:
                    print("codex-pace-watch.sh: could not save baseline", file=sys.stderr, flush=True)
            unavailable = False
            if args.once:
                return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

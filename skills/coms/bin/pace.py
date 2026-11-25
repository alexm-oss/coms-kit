#!/usr/bin/env python3
"""Advise how many Claude lanes to run against a five-hour usage window."""

import argparse
import datetime
import os
from pathlib import Path
import sys
import time

# Keep installed skill imports from creating bytecode beside the scripts.
sys.dont_write_bytecode = True

from operator_state import number, read_json, reset_time, watch_dir, write_json  # noqa: E402

HANDOVER_GUIDE = Path(__file__).resolve().parent.parent / "references" / "handover.md"
LEVELS = (
    "carry new work in the other pool",
    "2 Claude lanes",
    "3 Claude lanes",
    "4 Claude lanes",
    "5 Claude lanes",
    "6 Claude lanes",
)


def percent(value):
    result = float(value)
    if not number(result, 0, 100):
        raise argparse.ArgumentTypeError("expected a percentage from 0 to 100")
    return result


def load_state(path, start_level):
    try:
        state = read_json(path)
        if (isinstance(state, dict) and number(state.get("end"), 0, 253402300799)
                and type(state.get("level")) is int and 0 <= state["level"] < len(LEVELS)
                and isinstance(state.get("samples"), list)):
            state["samples"] = [
                item for item in state["samples"] if isinstance(item, dict)
                and number(item.get("t"), 0, 253402300799) and number(item.get("u"), 0, 100)
            ]
            return state
    except FileNotFoundError:
        return {"level": start_level, "samples": []}
    except (UnicodeError, ValueError, RecursionError):
        pass
    print("pace.py: invalid pacing state; starting fresh", file=sys.stderr)
    return {"level": start_level, "samples": []}


def advise(state, used, end, now, target):
    level = state["level"]
    samples = state["samples"] if state.get("end", end) == end else []
    samples = sorted((s for s in samples if now - 3600 <= s["t"] <= now), key=lambda s: s["t"])
    previous = next((s for s in reversed(samples) if now - s["t"] >= 300), None)
    minutes = max(0, (end - now) / 60)
    required = max(0, target - used) / max(minutes, 1) * 60
    measured = None
    if previous and used >= previous["u"]:
        measured = (used - previous["u"]) / ((now - previous["t"]) / 3600)
    if now >= end:
        verdict = "AFTER"
    elif used >= 98:
        verdict = "HANDOVER"
    elif used >= target or (measured is not None and measured > required * 1.3):
        verdict, level = "LOWER", max(0, level - 1)
    elif measured is not None and measured < required * 0.7:
        verdict, level = "RAISE", min(len(LEVELS) - 1, level + 1)
    else:
        verdict = "HOLD"
    result = {"level": level, "end": end, "samples": (samples + [{"t": now, "u": used}])[-1000:]}
    return result, verdict, minutes, required, measured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("used", type=percent, help="percentage from a usage display or trusted adapter")
    parser.add_argument("resets_at", nargs="?", help="timezone-aware ISO reset time; required on first use")
    parser.add_argument("--target", type=percent, default=os.environ.get("PACE_TARGET", "95"),
                        help="target percent at reset (default 95 or PACE_TARGET)")
    parser.add_argument("--state", type=Path, default=os.environ.get("PACE_STATE") or watch_dir() / "pace-state.json",
                        help="override the local sample file")
    parser.add_argument("--start-level", type=int, choices=range(len(LEVELS)), default=3,
                        help="starting width level (0-5; default 3)")
    args = parser.parse_args()
    if not 0 <= args.start_level < len(LEVELS):
        parser.error("--start-level must be an integer from 0 to 5")
    try:
        state = load_state(args.state, args.start_level)
        if args.resets_at is None and "end" not in state:
            parser.error("pass the window reset time on the first call")
        end = reset_time(args.resets_at) if args.resets_at is not None else state["end"]
    except (OSError, ValueError, OverflowError):
        parser.error("unable to read pacing state or parse reset time (include the timezone)")

    result, verdict, minutes, required, measured = advise(state, args.used, end, time.time(), args.target)
    try:
        write_json(args.state, result)
    except OSError:
        print("pace.py: could not save local pacing state", file=sys.stderr)
    rate = "unknown" if measured is None else f"{measured:.1f}%/h"
    reset = datetime.datetime.fromtimestamp(end, datetime.timezone.utc).strftime("%H:%M UTC")
    level = result["level"]
    print(f"used {args.used:g}% | {minutes:.0f} min to {reset} | required {required:.1f}%/h | "
          f"measured {rate} | {verdict} | level {level} ({LEVELS[level]})")
    if verdict == "HANDOVER":
        print(f"Open the handover guide: {HANDOVER_GUIDE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

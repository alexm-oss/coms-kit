#!/usr/bin/env python3
"""Summarize recent Codex activity. Reads Codex app files; may break on Codex updates."""

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

# Keep installed skill imports from creating bytecode beside the scripts.
sys.dont_write_bytecode = True

from codex_local import age_seconds, codex_home, describe, read_summary, rollout_paths  # noqa: E402
from operator_state import positive  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("minutes", nargs="?", type=positive, default="15", help="lookback in minutes (default 15)")
    parser.add_argument("--codex-home", type=Path, default=codex_home(),
                        help="Codex state directory (default CODEX_HOME or ~/.codex)")
    args = parser.parse_args()
    now = time.time()
    summaries = []
    unavailable = 0
    for stamp, path in rollout_paths(args.codex_home):
        if now - stamp > args.minutes * 60:
            break
        try:
            summaries.append((stamp, read_summary(path)))
        except OSError:
            unavailable += 1
    kinds = Counter(summary["kind"] for _, summary in summaries)
    print(f"Rollouts within {args.minutes:g} min | roots {kinds['root']} | helpers {kinds['helper']} | "
          f"unknown {kinds['unknown']} | unavailable {unavailable}")
    children = Counter(row["parent"] for _, row in summaries if row["kind"] == "helper" and row["parent"])
    roots = {summary["id"] for _, summary in summaries if summary["kind"] == "root" and summary["id"]}
    ungrouped = sum(count for parent, count in children.items() if parent not in roots) + (
        kinds["helper"] - sum(children.values()))
    for index, (stamp, summary) in enumerate(summaries, start=1):
        helpers = f" | recent helpers {children[summary['id']]}" if summary["kind"] == "root" else ""
        print(f"{index}. {summary['kind']} | wrote {age_seconds(stamp, now)}s ago | {describe(summary)}{helpers}")
    if ungrouped:
        print(f"Helpers without a recent root | {ungrouped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

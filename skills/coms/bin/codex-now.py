#!/usr/bin/env python3
"""Summarize recent Codex threads. Reads Codex app files; may break on Codex updates."""

import argparse
from pathlib import Path
import re
import sys
import time

# Keep installed skill imports from creating bytecode beside the scripts.
sys.dont_write_bytecode = True

from codex_local import age_seconds, codex_home, describe, matches_fragment  # noqa: E402
from codex_local import read_summary, rollout_paths  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("threads", nargs="*", help="literal ID fragments; default to the newest rollout")
    parser.add_argument("--codex-home", type=Path, default=codex_home(),
                        help="Codex state directory (default CODEX_HOME or ~/.codex)")
    args = parser.parse_args()
    if any(not re.fullmatch(r"[0-9A-Fa-f-]{4,36}", item) for item in args.threads):
        parser.error("use ID fragments of 4 to 36 hex characters/dashes")
    paths = rollout_paths(args.codex_home, include_archive=bool(args.threads))
    if not paths:
        print("No Codex rollouts found.")
        return 1 if args.threads else 0
    now = time.time()
    result = 0
    for index, item in enumerate(args.threads or [None], start=1):
        selected = [entry for entry in paths if item is None or matches_fragment(entry[1], item)]
        if item is not None and len(selected) > 1:
            print(f"Selection {index}: ambiguous fragment; use a longer ID fragment.", file=sys.stderr)
        elif not selected:
            print(f"Selection {index}: no matching rollout.", file=sys.stderr)
        else:
            stamp, path = selected[0]
            try:
                summary = read_summary(path)
            except OSError:
                print(f"Selection {index}: rollout unavailable.", file=sys.stderr)
            else:
                print(f"Selection {index} ({summary['kind']}) | wrote {age_seconds(stamp, now)}s ago | "
                      f"{describe(summary)}")
                continue
        result = 1
    return result


if __name__ == "__main__":
    sys.exit(main())

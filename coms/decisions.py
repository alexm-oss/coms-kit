"""The decision log: the human's answers, shared by every agent on this machine.

When the human answers a question in one agent's window, that agent records the
answer here, and every other agent checks here before asking the same thing
again. Each answer is one JSON line in <coms dir>/decisions.jsonl with six keys:
ts, by (the recording agent's holder id), repo, topic, answer and quote (the
human's exact words). Nothing is rewritten: answering a topic again appends a
newer line, and the newest line for a topic is its current answer.
"""

import json
import math
import os
import sys
import time

from coms.mutex import FileMutex

FIELDS = ("ts", "by", "repo", "topic", "answer", "quote")
TEXT_FIELDS = ("topic", "answer", "quote")


def _text(s):
    """Text that encodes as UTF-8: a lone surrogate (an undecodable byte)
    becomes U+FFFD replacement characters."""
    return s.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def topic_key(topic):
    """Topics match ignoring case and surrounding spaces."""
    return topic.strip().lower()


class DecisionLog:
    def __init__(self, coms_dir, holder_id="", clock=time.time):
        self.path = os.path.join(coms_dir, "decisions.jsonl")
        self.holder_id = holder_id
        self.clock = clock
        # The lock coms already takes for leases. A long quote can exceed the size
        # an O_APPEND write is sure not to interleave at, so appends take turns.
        self._mutex = FileMutex(os.path.join(coms_dir, "locks", "coord.lock"))

    def record(self, topic, answer, quote, repo=""):
        """Append one answer and return it. ValueError if topic, answer or quote is blank."""
        topic, answer, quote = (_text(s).strip() for s in (topic, answer, quote))
        if not (topic and answer and quote):
            raise ValueError("a decision needs a topic, an answer and the human's words (--quote)")
        entry = {"ts": self.clock(), "by": self.holder_id, "repo": repo,
                 "topic": topic, "answer": answer, "quote": quote}
        line = json.dumps(entry) + "\n"
        self._mutex.acquire(timeout=None)
        try:
            with open(self.path, "ab+") as fh:
                # a crash mid-write can leave a last line with no newline: start a new line after it
                size = fh.seek(0, os.SEEK_END)
                if size:
                    fh.seek(size - 1)
                    if fh.read(1) != b"\n":
                        line = "\n" + line
                fh.write(line.encode("utf-8"))
        finally:
            self._mutex.release()
        return entry

    def entries(self):
        """Every readable answer, oldest first. Lines that are not one are skipped."""
        try:
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except FileNotFoundError:
            return []
        out = []
        for ln in lines:
            try:
                e = json.loads(ln)
            except (ValueError, RecursionError):
                continue
            if not isinstance(e, dict):
                continue
            if not all(isinstance(e.get(k), str) and e[k].strip() for k in TEXT_FIELDS):
                continue
            ts = e.get("ts")
            if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                continue
            # a time format_line can print: 1970 to the end of year 9999, never NaN or Infinity
            if not (0 <= ts < 253402300800 and math.isfinite(ts)):
                continue
            e["by"] = e["by"] if isinstance(e.get("by"), str) else "?"
            e["repo"] = e["repo"] if isinstance(e.get("repo"), str) else ""
            for k in TEXT_FIELDS + ("by", "repo"):
                e[k] = _text(e[k])
            out.append(e)
        return out


def select(entries, text="", include_replaced=False):
    """What `coms decisions` shows: (entry, is_current) pairs, newest first.

    A topic is shown when every word of text appears, ignoring case, in its topic
    or in any of its answers or quotes, so an old wording still finds it. Only a
    topic's current (newest) answer is returned unless include_replaced is set.
    """
    words = text.lower().split()
    history = {}
    for e in entries:
        history.setdefault(topic_key(e["topic"]), []).append(e)
    shown = set()
    for key, recs in history.items():
        haystack = " ".join(r[f] for r in recs for f in TEXT_FIELDS).lower()
        if all(w in haystack for w in words):
            shown.add(key)
    rows = []
    for e in reversed(entries):
        key = topic_key(e["topic"])
        if key not in shown:
            continue
        current = e is history[key][-1]
        if current or include_replaced:
            rows.append((e, current))
    return rows


def format_line(entry, current=True):
    """One answer on one line: topic: answer (when, who in repo) "quote"."""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["ts"]))
    who = f"{entry['by']} in {entry['repo']}" if entry["repo"] else entry["by"]
    line = f'{entry["topic"]}: {entry["answer"]} ({when}, {who}) "{entry["quote"]}"'
    line = " ".join(line.split())  # a pasted multi-line answer still prints as one line
    return line if current else "(replaced) " + line


def cmd_decide(args, coms_dir, holder, repo):
    try:
        entry = DecisionLog(coms_dir, holder).record(
            args.topic, " ".join(args.answer), args.quote, repo=repo)
    except ValueError as exc:
        print(f"coms: {exc}", file=sys.stderr)
        return 2
    print(f"coms: recorded decision on {entry['topic']}")
    return 0


def cmd_decisions(args, coms_dir):
    entries = DecisionLog(coms_dir).entries()
    text = " ".join(args.text)
    rows = select(entries, text, include_replaced=args.all)
    if args.json:
        print(json.dumps([e for e, _ in rows], indent=2))
    elif rows:
        for e, current in rows:
            print(format_line(e, current))
    elif entries:
        print(f"coms: no decisions match {text!r}")
    else:
        print("coms: no decisions recorded")
    return 0


def add_parsers(sub):
    d = sub.add_parser("decide", help="record the human's answer so no agent asks again")
    d.add_argument("topic", help="a short name another agent would search for")
    d.add_argument("answer", nargs="+", help="the decision, in a few words")
    d.add_argument("--quote", required=True, help="the human's exact words")
    ds = sub.add_parser("decisions", help="list the human's recorded answers, newest first")
    ds.add_argument("text", nargs="*", help="only topics that mention every word")
    ds.add_argument("--all", action="store_true", help="also show answers since replaced")
    ds.add_argument("--json", action="store_true")

"""The decision log: the human's answers, shared by every agent on this machine.

When the human answers a question in one agent's window, that agent records the
answer here, and every other agent checks here before asking the same thing
again. Each answer is one JSON line in <coms dir>/decisions.jsonl with six required keys:
ts, by (the recording agent's holder id), repo, topic, answer and quote (the
human's exact words). Nothing is rewritten: answering a topic again appends a
newer line, and the newest line for a topic is its current answer. Optional
supersedes and expires_at metadata affect only the explicit current view.
"""

from datetime import datetime
import json
import math
import os
import re
import sys
import time

from coms.mutex import FileMutex
from coms.store import Store

FIELDS = ("ts", "by", "repo", "topic", "answer", "quote")
TEXT_FIELDS = ("topic", "answer", "quote")
FLAG = "-" * 2


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

    def record(self, topic, answer, quote, repo="", supersedes=None, expires_at=None):
        """Append one answer and return it. ValueError if topic, answer or quote is blank."""
        topic, answer, quote = (_text(s).strip() for s in (topic, answer, quote))
        if not (topic and answer and quote):
            raise ValueError("a decision needs a topic, an answer and the human's words (--quote)")
        entry = {"ts": self.clock(), "by": self.holder_id, "repo": repo,
                 "topic": topic, "answer": answer, "quote": quote}
        if supersedes is not None:
            if not isinstance(supersedes, str) or not supersedes.strip():
                raise ValueError("supersedes needs a nonblank old topic")
            supersedes = _text(supersedes).strip()
            if topic_key(supersedes) == topic_key(topic):
                raise ValueError("supersedes must name a different topic")
            entry["supersedes"] = supersedes
        if expires_at is not None:
            if not valid_expiry(expires_at):
                raise ValueError("expires_at must be a finite Unix timestamp from 1970 to year 9999")
            entry["expires_at"] = expires_at
        self._mutex.acquire(timeout=None)
        try:
            if supersedes is not None and not any(
                    topic_key(e["topic"]) == topic_key(supersedes) for e in self.entries()):
                raise ValueError("supersedes must name an already recorded topic")
            line = json.dumps(entry) + "\n"
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


def valid_expiry(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and 0 <= value < 253402300800 and math.isfinite(value))


def parse_expiry(value):
    """ISO timestamp with explicit offset. Never interpret natural-language dates."""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("missing offset")
        result = stamp.timestamp()
        if not valid_expiry(result):
            raise ValueError("outside range")
        return result
    except (ValueError, OverflowError) as exc:
        raise ValueError("expires-at needs an ISO timestamp with timezone offset, e.g. 2026-10-12T09:00:00-04:00") from exc


def select_current(entries, prefix="", now=None):
    """Latest appended ruling per topic, explicit replacement links and expiry.

    Walk the whole log before prefix filtering. Links retire only earlier records;
    later rulings can explicitly re-establish a retired topic. Links survive a
    same-topic update or expiration of the replacement. No old answer resurrects.
    Invalid optional metadata has no effect, preserving legacy readable records.
    """
    now = time.time() if now is None else now
    prefix = topic_key(prefix)
    seen, retired, rows = set(), set(), []
    for entry in reversed(entries):
        key = topic_key(entry["topic"])
        expiry = entry.get("expires_at")
        expired = valid_expiry(expiry) and expiry <= now
        if key not in seen and key not in retired and not expired and key.startswith(prefix):
            rows.append((entry, True))
        seen.add(key)
        target = entry.get("supersedes")
        if isinstance(target, str) and target.strip() and topic_key(target) != key:
            retired.add(topic_key(target))
    return rows


def format_line(entry, current=True):
    """One answer on one line: topic: answer (when, who in repo) "quote"."""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["ts"]))
    who = f"{entry['by']} in {entry['repo']}" if entry["repo"] else entry["by"]
    line = f'{entry["topic"]}: {entry["answer"]} ({when}, {who}) "{entry["quote"]}"'
    line = " ".join(line.split())  # a pasted multi-line answer still prints as one line
    return line if current else "(replaced) " + line


def cmd_decide(args, coms_dir, holder, repo):
    target = args.notify_holder
    if target is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", target):
        print("coms: notify holder must be a holder id using letters, digits, dots, underscores or hyphens", file=sys.stderr)
        return 2
    try:
        entry = DecisionLog(coms_dir, holder).record(
            args.topic, " ".join(args.answer), args.quote, repo=repo,
            supersedes=args.supersedes,
            expires_at=parse_expiry(args.expires_at) if args.expires_at is not None else None)
    except ValueError as exc:
        print(f"coms: {exc}", file=sys.stderr)
        return 2
    if target is not None:
        try:
            Store(coms_dir, holder).send_message(
                target, f"ANSWERED {entry['topic']}: {entry['answer']}")
        except OSError as exc:
            print(f"coms: recorded decision on {entry['topic']} but could not notify {target}: {exc}", file=sys.stderr)
            return 1
    print(f"coms: recorded decision on {entry['topic']}")
    return 0


def cmd_decisions(args, coms_dir):
    entries = DecisionLog(coms_dir).entries()
    text = " ".join(args.text)
    rows = (select_current(entries, text) if args.current
            else select(entries, text, include_replaced=args.all))
    if args.json:
        records = [e for e, _ in rows]
        if not args.current:
            records = [{k: v for k, v in e.items() if k not in ("supersedes", "expires_at")}
                       for e in records]
        print(json.dumps(records, indent=2))
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
    d.add_argument("--notify-holder", help="also send the answer to this holder's inbox")
    d.add_argument(FLAG + "supersedes", help="explicitly replace an existing different topic")
    d.add_argument(FLAG + "expires-at", help="temporary ruling expiry, ISO timestamp with timezone offset")
    ds = sub.add_parser("decisions", help="list the human's recorded answers, newest first")
    ds.add_argument("text", nargs="*", help="only topics that mention every word")
    view = ds.add_mutually_exclusive_group()
    view.add_argument(FLAG + "all", action="store_true", help="also show answers since replaced")
    view.add_argument(FLAG + "current", action="store_true", help="current rulings only, optional topic prefix")
    ds.add_argument("--json", action="store_true")

"""Candidate shared reader/writer for private pending owner questions.

This module does not grant
permission to perform any action described by an answer.
"""

import json
import math
import os
import stat
import sys
import time
from pathlib import Path

from coms.identity import valid_holder
from coms.mutex import FileMutex

QUEUE_FIELDS = frozenset(("version", "ts", "by", "repo", "topic", "question"))
DECISION_FIELDS = frozenset(("ts", "by", "repo", "topic", "answer", "quote"))
DECISION_OPTIONAL_FIELDS = frozenset(("supersedes", "expires_at"))
SHOW_FIELDS = frozenset(("version", "topic", "ask_ts", "shown_at", "shown_in"))
MAX_QUEUE_BYTES = 1024 * 1024
MAX_QUESTION_BYTES = 600
MAX_TS = 253402300800


def topic_key(topic):
    """Use the current decision log's case and surrounding-space matching."""
    return topic.strip().lower()


def _path_without_symlinks(path):
    candidate = Path(os.path.abspath(os.fspath(path)))
    for part in (candidate, *candidate.parents):
        try:
            if stat.S_ISLNK(part.lstat().st_mode):
                raise ValueError("owner ask state path contains a symlink; preserve originals")
        except FileNotFoundError:
            continue
    return candidate


def _read_bytes(path, *, required=False, private=False):
    candidate = _path_without_symlinks(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(candidate, flags)
    except FileNotFoundError:
        if required:
            raise ValueError("owner ask status is unknown because a required state file is unavailable") from None
        return b""
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("owner ask state must be a regular file; preserve originals")
        if private and stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("owner ask queue permissions are too broad; preserve originals")
        if info.st_size > MAX_QUEUE_BYTES:
            raise ValueError("owner ask state exceeds bounded size; preserve originals")
        chunks = []
        remaining = MAX_QUEUE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_QUEUE_BYTES:
            raise ValueError("owner ask state exceeds bounded size; preserve originals")
        return raw
    finally:
        os.close(fd)


def _rows(raw, *, kind):
    if not raw:
        return []
    try:
        source = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"malformed {kind} state; preserve original bytes") from exc
    if not source.endswith("\n"):
        raise ValueError(f"incomplete {kind} record; preserve original bytes")
    records = []
    for number, line in enumerate(source.splitlines(), 1):
        try:
            row = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"malformed {kind} record at line {number}; preserve original bytes") from exc
        records.append(row)
    return records


def _timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return 0 <= value < MAX_TS and math.isfinite(value)


def _validate_queue(raw):
    rows = _rows(raw, kind="owner ask queue")
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != QUEUE_FIELDS:
            raise ValueError(f"invalid owner ask schema at line {number}; preserve original bytes")
        if type(row["version"]) is not int or row["version"] != 1:
            raise ValueError(f"unsupported owner ask version at line {number}; preserve original bytes")
        if not _timestamp(row["ts"]):
            raise ValueError(f"invalid owner ask timestamp at line {number}; preserve original bytes")
        try:
            valid_holder(row["by"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid owner ask holder at line {number}; preserve original bytes") from exc
        if not isinstance(row["repo"], str):
            raise ValueError(f"invalid owner ask repo at line {number}; preserve original bytes")
        if not all(isinstance(row[key], str) and row[key].strip() for key in ("topic", "question")):
            raise ValueError(f"invalid owner ask text at line {number}; preserve original bytes")
        if len(row["question"].encode("utf-8")) > MAX_QUESTION_BYTES:
            print(f"coms: warning: owner ask question exceeds 600 UTF-8 bytes at line {number}; retaining original", file=sys.stderr)
    return rows


def _validate_decisions(raw):
    rows = _rows(raw, kind="decision log")
    for number, row in enumerate(rows, 1):
        valid = (isinstance(row, dict) and DECISION_FIELDS <= set(row)
                 and set(row) <= DECISION_FIELDS | DECISION_OPTIONAL_FIELDS
                 and _timestamp(row.get("ts"))
                 and all(isinstance(row.get(key), str) for key in ("by", "repo"))
                 and all(isinstance(row.get(key), str) and row[key].strip()
                         for key in ("topic", "answer", "quote")))
        if valid and "supersedes" in row:
            target = row["supersedes"]
            valid = (isinstance(target, str) and bool(target.strip())
                     and topic_key(target) != topic_key(row["topic"]))
        if valid and "expires_at" in row:
            valid = _timestamp(row["expires_at"])
        if not valid:
            raise ValueError(f"invalid decision record at line {number}; owner ask status is unknown")
    return rows


def _shows_path(queue_path):
    return os.path.join(os.path.dirname(os.fspath(queue_path)), "owner-ask-shows.jsonl")


def _validate_shows(raw, asks):
    rows = _rows(raw, kind="owner ask show log")
    ask_keys = {(topic_key(ask["topic"]), ask["ts"]) for ask in asks}
    for number, row in enumerate(rows, 1):
        valid = (isinstance(row, dict) and set(row) == SHOW_FIELDS
                 and type(row.get("version")) is int and row["version"] == 1
                 and isinstance(row.get("topic"), str) and row["topic"].strip()
                 and _timestamp(row.get("ask_ts"))
                 and _timestamp(row.get("shown_at")))
        if not valid:
            raise ValueError(f"invalid owner ask show record at line {number}; preserve original bytes")
        try:
            valid_holder(row.get("shown_in"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid owner ask show holder at line {number}; preserve original bytes") from exc
        key = (topic_key(row["topic"]), row["ask_ts"])
        if key not in ask_keys:
            raise ValueError(f"owner ask show history is inconsistent at line {number}; preserve original bytes")
    return rows


def _show_for(ask, shows):
    key = (topic_key(ask["topic"]), ask["ts"])
    return next((row for row in shows
                 if (topic_key(row["topic"]), row["ask_ts"]) == key), None)


def _ensure_private_file(path):
    path = _path_without_symlinks(path)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("owner ask metadata must be a regular file; preserve originals")
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def _append_jsonl(path, row):
    path = _path_without_symlinks(path)
    line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(_read_bytes(path, private=True)) + len(line) > MAX_QUEUE_BYTES:
        raise ValueError("owner ask state would exceed bounded size; preserve originals")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("owner ask state must be a regular file; preserve originals")
        os.fchmod(fd, 0o600)
        view = memoryview(line)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short write to owner ask state")
            view = view[count:]
    finally:
        os.close(fd)


def _load_state(queue_path, decisions_path):
    for path in (queue_path, decisions_path, _shows_path(queue_path)):
        os.makedirs(os.path.dirname(os.fspath(path)), mode=0o700, exist_ok=True)
        if not os.path.lexists(path):
            _ensure_private_file(path)
    asks = _validate_queue(_read_bytes(queue_path, private=True))
    decisions = _validate_decisions(_read_bytes(decisions_path))
    show_path = _shows_path(queue_path)
    shows = _validate_shows(_read_bytes(show_path, required=bool(asks), private=True), asks)
    return asks, decisions, shows


def _latest_decision(decisions, topic):
    matching = [row for row in decisions if topic_key(row["topic"]) == topic_key(topic)]
    return max(matching, key=lambda row: row["ts"]) if matching else None


def init_owner_asks(queue_path, decisions_path):
    """Create the first private ask state without replacing anything present."""
    queue_path = _path_without_symlinks(queue_path)
    decisions_path = _path_without_symlinks(decisions_path)
    show_path = _shows_path(queue_path)
    state_dir = os.path.dirname(os.fspath(queue_path))
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    queue_path = _path_without_symlinks(queue_path)
    decisions_path = _path_without_symlinks(decisions_path)
    show_path = _path_without_symlinks(show_path)
    lock_path = _path_without_symlinks(os.path.join(state_dir, "locks", "coord.lock"))
    lock = FileMutex(lock_path)
    lock.acquire(timeout=None)
    try:
        _load_state(queue_path, decisions_path)
    finally:
        lock.release()


def _answers(decision, ask_ts, now):
    return bool(decision and decision["ts"] > ask_ts
                and ("expires_at" not in decision or decision["expires_at"] > now))


def open_owner_asks(queue_path, decisions_path):
    """Return pending v1 rows; fail explicitly if any source is uncertain."""
    asks, decisions, _ = _load_state(queue_path, decisions_path)
    return [ask for ask in asks
            if not _answers(_latest_decision(decisions, ask["topic"]), ask["ts"], time.time())]


def append_owner_ask(queue_path, decisions_path, *, holder_id, repo, topic, question,
                     clock=time.time):
    """File an ask unless a current decision answers the latest ask for its topic."""
    valid_holder(holder_id)
    if not isinstance(repo, str) or not isinstance(topic, str) or not topic.strip():
        raise ValueError("owner ask repo and nonblank topic are required")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("owner ask question must be nonblank")
    if len(question.encode("utf-8")) > MAX_QUESTION_BYTES:
        print("coms: warning: owner ask question exceeds 600 UTF-8 bytes; recording original", file=sys.stderr)
    queue_path = _path_without_symlinks(queue_path)
    decision_path = _path_without_symlinks(decisions_path)
    lock = FileMutex(os.path.join(os.path.dirname(os.fspath(queue_path)), "locks", "coord.lock"))
    lock.acquire(timeout=None)
    try:
        asks, decisions, shows = _load_state(queue_path, decision_path)
        now = clock()
        if not _timestamp(now):
            raise ValueError("clock returned an invalid owner ask timestamp")
        key = topic_key(topic)
        existing = [row for row in asks if topic_key(row["topic"]) == key]
        previous = max(existing, key=lambda item: item["ts"]) if existing else None
        decision = _latest_decision(decisions, key)
        if _answers(decision, previous["ts"] if previous else 0, now):
            return {"created": False, "row": previous, "status": "answered",
                    "decision": decision, "shown": _show_for(previous, shows) if previous else None}
        row = {"version": 1, "ts": now, "by": holder_id, "repo": repo,
               "topic": topic.strip(), "question": question.strip()}
        _append_jsonl(queue_path, row)
        return {"created": True, "row": row, "status": "filed",
                "decision": None, "shown": None}
    finally:
        lock.release()


def show_owner_ask(queue_path, decisions_path, *, topic, holder_id, clock=time.time):
    """Record a display event for a pending canonical topic."""
    valid_holder(holder_id)
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("owner ask show requires a nonblank topic")
    queue_path = _path_without_symlinks(queue_path)
    decision_path = _path_without_symlinks(decisions_path)
    lock = FileMutex(os.path.join(os.path.dirname(os.fspath(queue_path)), "locks", "coord.lock"))
    lock.acquire(timeout=None)
    try:
        asks, decisions, shows = _load_state(queue_path, decision_path)
        matches = [row for row in asks if topic_key(row["topic"]) == topic_key(topic)]
        if not matches:
            decision = _latest_decision(decisions, topic)
            if decision:
                return {"status": "answered", "row": None, "decision": decision, "shown": None}
            raise ValueError("no owner ask exists for that topic")
        row = max(matches, key=lambda item: item["ts"])
        decision = _latest_decision(decisions, topic)
        shown = _show_for(row, shows)
        if _answers(decision, row["ts"], clock()):
            return {"status": "answered", "row": row, "decision": decision, "shown": shown}
        now = clock()
        if not _timestamp(now) or now < row["ts"]:
            raise ValueError("clock is invalid or earlier than the owner ask; show was not recorded")
        event = {"version": 1, "topic": row["topic"], "ask_ts": row["ts"],
                 "shown_at": now, "shown_in": holder_id}
        _append_jsonl(_shows_path(queue_path), event)
        return {"status": "shown", "row": row, "decision": None, "shown": event}
    finally:
        lock.release()

"""Read bounded Codex rollout metadata and return display-safe values."""

import json
import os
import re
import stat
from pathlib import Path

TAIL_BYTES = 4 * 1024 * 1024
HEADER_BYTES = 1024 * 1024
ROLLOUT = re.compile(r"rollout-[0-9A-Fa-fTZ:-]+-([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-"
                     r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\.jsonl\Z")
MODEL = re.compile(r"(?:gpt-[1-9][0-9]?(?:\.[0-9]{1,2})?(?:-(?:codex|mini|nano|sol|astra|terra))?"
                   r"(?:-(?:mini|nano|max))?|o[1-9](?:-(?:mini|pro))?)\Z")
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
TIERS = {"default", "standard", "priority", "flex", "ultrafast"}
ROOT_SOURCES = {"desktop", "cli", "vscode", "exec", "mcp", "app", "app_server"}


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def rollout_paths(home, include_archive=False):
    folders = [home / "sessions"]
    if include_archive:
        folders.append(home / "archived_sessions")
    found = []
    for folder in folders:
        if folder.is_symlink():
            continue
        for root, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in files:
                if not ROLLOUT.fullmatch(name):
                    continue
                path = Path(root) / name
                try:
                    info = path.lstat()
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    found.append((info.st_mtime, path))
    return sorted(found, reverse=True)


def matches_fragment(path, fragment):
    match = ROLLOUT.fullmatch(path.name)
    return bool(match and fragment.lower() in match.group(1).lower())


def decode(line):
    try:
        record = json.loads(line)
    except (UnicodeError, ValueError, RecursionError):
        return {}, {}
    if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
        return {}, {}
    return record, record["payload"]


def choice(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unknown"


def read_summary(path):
    summary = {"kind": "unknown", "turn": "unknown", "model": "unknown", "effort": "unknown",
               "tier": "unknown", "parent": None, "id": None}
    # Refuse a file switched to a symlink or pipe between listing and opening.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        data = os.fstat(fd)
        if not stat.S_ISREG(data.st_mode):
            return summary
        header = source.readline(HEADER_BYTES)
        record, meta = decode(header)
        if header.endswith(b"\n") and record.get("type") == "session_meta":
            src = meta.get("source")
            if isinstance(src, str) and src in ROOT_SOURCES:
                summary["kind"] = "root"
            if isinstance(meta.get("id"), str):
                summary["id"] = meta["id"]
            if isinstance(src, dict) and "subagent" in src:
                summary["kind"] = "helper"
                helper = src["subagent"]
                spawn = helper.get("thread_spawn") if isinstance(helper, dict) else None
                if isinstance(spawn, dict) and isinstance(spawn.get("parent_thread_id"), str):
                    summary["parent"] = spawn["parent_thread_id"]

        offset = max(0, data.st_size - TAIL_BYTES)
        source.seek(offset - 1 if offset else 0)
        chunk = source.read(TAIL_BYTES + 1)
        if offset:
            # Read one preceding byte to keep a complete record at an exact boundary.
            chunk = chunk.partition(b"\n")[2]
    lines = chunk.split(b"\n")
    for line in lines[:-1]:
        # Limit decoding to structured metadata; ignore transcript and tool-call records.
        if not any(marker in line for marker in (
                b'"turn_context"', b'"task_started"', b'"task_complete"', b'"turn_aborted"',
                b'"thread_settings_applied"', b'"turn_started"', b'"turn_completed"')):
            continue
        record, payload = decode(line)
        category, event = record.get("type"), payload.get("type")
        if category == "turn_context" or (category == "event_msg" and event == "thread_settings_applied"):
            settings = payload.get("thread_settings")
            if not isinstance(settings, dict):
                settings = payload
            model = settings.get("model")
            if "model" in settings:
                summary["model"] = model if isinstance(model, str) and MODEL.fullmatch(model) else "unknown"
            if "reasoning_effort" in settings or "effort" in settings:
                effort = settings.get("reasoning_effort", settings.get("effort"))
                summary["effort"] = choice(effort, EFFORTS)
            if "service_tier" in settings:
                summary["tier"] = choice(settings["service_tier"], TIERS)
        if category == "event_msg":
            if event in ("task_started", "turn_started"):
                summary["turn"] = "open"
            elif event in ("task_complete", "turn_aborted", "turn_completed"):
                summary["turn"] = "closed"
    return summary


def describe(summary):
    return (f"turn {summary['turn']} | model {summary['model']} | effort {summary['effort']} | "
            f"service tier {summary['tier']}")


def age_seconds(mtime, now):
    return max(0, int(now - mtime))

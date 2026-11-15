"""Accidental session routing checks, never sender authentication."""
import json
import os
import math
import re
import sys
from pathlib import Path


class IdentityError(ValueError):
    """The selected holder belongs to a different active session."""


def valid_holder(holder):
    if not isinstance(holder, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", holder):
        raise IdentityError("holder must use 1 to 128 letters, digits, dots, underscores or hyphens")
    return holder


def session_id():
    # Explicit per-window selection overrides inherited shared harness markers.
    for key in ("COMS_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        value = os.environ.get(key)
        if value:
            if len(value) > 256 or not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
                raise IdentityError("invalid session marker")
            return key + ":" + value
    return None


def valid_marker(value):
    return (isinstance(value, str) and re.fullmatch(
        r'(?:COMS_SESSION_ID|CLAUDE_CODE_SESSION_ID|CLAUDE_SESSION_ID|CODEX_THREAD_ID):[A-Za-z0-9._:-]{1,256}', value) is not None)


def enrollment(store):
    path = Path(store._thread_path())
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise IdentityError('enrollment path must not contain symlinks')
    try:
        with path.open() as source:
            entry = json.load(source)
    except FileNotFoundError:
        return None
    except (ValueError, RecursionError) as exc:
        raise IdentityError("malformed own enrollment; inspect then enroll --repair-enrollment") from exc
    if not isinstance(entry, dict) or entry.get('holder') != store.holder_id:
        raise IdentityError('malformed own enrollment; inspect then enroll --repair-enrollment')
    stamp = entry.get('updated_at')
    owner = entry.get('session')
    if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
            or not 0 <= stamp < 253402300800 or not math.isfinite(stamp)
            or 'retired' in entry and not isinstance(entry['retired'], bool)
            or owner is not None and not valid_marker(owner)):
        raise IdentityError('malformed own enrollment; inspect then enroll --repair-enrollment')
    return entry


def _check(store, strict=False, require_bound=False):
    valid_holder(store.holder_id)
    caller = session_id()
    entry = enrollment(store)
    if entry and entry.get("retired"):
        raise IdentityError("holder is retired; enroll a new holder")
    owner = entry.get("session") if entry else None
    if owner and caller != owner:
        if caller and caller.split(':', 1)[1] == owner.split(':', 1)[1]:
            raise IdentityError("holder marker namespace differs; use the same marker namespace chosen at enrollment, without overriding a native binding")
        raise IdentityError("holder belongs to a different live session; select your own COMS_HOLDER")
    if require_bound and not owner:
        raise IdentityError("holder is missing or unbound; own window must enroll (legacy: --adopt-legacy) before delivery")
    if strict and not caller:
        raise IdentityError("set COMS_SESSION_ID to this window's unique session id (or use a native harness marker)")
    return caller


def check(store, strict=False, require_bound=False):
    try:
        return _check(store, strict=strict, require_bound=require_bound)
    except IdentityError as exc:
        if strict or require_bound:
            raise
        print(f"coms: warning: {exc}; continuing", file=sys.stderr)
        return None

"""Small local state files used by the pacing scripts."""

import datetime
import json
import math
import os
import tempfile
from pathlib import Path


def watch_dir():
    if os.environ.get("COMS_WATCH_DIR"):
        return Path(os.environ["COMS_WATCH_DIR"])
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "coms-orchestrator"


def number(value, lower, upper):
    """Test the numeric range without coercing booleans or non-finite inputs."""
    return type(value) in (int, float) and lower <= value <= upper


def reset_time(value):
    """Accept an aware ISO stamp or an epoch in seconds."""
    if type(value) in (int, float):
        stamp = value
    elif isinstance(value, str):
        parsed = datetime.datetime.fromisoformat(value.removesuffix("Z") + (
            "+00:00" if value.endswith("Z") else ""))
        if parsed.utcoffset() is None:
            raise ValueError("reset time needs a timezone")
        stamp = parsed.timestamp()
    else:
        raise ValueError("invalid reset time")
    if not number(stamp, 0, 253402300799):
        raise ValueError("reset time out of range")
    return stamp


def read_json(path):
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def write_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".coms-state-", delete=False) as target:
        tmp = Path(target.name)
        try:
            json.dump(value, target, allow_nan=False)
            target.write("\n")
            target.close()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


def positive(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError("expected a positive number") from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError("expected a positive number")
    return result

#!/usr/bin/env python3
"""Check configured scheduled jobs and maintain one private broken-assets list."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

DEFAULT_COMS_DIR = "~/.claude/coms"
UNIT = r"[A-Za-z0-9][A-Za-z0-9_.@:-]*"
LAUNCH_TARGET = r"(?:(?:gui/[0-9]+|user/[0-9]+|system)/)?" + UNIT


def call(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, errors="replace",
                              timeout=15, env=dict(os.environ, LC_ALL="C"), check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def launchd_reason(result):
    """Empty string means healthy, None means the check could not determine health."""
    if result is None:
        return None
    if result.returncode:
        return "service missing" if "Could not find service" in result.stderr else None
    # Only inspect top-level launchctl fields, never nested variables or arguments.
    fields = dict(re.findall(r"^\t([^\t\n=]+?)\s*= *([^\r\n]*)$", result.stdout, re.M))
    if "state" not in fields:
        return None
    signal = fields.get("last terminating signal", "")
    if signal and signal != "0":
        return "terminated by signal"
    status = fields.get("last exit code", "")
    code = re.match(r"^-?[0-9]+", status)
    if code:
        return f"exit code {code[0]}" if int(code[0]) else ""
    if status == "(never exited)" or fields.get("runs") == "0":
        return ""
    return None


def systemd_properties(result):
    if result is None:
        return {}
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode and fields.get("LoadState") != "not-found":
        return {}
    return fields


def unit_reason(fields, timer=False):
    load = fields.get("LoadState")
    if not load:
        return None
    if load != "loaded":
        return "timer unavailable" if timer else "triggered unit unavailable"
    state, result = fields.get("ActiveState"), fields.get("Result")
    if not state:
        return None
    if state == "failed":
        if result and re.fullmatch(r"[a-z][a-z0-9-]*", result):
            return "failed (" + result + ")"
        return "failed"
    if result and result != "success":
        return "last run failed"
    if timer and state == "inactive":
        return "timer inactive"
    return ""


def systemd_reason(asset, system=False, invoke=call):
    command = ["systemctl"] if system else ["systemctl", "--user"]
    command.extend(["show", "--property=LoadState,ActiveState,Result,Unit", "--"])
    timer = systemd_properties(invoke([*command, asset]))
    reason = unit_reason(timer, timer=True)
    if reason != "":
        return reason
    triggered = timer.get("Unit", "")
    if not re.fullmatch(UNIT, triggered):
        return None
    return unit_reason(systemd_properties(invoke([*command, triggered])))


def read_assets(args, backend):
    assets = args.asset
    if args.watch_file:
        assets = [line.strip() for line in args.watch_file.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    pattern = LAUNCH_TARGET if backend == "launchd" else UNIT + r"\.timer"
    if any(not re.fullmatch(pattern, asset) for asset in assets):
        raise ValueError("invalid launchd service target or systemd timer name")
    return sorted(set(assets))


def read_state(path):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "ok"
    return {row["asset"]: row["reason"] for row in state["broken"]}, state.get("notified", "ok")


def write_state(path, broken, notified):
    data = {"broken": [{"asset": asset, "reason": why} for asset, why in broken.items()],
            "notified": notified}
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as target:
        temporary = Path(target.name)
        try:
            json.dump(data, target, indent=2)
            target.write("\n")
            target.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def notify_state(broken):
    if not broken:
        return "ok"
    return hashlib.sha256(json.dumps(broken, sort_keys=True).encode("ascii")).hexdigest()


def scan(path, assets, probe, notifier):
    previous, notified = read_state(path)
    broken, unknown = {}, 0
    for asset in assets:
        reason = probe(asset)
        if reason is None:
            unknown += 1
            reason = previous.get(asset)
        if reason:
            broken[asset] = reason
    if broken != previous or not path.exists():
        write_state(path, broken, notified)
    change = notify_state(broken)
    if notifier and change != notified:
        p = call([str(notifier), "--source", "job-health", "--state", change,
                  "--message", f"Monitored jobs needing attention: {len(broken)}. See the local broken-assets list."])
        if p is not None and p.returncode == 0:
            write_state(path, broken, change)
        else:
            print("job-health: notification did not complete", file=sys.stderr)
    print(f"job-health: checked={len(assets)} broken={len(broken)} unknown={unknown}")


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    source = cli.add_mutually_exclusive_group(required=True)
    source.add_argument("--asset", action="append", default=[], metavar="NAME",
                        help="launchd label or systemd timer unit, repeatable")
    source.add_argument("--watch-file", type=Path, help="one asset name on each line")
    cli.add_argument("--backend", choices=("launchd", "systemd"),
                     help="defaults to launchd on macOS and systemd on Linux")
    cli.add_argument("--system", action="store_true", help="probe system jobs instead of user jobs")
    cli.add_argument("--output", type=Path, help="defaults to broken-assets.json in the COMS state folder")
    cli.add_argument("--notify", type=Path, nargs="?", const=Path(__file__).resolve().with_name("notify"),
                     metavar="PATH", help="enable transition alerts using the adjacent or specified notify hook")
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    backend = args.backend or {"darwin": "launchd", "linux": "systemd"}.get(sys.platform)
    if not backend:
        cli.error("select a supported backend")
    default_state = Path(os.environ.get("COMS_DIR") or DEFAULT_COMS_DIR).expanduser()
    output = args.output or default_state / "broken-assets.json"
    domain = "system" if args.system else f"gui/{os.getuid()}"

    def probe(asset):
        if backend == "systemd":
            return systemd_reason(asset, args.system)
        target = asset if "/" in asset else f"{domain}/{asset}"
        return launchd_reason(call(["launchctl", "print", target]))

    try:
        assets = read_assets(args, backend)
        output.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
        lock = output.with_name(output.name + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            scan(output, assets, probe, args.notify)
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(f"job-health: unable to read configuration or update health state ({type(exc).__name__})",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

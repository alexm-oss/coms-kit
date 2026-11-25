"""Select the skill files and hooks installed by a public COMS profile."""
import argparse
import fcntl
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROLES = ("command", "codex-lead", "governor", "security", "keeper", "controller")
PIECES = {
    role: {"references/" + ("security-reviewer" if role == "security" else role) + ".md",
           "references/protocol.md"} for role in ROLES
}
PIECES.update({
    "notify": {"bin/notify"},
    "pacing": {"bin/pace.py", "bin/codex-pace-watch.sh", "bin/codex_pace_watch.py", "bin/operator_state.py"},
    "codex-view": {"bin/codex-now.py", "bin/codex-fleet.py", "bin/codex_local.py", "bin/operator_state.py"},
    "load-watch": {"bin/load-watch.sh"},
    "handover-guide": set(), "helper-brief": set(),
    "job-health": {"bin/job-health.py", "bin/job-watch.txt"},
    "known-fixes": {"bin/known-fixes", "bin/known-fixes.txt"},
    "current-rules": {"bin/current-rules", "bin/rule-pins.txt"},
})
SOLO = {"command", "notify"}
PAIR = SOLO | {"codex-lead", "pacing", "handover-guide", "load-watch", "codex-view"}
PROFILES = {"core": set(), "solo": SOLO, "pair": PAIR, "full-room": set(PIECES) - {"controller"}}
WHY = {"core": "locks, mail, decisions and handover",
       "solo": "one coordinator and a notification command you can configure",
       "pair": "recommended for two model families working together",
       "full-room": "review, memory and job health for a larger team"}
PREFIX = "# coms-kit selection: "


def staged_skill(source, dest, selected):
    optional = set().union(*PIECES.values())
    include = set().union(*(PIECES[name] for name in selected))
    for path in source.rglob("*"):
        name = path.relative_to(source)
        if (not path.is_file() or path.is_symlink() or path.suffix == ".pyc" or
                "__pycache__" in name.parts or name.as_posix() in optional - include):
            continue
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.as_posix() == "references/handover.md" and "handover-guide" not in selected:
            path = source.parents[1] / "profiles/core-handover.md"
        shutil.copy2(path, target)
    # Offline indexes remain usable even when a referenced role was omitted.
    for path in dest.rglob("*.md"):
        def link(match, directory=path.parent):
            label, href = match.groups()
            if href.startswith(("https://", "http://", "#")) or (directory / href.partition("#")[0]).exists():
                return match.group()
            return label
        text = path.read_text()
        path.write_text(re.sub(r"\[([^]\n]+)\]\(([^)\n]+)\)", link, text))


NEXT_PROFILE = {"core": "solo", "solo": "pair", "pair": "full-room", "full-room": "full-room"}
BENEFITS = {
    "command": "Give one window responsibility for routing work and owner questions",
    "notify": "Send a state-change alert through a backend you choose",
    "codex-lead": "Assign Codex work and check the evidence before integration",
    "pacing": "Compare usage with its reset window before widening the workload",
    "load-watch": "Check whether a slow machine is under sustained load or thermal pressure",
    "codex-view": "See recent local Codex activity and settings without opening transcripts",
    "handover-guide": "Use the full checklist when a window approaches its limit",
    "governor": "Give test scope and stuck test rigs a dedicated reviewer",
    "security": "Give sensitive changes an independent second-family review",
    "keeper": "Keep sourced decisions and follow-ups together for each product",
    "helper-brief": "Check a proposed helper assignment before starting the helper",
    "job-health": "List failing scheduled jobs on the local machine",
    "known-fixes": "Match a stuck card to a previously recorded fix",
    "current-rules": "Read the current decisions and their replacements on one local page",
    "controller": "Coordinate integration when several lanes land in one repository",
}


def installed(bin_dir):
    launcher = Path(bin_dir) / "coms"
    if not launcher.exists() and not launcher.is_symlink():
        return "not installed", set()
    if launcher.is_file() and not launcher.is_symlink():
        try:
            with launcher.open(encoding="utf-8") as source:
                for line in source:
                    if line.startswith(PREFIX):
                        words = line[len(PREFIX):].split()
                        if words and words[0] in PROFILES and set(words[1:]).issubset(PIECES):
                            return words[0], set(words[1:])
                        break
        except UnicodeError:
            pass
    return "legacy or unmanaged", set()


def show_status(profile, selected, target, command):
    print("Installed profile:", profile)
    print("Enabled:", ", ".join(sorted(selected)) or "none")
    print("Disabled:", ", ".join(sorted(PIECES.keys() - selected)) or "none")
    if profile == "legacy or unmanaged":
        print("Recommended next: inspect the existing launcher before choosing an install profile.")
        return
    target = target or NEXT_PROFILE.get(profile, "solo")
    missing = [name for name in BENEFITS if name in PROFILES[target] - selected]
    print("Recommended next:", target if missing else "none")
    for name in missing:
        print(f"- {name}: {BENEFITS[name]}. Add: {command} --add {name}")


def setup(installer, bin_dir, command, argv):
    parser = argparse.ArgumentParser(prog=command, description="Inspect or extend the existing installation.",
                                     allow_abbrev=False)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--status", action="store_true", help="read the selection and show missing pieces")
    action.add_argument("--add", action="append", choices=PIECES, help="add a piece, keeping existing choices")
    parser.add_argument("--profile", choices=PROFILES, help="compare status against this profile")
    args = parser.parse_args(argv)
    if args.status:
        show_status(*installed(bin_dir), args.profile, command)
        return 0
    if args.profile:
        parser.error("--profile selects a comparison for --status; --add keeps the installed profile")
    # Serialize read-modify-write additions from independent agents using this checkout.
    with open(installer, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        profile, selected = installed(bin_dir)
        if profile == "legacy or unmanaged":
            parser.error("resolve the existing launcher before adding a piece")
        if set(args.add) <= selected:
            print("Already enabled:", ", ".join(args.add))
            return 0
        if profile == "not installed":
            profile = "core"
        wanted = selected | set(args.add)
        options = ["--profile", profile]
        for name in sorted(wanted - PROFILES[profile]):
            options.extend(["--with", name])
        for name in sorted(PROFILES[profile] - wanted):
            options.extend(["--without", name])
        return subprocess.run([installer, *options], check=False).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n".join(f"{name}: {WHY[name]}" for name in PROFILES))
    parser.add_argument("--profile", choices=PROFILES, default="core")
    parser.add_argument("--with", dest="add", choices=PIECES, action="append", default=[])
    parser.add_argument("--without", dest="drop", choices=PIECES, action="append", default=[])
    args = parser.parse_args()
    selected = (PROFILES[args.profile] | set(args.add)) - set(args.drop)
    print(" ".join([args.profile, *sorted(selected)]))


if __name__ == "__main__":
    if sys.argv[1:2] == ["setup"]:
        sys.exit(setup(*sys.argv[2:5], sys.argv[5:]))
    main()

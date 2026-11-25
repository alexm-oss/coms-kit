"""Select the skill files and hooks installed by a public COMS profile."""
import argparse
import re
import shutil
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
            if href.startswith(("https://", "http://", "#")) or (directory / href).exists():
                return match.group()
            return label
        text = path.read_text()
        path.write_text(re.sub(r"\[([^]\n]+)\]\(([^)\n]+)\)", link, text))


def status(bin_dir):
    launcher = Path(bin_dir) / "coms"
    selected = set()
    profile = "not installed"
    if launcher.is_file() and not launcher.is_symlink():
        for line in launcher.read_text().splitlines():
            if line.startswith(PREFIX):
                profile, *pieces = line[len(PREFIX):].split()
                selected = set(pieces)
                break
        else:
            profile = "legacy or unmanaged"
    print("Installed profile:", profile)
    print("Enabled:", ", ".join(sorted(selected)) or "none")
    print("Disabled:", ", ".join(sorted(PIECES.keys() - selected)) or "none")
    order = ["command", "codex-lead", "handover-guide", "notify", "pacing", "load-watch", "codex-view",
             "security", "governor", "keeper", "helper-brief", "job-health", "known-fixes", "current-rules"]
    next_piece = next((piece for piece in order if piece not in selected), None)
    print("Recommended next:", next_piece or "controller only when multiple lanes integrate")
    return 0


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
    main()

"""Manage only directories created and skill files copied by the installer."""
import argparse
import contextlib
import hashlib
import json
import tempfile
from pathlib import Path

from install_profiles import staged_skill

DIR_MARK = ".coms-kit-directory"
SKILL_MARK = ".coms-kit"


def mkdir(path):
    if path.is_dir():
        return
    mkdir(path.parent)
    path.mkdir(mode=0o700)
    (path / DIR_MARK).write_text("created by coms-kit\n")


def prune(path):
    while not path.is_symlink() and path.is_dir():
        marker = path / DIR_MARK
        if marker.is_symlink() or not marker.is_file() or set(path.iterdir()) != {marker}:
            break
        if marker.read_text() != "created by coms-kit\n":
            break
        marker.unlink()
        path.rmdir()
        path = path.parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in root.rglob("*")
            if p.is_file() and not p.is_symlink() and p.name != SKILL_MARK}


def remove_skill(source, dest):
    marker = dest / SKILL_MARK
    if dest.is_symlink() or marker.is_symlink() or not marker.is_file():
        return
    text = marker.read_text()
    # The original installer used an empty marker. Match surviving distribution files.
    owned = json.loads(text) if text else files(source)
    for path in dest.rglob("*"):
        name = path.relative_to(dest).as_posix()
        if name in owned and not path.is_symlink() and path.is_file() and digest(path) == owned[name]:
            path.unlink()
    marker.unlink()
    for directory in sorted(dest.rglob("*"), reverse=True):
        if not directory.is_symlink() and directory.is_dir():
            # Only the parents of installed entries belong to the kit.
            prefix = directory.relative_to(dest).as_posix() + "/"
            if any(name.startswith(prefix) for name in owned):
                with contextlib.suppress(OSError):
                    directory.rmdir()
    with contextlib.suppress(OSError):
        dest.rmdir()


def copy_skill(source, dest, selection):
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        staged = Path(tmp) / "skill"
        staged_skill(source, staged, selection)
        (staged / SKILL_MARK).write_text(json.dumps(files(staged), sort_keys=True))
        if dest.exists():
            remove_skill(source, dest)
            if dest.exists():
                backup = Path(tempfile.mkdtemp(prefix="coms.bak-", dir=dest.parent))
                dest.rename(backup / "coms")
                print("  preserved local skill files in", backup)
        staged.rename(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("mkdir", "prune", "copy-skill", "remove-skill"))
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--selection", default="")
    args = parser.parse_args()
    if args.action in ("mkdir", "prune"):
        for path in args.paths:
            (mkdir if args.action == "mkdir" else prune)(path)
    elif args.action == "copy-skill":
        copy_skill(*args.paths, args.selection.split()[1:])
    else:
        remove_skill(*args.paths)


if __name__ == "__main__":
    main()

"""The `coms` command line: the on-ramp every actor uses.

  coms do <repo:resource> [--intent T] [--wait S] -- <cmd...>
      Acquire the lease, run <cmd> while a background heartbeat renews the lease,
      release, and exit with the command's own exit code. This is the safe way to
      wrap a risky operation (a merge to main, a ship) so it takes its turn.

  coms check <resource>     exit 0 if free or held by us, non-zero if held by another
  coms status [--json]      show live leases and, without --json, the enrolled agents
  coms unlock <resource>    admin override: drop a stuck lease (exit 1 if there is none)
  coms enroll [--intent T]  register this session for visibility

Resource keys are conventionally "<repo>:<what>", e.g. myrepo:main, myrepo:ship.
Configuration: COMS_DIR (default ~/.claude/coms), COMS_HOLDER (default pid-<pid>).
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

from coms.store import Store, Busy

DEFAULT_DIR = os.path.expanduser("~/.claude/coms")
EX_TEMPFAIL = 75


def coms_dir(explicit=None):
    return explicit or os.environ.get("COMS_DIR") or DEFAULT_DIR


def holder_id():
    return os.environ.get("COMS_HOLDER") or f"pid-{os.getpid()}"


def _store(explicit_dir, ttl=30.0):
    return Store(coms_dir(explicit_dir), holder_id=holder_id(), ttl=ttl)


def cmd_do(args, cdir):
    if not args.cmd:
        print("coms do: nothing after '--' to run", file=sys.stderr)
        return 2
    key = _resolve_key(args.key)
    store = _store(cdir)
    try:
        claim = store.acquire(key, intent=args.intent, wait=args.wait)
    except Busy as exc:
        print(f"coms: {key} held by {exc.holder}; did not run", file=sys.stderr)
        return EX_TEMPFAIL
    stop = threading.Event()

    def beat():
        interval = max(1.0, store.ttl / 2)
        while not stop.wait(interval):
            try:
                claim.heartbeat()
            except Busy:
                return

    heart = threading.Thread(target=beat, daemon=True)
    heart.start()
    try:
        # The command runs as this holder too, so a backstop hook that calls
        # `coms check` from inside it sees this lease as its own.
        env = dict(os.environ, COMS_HOLDER=store.holder_id)
        return subprocess.run(args.cmd, env=env).returncode
    finally:
        stop.set()
        claim.release()


def cmd_check(args, cdir):
    key = _resolve_key(args.key)
    st = _store(cdir).status()
    res = st["resources"].get(key)
    if res and res["holder"] != holder_id() and res["expires_at"] > time.time():
        print(f"{key} held by {res['holder']}", file=sys.stderr)
        return 3
    return 0


def cmd_unlock(args, cdir):
    key = _resolve_key(args.key)
    if _store(cdir).force_release(key):
        print(f"coms: dropped the lease on {key}")
        return 0
    print(f"coms: no lease on {key}; nothing dropped", file=sys.stderr)
    return 1


def cmd_status(args, cdir):
    store = _store(cdir)
    st = store.status()
    if args.json:
        print(json.dumps(st, indent=2, sort_keys=True))
        return 0
    resources = st.get("resources", {})
    if not resources:
        print("coms: no resources held")
    now = time.time()
    for key, res in sorted(resources.items()):
        left = int(res["expires_at"] - now)
        state = f"{left}s left" if left > 0 else "EXPIRED"
        intent = f" ({res['intent']})" if res.get("intent") else ""
        print(f"{key}: held by {res['holder']}{intent} [{state}]")
    agents = store.threads()
    if agents:
        print("Enrolled:")
        for t in agents:
            print(f"  {_agent_line(t)}")
    return 0


HOOK_NAMES = ("pre-commit", "pre-merge-commit")
HOOK_SENTINEL = "coms guard hook"
MARKER_NAME = "coms-enabled"
MARKER_TEXT = "coms guard marker v1\n"


def _git_out(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True
    ).stdout.strip()


def _git_common_dir(repo):
    """Absolute path of the repo's shared .git dir, or "" outside a repo."""
    common = _git_out(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:  # git older than 2.31
        common = _git_out(repo, "rev-parse", "--git-common-dir")
        if not common:
            return ""
        if not os.path.isabs(common):
            common = os.path.join(repo, common)
    return os.path.abspath(common)


def _repo_hooks_dir(repo):
    out = _git_out(repo, "rev-parse", "--git-path", "hooks")
    return out if os.path.isabs(out) else os.path.join(repo, out)


def _shipped_hook_text():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "hooks", "coms-hook")) as fh:
        return fh.read()


def _is_our_hook(path):
    """True when the hook at path is one coms guard wrote (it carries the sentinel)."""
    try:
        with open(path, "rb") as fh:
            return HOOK_SENTINEL.encode() in fh.read()
    except OSError:
        return False


def cmd_guard(args, cdir):
    common = _git_common_dir(args.repo)
    if not common:
        print(f"coms guard: {args.repo} is not inside a git repository", file=sys.stderr)
        return 2
    with open(os.path.join(common, MARKER_NAME), "w") as fh:
        fh.write(MARKER_TEXT)
    managed = _git_out(args.repo, "config", "--get", "core.hooksPath")
    if managed:
        print(f"coms: marked {args.repo}. Its git hooks come from core.hooksPath "
              f"({managed}), husky for example, so coms left them alone and added "
              "no backstop hook. Land shared work through 'coms do'.")
        return 0
    hooks = _repo_hooks_dir(args.repo)
    # coms never moves or edits a hook it did not write: hook managers can read
    # their own file name, so a repo with its own hooks gets the mark only.
    theirs = [name for name in HOOK_NAMES
              if os.path.lexists(os.path.join(hooks, name))
              and not _is_our_hook(os.path.join(hooks, name))]
    if theirs:
        noun = "hooks" if len(theirs) > 1 else "hook"
        print(f"coms: marked {args.repo}. It already has its own {' and '.join(theirs)} "
              f"{noun} in {hooks}, so coms left the repo's hooks alone and added no "
              "backstop hook. Land shared work through 'coms do'.")
        return 0
    os.makedirs(hooks, exist_ok=True)
    hook_text = _shipped_hook_text()
    for name in HOOK_NAMES:
        target = os.path.join(hooks, name)
        with open(target, "w") as fh:
            fh.write(hook_text)
        os.chmod(target, 0o755)
    print(f"coms: guarding {args.repo} ({', '.join(HOOK_NAMES)}) for branches: main master")
    return 0


def cmd_unguard(args, cdir):
    common = _git_common_dir(args.repo)
    if not common:
        print(f"coms unguard: {args.repo} is not inside a git repository", file=sys.stderr)
        return 2
    marker = os.path.join(common, MARKER_NAME)
    if os.path.exists(marker):
        os.remove(marker)
    hooks = _repo_hooks_dir(args.repo)
    for name in HOOK_NAMES:
        target = os.path.join(hooks, name)
        if not os.path.exists(target):
            continue
        with open(target) as fh:
            if HOOK_SENTINEL not in fh.read():
                continue  # not ours, leave it alone
        os.remove(target)
        orig = target + ".coms-orig"
        if os.path.exists(orig):
            os.rename(orig, target)
    print(f"coms: unguarded {args.repo}")
    return 0


PRIMER = """coms: you are one of several agents working on this machine at once (other
windows, other tools, scripts). Take your turn on risky operations so no one's
work is lost.

  Wrap anything only one agent should do at a time:
      coms do <name> -- <command>        e.g. coms do main -- git merge ...
  A bare name gets this repo's prefix (main -> <repo>:main). It waits your turn,
  runs the command, releases, and returns its exit code. If it cannot get the
  lease it refuses and does not run, so you never clobber someone else.

  Plain work (feature branches, editing files) needs no lease.
  See who holds what:  coms status
  A crashed holder's lease self-expires in 30s, so nothing stays wedged.

Full rules: the coms skill (SKILL.md)."""


def _current_repo():
    """Canonical repo name, stable across the main worktree and linked worktrees.

    Derives from the common git dir (shared by every worktree of a repo), so a
    window in a linked worktree resolves to the same name as one in the main
    checkout. Using --show-toplevel instead returned the worktree's own basename,
    which made worktree windows take the wrong lease key (worktree:main, not repo:main).
    """
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True,
        )
        common = r.stdout.strip()
        if r.returncode != 0 or not common:  # older git without --path-format
            r = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True
            )
            common = r.stdout.strip()
            if not common:
                return ""
            common = os.path.abspath(common)
        common = common.rstrip("/")
        base = os.path.basename(common)
        if base == ".git":  # normal repo: <repo>/.git
            return os.path.basename(os.path.dirname(common))
        if base.endswith(".git"):  # bare repo: <repo>.git
            return base[:-4]
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
        ).stdout.strip()
        return os.path.basename(top) if top else base
    except Exception:
        return ""


def _current_worktree():
    """Folder name of the checkout this process runs in: the main checkout's own
    folder, or a linked worktree's. Desktop session lists key windows by it."""
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        return ""
    return os.path.basename(top) if top else ""


def _resolve_key(key):
    """A bare resource (no colon) is prefixed with the canonical repo: main -> myrepo:main.

    Lets every window write `coms do main` and land on the same `myrepo:main` lease
    regardless of whether it sits in the main checkout or a linked worktree.
    """
    if ":" in key:
        return key
    repo = _current_repo()
    return f"{repo}:{key}" if repo else key


def cmd_primer(args, cdir):
    print(PRIMER)
    return 0


def _agent_line(t):
    """One enrolled agent, as enroll and status both print it:
    <holder> on <repo>[ in <worktree>][ [orchestrator]] (<intent or idle>)."""
    where = t.get("repo") or "?"
    if t.get("worktree") and t["worktree"] != where:
        where += f" in {t['worktree']}"
    tag = " [orchestrator]" if t.get("role") == "orchestrator" else ""
    return f"{t['holder']} on {where}{tag} ({t.get('intent') or 'idle'})"


def cmd_enroll(args, cdir):
    store = _store(cdir)
    store.register(repo=_current_repo(), worktree=_current_worktree(),
                   intent=args.intent, role=args.role)
    print(PRIMER)
    print()
    held = store.status().get("resources", {})
    peers = [t for t in store.threads() if t["holder"] != holder_id()]
    if held:
        print("Currently held:")
        for key, res in sorted(held.items()):
            print(f"  {key} by {res['holder']} ({res.get('intent') or ''})")
    if peers:
        print("Enrolled windows:")
        for t in peers:
            print(f"  {_agent_line(t)}")
    orch = store.orchestrator()
    if orch and orch != holder_id():
        print(f"\nOrchestrator is {orch}. Reach it any time:  coms msg {orch} \"<your update>\"")
    if not held and not peers:
        print("(no other actors active right now)")
    return 0


def _fmt_msg(m):
    import datetime
    t = datetime.datetime.fromtimestamp(m.get("ts", 0)).strftime("%H:%M:%S")
    return f"[{t}] {m.get('from', '?')}: {m.get('text', '')}"


def cmd_msg(args, cdir):
    store = _store(cdir)
    store.send_message(args.to, " ".join(args.text))
    print(f"coms: message delivered to {args.to}")
    return 0


def cmd_inbox(args, cdir):
    store = _store(cdir)
    if args.follow:
        seen = store.read_inbox()
        for m in seen:
            print(json.dumps(m) if args.json else _fmt_msg(m), flush=True)
        cursor = seen[-1]["ts"] if seen else 0.0
        try:
            while True:
                time.sleep(1.0)
                for m in store.read_inbox(since=cursor):
                    print(json.dumps(m) if args.json else _fmt_msg(m), flush=True)
                    cursor = m["ts"]
        except KeyboardInterrupt:
            return 0
    msgs = store.read_inbox(since=args.since)
    if args.json:
        print(json.dumps(msgs, indent=2))
    elif not msgs:
        print("coms: inbox empty")
    else:
        for m in msgs:
            print(_fmt_msg(m))
    return 0


def cmd_who(args, cdir):
    orch = _store(cdir).orchestrator()
    if not orch:
        print("coms: no orchestrator registered", file=sys.stderr)
        return 1
    print(orch)
    return 0


DISPATCH = {
    "do": cmd_do,
    "check": cmd_check,
    "unlock": cmd_unlock,
    "status": cmd_status,
    "enroll": cmd_enroll,
    "guard": cmd_guard,
    "unguard": cmd_unguard,
    "primer": cmd_primer,
    "msg": cmd_msg,
    "inbox": cmd_inbox,
    "who": cmd_who,
}


def build_parser():
    parser = argparse.ArgumentParser(prog="coms", description="parallel-work coordination")
    parser.add_argument("--dir", help="coms state dir (default $COMS_DIR or ~/.claude/coms)")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("do", help="run a command while holding a resource lease")
    d.add_argument("key")
    d.add_argument("--intent", default="")
    d.add_argument("--wait", type=float, default=600.0, help="seconds to wait for the lease")

    c = sub.add_parser("check", help="exit non-zero if the resource is held by another")
    c.add_argument("key")

    u = sub.add_parser("unlock", help="admin override: drop a stuck lease")
    u.add_argument("key")

    s = sub.add_parser("status", help="show live leases and the enrolled agents")
    s.add_argument("--json", action="store_true")

    e = sub.add_parser("enroll", help="register this session and show peers")
    e.add_argument("--intent", default="")
    e.add_argument("--role", default=None,
                   help="mark this actor's role, e.g. 'orchestrator' (sticky if omitted)")

    m = sub.add_parser("msg", help="send a message to another actor's inbox")
    m.add_argument("to", help="recipient holder id (see: coms who, coms status)")
    m.add_argument("text", nargs="+", help="message text")

    ib = sub.add_parser("inbox", help="read messages sent to you")
    ib.add_argument("--json", action="store_true")
    ib.add_argument("--since", type=float, default=None, help="only messages after this epoch ts")
    ib.add_argument("--follow", action="store_true", help="keep tailing for new messages")

    sub.add_parser("who", help="print the current orchestrator's holder id")

    sub.add_parser("primer", help="print the coms protocol (paste into any window)")

    g = sub.add_parser("guard", help="mark a repo, plus a backstop hook if it has "
                                     "no hooks of its own")
    g.add_argument("repo", nargs="?", default=".")

    ug = sub.add_parser("unguard", help="remove the coms hook from a repo")
    ug.add_argument("repo", nargs="?", default=".")
    return parser


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    cmd_args = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd_args = argv[:i], argv[i + 1:]
    args = build_parser().parse_args(argv)
    args.cmd = cmd_args
    return DISPATCH[args.command](args, args.dir)


if __name__ == "__main__":
    sys.exit(main())

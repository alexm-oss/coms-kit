"""The `coms` command line: the on-ramp every actor uses.

  coms do <repo:resource> [--intent T] [--wait S] -- <cmd...>
      Acquire the lease, run <cmd> while a background heartbeat renews the lease,
      release, and exit with the command's own exit code. This is the safe way to
      wrap a risky operation (a merge to main, a ship) so it takes its turn.

  coms check <resource>     exit 0 if free or held by us, non-zero if held by another
  coms status [--json]      show live holders and their intents
  coms unlock <resource>    admin override: drop a stuck lease
  coms enroll [--intent T]  register this session for visibility

Resource keys are conventionally "<repo>:<what>", e.g. sample:main, sample:ship.
Configuration: COMS_DIR (default ~/.claude/coms), COMS_HOLDER (default pid-<pid>).
"""

import argparse
import json
import os
import subprocess
import sys
import time

from coms.store import Store, Busy
from coms import decisions, handover, checkpoints, migration, notifications, rotate
from coms.inbox import cmd_inbox
from coms.identity import IdentityError, check as check_identity
from coms.owner_asks import append_owner_ask, init_owner_asks, open_owner_asks, show_owner_ask

DEFAULT_DIR = os.path.expanduser("~/.claude/coms")
EX_TEMPFAIL = 75


def coms_dir(explicit=None):
    return explicit or os.environ.get("COMS_DIR") or DEFAULT_DIR


def holder_id():
    return os.environ.get("COMS_HOLDER", "").strip() or f"pid-{os.getpid()}"


def _store(explicit_dir, ttl=30.0):
    return Store(coms_dir(explicit_dir), holder_id=holder_id(), ttl=ttl)


from coms.lease_run import command as cmd_do


def cmd_check(args, cdir):
    key = _resolve_key(args.key)
    st = _store(cdir).status()
    res = st["resources"].get(key)
    owned = (res and res["holder"] == holder_id()
             and os.environ.get("COMS_LEASE_KEY") == key
             and os.environ.get("COMS_LEASE_TOKEN") == str(res["token"]))
    if res and not owned and res["expires_at"] > time.time():
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
    st["threads"] = store.threads()
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
      coms do <name> -- <command>        e.g. coms do main -- git -C <main checkout> merge ...
  A bare name gets this repo's prefix (main -> <repo>:main). It waits your turn,
  runs the command, releases, and returns its exit code. If it cannot get the
  lease it refuses and does not run, so you never clobber someone else.
  A merge to a shared branch, a ship or a deploy needs the human's own yes first:
  coms do only makes agents take turns, it never grants permission.
  coms do runs in your own folder: name the target checkout, then prove the
  landing with: git merge-base --is-ancestor <sha> main

  Plain work (feature branches, editing files) needs no lease.
  See who holds what:  coms status
  Before asking the human anything:  coms decisions <keyword>
  When they answer, record it:  coms decide <topic> '<answer>' --quote '<their exact words>'
  (single quotes stop the shell running anything in their words; write ' as '\\'')
  A crashed holder's lease self-expires in 30s, so nothing stays wedged.

  Put your own holder id and session marker on EVERY coms command, reads included:
      COMS_HOLDER=<your-holder> COMS_SESSION_ID=<your-session-marker> coms <command>
  Messages: coms msg <holder> '<text>' appends to that agent's inbox. Read yours
  with coms inbox --consume (piped output only peeks; --peek just looks).
  A message never wakes an idle agent, so delivered is not read.

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
    """A bare resource (no colon) is prefixed with the canonical repo: main -> sample:main.

    Lets every window write `coms do main` and land on the same `sample:main` lease
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
    tag = f" [{t['role']}]" if t.get("role") in ("orchestrator", handover.REVIEW_ROLE) else ""
    return f"{t['holder']} on {where}{tag} ({t.get('intent') or 'idle'})"


def cmd_enroll(args, cdir):
    store = _store(cdir)
    store.register(repo=_current_repo(), worktree=_current_worktree(),
                   intent=args.intent, role=args.role, adopt=args.adopt_legacy, repair=args.repair_enrollment)
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
        print(f"\nOrchestrator is {orch}. Reach it any time:  {store.invocation_prefix()} msg {orch} \"<your update>\"")
    if not held and not peers:
        print("(no other actors active right now)")
    if store.holder_id.startswith("claude-"):
        print("\nRead earlier messages first: coms inbox --consume")
        print("Copy this complete own-window invocation:")
        print(store.invocation_prefix() + ' inbox --consume')
        print("Inbox Monitor command (persistent, 30-minute bound; rearm every 30 minutes):")
        print(store.monitor_command(orch))
    return 0


def _fmt_msg(m):
    import datetime
    t = datetime.datetime.fromtimestamp(m.get("ts", 0)).strftime("%H:%M:%S")
    return f"[{t}] {m.get('from', '?')}: {m.get('text', '')}"


def _named_holder(command):
    """msg and inbox need a real name: a pid-<pid> default reads a fresh, empty inbox and sends from a name nobody can answer."""
    if (os.environ.get("COMS_HOLDER") or "").strip():
        return True
    print(f"coms {command}: warning: set COMS_HOLDER=<your holder id> (without it this would {'read an empty one-off inbox' if command == 'inbox' else 'send from a one-off name nobody can answer'})", file=sys.stderr)
    return command != "inbox"


def cmd_msg(args, cdir):
    from coms.message_cards import validate_send
    if not _named_holder("msg"):
        return 2
    text = getattr(args, "_validated_message", None)
    if text is None:
        text = validate_send(" ".join(args.text))
    store = _store(cdir)
    store.send_message(args.to, text)
    print(f"coms: message delivered to {args.to}")
    return 0


def cmd_who(args, cdir):
    store = _store(cdir)
    requested_role = getattr(args, "role", None)
    if requested_role is None:
        holder = store.orchestrator()
        role = "orchestrator"
    else:
        role = requested_role
        actors = [t for t in store.threads() if t.get("role") == role]
        actors.sort(key=lambda t: t.get("updated_at", 0), reverse=True)
        holder = actors[0]["holder"] if actors else None
    if not holder:
        print(f"coms: no {role} registered", file=sys.stderr)
        return 1
    print(holder)
    return 0


def _owner_ask_paths(explicit_dir):
    root = coms_dir(explicit_dir)
    return (os.path.join(root, "owner-asks.jsonl"),
            os.path.join(root, "decisions.jsonl"))


def cmd_ask_owner(args, cdir):
    queue_path, decisions_path = _owner_ask_paths(cdir)
    result = append_owner_ask(
        queue_path, decisions_path, holder_id=holder_id(),
        repo=_current_repo(), topic=args.topic, question=" ".join(args.question))
    if result["created"]:
        print(f"coms: owner ask recorded for {result['row']['topic']}")
        return 0
    _print_existing_owner_ask(result)
    return 1


def _print_existing_owner_ask(result):
    row = result["row"]
    shown = result.get("shown") or {}
    if row:
        filer, filed_at = row["by"], row["ts"]
        topic = row["topic"]
    else:
        decision = result.get("decision") or {}
        filer, filed_at = decision.get("by", "unknown"), "not-filed"
        topic = decision.get("topic", "unknown")
    shown_at = shown.get("shown_at", "not-shown")
    shown_in = shown.get("shown_in", "not-shown")
    print(f"coms: owner ask refused status={result['status']} topic={topic} "
          f"filer={filer} filed_at={filed_at} shown_at={shown_at} shown_in={shown_in}")


def cmd_asks(args, cdir):
    queue_path, decisions_path = _owner_ask_paths(cdir)
    if args.init:
        init_owner_asks(queue_path, decisions_path)
        print("coms: owner asks initialized")
        return 0
    if args.show_topic:
        result = show_owner_ask(queue_path, decisions_path,
                                topic=args.show_topic, holder_id=holder_id())
        if result["status"] != "shown":
            _print_existing_owner_ask(result)
            return 1
        row, shown = result["row"], result["shown"]
        print(f"coms: shown {row['topic']}: {row['question']} "
              f"filer={row['by']} filed_at={row['ts']} "
              f"shown_at={shown['shown_at']} shown_in={shown['shown_in']}")
        return 0
    rows = open_owner_asks(queue_path, decisions_path)
    if not rows:
        print("coms: no owner asks open")
        return 0
    for row in rows:
        who = f"{row['by']} in {row['repo']}" if row["repo"] else row["by"]
        print(f"{row['topic']}: {row['question']} ({who}, filed_at={row['ts']})")
    return 0


DISPATCH = {
    "hook-notifications": notifications.command,
    "checkpoint-deliver": checkpoints.delivered,
    "migrate": migration.command,
    "checkpoint-ack": checkpoints.acknowledge,
    "handover": handover.command,
    "inbox-rotate": rotate.command,
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
    "identity-check": lambda a, d: (check_identity(_store(d), strict=True, require_bound=True) and 0),
    "who": cmd_who,
    "decide": lambda a, d: decisions.cmd_decide(a, coms_dir(d), holder_id(), _current_repo()),
    "decisions": lambda a, d: decisions.cmd_decisions(a, coms_dir(d)),
    "ask-owner": cmd_ask_owner,
    "asks": cmd_asks,
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
    e.add_argument("--adopt-legacy", action="store_true", help="own-window attestation to bind an unmarked legacy holder")
    e.add_argument("--repair-enrollment", action="store_true", help="preserve and replace malformed own enrollment")
    e.add_argument("--role", default=None,
                   help="mark this actor's role, e.g. 'orchestrator' (sticky if omitted)")

    m = sub.add_parser("msg", help="send a message to another actor's inbox")
    m.add_argument("to", help="recipient holder id (see: coms who, coms status)")
    m.add_argument("text", nargs="+", help="message text")

    ib = sub.add_parser("inbox", help="read messages sent to you")
    ib.add_argument("--json", action="store_true")
    ib.add_argument("--since", type=float, default=None, help="only messages after this epoch ts")
    ib.add_argument("--receipt", action="store_true", help="emit exact messages and checkpoint for explicit handling")
    ib.add_argument("--all", action="store_true", help="explicit full history, without advancing last-read")
    ib.add_argument("--consume", action="store_true", help="explicitly advance delivery even with redirected output")
    ib.add_argument("--exit-after-first-batch", action="store_true", help="exit follow after emitting its first nonempty complete batch")
    ib.add_argument("--peek", action="store_true", help="leave delivery checkpoint unchanged")
    ib.add_argument("--reset-checkpoint", action="store_true", help="replay after manually reconciling changed inbox bytes")
    ib.add_argument("--digest-mode", choices=("normal", "night", "lowpower", "full"))
    ib.add_argument("--batch-seconds", type=float, default=None, help="deprecated: ignored; FYI is pulled manually")
    ib.add_argument("--batch-read", action="store_true")
    ib.add_argument("--print-chars", type=int)
    ib.add_argument("--delivery-receipt", help="private staged delivery checkpoint for watch supervisor")
    ib.add_argument("--follow", action="store_true", help="keep tailing for new messages")

    sub.add_parser("identity-check", help="require an explicit session marker and check holder routing")

    who = sub.add_parser("who", help="print the current orchestrator's holder id, or an enrolled role")
    who.add_argument("--role", help="look up the most recently enrolled holder with this role")

    sub.add_parser("primer", help="print the coms protocol (paste into any window)")

    g = sub.add_parser("guard", help="mark a repo, plus a backstop hook if it has "
                                     "no hooks of its own")
    g.add_argument("repo", nargs="?", default=".")

    ug = sub.add_parser("unguard", help="remove the coms hook from a repo")
    ug.add_argument("repo", nargs="?", default=".")

    notifications.add_parser(sub)
    checkpoints.add_parser(sub)
    migration.add_parser(sub)
    handover.add_parser(sub)
    rotate.add_parser(sub)
    decisions.add_parsers(sub)
    ask = sub.add_parser("ask-owner", help="record one bounded pending question for the human")
    ask.add_argument("topic", help="short exact decision topic")
    ask.add_argument("question", nargs="+", help="the question to ask")
    asks = sub.add_parser("asks", help="show pending questions for the human")
    actions = asks.add_mutually_exclusive_group(required=True)
    actions.add_argument("".join(("-", "-init")), action="store_true",
                         help="create empty private question state without replacing existing state")
    actions.add_argument("".join(("-", "-open")), action="store_true",
                         help="list currently open questions")
    actions.add_argument("".join(("-", "-show")), dest="show_topic",
                         help="record a display of a pending topic")
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
    try:
        # The name check runs before the identity check, so a missing or blank
        # COMS_HOLDER on inbox exits 2 with the name hint, never 75.
        if args.command == "inbox" and not _named_holder(args.command):
            return 2
        if args.command == "msg":
            from coms.message_cards import validate_send
            args._validated_message = validate_send(" ".join(args.text))
        if args.command != "enroll":
            protected = args.command in ("inbox", "checkpoint-ack", "checkpoint-deliver", "handover", "migrate")
            check_identity(_store(args.dir), strict=protected)
        return DISPATCH[args.command](args, args.dir)
    except (IdentityError, ValueError, OSError, OverflowError, RecursionError) as exc:
        # An oversized number or a value nested past the interpreter's limit is bad input like any other.
        print(f"coms: {exc}", file=sys.stderr)
        return 75 if isinstance(exc, IdentityError) else 2


if __name__ == "__main__":
    sys.exit(main())

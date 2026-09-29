# Waking peers in the Claude desktop app

`coms msg` appends to a file, so it never reaches an idle window. In the Claude
desktop app's Code tab, sessions can message each other in a way that wakes
them: the session-management `send_message` tool
(`mcp__ccd_session_mgmt__send_message`) arrives in the other window as a new user
turn. Use it for anything a peer must act on: an approval, a task, a hold, a go.
Keep `coms msg` for the record and for agents outside the desktop app.

## Resolve the peer's current session

The room shows holder ids and worktrees, not desktop session ids. The session
list tool (`mcp__ccd_session_mgmt__list_sessions`) is the only current source for
those; old session logs on disk go stale and misroute. Pipe its JSON into the
helper, which joins on the worktree folder name (not the branch, which can
differ):

    <list_sessions JSON> | python3 [skill-root]/bin/coms-peer <holder-or-worktree>
    # one line per match, newest first: <session id> <tab> <title> <tab> <cwd>

`coms enroll` records the folder each window works in. Several windows in the
same folder (the main checkout, say) all match; pick one by title or intent.

## Send, then check it arrived

Send with `send_message(session_id, message)`. Depending on the permission mode,
the desktop app may ask the human to approve each send; keep that approval, it is
the human at the wheel. Say in the
message how you want the answer: a short one as `coms msg <your-id> "..."`, a
long one as a `send_message` back to your session.

Afterwards, treat silence as "maybe never delivered", not "working on it": check
the peer's last activity in the session list before assuming it acted.

Holder ids look like `claude-<8 hex>` when the coms Claude Code hook assigned
them. A `pid-<n>` entry for the same worktree is a stale shadow of an early
enroll; treat the `claude-` entry as the live window.

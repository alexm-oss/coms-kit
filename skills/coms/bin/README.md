# coms helper scripts

Small optional scripts used next to the coms CLI: inbox readers, a watch that wakes a window when mail
arrives, a desktop session lookup and the handover note writer. Every path comes from an argument or
an environment variable with a default, every script answers `--help`, and none holds a secret. They
need bash 3.2 or newer, python3, and the `coms` launcher on PATH.

## Wait for mail in a background task

    COMS_HOLDER=my-holder COMS_SESSION_ID=my-session bash ~/.claude/skills/coms/bin/watch-once.sh

- Run it as a BACKGROUND TASK (for example a background shell with a timeout up to two hours). The
  task ending is the wake-up: one wake-up per event.
- It prints what arrived, stops its watchers and exits. Start it again after you have acted on what it
  printed. A watch that is not running means nobody is listening.
- ASK, RULING, BLOCKER and DONE cards wake it at once; a valid FYI-only card waits for a manual inbox
  read. Untyped and malformed legacy mail always wakes, from every writer.
- After a wake-up, or whenever you read the inbox by hand:
  `COMS_HOLDER=my-holder COMS_SESSION_ID=my-session bash ~/.claude/skills/coms/bin/read-batch.sh 1200`.
  It prints only what is new, cut short, and advances the delivery checkpoint.
- Alternative: run `watch-all.sh` under a tool that streams output line by line. It runs until stopped.

## The scripts

| script | what it does |
|---|---|
| `watch-once.sh` | starts the watchers, waits for the first event or a timeout, stops them, prints, exits. See WATCH_MAX_S and WATCH_SETTLE_S |
| `watch-all.sh` | one process tree: the inbox digest, plus your own watchers through WATCH_EXTRA=/a.sh:/b.py |
| `watch-run.py` | the supervisor behind both watches; it owns and stops the watcher process groups |
| `inbox-digest.sh` | prints actionable cards and all untyped mail as it arrives; FYI-only mail waits for a manual read |
| `read-batch.sh` | prints complete messages after the delivery point, then advances it |
| `coms-peer` | joins the Claude desktop app's session list with a coms holder or worktree (see `references/claude-desktop.md`) |
| `handover-note` | writes a handover note for moving a window (see `references/handover.md`) |

## Environment you set

| variable | meaning |
|---|---|
| `COMS_HOLDER` | explicit holder id of this window, required by the inbox and handover scripts |
| `COMS_SESSION_ID` | explicit unique window marker, overrides an inherited native marker |
| `COMS_DIR` | the coms state folder (default `~/.claude/coms`) |
| `COMS_HANDOVER_DIR` | private handover notes, default `~/.local/share/coms/handovers` |
| `COMS_WATCH_DIR` | small state files for the watches (default `${XDG_STATE_HOME:-~/.local/state}/coms-orchestrator`) |
| `WATCH_EXTRA` | colon-separated paths to your own watcher scripts |

The CLI batch-seconds option and `INBOX_URGENT_FROM`, `INBOX_EXTRA_URG`, `INBOX_ROUTINE_RE` are
deprecated and ignored. An explicit CLI use prints one combined deprecation line to stderr per
invocation. The digest also ignores `INBOX_BATCH_S`, warns once at startup for old knobs that are
set, then removes them from its child environment.

## Add your own watcher

A watcher is a loop that prints one line per state change and nothing while all is well. Put it
somewhere outside this folder and pass it with `WATCH_EXTRA=/path/to/it.sh`.

## Checking the scripts

`bash inbox-digest.sh --once --from-start`, with explicit COMS_HOLDER and COMS_SESSION_ID and COMS_DIR
pointing at a scratch store, shows the digest without waiting. The kit's test suite (`./run-tests.sh`)
runs every script against fake inputs.

## Delivery and handling

Inbox scripts call the installed coms CLI and share its per-holder checkpoint. COMS_INBOX_FILE is
refused. `watch-once.sh` stages inbox output with peek and advances delivery only after the output is
visible; an interruption while it settles leaves that mail unread. A printed message is delivered, not
handled: record handled work and pending dependencies separately (see `references/handover.md`). For a
bounded wait without these scripts use:

    COMS_HOLDER=my-holder COMS_SESSION_ID=my-session coms inbox --peek --follow --exit-after-first-batch

Markers are cooperative routing checks, never authentication. On macOS, use resolved temporary paths
such as /private/tmp for scratch stores; symlinked state paths refuse.
## Pacing and host checks

Run these optional tools directly from the installed skill's `bin` directory.
The pacing tools accept values from your usage display or a trusted adapter.

- `./pace.py 61 '2044-01-01T12:30:00Z'` reads a Claude five-hour percent used and its reset time.
  Supply the current reset on each call so a new window discards the old rate samples. A later call
  can omit the reset to reuse the recorded one. The default target is 95 percent and can be set with
  `--target`. LOWER and RAISE suggest dropping or adding one lane level. HOLD keeps the current width.
  At 98 percent while the window is still open, HANDOVER prints the path to
  [the handover guide](../references/handover.md).
- `./codex-pace-watch.sh --usage-file ./weekly-meter.json --once` reads a JSON object such as
  `{"used_percent": 33.5, "resets_at": "2044-01-01T12:30:00Z"}`. Use the **weekly** percentage from
  your usage display and its renewal time, either as timezone-aware ISO time or Unix seconds.
  Set `CODEX_WEEKLY_USAGE_FILE` to supply the file every time or use `--usage-file - --once` for stdin.
  Without `--once` it polls for new samples and prints changes to over-pace, used-plan and expired
  states, plus a new-window event. The first reading anchors a line, and a fresh renewal resets the
  baseline. Set `--rate` in percentage points per hour and `--over-by` in points, for example based on
  the amount of allowance and time remaining. It sends no messages and takes no spending actions.
- `./load-watch.sh --once` shows one-minute load and optional thermal data. The regular watch exits
  with a line once strain persists for `STRAIN_MINUTES` (default 10). On Linux it reads `/proc/loadavg`
  and the hottest readable thermal zone in degrees Celsius, with thermal strain at 85 C by default.
  On macOS it reads `vm.loadavg` and optionally thermal pressure, with strain at level 2 by default.
  If thermal data is unavailable, load still works. Use `--samples FILE` with rows like `17.5 -` to
  exercise the threshold with synthetic load and no thermal reading. `--help` lists the thresholds.
- `./codex-now.py` summarizes the newest rollout, or pass literal ID fragments to select rollouts.
  `./codex-fleet.py 20` shows activity within a 20-minute lookback. Both read Codex app files and
  may break on Codex updates. They read rollout metadata under `CODEX_HOME` (default `~/.codex`).
  They print age, turn state and recognized model settings and omit transcript text, tool arguments,
  titles, paths and full identifiers. `unknown` means the relevant metadata was missing,
  unrecognized, or outside the bounded file read. Activity reflects writes observed on disk.

Pacing samples and the baseline are local files under `COMS_WATCH_DIR` by default. Each has a
`--state` override. Use separate state files for independent meter profiles. The dashboard-specific
usage writer is omitted. The examples above use direct usage values and a plain JSON input file.

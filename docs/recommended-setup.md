# Recommended setup

Run the installer from the kit checkout. Every profile includes the same
core CLI for leases, inboxes, decisions and handover. Profiles add optional guides
and helpers. Installing a profile creates no agent windows or scheduled
jobs and configures no live notification endpoint.

| Setup | Install | Pieces |
| --- | --- | --- |
| Core | `./install.sh` | Basic local coordination and the starter skill. |
| Solo | `./install.sh --profile solo` | Core plus `command` and `notify`. |
| Pair (recommended) | `./install.sh --profile pair` | Solo plus `codex-lead`, `pacing`, `handover-guide`, `load-watch` and `codex-view`. |
| Full room | `./install.sh --profile full-room` | Pair plus `governor`, `security`, `keeper`, `helper-brief`, `job-health`, `known-fixes` and `current-rules`. |

## Match the setup to your work

- **Core** covers agents that already know their tasks and just need shared
  locks, messages and decisions. Keep your usual windows.
- **Solo** suits one main window that talks to you and hands off bounded work
  as needed. It adds an orchestrator guide and an optional notifier. Keep
  routine status in COMS and configure an endpoint only if you want alerts.
- **Pair** is a good starting point for work split between two model
  families. Keep an orchestrator in one window and a lead for Codex work in
  a second. They share decisions and hand off tasks while separate workers
  can take bounded assignments. The extra standing window takes attention
  and uses model allowance when it works. The tools help inspect usage,
  hand over a window, monitor host strain and view local Codex activity.
- **Full room** adds guides and optional helpers for teams with enough
  concurrent work to need dedicated review or records. Use short-lived
  reviewer windows for occasional reads. Keep an extra role active when
  its queue warrants the attention and model usage. Enable helpers and
  schedule any checks yourself when they have a job to do.

## Decide who does what

| Role | What it owns |
| --- | --- |
| Orchestrator (`command`) | Talks to the owner, delegates, records decisions and routes questions. |
| Lead for Codex work (`codex-lead`) | Assigns Codex work, checks source and test evidence, and hands results to integration. |
| Integration lead (`controller`) | Organises writers, combines their changes, runs the shared tests and proves what landed. Add this role with `coms setup --add controller` when several lanes regularly need integration. |
| Test plan and rig reviewer (`governor`) | Helps size test plans, resolve rig faults and review changes to the permission boundary. |
| Security reviewer (`security`) | Independently reviews changes to the permission boundary, secret handling, sessions and keys before landing. |
| Keeper (`keeper`) | Files one sourced card per affected product for each decision and follow-up. |

In a fictional review, a writer tightened a log scrubber. The second reviewer
caught a check that would have restored the original, unsanitized line whenever
the scrubber changed it. A synthetic input showed the mistake before landing.
Use a reviewer from another model family for that read when available. Review subsequent fixes against the
previously reviewed commit. A small pair can open that review window when
needed and budget for one focused review session.

## Pick individual pieces

From an installed window, run `coms setup --status` for suggestions or
`coms setup --status --profile pair` for the two-window comparison. Use
`coms setup --add PIECE` to keep all your other choices. Older launchers
can use `./install.sh --status` and `./install.sh --add PIECE` from the
updated checkout.

To select a whole setup, start with a profile and repeat `--with PIECE` or `--without PIECE` for each
addition or omission. To add the integration guide and omit two optional
helpers, run:

    ./install.sh --profile pair --with controller --without codex-view --without load-watch
    ./install.sh --status

See the [operating model](../skills/coms/references/operating-model.md) for role
handoffs, and [operator tools](../skills/coms/bin/README.md) for configuring
helpers you choose to run.

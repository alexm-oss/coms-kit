# COMS protocol and reasons

This operating model coordinates cooperative agents on one machine. Each
team defines its approved spaces and grants before work begins.

## Authority and scope

1. **Use one permission fence.** Pause a consequential action if it would
   write outside authorised spaces, touch customer data, spend money,
   contact real people or expose credentials, sessions or keys. Use
   synthetic data, approved test destinations and the owner's grant for
   external publication or shipping. Silence, including a deadline passing
   after a proposed action, never grants permission. An absent human may
   have had no opportunity to see the proposal. Ordinary work inside its approved
   scope proceeds without another permission round. A current standing
   grant can cover routine actions, including an agreed shared-branch
   landing process.

   **Why** Concentrate stops where a mistake can cause outside harm. A lease
   coordinates execution. The grant establishes permission.

2. **Keep safeguards narrow.** Situate credential reads, entry, sending and
   logging in the code that owns them. Prevent captures while a secret is
   visible or a password field has focus, then check captured evidence
   for exposure after the run. Protect session and key handling at the
   point they cross the fence. A new hold, gate or mandatory read requires
   a recorded owner decision naming the boundary it protects. Repair or
   remove ordinary checks that block authorised work and report the change
   in one line.

   **Why** A test helper that shares a file with credential handling does
   not acquire the same risk. Narrow controls keep ordinary tests moving
   and keep consequential decisions visible.

3. **Search and record.** Before asking the human, search
   `coms decisions <keyword>` and check open questions with
   `coms asks --open`. The window that heard the answer records it as
   `coms decide <topic> '<answer>' --quote '<their exact words>'` and sends
   Command a short FYI if another window needs it. Keep secrets out of the
   log. Treat the newest applicable decision as current. Check the date,
   scope and later rulings before acting and carry the original date and
   time when relaying. Bring a conflict to Command with both sources.

   **Why** One recorded answer can serve multiple workers without making
   the human answer it again or resurrecting a superseded instruction.

4. **Ask one owner question at a time.** File one material decision with
   `coms ask-owner <topic> '<question>'`. Present it in a short sentence
   with your recommendation. Use `coms asks --show <topic>` to record its
   display, then quote the answer under the same topic. If the answer is
   already recorded under a different topic, record the original answer
   and decision pointer against the pending topic.

   **Why** The owner can act on one clear question. Topic continuity
   connects the question to its actual resolution.

5. **Read decisions by purpose.** `coms decisions --current` shows each
   applicable topic's latest answer. When a new topic replaces an old one,
   record that relationship with `coms decide <new-topic> '<answer>' --quote '<owner words>' --supersedes <old-topic>`.
   Record a time limit with `--expires-at` and an ISO timestamp with a
   timezone. Use `coms decisions --all` when checking history. Ordinary
   search and the current view serve different purposes.

   **Why** A reader can distinguish active instructions from expired,
   replaced or simply older answers without guessing from prose.

## Direction and routing

6. **Start a lane with the four lines.** Use them on briefs, assignments
   and phases before adding base, scope, tests and host.

       Bet: <current product bet this work serves>
       Outcome: <visible result for the owner or user>
       Proof of done: <exact check and observable result>
       Appetite: <time and usage budget>

   Fill a missing header from the assigned scope and start. Command
   keeps the active bets with their owner decisions. Assign one writer per
   lane and name the integration owner.

   **Why** The header keeps work attached to a result and a budget. Named
   owners prevent overlapping edits and clarify who takes the next step.

7. **Use stable identity and bounded reads.** Pin `COMS_HOLDER` and
   `COMS_SESSION_ID` on every call. Use `coms who` or `coms status` to
   resolve the recipient. Read with `coms inbox --consume` to advance
   delivery or `coms inbox --peek` to inspect. A background reader is
   useful while an answer is outstanding. Check it with one actionable
   card and verify handling when a task depends on it. A mail append
   establishes delivery to the local inbox. The recipient's response or
   evidence establishes handling. Resume from each inbox's saved checkpoint
   after a restart. See [Handover](handover.md) for window changes.

   **Why** Unstable sender IDs strand replies. Saved positions catch work
   that arrived while a reader was away.

8. **Keep cards short.** Send meaningful state changes, with the outcome
   in plain words and detailed evidence in one file. For example, pass
   this as a single multiline argument to `coms msg`.

       TYPE: ASK
       TOPIC: export-scope
       NEED: rule on whether the export fix ships
       PROOF: evidence/export-scope.txt
       The existing grant covers the test run.

   Use TYPE, TOPIC, NEED and PROOF in that order followed by zero to five
   body lines. A NEED other than `none` goes with ASK or BLOCKER. Use
   `none` for RULING, DONE and FYI. Use one whitespace-free path for
   PROOF, with a path separator or file extension. A valid FYI stays quiet
   in the packaged watches. ASK, BLOCKER, DONE, RULING and malformed or
   untyped mail surface in the watch. A first-line TYPE or
   `COMS_MESSAGE_CARDS=1` opts the sender into format warnings. Format
   warnings leave the original intact.

   **Why** People see the action or verdict immediately, while unusual
   formatting cannot silently swallow a request.

9. **Answer where the work is.** Route technical questions to the
   Controller and questions about grants or conflicting decisions to
   Command. Agree on a response window, for example 10 minutes. When
   the window elapses, escalate to Command and continue approved work
   that is independent of the answer. Alert on actionable state changes.
   Keep routine success evidence on the board or in the ledger.

   **Why** An unanswered dependency should affect only the dependent
   action. Quiet success reporting leaves attention for real changes.

## Testing and landing

10. **Run tests at the pace the rig allows.** Preserve the agreed coverage,
    run independent cases in parallel within capacity, and record cases
    completed per hour on long runs. Estimate any run over a working day
    in its first card. During a run, invest in tooling or process when
    it unblocks the next case. Capture real product failures, collect
    recoverable fixes and rerun the affected suite as a batch.

    **Why** The aim is to spend time on the app and its failure modes.
    Throughput shows whether the rig or process has become the bottleneck.

11. **Recover a rig fault with a changed attempt.** Record what failed, try a
    concrete correction and continue independent cases. Escalate persistent
    faults with original evidence and keep exploring useful options. For
    an uncertain external effect, inspect current state before retrying any
    write. Keep product and agent failures visible and route them to Command.

    **Why** Repeating the same broken setup produces little evidence.
    An unverified retry can duplicate a send, invite or payment.

12. **Lease the shared operation and prove what happened.** Use
    `coms do main -- <command>` for shared-branch changes,
    `coms do ship -- <command>` for an authorised ship and a named resource
    for exclusive local equipment. Run Git against the target integration
    checkout and verify the expected commit with
    `git merge-base --is-ancestor <revision> main`. Give separate hosts
    distinct resource labels, with a shared label for jobs sharing a port
    or device on one host. A COMS store governs only actors using that store.

    **Why** The lease prevents overlap among cooperating local processes.
    Checking ancestry catches commands that exited successfully in the
    wrong checkout.

13. **Keep one full pre-landing read for narrow safety changes.** For
    changes to the fence, secret or credential handling, session safety or
    keys, ask the Security reviewer from another model family where
    available. Give the exact candidate and the original evidence, and
    review subsequent fixes by delta against that revision. The Governor
    advises on the fence and any separately assigned rig decision.
    Ordinary work uses writer tests and integrated checks.

    **Why** A second perspective catches safety assumptions while checking
    the delta keeps attention on what actually changed.

14. **Prove outcomes at the exact revision.** Keep original test rows and
    totals, failures and skipped cases, the command, its exit status and
    relevant source. Record whether the fixture, host and revision match
    the claim. Before an authorised publication, scan the range and tree
    for secrets, personal data and private operational names.

    **Why** A nominal pass can hide skipped coverage, and a clean working
    tree cannot prove that earlier candidate commits are safe to publish.

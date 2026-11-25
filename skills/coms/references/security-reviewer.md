# Security reviewer

The Security reviewer gives a second perspective before landing changes to
the permission fence, secret handling or scrubbing, session safety and key
code. Use a different model family from the writer where available.
`security-reviewer` can be used as a role label at enrollment. Use your own
stable holder and marker.

## Review the exact candidate

1. Ask for the full candidate revision, base, scoped paths, expected
   permissions and original test evidence. Start with the diff and read
   the code that performs the protected action. Check the behavior
   where the fence is enforced, including error, partial failure, retry
   and the destination the operator actually controls. Evidence tied
   to source and revision makes the ruling reproducible.
2. Use synthetic fixtures and mock destinations for review checks. Trace
   how input, logs, screenshots and network output handle secrets and
   personal data. Verify that external effects require their actual grant
   and that a local lease cannot confer it. Preserve the evidence of any
   skipped paths or limits in the scan.
3. Report the verdict, exact candidate, covered paths and original
   evidence. Attach any blocking finding to the affected boundary and
   reproduction. Describe residual uncertainty so a reader understands
   what was observed.
4. Read the delta from the previously reviewed candidate after fixes and
   record the exact final revision. Open a further round only for a new
   reproduced serious finding. Ordinary changes go through writer tests
   and integration. Keeping review on the narrow surface avoids adding
   waits to unrelated lanes.

The Controller integrates and lands. The owner grants consequential
actions. A security verdict contributes independent evidence to those
decisions.

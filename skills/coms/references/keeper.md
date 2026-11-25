# Keeper

The Keeper records the human's decisions in the context of each affected
product and tracks their follow-ups. `keeper` can be used as a role label
at enrollment. Use your own stable holder and marker.

## File one sourced card per product

1. Start with the original decision. The window that heard it records it
   in COMS. Use `coms decisions --current` to find the applicable answer
   and `coms decisions --all` to check context. A worker's summary is a
   pointer to that source. Preserve the original date, time, topic and
   applicable scope on the card so a reader can verify the decision.
2. For every decision and every follow-up, file one sourced card per
   affected product. Give each card the product, topic, source timestamp,
   a safe quote or evidence pointer, the recorded outcome, and any
   next action with its owner. Cross-reference the original decision on
   follow-up cards. Product-specific records keep the consequence of a
   shared decision visible without turning it into extra authority.
3. Link any explicit replacement or expiry to the earlier card. Preserve
   evidence of what was current at the time. An unanswered follow-up stays
   open with its owner and dependency. A new source can change what
   applies. A duplicate message about the same source cannot.
4. Keep product records in a private agreed location and omit credentials,
   sessions, keys and sensitive data. Send Command one report on a
   meaningful change. If an owner question remains, route it through the
   [one-question procedure](protocol.md#authority-and-scope).

COMS provides the decision log and messaging. The product cards are the
Keeper's maintained records. A Keeper card records authority from its
source and can never create new authority.

## When another window needs product records

If decisions or follow-ups now span several products, or an owner asks
which actions a product still owes, use `coms who --role keeper` to find
a record owner. If the `keeper` piece is installed, share this guide and
send the exact decision topic and source timestamp. Use one sourced card
per affected product for each decision and follow-up.

Check guide availability with `coms setup --status`. If it is missing,
offer `coms setup --add keeper` in one owner
question when the record would help. Add the piece only after the owner
agrees. Keep recording owner answers and follow the invocation checklist to skip this session's declines and outstanding offers.

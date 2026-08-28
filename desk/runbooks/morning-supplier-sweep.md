---
cycle: morning-supplier-sweep
title: Morning supplier sweep
lease_seconds: 1800
lanes:
  - learning
  - drafting
  - deliveries
---

# Morning supplier sweep

Runs once each weekday morning for the Rolling Pin Bakery supplier desk. Any
agent on the roster may run it. The first one to answer the poke claims it, and
everyone else stands down.

## 0. Catch up before you start

Read the work log. If yesterday's cycles have no row, that cycle did not finish.
Finish the missing one before starting today's, and say so in your report.

## 1. Learning lane (always first)

Read every message the owner sent since the last run.

1. For each sent message, find the draft this desk had left on that thread.
2. If a draft existed, diff it against what she actually sent. **The edits are
   the correction.** Record what she added, what she cut, who she routed the
   request to, what she declined, and what she committed to.
3. If no draft existed, the whole message is the model. Record it anyway,
   including messages she originated on threads this desk never saw.
4. Append every lesson to the shared learning pool. Never keep it in your own
   memory: the next cycle may be run by a different model.

This lane runs before drafting, so today's drafts already carry today's lessons.

## 2. Drafting lane

For every thread with inbound mail:

1. **Sent reply gate.** Load the whole thread including sent mail. If the newest
   message is the owner's own reply, the thread is answered. Do not draft.
   Drafting on an answered thread produces a duplicate that usually also
   contradicts the position she already took.
2. If the newest inbound message is a courtesy close ("thanks", "confirmed"),
   the thread is closed. Do not draft.
3. Otherwise draft a reply, applying the learning pool guidance verbatim where
   it applies.
4. Save the draft. **Never send.** Sending is the owner's action, and the
   credential wrapper will refuse you if you try.

## 3. Deliveries lane

Check the standing delivery windows for changes requested by suppliers this
week. Report the count. If a supplier asked to move a window into the 4am to 9am
prep block, flag it rather than accepting it.

## 4. Sign off

Post one report with a line per lane, using exactly one status word: COMPLETE,
ZERO WORK, PARTIAL, or BLOCKED. Then append the row to the work log. No row
means the cycle did not finish. Silence is not allowed, and a lane with no work
still gets its line.

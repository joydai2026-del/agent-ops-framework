---
cycle: hourly-inbox-sweep
title: Hourly inbox sweep
lease_seconds: 600
lanes:
  - learning
  - drafting
---

# Hourly inbox sweep

The cheap lane. Runs on the hour during opening hours and needs no frontier
model judgment, so the agent that claims it should route the execution to the
cheapest model that can do the job well. The claiming agent is the router.

1. **Skip rule.** If a full cycle finished within the last 45 minutes, reply
   `SKIPPED-RECENT` and stop. Two sweeps racing over the same inbox produce
   duplicate drafts, and the claim lock only protects against concurrent runs,
   not against redundant ones.
2. **Learn first**, exactly as in the morning sweep: read the owner's sent mail
   since the last run, diff it against any draft this desk left, append the
   lessons to the shared pool.
3. **Then draft**, subject to the same sent reply gate.
4. Report one line: `N drafts, M threads closed`, or `ZERO WORK`, or
   `SKIPPED-RECENT`, or `BLOCKED`. Append a work log row only when drafts were
   created or the sweep was blocked, so the hourly lane does not bury the log.

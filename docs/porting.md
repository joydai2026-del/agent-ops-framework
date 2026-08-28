# Porting this to a real desk

Everything vendor specific is behind one of four seams. Nothing else in the repo
changes when you swap a model, a messaging system, or a mail provider.

| Seam | Demo version | Real version |
|---|---|---|
| Transport for the poke | `scripts/send-poke.sh` prints it | set `POKE_TRANSPORT` to a CLI that reads the message on stdin and takes `--channel` plus repeated `--mention` |
| The roster | `desk/channel/poke.conf` | the same file, with real agent identifiers. Adding or dropping an agent stays a one line config change |
| Message store | `agentops/mailbox.py` reads JSON files | an adapter over the real API, exposing `inbox`, `outbox`, `threads`, `existing_drafts`, `save_draft` |
| The model | `draft_reply` in `scripts/run_cycle.py` is a stub | a call to whichever model the claiming agent routed the lane to, given the thread, the runbook prose, and `pool.guidance()` |

Four things to keep as they are, because they are the load bearing parts:

1. **The claim stays a file on shared storage.** If the roster spans machines,
   the state directory has to be visible to all of them, and it has to be one
   filesystem so that `O_CREAT|O_EXCL` remains atomic. Object storage with
   eventual consistency does not satisfy this; a conditional write on a database
   row does, if you would rather run a database.
2. **The send block stays below the agent.** Give the agents the wrapper, never
   the client it wraps. A rule written in a prompt is a promise; a credential
   that cannot send is a guarantee.
3. **The work log stays readable without an agent.** Whatever the format, a
   human should be able to open it and see the week. That constraint is what
   makes it useful during an incident, which is the only time anyone reads it.
4. **The learning pool stays shared and in the project.** The moment it moves
   into one agent's memory, every other agent on the roster starts from zero.

## Adding a cycle

1. Write the runbook in `desk/runbooks/`, with a unique `cycle` and its lanes.
2. Add the poke text next to `poke.conf` naming the runbook, the claim rule, the
   catch-up rule, and the required closing statuses.
3. Copy the workflow, set the cron.
4. Add the new lanes to the work log schema deliberately. The appender refuses a
   lane it has no column for, on purpose: a lane silently dropped from the log
   is a lane nobody notices has stopped.

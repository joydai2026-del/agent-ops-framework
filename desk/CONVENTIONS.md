# Conventions for any agent running this desk

These bind whichever agent picks up the work, on whichever model, on whichever
surface. They are the part that does not change when the roster does.

## 1. The claiming agent is the router

Whoever answers the poke owns the cycle and decides, per step, the cheapest
model that can do that step well. Judgment, adjudication, and anything the owner
will read goes to the strong model. Mechanical bulk (classification, parsing,
form filling from an established template) goes to a cheap one. Nothing runs on
the frontier model that a smaller one could do.

This is a convention, not a code path, precisely so it survives a change of
vendor. If the router is a Claude agent it dispatches Claude subagents; if it is
a different vendor's agent it dispatches that vendor's. The runbook does not
name a model anywhere.

## 2. Silence is not allowed

Every scheduled cycle ends with a report carrying one line per declared lane and
exactly one status word: `COMPLETE`, `ZERO WORK`, `PARTIAL`, `BLOCKED`. A lane
with no work still gets its line. "Nothing happened" and "nobody woke up" must
never look the same from outside.

## 3. Drafts only

The desk drafts, the owner sends. This is not a promise the agent makes, it is a
property of the credential it holds: the wrapper in `bin/` refuses the send verb
below the agent, so an agent that decides to send has nothing to send with.

## 4. Learn before you draft

Every cycle reads the owner's own sent output before writing anything new, and
records what it learned in the shared pool. She should never have to teach the
same edit twice, no matter which agent picks the work up next.

## 5. Shared state, never private memory

Canonical state is the project folder and the shared pool. A lesson stored in
one agent's private memory is a silo the next agent cannot see, and that is the
bug this whole arrangement exists to remove.

## 6. Changing behaviour is a commit

Runbooks are versioned files. To change what the desk does, change the runbook
and log the change. Do not re-prompt an agent into new behaviour that no file
records.

#!/usr/bin/env python3
"""Run one cycle of the demo desk end to end.

This is the shape of a real run: claim, learn, draft, report, sign off. What it
deliberately does NOT contain is a model. The drafting step here is a stub that
assembles a reply skeleton and attaches the learning pool's guidance to it; a
real desk hands that same input to whichever model the router chose. Everything
around the stub is the part that is worth keeping, and the part that survives
the model being swapped.

    python3 scripts/run_cycle.py --agent agent-north
    python3 scripts/run_cycle.py --agent agent-south   # stands down, already claimed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentops import claim, learning, runbook, worklog  # noqa: E402
from agentops.mailbox import Mailbox  # noqa: E402
from agentops.report import CycleReport  # noqa: E402

DESK = ROOT / "desk"
DESK_LANES = ["learning", "drafting", "deliveries"]
WINDOW_WORDS = ("delivery", "drop", "window", "route", "minimum")


def draft_reply(thread_id: str, subject: str, inbound_body: str,
                guidance: list[str]) -> str:
    """Stub for the model call. Structure only, no generated prose.

    A real desk replaces this function body with a call to whichever model the
    claiming agent routed the drafting lane to, passing the same three inputs:
    the thread, the runbook's instructions, and the learning pool guidance.
    """
    head = f"Re: {subject}"
    ask = inbound_body.strip().splitlines()[0][:120]
    applied = "\n".join(f"  - {line}" for line in guidance[-5:]) or "  - (pool is empty)"
    return (
        f"{head}\n\n"
        f"[draft skeleton for thread {thread_id}]\n"
        f"Their ask: {ask}\n\n"
        f"Applied from the learning pool before writing:\n{applied}\n"
    )


def run(agent: str, cycle: str, state_dir: Path, log_path: Path,
        lease_override: int | None = None, keep_claim: bool = False) -> int:
    books = runbook.load_all(DESK / "runbooks")
    if cycle not in books:
        print(f"no runbook for cycle {cycle!r}; have {sorted(books)}", file=sys.stderr)
        return 2
    book = books[cycle]

    held = claim.claim_cycle(state_dir, book.cycle, agent,
                             lease_seconds=lease_override or book.lease_seconds)
    if held is None:
        holder = claim.current_claim(state_dir, book.cycle)
        print(f"{agent}: standing down, {holder.holder if holder else 'someone'} "
              f"claimed {book.cycle}")
        return 0
    if held.took_over_from:
        print(f"{agent}: took over a stale claim from {held.took_over_from}")
    print(f"{agent}: claimed {book.cycle} ({book.title})")

    mailbox = Mailbox(DESK / "data")
    report = CycleReport(cycle=book.cycle, agent=agent, lanes=book.lanes)
    pool = learning.LearningPool(DESK / "knowledge" / "learning-pool.jsonl")

    try:
        # --- Lane 1: learn from what the owner actually sent, before drafting.
        outbox = mailbox.outbox()
        inbound_threads = {m.thread for m in mailbox.inbox()}
        since = pool.high_water_mark(outbox)
        lessons = learning.learn_from_outbox(
            outbox, mailbox.existing_drafts(), since=since,
            inbound_threads=inbound_threads,
        )
        written = pool.add(lessons)
        if written:
            report.record("learning", f"{len(written)} new lessons from sent mail "
                                      f"({', '.join(sorted({l.kind for l in written}))})")
        else:
            report.record("learning", "ZERO WORK, no new sent mail since the last run")
        for lesson in written:
            print(f"  learned [{lesson.kind}] {lesson.subject}")
            for signal in lesson.signals:
                print(f"      signal: {signal}")

        # --- Lane 2: draft, with this run's lessons already in hand.
        guidance = pool.guidance()
        drafted, closed = 0, 0
        for thread_id, messages in sorted(mailbox.threads().items()):
            should, reason = learning.thread_state(messages)
            if not should:
                closed += 1
                print(f"  skip {thread_id}: {reason}")
                continue
            latest = learning.newest(messages)
            mailbox.save_draft(thread_id, latest.subject,
                               draft_reply(thread_id, latest.subject,
                                           latest.body, guidance))
            drafted += 1
            print(f"  draft {thread_id}: saved (never sent)")
        report.record("drafting", f"{drafted} drafts saved, {closed} threads needed none")

        # --- Lane 3: a plain domain lane, present so it can report ZERO WORK.
        if "deliveries" in book.lanes:
            flagged = [m.thread for m in mailbox.inbox()
                       if any(word in m.body.lower() for word in WINDOW_WORDS)]
            report.record("deliveries",
                          f"{len(flagged)} supplier window or minimum changes flagged"
                          if flagged else "ZERO WORK, no window changes requested")
    finally:
        if not keep_claim:
            claim.release_cycle(state_dir, book.cycle, agent)

    print()
    print(report.render())
    row = worklog.append(log_path, report, schema=DESK_LANES)
    print(f"\nwork log row appended: {log_path}")
    print("  " + " | ".join(f"{k}={v}" for k, v in row.items()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, help="the agent claiming this cycle")
    parser.add_argument("--cycle", default="morning-supplier-sweep")
    parser.add_argument("--state", default=str(DESK / "state"))
    parser.add_argument("--log", default=str(DESK / "work-log.csv"))
    parser.add_argument("--lease", type=int, default=None,
                        help="override the runbook lease, in seconds")
    parser.add_argument("--keep-claim", action="store_true",
                        help="hold the claim after finishing, to show a second "
                             "agent standing down")
    args = parser.parse_args()
    return run(args.agent, args.cycle, Path(args.state), Path(args.log),
               args.lease, args.keep_claim)


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests that use real concurrent processes.

The serial tests elsewhere prove the logic; these prove the locking. A claim
checked serially always looks correct, because the whole failure mode is what
happens in the window between one agent reading and another writing.
"""

import multiprocessing as mp
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentops import worklog  # noqa: E402
from agentops.claim import claim_cycle  # noqa: E402
from agentops.learning import LearningPool, Lesson  # noqa: E402
from agentops.report import CycleReport  # noqa: E402


def _try_claim(args):
    state, agent, barrier = args
    barrier.wait()
    got = claim_cycle(state, "morning", agent, lease_seconds=300)
    return agent if got is not None else None


def _try_takeover(args):
    state, agent, barrier = args
    barrier.wait()
    # Every worker sees the same expired claim and races to take it over.
    got = claim_cycle(state, "morning", agent, lease_seconds=300, now=2_000_000.0)
    return agent if got is not None else None


def _append_row(args):
    log, cycle, barrier = args
    barrier.wait()
    report = CycleReport(cycle=cycle, agent=f"agent-{cycle}",
                         lanes=["learning", "drafting", "deliveries"])
    for lane in ("learning", "drafting", "deliveries"):
        report.record(lane, f"{cycle} did {lane}")
    worklog.append(log, report, when=datetime(2026, 3, 2, tzinfo=timezone.utc),
                   schema=["learning", "drafting", "deliveries"])
    return cycle


def _add_lesson(args):
    pool_path, barrier = args
    barrier.wait()
    lesson = Lesson(kind="edit_of_draft", thread="t", source_message="out-1",
                    subject="s", summary="the same lesson from every worker")
    return len(LearningPool(pool_path).add([lesson]))


class ConcurrentClaimTests(unittest.TestCase):
    WORKERS = 8

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _race(self, target, args_extra=()):
        ctx = mp.get_context("fork")
        with ctx.Manager() as manager:
            barrier = manager.Barrier(self.WORKERS)
            payload = [(str(self.state), f"agent-{i}", barrier, *args_extra)
                       for i in range(self.WORKERS)]
            with ctx.Pool(self.WORKERS) as pool:
                return [r for r in pool.map(target, payload) if r]

    def test_a_real_race_still_produces_exactly_one_winner(self):
        # The bug this catches: a claim published in two steps (create, then
        # write) is briefly an empty file, and a contender that reads it mid
        # publish concludes the lock is corrupt and becomes a second winner.
        winners = self._race(_try_claim)
        self.assertEqual(len(winners), 1, f"expected one winner, got {winners}")

    def test_simultaneous_takeovers_of_one_expired_claim_yield_one_winner(self):
        claim_cycle(self.state, "morning", "agent-dead", lease_seconds=60,
                    now=1_000_000.0)
        winners = self._race(_try_takeover)
        self.assertEqual(len(winners), 1, f"expected one winner, got {winners}")


class ConcurrentWriteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_simultaneous_sign_offs_do_not_lose_a_row(self):
        # Read-modify-write on a shared CSV loses a row unless the whole
        # transaction is guarded. A lost row reads as "that cycle never
        # finished", which is the single claim the work log has to get right.
        log = self.dir / "work-log.csv"
        cycles = [f"cycle-{i}" for i in range(8)]
        ctx = mp.get_context("fork")
        with ctx.Manager() as manager:
            barrier = manager.Barrier(len(cycles))
            with ctx.Pool(len(cycles)) as pool:
                pool.map(_append_row, [(str(log), c, barrier) for c in cycles])
        logged = {row["cycle"] for row in worklog.read(log)}
        self.assertEqual(logged, set(cycles))

    def test_simultaneous_pool_writers_do_not_double_count_one_lesson(self):
        # Check-then-append is only deduplication if nothing can interleave.
        path = self.dir / "pool.jsonl"
        ctx = mp.get_context("fork")
        with ctx.Manager() as manager:
            barrier = manager.Barrier(8)
            with ctx.Pool(8) as pool:
                written = pool.map(_add_lesson, [(str(path), barrier)] * 8)
        self.assertEqual(sum(written), 1, "the same lesson was recorded twice")
        self.assertEqual(len(LearningPool(path).load()), 1)


if __name__ == "__main__":
    unittest.main()

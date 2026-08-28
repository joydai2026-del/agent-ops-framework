import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentops.claim import (  # noqa: E402
    CORRUPT_GRACE_SECONDS, ClaimError, claim_cycle, current_claim, release_cycle,
)


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_agent_wins_and_the_rest_stand_down(self):
        first = claim_cycle(self.state, "morning", "agent-north")
        self.assertIsNotNone(first)
        self.assertEqual(first.holder, "agent-north")
        for other in ("agent-south", "agent-relief"):
            self.assertIsNone(claim_cycle(self.state, "morning", other))

    def test_whole_roster_poked_at_once_yields_exactly_one_winner(self):
        roster = [f"agent-{i}" for i in range(12)]
        winners = [a for a in roster
                   if claim_cycle(self.state, "morning", a) is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(current_claim(self.state, "morning").holder, winners[0])

    def test_same_agent_poked_twice_keeps_its_own_live_claim(self):
        first = claim_cycle(self.state, "morning", "agent-north")
        again = claim_cycle(self.state, "morning", "agent-north")
        self.assertIsNotNone(again)
        self.assertEqual(again.token, first.token)

    def test_expired_lease_is_taken_over_and_the_takeover_is_recorded(self):
        claim_cycle(self.state, "morning", "agent-north", lease_seconds=60,
                    now=1_000_000.0)
        # Still inside the lease: nobody may take it.
        self.assertIsNone(claim_cycle(self.state, "morning", "agent-south",
                                      now=1_000_030.0))
        # Past the lease: the desk must not stay wedged behind a dead agent.
        taken = claim_cycle(self.state, "morning", "agent-south", now=1_000_061.0)
        self.assertIsNotNone(taken)
        self.assertEqual(taken.holder, "agent-south")
        self.assertEqual(taken.took_over_from, "agent-north")

    def test_two_cycles_are_independent(self):
        self.assertIsNotNone(claim_cycle(self.state, "morning", "agent-north"))
        self.assertIsNotNone(claim_cycle(self.state, "hourly", "agent-south"))

    def test_release_is_holder_only(self):
        claim_cycle(self.state, "morning", "agent-north")
        with self.assertRaises(ClaimError):
            release_cycle(self.state, "morning", "agent-south")
        self.assertTrue(release_cycle(self.state, "morning", "agent-north"))
        self.assertIsNone(current_claim(self.state, "morning"))
        # After release the next poke can be claimed normally.
        self.assertIsNotNone(claim_cycle(self.state, "morning", "agent-south"))

    def test_release_of_an_unheld_cycle_is_false_not_an_error(self):
        self.assertFalse(release_cycle(self.state, "morning", "agent-north"))

    def test_a_freshly_unreadable_lock_is_left_alone(self):
        # The dangerous direction. Treating an unreadable lock as debris
        # immediately is how a contender that catches a claim mid write
        # deletes it and becomes a second winner. Claims are published
        # complete, so waiting costs one cycle and clearing costs correctness.
        claim_cycle(self.state, "morning", "agent-north")
        path = self.state / "morning.claim.json"
        path.write_text("{ truncated", encoding="utf-8")
        self.assertIsNone(claim_cycle(self.state, "morning", "agent-south"))

    def test_lasting_debris_does_not_wedge_the_desk_forever(self):
        claim_cycle(self.state, "morning", "agent-north")
        path = self.state / "morning.claim.json"
        path.write_text("{ truncated", encoding="utf-8")
        old = time.time() - (CORRUPT_GRACE_SECONDS + 60)
        os.utime(path, (old, old))
        taken = claim_cycle(self.state, "morning", "agent-south")
        self.assertIsNotNone(taken)
        self.assertEqual(taken.holder, "agent-south")

    def test_an_agents_own_expired_claim_gets_no_free_pass(self):
        # Re-entrancy is for a live claim only. If the lease lapsed, the same
        # named agent restarting must go through takeover like anyone else, so
        # the rescue is recorded rather than looking like an unbroken run.
        claim_cycle(self.state, "morning", "agent-north", lease_seconds=60,
                    now=1_000_000.0)
        again = claim_cycle(self.state, "morning", "agent-north", now=1_000_061.0)
        self.assertIsNotNone(again)
        self.assertEqual(again.took_over_from, "agent-north")

    def test_a_replaced_agent_cannot_release_its_successors_claim(self):
        # The slow-worker cleanup bug: an agent that overran its lease finishes,
        # calls release, and deletes the live claim of the agent that took over.
        stale = claim_cycle(self.state, "morning", "agent-north", lease_seconds=60,
                            now=1_000_000.0)
        claim_cycle(self.state, "morning", "agent-south", now=1_000_061.0)
        with self.assertRaises(ClaimError):
            release_cycle(self.state, "morning", "agent-north", token=stale.token)
        self.assertEqual(current_claim(self.state, "morning").holder, "agent-south")

    def test_anonymous_and_nonsense_claims_are_refused(self):
        with self.assertRaises(ClaimError):
            claim_cycle(self.state, "morning", "")
        with self.assertRaises(ClaimError):
            claim_cycle(self.state, "morning", "agent-north", lease_seconds=0)

    def test_claim_survives_a_process_boundary(self):
        # The lock is a file, not memory, so a fresh process sees it. This is
        # what lets a GitHub Actions run and a local backup clock coordinate.
        claim_cycle(self.state, "morning", "agent-north")
        code = (
            "import sys; sys.path.insert(0, %r);"
            "from agentops.claim import claim_cycle;"
            "print(claim_cycle(%r, 'morning', 'agent-south'))"
            % (str(Path(__file__).resolve().parent.parent), str(self.state))
        )
        out = os.popen(f"{sys.executable} -c \"{code}\"").read()
        self.assertIn("None", out)


if __name__ == "__main__":
    unittest.main()

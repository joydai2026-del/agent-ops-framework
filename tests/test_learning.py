import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentops.learning import (  # noqa: E402
    CLOSED_NO_DRAFT, COURTESY_CLOSE, DIRECT_REPLY, DRAFT_WARRANTED,
    EDIT_OF_DRAFT, ORIGINATED, LearningPool, Message, diff_draft_against_sent,
    learn_from_outbox, thread_state,
)


def msg(mid, thread, direction, sent_at, body, subject="Subject"):
    return Message(id=mid, thread=thread, direction=direction, sent_at=sent_at,
                   subject=subject, body=body)


class SentReplyGateTests(unittest.TestCase):
    """The gate that stops the desk answering a thread the owner already answered."""

    def test_newest_message_outbound_closes_the_thread(self):
        thread = [
            msg("in-1", "t", "inbound", "2026-03-02T08:00:00Z", "Can you confirm?"),
            msg("out-1", "t", "outbound", "2026-03-02T09:00:00Z", "Confirmed, see you."),
        ]
        should, reason = thread_state(thread)
        self.assertFalse(should)
        self.assertEqual(reason, CLOSED_NO_DRAFT)

    def test_the_gate_needs_the_whole_thread_not_just_the_inbound_message(self):
        # This is the exact failure the gate exists for: judging from inbound
        # mail alone hides the owner's reply and produces a duplicate draft
        # that frequently contradicts the position she already took.
        inbound_only = [msg("in-1", "t", "inbound", "2026-03-02T08:00:00Z", "Can you confirm?")]
        self.assertTrue(thread_state(inbound_only)[0])
        with_sent = inbound_only + [
            msg("out-1", "t", "outbound", "2026-03-02T09:00:00Z", "Confirmed.")]
        self.assertFalse(thread_state(with_sent)[0])

    def test_out_of_order_input_is_sorted_by_timestamp(self):
        thread = [
            msg("out-1", "t", "outbound", "2026-03-02T09:00:00Z", "Confirmed."),
            msg("in-2", "t", "inbound", "2026-03-02T11:00:00Z", "One more question."),
        ]
        should, reason = thread_state(list(reversed(thread)))
        self.assertTrue(should)
        self.assertEqual(reason, DRAFT_WARRANTED)

    def test_courtesy_close_needs_no_reply(self):
        thread = [msg("in-1", "t", "inbound", "2026-03-02T08:00:00Z",
                      "Thanks, confirmed on our end.")]
        self.assertEqual(thread_state(thread)[1], COURTESY_CLOSE)

    def test_empty_thread_is_not_drafted_on(self):
        self.assertFalse(thread_state([])[0])


class DiffTests(unittest.TestCase):
    def test_added_and_removed_sentences_are_both_captured(self):
        draft = "Please include the rye in the quote. I can look at a volume step up."
        sent = "Please leave the rye off the quote. Same volume, no step up."
        added, removed = diff_draft_against_sent(draft, sent)
        self.assertTrue(any("leave the rye off" in a for a in added))
        self.assertTrue(any("include the rye" in r for r in removed))

    def test_an_unedited_draft_produces_no_diff(self):
        body = "Same volume on the high-gluten line. Send the sheet by Friday."
        self.assertEqual(diff_draft_against_sent(body, body), ([], []))


class LearnFromOutboxTests(unittest.TestCase):
    def test_edited_draft_is_the_highest_value_lesson(self):
        outbox = [msg("out-1", "t", "outbound", "2026-03-02T13:00:00Z",
                      "Please leave the rye off the quote. Same volume, no step up.")]
        drafts = {"t": "Please include the rye in the quote. I can look at a step up."}
        [lesson] = learn_from_outbox(outbox, drafts, inbound_threads={"t"})
        self.assertEqual(lesson.kind, EDIT_OF_DRAFT)
        self.assertTrue(lesson.added and lesson.removed)

    def test_unchanged_draft_is_recorded_as_a_confirmed_pattern(self):
        body = "Same volume, no step up."
        [lesson] = learn_from_outbox([msg("out-1", "t", "outbound", "2026-03-02T13:00:00Z", body)],
                                     {"t": body}, inbound_threads={"t"})
        self.assertEqual(lesson.kind, EDIT_OF_DRAFT)
        self.assertIn("unchanged", lesson.summary)

    def test_reply_with_no_draft_is_learned_from_anyway(self):
        [lesson] = learn_from_outbox(
            [msg("out-1", "t", "outbound", "2026-03-02T13:00:00Z", "Yes to 500 units.")],
            {}, inbound_threads={"t"})
        self.assertEqual(lesson.kind, DIRECT_REPLY)

    def test_message_the_desk_never_saw_is_still_recorded(self):
        # The point is a durable model of how the desk is run, not only draft repair.
        [lesson] = learn_from_outbox(
            [msg("out-9", "t-new", "outbound", "2026-03-02T07:00:00Z",
                 "Any visit has to land after 1pm.")],
            {}, inbound_threads={"t-known"})
        self.assertEqual(lesson.kind, ORIGINATED)

    def test_since_watermark_excludes_already_learned_mail(self):
        outbox = [
            msg("out-1", "t1", "outbound", "2026-03-01T10:00:00Z", "Old news."),
            msg("out-2", "t2", "outbound", "2026-03-02T10:00:00Z", "New news."),
        ]
        lessons = learn_from_outbox(outbox, {}, since="2026-03-01T10:00:00Z")
        self.assertEqual([l.source_message for l in lessons], ["out-2"])

    def test_inbound_messages_are_never_learned_from_as_output(self):
        inbound = [msg("in-1", "t", "inbound", "2026-03-02T10:00:00Z", "Hello.")]
        self.assertEqual(learn_from_outbox(inbound, {}), [])

    def test_signals_name_the_reusable_approach(self):
        outbox = [msg("out-1", "t", "outbound", "2026-03-02T13:00:00Z",
                      "We cannot carry that volume. Please reach out to the Vine Street shop.")]
        drafts = {"t": "I can carry the larger minimum for you. Happy to take all 1000 units."}
        [lesson] = learn_from_outbox(outbox, drafts, inbound_threads={"t"})
        self.assertIn("routes the request to someone else", lesson.signals)
        self.assertIn("declines something the draft accepted", lesson.signals)


class LearningPoolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "nested" / "pool.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _lessons(self):
        return learn_from_outbox(
            [msg("out-1", "t", "outbound", "2026-03-02T13:00:00Z", "Same volume.")],
            {"t": "A step up in volume, please."}, inbound_threads={"t"})

    def test_pool_is_created_on_first_write_and_survives_reload(self):
        pool = LearningPool(self.path)
        self.assertEqual(pool.load(), [])
        self.assertEqual(len(pool.add(self._lessons())), 1)
        self.assertEqual(len(LearningPool(self.path).load()), 1)

    def test_rerunning_the_same_cycle_does_not_double_count(self):
        pool = LearningPool(self.path)
        pool.add(self._lessons())
        self.assertEqual(pool.add(self._lessons()), [])
        self.assertEqual(len(pool.load()), 1)

    def test_watermark_lets_the_next_run_start_where_this_one_stopped(self):
        outbox = [
            msg("out-1", "t1", "outbound", "2026-03-01T10:00:00Z", "First."),
            msg("out-2", "t2", "outbound", "2026-03-02T10:00:00Z", "Second."),
        ]
        pool = LearningPool(self.path)
        self.assertIsNone(pool.high_water_mark(outbox))
        pool.add(learn_from_outbox(outbox, {}))
        self.assertEqual(pool.high_water_mark(outbox), "2026-03-02T10:00:00Z")
        self.assertEqual(
            learn_from_outbox(outbox, {}, since=pool.high_water_mark(outbox)), [])

    def test_guidance_is_what_the_drafting_step_reads(self):
        pool = LearningPool(self.path)
        pool.add(self._lessons())
        [line] = pool.guidance()
        self.assertIn(EDIT_OF_DRAFT, line)

    def test_pool_is_a_shared_file_so_a_second_agent_inherits_it(self):
        LearningPool(self.path).add(self._lessons())
        other_agent_view = LearningPool(self.path)
        self.assertEqual(len(other_agent_view.guidance()), 1)


if __name__ == "__main__":
    unittest.main()

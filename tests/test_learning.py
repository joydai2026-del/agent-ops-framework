import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentops.learning import (  # noqa: E402
    CLOSED_NO_DRAFT, COURTESY_CLOSE, DIRECT_REPLY, DRAFT_WARRANTED,
    EDIT_OF_DRAFT, ORIGINATED, LearningPool, Message, diff_draft_against_sent,
    learn_from_outbox, parse_time, thread_state,
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

    def test_a_thank_you_that_also_asks_for_something_stays_open(self):
        # The dangerous direction. Drafting one reply too many is recoverable;
        # closing a thread that contained a live request means the desk never
        # answers it and nobody finds out.
        for body in ("Thanks! But can you also send the invoice?",
                     "Thanks, confirmed. One more thing, when does the rate change?",
                     "Got it, thank you. Please forward the signed sheet."):
            with self.subTest(body=body):
                thread = [msg("in-1", "t", "inbound", "2026-03-02T08:00:00Z", body)]
                should, reason = thread_state(thread)
                self.assertTrue(should, f"wrongly closed: {reason}")

    def test_a_long_message_opening_with_thanks_is_not_a_courtesy_close(self):
        body = "Thanks. " + ("We should also revisit the delivery schedule. " * 6)
        thread = [msg("in-1", "t", "inbound", "2026-03-02T08:00:00Z", body)]
        self.assertTrue(thread_state(thread)[0])

    def test_empty_thread_is_not_drafted_on(self):
        self.assertFalse(thread_state([])[0])


class TimestampTests(unittest.TestCase):
    """Timestamps are compared, never sorted as text."""

    def test_mixed_offsets_order_by_real_time_not_by_string(self):
        # 09:00-05:00 is 14:00 UTC, which is LATER than 13:30Z, but sorts
        # earlier as a string. Under string ordering the gate sees the inbound
        # message as newest and drafts a duplicate of a reply already sent.
        thread = [
            msg("in-1", "t", "inbound", "2026-03-02T13:30:00Z", "Can you confirm?"),
            msg("out-1", "t", "outbound", "2026-03-02T09:00:00-05:00", "Confirmed."),
        ]
        should, reason = thread_state(thread)
        self.assertFalse(should, "string ordering let a closed thread look open")
        self.assertEqual(reason, CLOSED_NO_DRAFT)

    def test_the_same_instant_in_two_notations_is_one_instant(self):
        self.assertEqual(parse_time("2026-03-02T14:00:00Z"),
                         parse_time("2026-03-02T09:00:00-05:00"))

    def test_a_timestamp_with_no_timezone_is_refused_not_guessed(self):
        with self.assertRaises(ValueError):
            parse_time("2026-03-02T09:00:00")
        with self.assertRaises(ValueError):
            Message.from_dict({"id": "x", "thread": "t", "direction": "inbound",
                               "sent_at": "2026-03-02 09:00", "subject": "s",
                               "body": "b"})


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

    def test_seen_ids_are_what_exclude_already_learned_mail(self):
        outbox = [
            msg("out-1", "t1", "outbound", "2026-03-01T10:00:00Z", "Old news."),
            msg("out-2", "t2", "outbound", "2026-03-02T10:00:00Z", "New news."),
        ]
        lessons = learn_from_outbox(outbox, {}, since="2026-03-01T10:00:00Z",
                                    seen_ids={"out-1"})
        self.assertEqual([l.source_message for l in lessons], ["out-2"])

    def test_a_second_message_in_the_same_second_is_not_skipped(self):
        # An exclusive timestamp watermark drops this one permanently, and it
        # is the case that actually happens: two replies fired back to back.
        outbox = [
            msg("out-1", "t1", "outbound", "2026-03-02T10:00:00Z", "First."),
            msg("out-2", "t2", "outbound", "2026-03-02T10:00:00Z", "Second."),
        ]
        lessons = learn_from_outbox(outbox, {}, since="2026-03-02T10:00:00Z",
                                    seen_ids={"out-1"})
        self.assertEqual([l.source_message for l in lessons], ["out-2"])

    def test_mail_arriving_late_with_an_older_timestamp_is_still_learned_from(self):
        outbox = [
            msg("out-2", "t2", "outbound", "2026-03-02T10:00:00Z", "Newer."),
            msg("out-1", "t1", "outbound", "2026-03-01T10:00:00Z", "Older, seen late."),
        ]
        lessons = learn_from_outbox(outbox, {}, since="2026-03-02T10:00:00Z",
                                    seen_ids={"out-2"})
        self.assertEqual([l.source_message for l in lessons], ["out-1"])

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
        self.assertEqual(pool.learned_message_ids(), {"out-1", "out-2"})
        self.assertEqual(
            learn_from_outbox(outbox, {}, since=pool.high_water_mark(outbox),
                              seen_ids=pool.learned_message_ids()), [])

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

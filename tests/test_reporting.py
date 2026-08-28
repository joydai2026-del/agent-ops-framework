import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentops import worklog  # noqa: E402
from agentops.mailbox import Mailbox, SendBlocked  # noqa: E402
from agentops.report import (  # noqa: E402
    BLOCKED, COMPLETE, PARTIAL, ZERO_WORK, CycleReport, ReportError,
)
from agentops.runbook import RunbookError, load, load_all  # noqa: E402

STAMP = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)


def report(**lines):
    r = CycleReport(cycle="morning-supplier-sweep", agent="agent-north",
                    lanes=["learning", "drafting", "deliveries"])
    for lane, line in lines.items():
        r.record(lane, line)
    return r


class ReportContractTests(unittest.TestCase):
    def test_all_lanes_answered_is_complete(self):
        r = report(learning="3 lessons", drafting="2 drafts", deliveries="1 flagged")
        self.assertEqual(r.status(), COMPLETE)

    def test_a_missing_lane_downgrades_to_partial_and_stays_visible(self):
        r = report(learning="3 lessons", drafting="2 drafts")
        self.assertEqual(r.status(), PARTIAL)
        self.assertEqual(r.missing_lanes(), ["deliveries"])
        self.assertIn("NOT RUN", r.render())

    def test_nothing_to_do_is_a_result_that_must_be_said(self):
        r = report(learning="ZERO WORK", drafting="ZERO WORK", deliveries="ZERO WORK")
        self.assertEqual(r.status(), ZERO_WORK)
        self.assertIn(ZERO_WORK, r.render())

    def test_one_blocked_lane_blocks_the_cycle(self):
        r = report(learning="3 lessons", drafting="BLOCKED credential refused",
                   deliveries="ZERO WORK")
        self.assertEqual(r.status(), BLOCKED)

    def test_an_undeclared_lane_cannot_be_smuggled_into_the_report(self):
        with self.assertRaises(ReportError):
            report(invoices="12 paid")

    def test_an_empty_line_is_not_an_answer(self):
        with self.assertRaises(ReportError):
            report(learning="   ")

    def test_a_cycle_with_no_lanes_cannot_be_reported(self):
        with self.assertRaises(ReportError):
            CycleReport(cycle="c", agent="a", lanes=[]).render()


class WorkLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "work-log.csv"
        self.schema = ["learning", "drafting", "deliveries"]

    def tearDown(self):
        self._tmp.cleanup()

    def test_one_row_per_cycle_is_the_sign_off(self):
        row = worklog.append(self.path,
                             report(learning="3", drafting="2", deliveries="1"),
                             when=STAMP, schema=self.schema)
        self.assertEqual(row["status"], COMPLETE)
        self.assertEqual(row["signed_off"], "2026-03-02T14:30:00+00:00")
        self.assertEqual(len(worklog.read(self.path)), 1)

    def test_a_repoked_cycle_updates_its_row_instead_of_duplicating_it(self):
        worklog.append(self.path, report(learning="3", drafting="2"),
                       when=STAMP, schema=self.schema)
        worklog.append(self.path,
                       report(learning="3", drafting="2", deliveries="1"),
                       when=STAMP, schema=self.schema)
        rows = worklog.read(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], COMPLETE)

    def test_a_lane_a_cycle_does_not_run_reads_differently_from_one_that_failed(self):
        hourly = CycleReport(cycle="hourly-inbox-sweep", agent="agent-south",
                             lanes=["learning", "drafting"])
        hourly.record("learning", "1 lesson")
        hourly.record("drafting", "1 draft")
        row = worklog.append(self.path, hourly, when=STAMP, schema=self.schema)
        self.assertEqual(row["deliveries"], "n/a")

    def test_a_lane_outside_the_schema_is_refused_not_silently_dropped(self):
        rogue = CycleReport(cycle="c", agent="a", lanes=["invoices"])
        rogue.record("invoices", "12 paid")
        with self.assertRaises(worklog.WorkLogError):
            worklog.append(self.path, rogue, when=STAMP, schema=self.schema)

    def test_the_log_answers_what_did_not_finish(self):
        worklog.append(self.path, report(learning="3", drafting="2", deliveries="1"),
                       when=STAMP, schema=self.schema)
        self.assertEqual(
            worklog.missing_cycles(self.path, "2026-03-02",
                                   ["morning-supplier-sweep", "hourly-inbox-sweep"]),
            ["hourly-inbox-sweep"])

    def test_the_log_is_plain_csv_any_tool_can_read(self):
        worklog.append(self.path, report(learning="3", drafting="2", deliveries="1"),
                       when=STAMP, schema=self.schema)
        head = self.path.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(head,
                         "date,cycle,agent,status,learning,drafting,deliveries,"
                         "notes,signed_off")


class RunbookTests(unittest.TestCase):
    def test_the_shipped_runbooks_parse_and_declare_distinct_cycles(self):
        books = load_all(ROOT / "desk" / "runbooks")
        self.assertIn("morning-supplier-sweep", books)
        self.assertIn("hourly-inbox-sweep", books)
        self.assertEqual(books["morning-supplier-sweep"].lanes,
                         ["learning", "drafting", "deliveries"])

    def test_the_prose_a_model_executes_survives_parsing(self):
        book = load(ROOT / "desk" / "runbooks" / "morning-supplier-sweep.md")
        self.assertIn("Sent reply gate", book.instructions())

    def test_a_headerless_or_broken_runbook_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.md"
            bad.write_text("# no header here\n", encoding="utf-8")
            with self.assertRaises(RunbookError):
                load(bad)
            bad.write_text("---\ntitle: x\nlanes:\n  - a\n---\n", encoding="utf-8")
            with self.assertRaises(RunbookError):
                load(bad)


class SendGateTests(unittest.TestCase):
    def test_the_desk_api_refuses_to_send(self):
        with self.assertRaises(SendBlocked):
            Mailbox(ROOT / "desk" / "data").send("t", "hello")

    def test_the_credential_wrapper_blocks_send_below_the_agent(self):
        wrapper = ROOT / "bin" / "mailer-gw"
        blocked = subprocess.run([str(wrapper), "send", "--thread", "t"],
                                 capture_output=True, text=True)
        self.assertEqual(blocked.returncode, 3)
        self.assertIn("BLOCKED by mailer-gw", blocked.stderr)

    def test_reading_and_drafting_pass_through_untouched(self):
        wrapper = ROOT / "bin" / "mailer-gw"
        for verb in ("list", "read", "draft", "label"):
            ok = subprocess.run([str(wrapper), verb], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn(verb, ok.stdout)

    def test_the_escape_hatch_is_explicit_and_per_call(self):
        wrapper = ROOT / "bin" / "mailer-gw"
        allowed = subprocess.run([str(wrapper), "send"], capture_output=True,
                                 text=True, env={"PATH": "/usr/bin:/bin",
                                                 "MAILER_GW_ALLOW_SEND": "1"})
        self.assertEqual(allowed.returncode, 0, allowed.stderr)


class PokeSenderTests(unittest.TestCase):
    def _run(self, channel_dir, message="poke-morning.txt"):
        return subprocess.run(
            [str(ROOT / "scripts" / "send-poke.sh"), str(channel_dir), message],
            capture_output=True, text=True)

    def test_the_poke_tags_every_agent_on_the_roster(self):
        done = self._run(ROOT / "desk" / "channel")
        self.assertEqual(done.returncode, 0, done.stderr)
        for agent in ("agent-north", "agent-south", "agent-relief"):
            self.assertIn(agent, done.stdout)
        self.assertIn("First reply in this thread claims it", done.stdout)

    def test_a_poke_that_would_wake_nobody_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            channel = Path(tmp)
            (channel / "poke.conf").write_text("CHANNEL=test\nROSTER=\n",
                                               encoding="utf-8")
            (channel / "poke.txt").write_text("wake up\n", encoding="utf-8")
            failed = self._run(channel, "poke.txt")
            self.assertEqual(failed.returncode, 1)
            self.assertIn("ROSTER is empty", failed.stderr)

    def test_an_optional_missing_key_does_not_silently_kill_the_sender(self):
        # A real regression: `grep` exiting 1 on an absent optional key, under
        # pipefail and set -e, killed the sender before it sent anything, with
        # no error output and a green workflow.
        conf = (ROOT / "desk" / "channel" / "poke.conf").read_text(encoding="utf-8")
        self.assertNotIn("\nALLOW_EMPTY_ROSTER=", conf)
        self.assertEqual(self._run(ROOT / "desk" / "channel").returncode, 0)


if __name__ == "__main__":
    unittest.main()

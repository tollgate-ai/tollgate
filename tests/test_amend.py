"""A claim that the locked ticket or plan is wrong, and the amend of the plan lock that
the advisor or a person may decide on it (driver/driver.py, driver/task.py)."""

import json
import unittest
from pathlib import Path

from driver import history, roles
from driver.driver import Driver, Parked
from driver.log import Log, read
from gate import settings as st
from gate import state
from tests.test_task_steps import TaskCase

CLAIM = {"what": "C1 tests app.loud_name, which is not in seams:",
         "evidence": "undefined: loud_name", "change": "add the seam app.loud_name"}
SEAMS = "seams: app.shout\n"
AMENDED = "seams: app.shout, app.loud_name\n"


class Roles:
    """A caller for the ticket's driver: the test writer answers with `claim`, the
    reviewer rejects the tests, and the advisor decides `decision`."""

    def __init__(self, claim=None, decision="park", hint=""):
        self.claim, self.decision, self.hint = claim, decision, hint
        self.calls = []

    def __call__(self, call):
        self.calls.append((call.role, call.inputs))
        if call.role == "test_writer":
            data = {"tests": [{"item": "C1", "file": "tests/test_t1.py",
                               "name": "test_c1_shout"}]}
            if self.claim:
                data["ticket_wrong"] = self.claim
            return roles.Answer(data, 0, 0)
        if call.role == "reviewer":
            return roles.Answer({"verdict": "fail", "findings": [
                {"level": "blocking", "text": "C1 has no direct test"}]}, 0, 0)
        if call.role == "advisor":
            return roles.Answer({"decision": self.decision, "hint": self.hint,
                                 "reason": "x"}, 0, 0)
        raise AssertionError(f"unexpected role {call.role}")

    def of(self, role):
        return [i for r, i in self.calls if r == role]


class AmendCase(TaskCase):
    def setUp(self):
        super().setUp()
        self.s.commit_settings(advisor=("ai-advisor",))
        self.task.settings = st.load_from_ref(str(self.s.project), "main")
        self.task.lock_plan()
        self.ticket = Path(self.task.copy, ".tollgate/tickets/demo-1/ticket.md")

    def driver(self, caller, text=None):
        return Driver(str(self.s.project), "demo-1", self.store, caller=caller,
                      ticket_text=text or self.task.read_locked(
                          ".tollgate/tickets/demo-1/ticket.md"))

    def ticket_events(self, name=None):
        found = self.s.log("demo-1")
        return [e for e in found if name is None or e["event"] == name]

    def plan_record(self):
        return json.loads(state.path(str(self.s.project), "plans", "demo.json").read_text())

    def amend_role(self, edit):
        def role(name, inputs, lens=None, command=None):
            self.planner_inputs = inputs
            assert (name, inputs["mode"]) == ("builder", "amend"), (name, inputs)
            edit()
            return roles.Answer({"ticket": str(self.ticket)}, 0, 0)
        return role


class ClaimTest(AmendCase):
    def test_a_claim_of_the_test_writer_reaches_the_advisor_at_once(self):
        caller = Roles(claim=CLAIM)
        self.assertEqual(self.driver(caller).run(), 3)
        # One round, not three: writing the tests again cannot fix the ticket.
        self.assertEqual(len(caller.of("test_writer")), 1)
        reason = caller.of("advisor")[0]["reason"]
        self.assertIn("The test writer also says the locked ticket or plan is wrong", reason)
        self.assertIn("add the seam app.loud_name", reason)
        self.assertEqual(self.ticket_events("tests_written")[0]["ticket_wrong"], CLAIM)

    def test_a_builder_claim_goes_into_the_stop_reason(self):
        answer = roles.Answer({"ticket_wrong": CLAIM, "locked_test_wrong": "test_c1"}, 0, 0)
        said = Driver.claim(answer)
        self.assertIn("The builder also says a locked test is wrong: test_c1", said)
        self.assertIn("The builder also says the locked ticket or plan is wrong", said)
        self.assertEqual(Driver.claim(roles.Answer({}, 0, 0)), "")

    def test_with_no_claim_the_tests_are_rejected_three_times_as_before(self):
        caller = Roles()
        self.assertEqual(self.driver(caller).run(), 3)
        self.assertEqual(len(caller.of("test_writer")), 3)
        reason = caller.of("advisor")[0]["reason"]
        self.assertEqual(reason, "the tests were rejected 3 times")
        self.assertFalse(any("ticket_wrong" in e for e in self.ticket_events("tests_written")))
        self.assertFalse(self.task.amend_wanted("demo-1"))

    def test_a_refusal_keeps_the_lock(self):
        before = self.plan_record()
        caller = Roles(claim=CLAIM, decision="park")
        self.assertEqual(self.driver(caller).run(), 3)
        self.assertIsNone(self.task.amend_wanted("demo-1"))
        self.assertEqual(self.plan_record(), before)
        self.assertEqual(self.events("plan_amended"), [])


class AmendTest(AmendCase):
    def request(self):
        caller = Roles(claim=CLAIM, decision="amend", hint=CLAIM["change"])
        self.assertEqual(self.driver(caller).run(), 3)
        wanted = self.task.amend_wanted("demo-1")
        self.assertEqual(wanted["change"], "add the seam app.loud_name")
        return wanted

    def test_amend_changes_the_seams_and_locks_the_plan_again(self):
        wanted = self.request()
        before = self.plan_record()
        self.task.role = self.amend_role(
            lambda: self.ticket.write_text(self.ticket.read_text().replace(SEAMS, AMENDED)))
        self.task.amend_ticket("demo-1", wanted)
        self.assertEqual(self.planner_inputs["change"], "add the seam app.loud_name")
        self.assertIn("app.loud_name", self.planner_inputs["reason"])
        amended = self.events("plan_amended")[0]
        path = ".tollgate/tickets/demo-1/ticket.md"
        self.assertEqual(amended["old"], before["files"][path])
        self.assertEqual(amended["new"], self.plan_record()["files"][path])
        self.assertNotEqual(amended["old"], amended["new"])
        self.assertEqual(amended["reason"], wanted["reason"])
        self.assertEqual(self.store.amended, ["demo-1"])
        # The other ticket stays as the first lock had it.
        other = ".tollgate/tickets/demo-2/ticket.md"
        self.assertEqual(self.plan_record()["files"][other], before["files"][other])
        # The ticket is built from the new lock, and its driver takes the new ticket.
        text = self.task.read_locked(path)
        self.assertIn(AMENDED, text)
        self.assertIsNone(self.task.amend_wanted("demo-1"))
        t = history.replay(self.s.log("demo-1"))
        self.assertEqual((t.amending, t.rejections, t.hint),
                         (True, 0, "add the seam app.loud_name"))
        caller = Roles(decision="park")
        self.driver(caller, text).run()
        first = caller.of("test_writer")[0]
        self.assertIn(AMENDED, first["ticket"])
        self.assertEqual(first["hint"], "add the seam app.loud_name")
        copy = Path(state.path(str(self.s.project), "builders", "demo-1"), path)
        self.assertIn(AMENDED, copy.read_text())
        self.assertEqual(len(self.ticket_events("amend_applied")), 1)

    def test_an_amend_that_changes_another_ticket_is_refused_and_the_lock_stays(self):
        wanted = self.request()
        before = self.plan_record()
        other = Path(self.task.copy, ".tollgate/tickets/demo-2/ticket.md")
        locked = other.read_text()

        def edit():
            self.ticket.write_text(self.ticket.read_text().replace(SEAMS, AMENDED))
            other.write_text(locked.replace("There is no whisper.", "Changed."))
        self.task.role = self.amend_role(edit)
        with self.assertRaisesRegex(Parked, "changes only ticket demo-1 and the plan"):
            self.task.amend_ticket("demo-1", wanted)
        self.assertEqual(self.plan_record(), before)
        self.assertEqual(self.events("plan_amended"), [])
        self.assertEqual(other.read_text(), locked)

    def test_an_amend_after_the_lock_keeps_the_locked_tests(self):
        # A ticket whose tests are locked: the same tests are locked again with the new
        # ticket, as the one replacement lock, and the builder's code comes back after.
        log_path = str(state.path(str(self.s.project), "logs", "demo-1.jsonl"))
        with Log(log_path, ticket="demo-1", task="demo") as log:
            log.add("frozen", commit="a" * 40)
            log.add("ticket_amended", change="add the seam app.loud_name", old="x", new="y")
        t = history.replay(read(log_path))
        self.assertEqual((t.frozen, t.relock_from, t.tests_done, t.relocks, t.amending),
                         ("", "a" * 40, True, 1, True))


class QuestionTest(AmendCase):
    def asked(self, d):
        d.settings = st.load_from_ref(str(self.s.project), "main")
        d.copy = roles.make_copy(str(self.s.project), "demo-1")
        d.log = Log(d.log_path, ticket="demo-1", task="demo")
        self.addCleanup(d.log.close)
        st_ = history.Ticket()
        with self.assertRaisesRegex(Parked, "asked in question"):
            d.ask_advisor(st_, "the tests were rejected 3 times", person_only=True)
        return st_

    def test_a_person_can_choose_amend_at_the_rejected_tests_question(self):
        d = self.driver(Roles())
        st_ = self.asked(d)
        n, q = next(iter(self.store.open_questions().items()))
        self.assertEqual(q["options"], ["retry", "stronger", "amend", "split", "park"])
        self.store.answer(n, "tollgate decide amend add the seam app.loud_name")
        with self.assertRaisesRegex(Parked, "the planner changes the ticket"):
            d.ask_advisor(st_, "the tests were rejected 3 times", person_only=True)
        self.assertEqual(self.task.amend_wanted("demo-1")["change"],
                         "add the seam app.loud_name")

    def test_a_ticket_waits_for_its_amend_and_asks_no_new_question(self):
        # Amend was chosen, and the task has not changed the ticket yet. A second run of
        # the ticket must hand back to the task, not stop on the old count and ask again.
        d = self.driver(Roles())
        st_ = self.asked(d)
        n = next(iter(self.store.open_questions()))
        self.store.answer(n, "tollgate decide amend add the seam app.loud_name")
        with self.assertRaises(Parked):
            d.ask_advisor(st_, "the tests were rejected 3 times", person_only=True)
        d.log.close()
        log = str(Path(d.log_path))
        before = [e["event"] for e in read(log)]
        self.assertEqual(self.driver(Roles()).run(), 3)
        self.assertEqual([e["event"] for e in read(log)][len(before):].count("waiting_for_advisor"), 0)
        self.assertEqual(self.store.open_questions(), {})
        self.assertTrue(history.replay(read(log)).amend_wanted)

    def test_a_ticket_with_no_plan_has_no_amend_option(self):
        d = self.driver(Roles(), text=self.task.read_locked(
            ".tollgate/tickets/demo-1/ticket.md").replace("plan: demo\n", ""))
        self.assertNotIn("amend", d.options())

    def test_amend_with_no_change_parks_with_no_request(self):
        d = self.driver(Roles())
        st_ = self.asked(d)
        n = next(iter(self.store.open_questions()))
        self.store.answer(n, "tollgate decide amend")
        with self.assertRaisesRegex(Parked, "no change to make"):
            d.ask_advisor(st_, "x", person_only=True)
        self.assertIsNone(self.task.amend_wanted("demo-1"))


if __name__ == "__main__":
    unittest.main()

"""Single steps of the task loop (driver/task.py), with a scripted role in place of the
subagents: the plan review and what goes to each role."""

import unittest
from pathlib import Path

from driver import roles
from driver.log import Log, read
from driver.task import Task
from gate import settings as st
from tests.driver_fakes import FakeStore, Sample, sh
from tests.fake_role import PLAN, TICKET_T1, TICKET_T2

MINOR = {"level": "minor", "text": "the plan says greet is in main.py; it is in app.py"}
MAJOR = {"level": "major", "text": "drop demo-2?"}


class Script:
    """A role that answers from a script. reviews maps a lens to a list of findings for
    each round; fix(inputs) runs in the builder's answer step."""

    def __init__(self, reviews=None, fix=None):
        self.reviews = reviews or {}
        self.fix = fix
        self.calls = []
        self.rounds = {}

    def __call__(self, name, inputs, lens=None, command=None):
        self.calls.append((name, lens, inputs))
        if name == "reviewer":
            n = self.rounds[lens] = self.rounds.get(lens, 0) + 1
            found = self.reviews.get(lens, [])
            return roles.Answer({"verdict": "pass",
                                 "findings": found[n - 1] if n <= len(found) else []}, 0, 0)
        if name == "builder" and inputs.get("mode") == "answer":
            if self.fix:
                self.fix(inputs)
            return roles.Answer({"answers": [{"finding": i, "action": "fix", "evidence": "x"}
                                             for i, _ in enumerate(inputs["findings"])]}, 0, 0)
        if name == "builder" and inputs.get("mode") == "plan":
            return roles.Answer({"plan": ".tollgate/plans/demo.md"}, 0, 0)
        raise AssertionError(f"unexpected role {name}")

    def answers(self):
        return [i for n, _, i in self.calls if n == "builder" and i.get("mode") == "answer"]


class TaskCase(unittest.TestCase):
    def setUp(self):
        self.s = Sample(ticket=False)
        self.addCleanup(self.s.close)
        p = self.s.project
        self.store = FakeStore(p)
        self.task = Task(str(p), "demo", "Add shout and whisper.", self.store)
        copy = self.task.copy = roles.make_copy(str(p), "task-demo")
        sh(copy, "git", "checkout", "-q", "-B", "tg-plan/demo", "main")
        self.plan_file = Path(copy, ".tollgate/plans/demo.md")
        self.plan_file.parent.mkdir(parents=True)
        self.plan_file.write_text(PLAN.format(task="demo", request="Add shout and whisper."))
        for tid, text in (("demo-1", TICKET_T1), ("demo-2", TICKET_T2)):
            self.write_ticket(tid, text.format(task="demo"))
        self.task.settings = st.load_from_ref(str(p), "main")
        self.task.log = Log(self.task.log_path, task="demo")
        self.addCleanup(self.task.log.close)

    def write_ticket(self, tid, text):
        Path(self.task.copy, f".tollgate/tickets/{tid}").mkdir(parents=True, exist_ok=True)
        Path(self.task.copy, f".tollgate/tickets/{tid}/ticket.md").write_text(text)

    def events(self, name=None):
        found = read(self.task.log_path)
        return [e for e in found if name is None or e["event"] == name]

    def fix_plan(self, inputs):
        self.plan_file.write_text(self.plan_file.read_text().replace(
            "## Design\n", "## Design\nThe code is in app.py.\n", 1))


class MinorFindingsTest(TaskCase):
    def test_a_clean_round_gives_its_minor_findings_to_the_builder(self):
        self.task.role = Script({"readiness": [[MINOR], [MINOR]]}, fix=self.fix_plan)
        self.assertEqual(self.task.review_plan(), [])
        self.assertEqual(self.task.role.answers()[0]["findings"], [dict(MINOR, lens="readiness")])
        # The builder changed the plan, so a second round reads it. Its minor finding,
        # the same one, makes it no less clean: no third answer step.
        self.assertEqual([e["round"] for e in self.events("plan_reviewed")], [1, 1, 1, 2, 2, 2])
        self.assertEqual(len(self.task.role.answers()), 1)
        self.assertEqual(self.events("plan_review_clean")[0]["round"], 2)

    def test_an_answer_that_changes_nothing_ends_the_review_clean(self):
        self.task.role = Script({"readiness": [[MINOR]]})
        self.assertEqual(self.task.review_plan(), [])
        self.assertEqual(len(self.task.role.answers()), 1)
        self.assertEqual(self.events("plan_review_clean")[0]["round"], 1)
        self.assertEqual(self.events("plan_answered")[0]["minor"], 1)

    def test_minor_findings_go_with_the_open_ones_and_cannot_be_escalated(self):
        self.task.role = Script({"simplicity": [[MAJOR, MINOR]]}, fix=self.fix_plan)
        self.assertEqual(self.task.review_plan(), [])
        first = self.task.role.answers()[0]["findings"]
        self.assertEqual(first, [dict(MAJOR, lens="simplicity"), dict(MINOR, lens="simplicity")])


NEW_MAJOR = {"level": "major", "text": "the word greeting is vague in Goal"}
BLOCKING = {"level": "blocking", "text": "the plan says main.py exists; it does not"}


class LaterRoundTest(TaskCase):
    """From round 2 on, a round reviews the last answer: the change and the earlier
    findings."""

    def fix_once(self, inputs):
        if "The code is in app.py." not in self.plan_file.read_text():
            self.fix_plan(inputs)

    def reviewer_inputs(self, lens):
        return [i for n, ln, i in self.task.role.calls if n == "reviewer" and ln == lens]

    def rounds(self):
        return [e["round"] for e in self.events("plan_reviewed")]

    def test_round_2_gets_the_diff_and_the_earlier_findings_with_their_answers(self):
        self.task.role = Script({"simplicity": [[MAJOR]]}, fix=self.fix_once)
        self.assertEqual(self.task.review_plan(), [])
        first, second = self.reviewer_inputs("simplicity")
        self.assertNotIn("diff", first)
        self.assertNotIn("earlier_findings", first)
        self.assertIn("+The code is in app.py.", second["diff"])
        self.assertIn("b/.tollgate/plans/demo.md", second["diff"])
        self.assertEqual(second["earlier_findings"], [dict(
            MAJOR, lens="simplicity", answer={"action": "fix", "evidence": "x"})])
        # Each lens gets its own earlier findings, and the fresh reader still no code.
        fresh = self.reviewer_inputs("fresh_reader")[1]
        self.assertEqual(fresh["earlier_findings"], [])
        self.assertNotIn("tickets", fresh)

    def test_a_new_major_on_unchanged_text_does_not_reopen_the_review(self):
        self.task.role = Script({"simplicity": [[MAJOR], [NEW_MAJOR]]}, fix=self.fix_once)
        self.assertEqual(self.task.review_plan(), [])
        self.assertEqual(self.rounds(), [1, 1, 1, 2, 2, 2])
        demoted = dict(NEW_MAJOR, lens="simplicity", level="minor", was="major")
        self.assertEqual(self.events("plan_findings_demoted")[0]["findings"], [demoted])
        # It goes to the builder once, as a minor finding, with no round after it.
        answers = self.task.role.answers()
        self.assertEqual(len(answers), 2)
        self.assertEqual(answers[1]["findings"], [demoted])
        self.assertEqual(self.events("plan_review_clean")[0]["round"], 2)

    def test_a_blocking_finding_on_unchanged_text_reopens_the_review(self):
        self.task.role = Script({"simplicity": [[MAJOR], [BLOCKING]]}, fix=self.fix_once)
        self.assertEqual(self.task.review_plan(), [])
        self.assertEqual(self.rounds(), [1, 1, 1, 2, 2, 2, 3, 3, 3])
        self.assertEqual(self.events("plan_findings_demoted"), [])
        third = self.reviewer_inputs("simplicity")[2]
        self.assertEqual(third["earlier_findings"][0]["text"], BLOCKING["text"])
        self.assertEqual(third["diff"], "")   # the second answer changed nothing

    def test_a_major_about_an_earlier_finding_or_the_change_reopens_the_review(self):
        for about in ("earlier", "change"):
            with self.subTest(about=about):
                self.setUp()
                found = dict(NEW_MAJOR, about=about)
                self.task.role = Script({"simplicity": [[MAJOR], [found]]}, fix=self.fix_once)
                self.assertEqual(self.task.review_plan(), [])
                self.assertEqual(self.rounds(), [1, 1, 1, 2, 2, 2, 3, 3, 3])

    def test_a_minors_only_answer_gets_a_change_only_round(self):
        self.task.role = Script({"readiness": [[MINOR], [NEW_MAJOR]]}, fix=self.fix_plan)
        self.assertEqual(self.task.review_plan(), [])
        # The answer changed the plan, so round 2 reads the change and the minor finding,
        # and its new major finding on unchanged text keeps it clean: no round 3.
        second = self.reviewer_inputs("readiness")[1]
        self.assertIn("+The code is in app.py.", second["diff"])
        self.assertEqual(second["earlier_findings"][0]["text"], MINOR["text"])
        self.assertEqual(self.rounds(), [1, 1, 1, 2, 2, 2])
        self.assertEqual(len(self.task.role.answers()), 1)
        clean = self.events("plan_review_clean")[0]
        self.assertEqual(clean["round"], 2)
        self.assertEqual(clean["minor"][0]["was"], "major")

    def test_the_cap_holds_and_returns_the_open_findings(self):
        about = dict(NEW_MAJOR, about="change")
        self.task.role = Script({"simplicity": [[MAJOR]] + [[about]] * 9}, fix=self.fix_plan)
        found = self.task.review_plan()
        self.assertEqual(self.rounds(), [r for r in (1, 2, 3, 4) for _ in range(3)])
        self.assertEqual(found, [{"level": "major", "text": "4 rounds with findings"},
                                 dict(about, lens="simplicity")])


class ApprovalHintTest(TaskCase):
    def test_a_hint_given_with_approve_reaches_the_ticket_roles(self):
        self.task.lock_plan("keep the error text short")
        locked = self.events("plan_locked")[0]
        self.assertEqual(locked["hint"], "keep the error text short")
        self.assertEqual(self.task.approval_hint("demo-1"), "keep the error text short")
        self.assertEqual(self.task.approval_hint("demo-9"), "")
        # The ticket's driver gives it to the test writer and to the builder.
        from driver.driver import Driver
        seen = []

        def caller(call):
            seen.append((call.role, call.inputs))
            raise roles.RoleError("stop here")
        text = Path(self.task.copy, ".tollgate/tickets/demo-1/ticket.md").read_text()
        d = Driver(str(self.s.project), "demo-1", self.store, ticket_text=text, caller=caller,
                   approval_hint=self.task.approval_hint("demo-1"))
        with self.assertRaises(roles.RoleError):
            d.run()
        self.assertEqual(seen[0][0], "test_writer")
        self.assertEqual(seen[0][1]["approval_hint"], "keep the error text short")

    def test_no_hint_leaves_the_inputs_as_they_were(self):
        self.task.lock_plan()
        self.assertNotIn("hint", self.events("plan_locked")[0])
        from driver.driver import Driver
        text = Path(self.task.copy, ".tollgate/tickets/demo-1/ticket.md").read_text()
        d = Driver(str(self.s.project), "demo-1", self.store, ticket_text=text)
        self.assertEqual(d.approval, {})


class FixRoundAnswerTest(TaskCase):
    def test_a_minor_answer_in_a_fix_round_cannot_change_a_locked_ticket(self):
        self.task.lock_plan()
        ticket = Path(self.task.copy, ".tollgate/tickets/demo-1/ticket.md")
        locked = ticket.read_text()

        def edit_locked(inputs):
            ticket.write_text(locked.replace("There is no shout.", "Changed by an answer."))
        self.task.role = Script({"readiness": [[MINOR]]}, fix=edit_locked)
        self.assertEqual(self.task.review_plan(), [])
        self.assertEqual(self.task.role.answers()[0]["locked_tickets"], ["demo-1", "demo-2"])
        self.assertEqual(ticket.read_text(), locked)
        invalid = self.events("plan_invalid")
        self.assertIn("ticket demo-1 is locked by the approved plan", invalid[0]["problems"][0])

    def test_before_a_lock_the_answer_inputs_stay_as_they_were(self):
        self.task.role = Script({"readiness": [[MINOR]]})
        self.task.review_plan()
        self.assertNotIn("locked_tickets", self.task.role.answers()[0])


class DecisionsTest(TaskCase):
    def test_the_plan_s_reviewers_and_planner_read_the_earlier_decisions(self):
        hint = "the owner confirms the change to docs/design.md"
        self.task.log.add("advisor_decided", by="person", question=1, decision="send_back",
                          hint=hint)
        self.task.log.add("advisor_decided", by="ai", checkpoint="ticket", decision="continue",
                          hint="")
        self.task.role = Script()
        self.task.write_plan(hint=hint)
        self.assertEqual(self.task.review_plan(), [])
        want = [{"by": "person", "decision": "send_back", "hint": hint}]
        for name, lens, inputs in self.task.role.calls:
            with self.subTest(role=name, lens=lens):
                if lens == "fresh_reader":
                    self.assertNotIn("decisions", inputs)   # it reads the plan alone
                else:
                    self.assertEqual(inputs["decisions"], want)

    def test_with_no_decision_the_inputs_stay_as_they_were(self):
        self.task.role = Script()
        self.task.review_plan()
        self.assertFalse(any("decisions" in i for _, _, i in self.task.role.calls))


class PlanLockTest(TaskCase):
    def test_the_lock_commits_only_the_plan_and_the_tickets_it_lists(self):
        # demo-old was in the plan's first round, and the builder then dropped it.
        self.write_ticket("demo-old", TICKET_T1.format(task="demo").replace(
            "id: demo-1", "id: demo-old"))
        # A ticket of another plan in the copy stays as it is, out of this commit.
        self.write_ticket("other-1", TICKET_T1.format(task="other"))
        self.task.lock_plan()
        files = sh(self.s.project, "git", "diff", "--name-only", "main", "tg-plan/demo").split()
        self.assertEqual(sorted(files), [".tollgate/analysis/demo.md", ".tollgate/plans/demo.md",
                                         ".tollgate/tickets/demo-1/ticket.md",
                                         ".tollgate/tickets/demo-2/ticket.md"])
        self.assertEqual(self.events("plan_tickets_dropped")[0]["tickets"], ["demo-old"])
        self.assertFalse(Path(self.task.copy, ".tollgate/tickets/demo-old").exists())
        self.assertTrue(Path(self.task.copy, ".tollgate/tickets/other-1/ticket.md").exists())

    def test_a_ticket_on_main_that_the_builder_edited_goes_back_to_main(self):
        on_main = TICKET_T1.format(task="demo").replace("id: demo-1", "id: demo-0")
        Path(self.s.project, ".tollgate/tickets/demo-0").mkdir(parents=True)
        Path(self.s.project, ".tollgate/tickets/demo-0/ticket.md").write_text(on_main)
        sh(self.s.project, "git", "add", "-A")
        sh(self.s.project, "git", "commit", "-q", "-m", "a ticket on main")
        sh(self.task.copy, "git", "fetch", "-q", str(self.s.project), "main:main")
        self.write_ticket("demo-0", on_main.replace("There is no shout.", "Edited."))
        Path(self.task.copy, ".tollgate/tickets/demo-0/notes.md").write_text("extra\n")
        self.task.lock_plan()
        self.assertEqual(Path(self.task.copy, ".tollgate/tickets/demo-0/ticket.md").read_text(),
                         on_main)
        files = sh(self.s.project, "git", "diff", "--name-only", "main", "tg-plan/demo").split()
        self.assertFalse([f for f in files if "demo-0" in f])

    def test_a_stray_ticket_an_earlier_lock_committed_is_dropped_too(self):
        # Before this fix, a lock committed every ticket file. The next version, in a fix
        # round, drops it, so the store does not refuse the new plan commit.
        self.write_ticket("demo-old", TICKET_T1.format(task="demo").replace(
            "id: demo-1", "id: demo-old"))
        sh(self.task.copy, "git", "add", "-A", ".tollgate")
        sh(self.task.copy, "git", "commit", "-q", "-m", "an old lock")
        self.task.lock_plan()
        files = sh(self.s.project, "git", "diff", "--name-only", "main", "tg-plan/demo").split()
        self.assertNotIn(".tollgate/tickets/demo-old/ticket.md", files)


if __name__ == "__main__":
    unittest.main()

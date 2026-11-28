"""Tests for the store: each method has a passing and a failing case."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from gate import freeze, state
from gate import manifest as mf
from gate import plan as pl
from gate.store import Store, StoreError
from tests.fakes import RUN_TESTS, TICKET
from tests.helpers import Repo, write_checks
from tests.test_plan_review import PLAN

TESTS = '''import unittest
import app


class T(unittest.TestCase):
    def test_c1_shout(self):
        self.assertEqual(app.shout("ann"), "HELLO ANN")

    def test_c2_greet(self):
        self.assertEqual(app.greet("ann"), "hello ann")
'''


class StoreCase(unittest.TestCase):
    """A project on main with a state folder, all temporary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, folder in (("TOLLGATE_STATE", "state"), ("TOLLGATE_KEYS", "keys")):
            old = os.environ.get(name)
            os.environ[name] = str(Path(self.tmp.name, folder))
            self.addCleanup(lambda n=name, o=old: os.environ.pop(n) if o is None
                            else os.environ.__setitem__(n, o))
        self.repo = Repo()
        self.addCleanup(self.repo.close)
        r = self.repo
        r.write("app.py", 'def greet(name):\n    return f"hello {name}"\n')
        r.write("run_tests.py", RUN_TESTS)
        r.write("tests/__init__.py", "")
        r.write(".gitignore", "report.xml\n__pycache__/\n")
        self.write_settings()
        self.main = r.commit("main")
        self.store = Store(str(r.path))

    def write_settings(self, **extra):
        write_checks(self.repo.path, {
            "checks": {"tests": {"run": f"{sys.executable} run_tests.py", "report": "report.xml"}},
            "harness": ["run_tests.py"], "require_plan": False, **extra})

    def ticket_branch(self, tests=TESTS, ticket=TICKET):
        r = self.repo
        r.git("checkout", "-q", "-b", "tg/T-1", "main")
        r.write(mf.ticket_path("T-1"), ticket)
        r.write("tests/test_t1.py", tests)
        commit = r.commit("test(T-1): the ticket and its locked tests")
        r.git("checkout", "-q", "main")
        return commit

    def change(self, path="app.py", text='def greet(name):\n    return f"hello {name}"\n\n'
               'def shout(name):\n    return greet(name).upper()\n'):
        r = self.repo
        r.git("checkout", "-q", "-B", "tg/T-1-build", "main")
        r.write(path, text)
        commit = r.commit("feat(T-1): shout")
        r.git("checkout", "-q", "main")
        return commit

    def pass_record(self, commit, tier=2, ticket="T-1"):
        """A pass record, and a lock of the ticket from main when it has none."""
        base = None
        if ticket:
            path = freeze.lock_path(str(self.repo.path), ticket)
            if not path.exists():
                state.write_record(path, {"ticket": ticket, "base_commit": self.main, "commit": commit,
                                  "number": 1, "at": "2026-11-22T00:00:00Z",
                                  "manifest": {"files": {}, "tests": [], "keeps_behavior": []}})
            base = json.loads(path.read_text())["base_commit"]
        return {"commit": commit, "ticket": ticket, "tier": tier, "base_commit": base,
                "lock_sha256": freeze.lock_sha256(str(self.repo.path), ticket) if ticket else None,
                "checks": {"tests": True}, "score": 100}

    def approve(self, commit):
        path = state.path(str(self.repo.path), "approvals", f"{commit}.json")
        state.write_record(path, {"commit": commit, "ticket": "T-1", "approver": "owner",
                          "at": "2026-11-22T00:00:00Z"})


class FreezeTest(StoreCase):
    def test_writes_a_lock(self):
        commit = self.ticket_branch()
        path = Path(self.store.freeze("T-1"))
        lock = state.read_record(path)
        self.assertEqual((lock["commit"], lock["base_commit"], lock["number"]),
                         (commit, self.main, 1))
        self.assertEqual(lock["manifest"]["tests"],
                         ["tests.test_t1::test_c1_shout", "tests.test_t1::test_c2_greet"])
        self.assertEqual(lock["manifest"]["keeps_behavior"], ["tests.test_t1::test_c2_greet"])

    def test_a_ticket_may_start_from_a_ready_change_that_passed(self):
        start = self.change()
        r = self.repo
        r.git("checkout", "-q", "-b", "tg/T-1", start)
        r.write(mf.ticket_path("T-1"), TICKET)
        r.write("tests/test_t1.py", TESTS.replace('app.shout("ann"), "HELLO ANN"',
                                                  'app.loud("ann"), "hello ann!"'))
        r.commit("test(T-1): the ticket and its locked tests")
        r.git("checkout", "-q", "main")
        with self.assertRaisesRegex(StoreError, "no pass record"):
            self.store.freeze("T-1", start=start)
        self.store.record_pass(start, self.pass_record(start, ticket=""))
        lock = state.read_record(Path(self.store.freeze("T-1", start=start)))
        self.assertEqual(lock["base_commit"], start)

    def test_refuses_a_test_that_passes_before_the_build(self):
        self.ticket_branch(tests=TESTS.replace('app.shout("ann")', '"HELLO ANN"'))
        with self.assertRaisesRegex(StoreError, "passed before the build"):
            self.store.freeze("T-1")

    def test_a_report_with_no_test_shows_what_the_test_check_printed(self):
        self.write_settings(checks={"tests": {"run": "echo the build file is missing; exit 1",
                                              "report": "report.xml"}})
        self.repo.commit("settings")
        self.ticket_branch()
        with self.assertRaisesRegex(StoreError, "(?s)no test from the ticket's test files.*"
                                    "The test check said:.*the build file is missing"):
            self.store.freeze("T-1")

    def test_the_tests_of_a_second_test_check_are_frozen_with_its_variables(self):
        # The ticket's tests run only in the `integration` check, as tests behind a build
        # tag do, and they need a token that the secret filter drops unless `env` names it.
        r = self.repo
        r.write("run_integration.py", RUN_TESTS.replace('open("report.xml"',
                                                        'open("report-integration.xml"'))
        r.write(".gitignore", "report.xml\nreport-integration.xml\n__pycache__/\n")
        empty = "<testsuites><testsuite></testsuite></testsuites>"
        unit = f"{sys.executable} -c \"open('report.xml', 'w').write('{empty}')\""
        integration = {"run": f"{sys.executable} run_integration.py",
                       "report": "report-integration.xml", "env": ["TEST_VAULT_TOKEN"]}
        self.write_settings(checks={"tests": {"run": unit, "report": "report.xml"},
                                    "integration": integration},
                            harness=["run_tests.py", "run_integration.py"])
        r.commit("an integration check")
        old = os.environ.get("TEST_VAULT_TOKEN")
        self.addCleanup(lambda: os.environ.pop("TEST_VAULT_TOKEN", None) if old is None
                        else os.environ.__setitem__("TEST_VAULT_TOKEN", old))
        os.environ["TEST_VAULT_TOKEN"] = "t"
        tests = TESTS.replace("import unittest\n", "import os\nimport unittest\n").replace(
            'self.assertEqual(app.greet("ann"), "hello ann")',
            'self.assertEqual(os.environ.get("TEST_VAULT_TOKEN"), "t")\n'
            '        self.assertEqual(app.greet("ann"), "hello ann")')
        self.ticket_branch(tests=tests)
        lock = state.read_record(Path(self.store.freeze("T-1")))
        self.assertEqual(lock["manifest"]["tests"],
                         ["tests.test_t1::test_c1_shout", "tests.test_t1::test_c2_greet"])
        self.assertEqual(lock["manifest"]["keeps_behavior"], ["tests.test_t1::test_c2_greet"])

    def test_runs_with_mains_harness(self):
        # The ticket's commit weakens the harness so that every test passes; main's copy wins.
        self.repo.git("checkout", "-q", "-b", "tg/T-1", "main")
        self.repo.write(mf.ticket_path("T-1"), TICKET)
        self.repo.write("tests/test_t1.py", TESTS)
        self.repo.write("run_tests.py", 'open("report.xml", "w").write("<testsuites/>")\n')
        self.repo.commit("weak harness")
        self.repo.git("checkout", "-q", "main")
        self.assertTrue(self.store.freeze("T-1"))

    def test_a_second_lock_replaces_the_first_and_a_third_is_refused(self):
        self.ticket_branch()
        first = Path(self.store.freeze("T-1")).read_bytes()
        second = state.read_record(Path(self.store.freeze("T-1")))
        self.assertEqual(second["number"], 2)
        self.assertEqual(freeze.replaced_path(str(self.repo.path), "T-1").read_bytes(), first)
        with self.assertRaisesRegex(StoreError, "already has 2 freezes"):
            self.store.freeze("T-1")

    def test_a_kept_test_in_the_compile_unit_of_the_new_tests_passes_at_the_start(self):
        # In a compiled language, the new tests and an old one may build as one unit, such
        # as a Go package: while the new tests call a seam that is not there, the old test
        # cannot run. This harness plays that: with tests/test_t1.py there, tests/test_old.py
        # does not run.
        one_unit = RUN_TESTS.replace("walk(suite)\n", "walk(suite)\nimport os\n"
                                     "if os.path.exists('tests/test_t1.py'):\n"
                                     "    cases[:] = [c for c in cases\n"
                                     "                if type(c).__module__ != 'tests.test_old']\n")
        old = 'import unittest\nimport app\n\n\nclass T(unittest.TestCase):\n' \
              '    def test_greet(self):\n        self.assertEqual(app.greet("ann"), "hello ann")\n'
        r = self.repo
        r.write("run_tests.py", one_unit)
        r.write("tests/test_old.py", old)
        self.main = r.commit("an old test")
        r.git("checkout", "-q", "-b", "tg/T-1", "main")
        r.write(mf.ticket_path("T-1"), TICKET)
        r.write("tests/test_t1.py", TESTS.split("    def test_c2_greet")[0])
        r.write(mf.map_path("T-1"), json.dumps(
            [{"item": "C2", "file": "tests/test_old.py", "name": "test_greet"}]))
        r.commit("test(T-1): the ticket and its locked tests")
        r.git("checkout", "-q", "main")
        lock = state.read_record(Path(self.store.freeze("T-1")))
        kept = "tests.test_old::test_greet"
        self.assertEqual(lock["manifest"]["kept_at_start"], [kept])
        self.assertEqual(lock["manifest"]["keeps_behavior"], [kept])
        # The gate requires each locked test to pass after the build.
        self.assertIn(kept, lock["manifest"]["tests"])
        with self.subTest("it fails at the start"):
            r.write("app.py", 'def greet(name):\n    return name\n')
            self.main = r.commit("greet changed")
            r.git("branch", "-q", "-f", "tg/T-1", "main")
            r.git("checkout", "-q", "tg/T-1")
            r.write(mf.ticket_path("T-1"), TICKET)
            r.write("tests/test_t1.py", TESTS.split("    def test_c2_greet")[0])
            r.write(mf.map_path("T-1"), json.dumps(
                [{"item": "C2", "file": "tests/test_old.py", "name": "test_greet"}]))
            r.commit("test(T-1): the ticket and its locked tests")
            r.git("checkout", "-q", "main")
            with self.assertRaisesRegex(StoreError, "test_greet keeps behavior, so it must pass"):
                self.store.freeze("T-1")

    def test_refuses_a_ticket_that_differs_from_the_locked_plan(self):
        self.ticket_branch(ticket=TICKET.replace("plan: demo", "plan: retries"))
        r = self.repo
        r.git("checkout", "-q", "-b", "tg-plan/retries", "main")
        r.write(pl.plan_path("retries"), PLAN)
        r.write(mf.ticket_path("T-1"), TICKET.replace("plan: demo", "plan: retries")
                .replace("There is no shout.", "There is no shout at all."))
        r.commit("the plan")
        r.git("checkout", "-q", "main")
        self.store.lock_plan("retries")
        with self.assertRaisesRegex(StoreError, "differs from the approved ticket"):
            self.store.freeze("T-1")

class LockPlanTest(StoreCase):
    def plan_branch(self, with_ticket=True):
        r = self.repo
        r.git("checkout", "-q", "-b", "tg-plan/retries", "main")
        r.write(pl.plan_path("retries"), PLAN)
        if with_ticket:
            r.write(mf.ticket_path("T-1"), TICKET.replace("plan: demo", "plan: retries"))
        commit = r.commit("the plan")
        r.git("checkout", "-q", "main")
        return commit

    def test_signs_the_plan_and_its_tickets(self):
        commit = self.plan_branch()
        found = state.read_record(Path(self.store.lock_plan("retries")))
        self.assertEqual(found["commit"], commit)
        self.assertEqual(sorted(found["files"]), sorted([mf.ticket_path("T-1"), pl.plan_path("retries")]))
        self.assertEqual(self.repo.git("tag", "--list"), "")

    def test_refuses_a_ticket_of_the_plan_that_its_header_does_not_list(self):
        r = self.repo
        r.git("checkout", "-q", "-b", "tg-plan/retries", "main")
        r.write(pl.plan_path("retries"), PLAN)
        r.write(mf.ticket_path("T-1"), TICKET.replace("plan: demo", "plan: retries"))
        # A ticket the builder wrote in an early round, then left out of the header.
        r.write(mf.ticket_path("T-old"), TICKET.replace("plan: demo", "plan: retries")
                .replace("id: T-1", "id: T-old"))
        # A ticket of another plan is no concern of this one.
        r.write(mf.ticket_path("T-other"), TICKET.replace("id: T-1", "id: T-other"))
        r.commit("the plan")
        r.git("checkout", "-q", "main")
        with self.assertRaisesRegex(StoreError, "holds ticket T-old, which names plan retries"):
            self.store.lock_plan("retries")

    def test_refuses_a_missing_ticket_and_a_second_lock(self):
        self.plan_branch(with_ticket=False)
        with self.assertRaisesRegex(StoreError, "T-1 has no file"):
            self.store.lock_plan("retries")
        self.repo.git("branch", "-D", "tg-plan/retries")
        self.plan_branch()
        self.store.lock_plan("retries")
        with self.assertRaisesRegex(StoreError, "already locked"):
            self.store.lock_plan("retries")

    def amend(self, ticket_text=None, add=True):
        """A new version of the plan on tg-plan/retries: a second ticket, T-2."""
        r = self.repo
        r.git("checkout", "-q", "tg-plan/retries")
        plan = PLAN.replace("tickets: T-1", "tickets: T-1, T-2") if add else PLAN
        r.write(pl.plan_path("retries"), plan.replace("## Tickets\n", "## Tickets\n- T-2: fix.\n")
                if add else plan + "\n")
        if ticket_text:
            r.write(mf.ticket_path("T-1"), ticket_text)
        if add:
            r.write(mf.ticket_path("T-2"), TICKET.replace("plan: demo", "plan: retries")
                    .replace("id: T-1", "id: T-2"))
        commit = r.commit("the fix plan")
        r.git("checkout", "-q", "main")
        return commit

    def test_a_new_version_of_the_plan_is_locked_and_the_old_one_kept(self):
        self.plan_branch()
        first = Path(self.store.lock_plan("retries")).read_bytes()
        commit = self.amend()
        found = state.read_record(Path(self.store.lock_plan("retries")))
        self.assertEqual((found["commit"], found["version"]), (commit, 2))
        self.assertIn(mf.ticket_path("T-2"), found["files"])
        kept = state.path(str(self.repo.path), "plans", "replaced", "retries.1.json")
        self.assertEqual(kept.read_bytes(), first)

    def test_a_new_version_may_not_change_or_drop_a_locked_ticket(self):
        self.plan_branch()
        self.store.lock_plan("retries")
        self.amend(ticket_text=TICKET.replace("plan: demo", "plan: retries")
                   .replace("There is no shout.", "Shout exists."))
        with self.assertRaisesRegex(StoreError, "changes ticket T-1"):
            self.store.lock_plan("retries")


class QuestionTest(StoreCase):
    def test_ask_answer_close(self):
        n = self.store.ask("T-1 waits", "Pick one.", options=["retry", "stop"])
        self.assertEqual(list(self.store.open_questions()), [n])
        self.store.answer(n, "tollgate decide retry keep it short")
        self.assertEqual(self.store.open_questions(), {})
        self.assertEqual(self.store.question(n)["answer"]["hint"], "keep it short")
        self.store.close_question(n)
        self.assertEqual(self.store.question(n)["answer"]["decision"], "retry")
        self.assertEqual(self.store.ask("next", ""), n + 1)

    def test_refuses_an_answer_outside_the_options_and_a_closed_question(self):
        n = self.store.ask("T-1 waits", "Pick one.", options=["retry", "stop"])
        with self.assertRaisesRegex(StoreError, "not one of"):
            self.store.answer(n, "merge")
        self.assertIn(n, self.store.open_questions())
        self.store.close_question(n)
        with self.assertRaisesRegex(StoreError, "not open"):
            self.store.answer(n, "retry")
        with self.assertRaisesRegex(StoreError, "not open"):
            self.store.close_question(n)


class PassTest(StoreCase):
    def test_a_recorded_pass_reads_back(self):
        commit = self.change()
        self.store.record_pass(commit, self.pass_record(commit))
        self.assertEqual(self.store.passed(commit)["score"], 100)

    def test_a_missing_pass_is_none(self):
        commit = self.change()
        self.assertIsNone(self.store.passed(commit))

    def test_a_pass_record_with_other_keys_is_none(self):
        commit = self.change()
        self.store.record_pass(commit, self.pass_record(commit))
        path = state.path(str(self.repo.path), "passes", f"{commit}.json")
        state.write_record(path, {"commit": commit, "tier": 1})
        self.assertIsNone(self.store.passed(commit))

    def test_record_pass_refuses_a_bad_record(self):
        commit = self.change()
        with self.assertRaisesRegex(StoreError, "keys"):
            self.store.record_pass(commit, {"commit": commit})
        with self.assertRaisesRegex(StoreError, "names"):
            self.store.record_pass(commit, self.pass_record("a" * 40))


class ApprovalTest(StoreCase):
    def test_an_approval_reads_back(self):
        commit = self.change()
        self.approve(commit)
        self.assertEqual([a["approver"] for a in self.store.approvals(commit)], ["owner"])

class ReadyTest(StoreCase):
    def test_marks_a_passed_branch(self):
        commit = self.change()
        self.store.record_pass(commit, self.pass_record(commit))
        self.store.ready("tg/T-1-build", "T-1")
        path = state.path(str(self.repo.path), "passes", "ready", "T-1.json")
        self.assertEqual(state.read_record(path)["commit"], commit)

    def test_refuses_a_branch_with_no_pass(self):
        self.change()
        with self.assertRaisesRegex(StoreError, "no pass record"):
            self.store.ready("tg/T-1-build", "T-1")


class MergeTest(StoreCase):
    def test_merges_a_passed_change_with_a_merge_commit(self):
        commit = self.change()
        self.store.record_pass(commit, self.pass_record(commit))
        self.store.ready("tg/T-1-build", "T-1")
        merged = self.store.merge(commit)
        parents = self.repo.git("rev-list", "--parents", "-n", "1", merged).split()[1:]
        self.assertEqual(parents, [self.main, commit])
        self.assertFalse(state.path(str(self.repo.path), "passes", "ready", "T-1.json").exists())

    def test_the_merge_commit_passes_the_commit_message_check(self):
        # A project set up by /tollgate:init runs this check on the store's merge too.
        from setup import check_commit_msg
        commit = self.change()
        self.store.record_pass(commit, self.pass_record(commit))
        self.store.ready("tg/T-1-build", "T-1")
        merged = self.store.merge(commit)
        message = self.repo.git("log", "-1", "--format=%B", merged)
        self.assertEqual(check_commit_msg.problems(message, in_merge=True), [])

    def test_refuses_a_ticket_that_builds_on_a_change_not_on_main(self):
        # T-1 is tier 3; T-2 starts from it and counts only its own files, tier 1.
        first = self.change()
        r = self.repo
        r.git("checkout", "-q", "-b", "tg/T-2", first)
        r.write("b.py", "y = 2\n")
        second = r.commit("feat(T-2): b")
        r.git("checkout", "-q", "main")
        state.write_record(freeze.lock_path(str(r.path), "T-2"), {"ticket": "T-2", "base_commit": first, "commit": second, "number": 1,
                    "manifest": {"files": {}, "tests": [], "keeps_behavior": []},
                    "at": "2026-11-22T00:00:00Z"})
        self.store.record_pass(second, self.pass_record(second, tier=1, ticket="T-2"))
        self.store.record_pass(first, self.pass_record(first, tier=3))
        with self.assertRaisesRegex(StoreError, "is not on main"):
            self.store.merge(second)
        self.approve(first)
        self.store.merge(first)
        self.assertTrue(self.store.merge(second))

    def test_refuses_a_ticket_whose_lock_is_gone_or_names_another_start(self):
        commit = self.change()
        record = self.pass_record(commit)
        self.store.record_pass(commit, dict(record, base_commit="0" * 40))
        with self.assertRaisesRegex(StoreError, "not its lock's start"):
            self.store.merge(commit)
        self.store.record_pass(commit, record)
        freeze.lock_path(str(self.repo.path), "T-1").unlink()
        with self.assertRaisesRegex(StoreError, "lock"):
            self.store.merge(commit)

    def test_a_pass_record_of_an_older_tollgate_must_pass_again(self):
        commit = self.change()
        record = self.pass_record(commit)
        record.pop("base_commit")
        record["at"] = "2026-11-01T00:00:00Z"
        state.write_record(state.path(str(self.repo.path), "passes", f"{commit}.json"), record)
        with self.assertRaisesRegex(StoreError, "older Tollgate.*pass the gate again"):
            self.store.merge(commit)

    def test_refuses_with_no_pass(self):
        commit = self.change()
        with self.assertRaisesRegex(StoreError, "no pass record"):
            self.store.merge(commit)

    def test_tier_3_needs_an_approval(self):
        commit = self.change()
        self.store.record_pass(commit, self.pass_record(commit, tier=3))
        with self.assertRaisesRegex(StoreError, "needs your approval"):
            self.store.merge(commit)
        self.approve(commit)
        self.assertTrue(self.store.merge(commit))

    def test_a_change_made_by_hand_merges_on_an_owner_approval(self):
        commit = self.change()
        with self.assertRaisesRegex(StoreError, "no approval of a change made by hand"):
            self.store.merge(commit)
        state.write_record(state.path(str(self.repo.path), "approvals", f"{commit}.json"), {"commit": commit, "ticket": "", "approver": "owner",
                    "at": "2026-11-22T00:00:00Z"})
        self.assertTrue(self.store.merge(commit))

    def test_refuses_a_pass_for_an_old_lock(self):
        self.ticket_branch()
        self.store.freeze("T-1")
        commit = self.change()
        self.store.record_pass(commit, self.pass_record(commit))
        self.store.freeze("T-1")
        with self.assertRaisesRegex(StoreError, "not for the current lock"):
            self.store.merge(commit)

    def test_refuses_a_main_worktree_with_changes(self):
        commit = self.change()
        self.store.record_pass(commit, self.pass_record(commit))
        self.repo.write("app.py", "changed = True\n")
        with self.assertRaisesRegex(StoreError, "has changes"):
            self.store.merge(commit)

    def test_an_untracked_file_the_change_adds_with_the_same_bytes_is_removed(self):
        # You wrote the ticket on main before the task; the change adds the same file.
        commit = self.change(path=mf.ticket_path("T-1"), text=TICKET)
        self.store.record_pass(commit, self.pass_record(commit))
        self.repo.write(mf.ticket_path("T-1"), TICKET)
        self.assertTrue(self.store.merge(commit))
        self.assertEqual(Path(self.repo.path, mf.ticket_path("T-1")).read_text(), TICKET)
        self.assertEqual(self.repo.git("status", "--porcelain").strip(), "")

    def test_an_untracked_file_the_change_adds_with_other_bytes_is_refused(self):
        commit = self.change(path=mf.ticket_path("T-1"), text=TICKET)
        self.store.record_pass(commit, self.pass_record(commit))
        self.repo.write(mf.ticket_path("T-1"), "my own notes\n")
        with self.assertRaisesRegex(StoreError, "untracked files .* other content"):
            self.store.merge(commit)
        self.assertEqual(Path(self.repo.path, mf.ticket_path("T-1")).read_text(),
                         "my own notes\n")
        self.assertNotIn("T-1", self.repo.git("log", "--oneline", "main"))


if __name__ == "__main__":
    unittest.main()

"""Tests for settings, paths, tickets, JUnit reports, manifests, tiers, and checks."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from gate import checks, junit, manifest, paths, settings, tickets, tiers
from gate import precommit_config as pc
from tests.helpers import TICKET, Repo, hook_checks, within
from tests.helpers import junit as make_junit


class PathsTest(unittest.TestCase):
    def test_double_star_matches_any_depth(self):
        self.assertTrue(paths.matches("src/auth/a/b.py", ["src/auth/**"]))
        self.assertTrue(paths.matches("conftest.py", ["**/conftest.py"]))
        self.assertTrue(paths.matches("a/b/conftest.py", ["**/conftest.py"]))

    def test_single_star_stays_in_one_folder(self):
        self.assertFalse(paths.matches("src/a/b.py", ["src/*.py"]))
        self.assertTrue(paths.matches("src/b.py", ["src/*.py"]))


class SettingsTest(unittest.TestCase):
    def test_unknown_key_is_named(self):
        with self.assertRaisesRegex(settings.SettingsError, "unknown key: stop.retries"):
            settings.parse('{"stop": {"retries": 2}}')

    def test_a_check_names_a_hook_and_only_the_writing_check_keeps_a_command(self):
        found = settings.parse('{"checks": {"tests": {"hook": "check-tests", "report": "r.xml"},'
                               ' "writing": {"run": "uv run python check_writing.py"}}}')
        self.assertEqual(found["checks"]["tests"]["hook"], "check-tests")
        cases = [
            ('{"tests": {"report": "x.xml"}}', "checks.tests needs a hook"),
            ('{"tests": {"hook": " "}}', "checks.tests needs a hook"),
            ('{"tests": {"run": "make test"}}', "checks.tests has a run command.*tollgate:init"),
            ('{"writing": {"hook": "w", "run": "x"}}', "both a hook and a run command"),
            ('{"writing": {}}', "checks.writing needs a run command"),
            ('{"tests": {"hook": "t", "stage": "x"}}', "unknown key: checks.tests.stage"),
            ('{"tests": "make test"}', "checks.tests must be an object"),
        ]
        for checks_, error in cases:
            with self.subTest(checks_), self.assertRaisesRegex(settings.SettingsError, error):
                settings.parse(f'{{"checks": {checks_}}}')

    def test_a_check_may_name_the_variables_it_gets_through_the_secret_filter(self):
        found = settings.parse('{"checks": {"integration": {"hook": "check-integration", '
                               '"report": "r.xml", "env": ["TEST_VAULT_TOKEN"]}}}')
        self.assertEqual(found["checks"]["integration"]["env"], ["TEST_VAULT_TOKEN"])
        cases = [
            ('{"t": {"hook": "t", "env": "TEST_VAULT_TOKEN"}}', "env must be a list"),
            ('{"t": {"hook": "t", "env": [""]}}', "env must be a list"),
            ('{"t": {"hook": "t", "env": ["TOLLGATE_KEYS"]}}', "env may not name TOLLGATE_KEYS"),
            ('{"t": {"hook": "t", "env": ["SSH_AUTH_SOCK"]}}', "env may not name SSH_AUTH_SOCK"),
        ]
        for checks_, error in cases:
            with self.subTest(checks_), self.assertRaisesRegex(settings.SettingsError, error):
                settings.parse(f'{{"checks": {checks_}}}')

    def test_only_the_variables_a_check_names_pass_the_secret_filter(self):
        from gate import checks
        old = {k: os.environ.get(k) for k in ("TEST_VAULT_TOKEN", "OTHER_TOKEN", "TOLLGATE_KEYS")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None
                                 else os.environ.__setitem__(k, v) for k, v in old.items()])
        os.environ.update(TEST_VAULT_TOKEN="t", OTHER_TOKEN="o", TOLLGATE_KEYS="k")
        self.assertNotIn("TEST_VAULT_TOKEN", checks.project_env())
        kept = checks.project_env(["TEST_VAULT_TOKEN", "TOLLGATE_KEYS"])
        self.assertEqual(kept.get("TEST_VAULT_TOKEN"), "t")
        self.assertNotIn("OTHER_TOKEN", kept)
        self.assertNotIn("TOLLGATE_KEYS", kept, "a dropped variable never comes back")

    def test_isolation_is_container_or_sandbox(self):
        self.assertEqual(settings.parse('{"isolation": "sandbox"}')["isolation"], "sandbox")
        with self.assertRaisesRegex(settings.SettingsError, "isolation must be"):
            settings.parse('{"isolation": "none"}')

    def test_the_default_image_is_docker_s_claude_code_image_pinned_by_digest(self):
        image = settings.parse("{}")["container_image"]
        self.assertRegex(image, r"^docker/sandbox-templates:claude-code@sha256:[0-9a-f]{64}$")

    def test_a_project_may_name_its_own_image(self):
        self.assertEqual(settings.parse('{"container_image": "mine:1"}')["container_image"],
                         "mine:1")

    def test_the_settings_on_a_ref_ignore_replace_refs(self):
        repo = Repo()
        self.addCleanup(repo.close)
        repo.write(".tollgate/settings.json", '{"pass_score": 70}')
        real = repo.commit("settings")
        repo.git("checkout", "-q", "-b", "other")
        repo.write(".tollgate/settings.json", '{"pass_score": 1}')
        evil = repo.commit("loose settings")
        repo.git("checkout", "-q", "main")
        repo.git("replace", real, evil)
        self.assertEqual(settings.load_from_ref(str(repo.path), "main")["pass_score"], 70)

    def test_the_runner_is_session_or_background_and_session_by_default(self):
        self.assertEqual(settings.parse("{}")["runner"], "session")
        self.assertEqual(settings.parse('{"runner": "background"}')["runner"], "background")
        with self.assertRaisesRegex(settings.SettingsError, "runner must be"):
            settings.parse('{"runner": "docker"}')

    def test_parallel_is_true_by_default_and_takes_a_bool_or_a_whole_number(self):
        self.assertIs(settings.parse("{}")["parallel"], True)
        self.assertIs(settings.parse('{"parallel": false}')["parallel"], False)
        self.assertEqual(settings.parse('{"parallel": 2}')["parallel"], 2)
        for bad in ('0', '-1', '1.5', '"all"', 'null'):
            with self.subTest(value=bad), self.assertRaisesRegex(settings.SettingsError,
                                                                 "parallel must be"):
                settings.parse(f'{{"parallel": {bad}}}')

    def test_signers_and_owners_are_not_settings(self):
        for key in ("signers", "owners"):
            with self.subTest(key=key), self.assertRaisesRegex(settings.SettingsError,
                                                               f"unknown key: {key}"):
                settings.parse(json.dumps({key: []}))

    def test_checkpoints_default_to_the_plan_and_the_task(self):
        self.assertEqual(settings.parse("{}")["checkpoints"], ["plan", "task"])
        self.assertEqual(settings.parse('{"checkpoints": ["plan", "ticket", "task"]}')["checkpoints"],
                         ["plan", "ticket", "task"])
        self.assertEqual(settings.parse('{"checkpoints": []}')["checkpoints"], [])

    def test_spec_is_a_list_of_relative_paths_and_defaults_to_none(self):
        self.assertEqual(settings.parse("{}")["spec"], [])
        self.assertEqual(settings.parse('{"spec": ["docs/design.md", "docs/architecture/"]}')["spec"],
                         ["docs/design.md", "docs/architecture/"])

    def test_a_bad_spec_is_refused(self):
        for bad in ('"docs"', '{"a": 1}', '[1]', '[""]', '["/etc/design.md"]', '["../design.md"]',
                    '["docs/../../x"]', '["docs", null]', '["C:\\\\docs"]'):
            with self.subTest(bad=bad), self.assertRaisesRegex(settings.SettingsError, "spec"):
                settings.parse(f'{{"spec": {bad}}}')

    def test_an_unknown_checkpoint_is_refused(self):
        for bad in ('["plan", "tikcet"]', '"plan"', '[1]'):
            with self.subTest(bad=bad), self.assertRaisesRegex(settings.SettingsError, "checkpoints"):
                settings.parse(f'{{"checkpoints": {bad}}}')

    def test_an_unknown_key_is_refused(self):
        for key in ("action", "version", "approvers", "sandbox"):
            with self.assertRaisesRegex(settings.SettingsError, f"unknown key: {key}"):
                settings.parse(json.dumps({key: ""}))

    def test_built_in_harness_cannot_be_removed(self):
        s = settings.parse('{"harness": []}')
        self.assertIn(".github/**", s["harness"])
        self.assertIn("**/conftest.py", s["harness"])

    def test_reads_from_the_main_branch_not_the_working_folder(self):
        repo = Repo()
        self.addCleanup(repo.close)
        repo.write(settings.PATH, '{"pass_score": 70}')
        repo.commit()
        repo.write(settings.PATH, '{"pass_score": 1}')
        self.assertEqual(settings.load_from_ref(str(repo.path), "main")["pass_score"], 70)


class TicketsTest(unittest.TestCase):
    def test_reads_items_and_marks(self):
        t = tickets.parse(TICKET)
        self.assertEqual(t.id, "T-1")
        self.assertEqual([i.id for i in t.items], ["C1", "C2", "C3"])
        self.assertTrue(t.item("C2").keeps_behavior)
        self.assertFalse(t.item("C3").must)
        self.assertEqual(t.tests, ["tests/test_t1.py"])

    def test_needs_three_items(self):
        short = TICKET.replace("- [C3, error] A delivery that fails 5 times is marked failed.\n", "")
        with self.assertRaisesRegex(tickets.TicketError, "3 to 10 items"):
            tickets.parse(short)

    def test_needs_a_must_have_item(self):
        text = TICKET.replace("C1, must", "C1").replace("C2, must, keeps behavior", "C2")
        with self.assertRaisesRegex(tickets.TicketError, "at least one must-have"):
            tickets.parse(text)

    def test_keeps_behavior_must_be_must(self):
        text = TICKET.replace("C2, must, keeps behavior", "C2, keeps behavior")
        with self.assertRaisesRegex(tickets.TicketError, "must be a must-have"):
            tickets.parse(text)

    def test_a_long_broken_mark_list_is_refused_quickly(self):
        text = TICKET.replace("- [C3, error] ", "- [C3," + " ," * 40 + " ")
        with within(2), self.assertRaisesRegex(tickets.TicketError, "must look like"):
            tickets.parse(text)

    def test_reads_kinds_and_not_covered(self):
        t = tickets.parse(TICKET)
        self.assertEqual([i.kind for i in t.items], ["happy", "regression", "error"])
        self.assertEqual(t.not_covered["e2e"], "there is no screen.")
        self.assertEqual(t.fixes, "")

    def test_two_kinds_on_one_item_are_refused(self):
        text = TICKET.replace("C1, must, happy", "C1, must, happy, edge")
        with self.assertRaisesRegex(tickets.TicketError, "C1 has two kinds"):
            tickets.parse(text)

    def test_an_unknown_mark_is_refused(self):
        text = TICKET.replace("C1, must, happy", "C1, must, quick")
        with self.assertRaisesRegex(tickets.TicketError, "unknown mark on C1: quick"):
            tickets.parse(text)

    def test_each_kind_is_marked_or_not_covered(self):
        text = TICKET.replace("- concurrency: one delivery runs at a time.\n", "")
        with self.assertRaisesRegex(tickets.TicketError, "concurrency"):
            tickets.parse(text)

    def test_a_kind_marked_and_not_covered_is_refused(self):
        text = TICKET.replace("- e2e: there is no screen.", "- e2e: no screen.\n- error: none.")
        with self.assertRaisesRegex(tickets.TicketError, "error is marked on an item"):
            tickets.parse(text)

    def test_an_unknown_kind_under_not_covered_is_refused(self):
        text = TICKET.replace("- e2e: there is no screen.", "- e2e: no screen.\n- smoke: none.")
        with self.assertRaisesRegex(tickets.TicketError, "unknown kind under Not covered: smoke"):
            tickets.parse(text)

    def test_not_covered_needs_a_reason(self):
        text = TICKET.replace("- e2e: there is no screen.", "- e2e:")
        with self.assertRaisesRegex(tickets.TicketError, "e2e under Not covered needs a reason"):
            tickets.parse(text)

    def test_happy_and_error_cannot_be_not_covered(self):
        text = (TICKET.replace("C3, error", "C3, edge")
                .replace("- edge: a delivery has no input of its own.", "- error: no failure."))
        with self.assertRaisesRegex(tickets.TicketError, "needs an error item"):
            tickets.parse(text)
        text = (TICKET.replace("C1, must, happy", "C1, must, edge")
                .replace("- edge: a delivery has no input of its own.", "- happy: none."))
        with self.assertRaisesRegex(tickets.TicketError, "needs a happy item"):
            tickets.parse(text)

    def test_a_ticket_that_only_keeps_behavior_needs_no_happy_or_error(self):
        text = (TICKET.replace("C1, must, happy", "C1, must, keeps behavior, edge")
                .replace("C3, error", "C3, must, keeps behavior, boundary")
                .replace("- edge: a delivery has no input of its own.", "- happy: no new behavior.")
                .replace("- boundary: the only limit, 5 tries, is in C3.", "- error: no new failure."))
        self.assertEqual(tickets.parse(text).item("C3").kind, "boundary")

    def test_a_fix_needs_a_regression_item(self):
        fixes = TICKET.replace("tests: tests/test_t1.py", "tests: tests/test_t1.py\nfixes: a dropped delivery")
        self.assertEqual(tickets.parse(fixes).fixes, "a dropped delivery")
        text = (fixes.replace("C2, must, keeps behavior, regression", "C2, must, keeps behavior")
                .replace("- e2e: there is no screen.", "- e2e: no screen.\n- regression: none."))
        with self.assertRaisesRegex(tickets.TicketError, "needs a regression item"):
            tickets.parse(text)

    def test_a_performance_item_names_a_number(self):
        text = (TICKET.replace("C3, error] A delivery that fails 5 times is marked failed.",
                               "C3, performance] A delivery is sent within 2 seconds.\n"
                               "- [C4, error] A delivery that fails 5 times is marked failed.")
                .replace("- performance: no time limit is asked for.\n", ""))
        self.assertEqual(tickets.parse(text).item("C3").kind, "performance")
        with self.assertRaisesRegex(tickets.TicketError, "C3 is a performance item"):
            tickets.parse(text.replace("within 2 seconds", "quickly"))

    def test_a_concurrency_item_names_a_number(self):
        text = (TICKET.replace("C3, error] A delivery that fails 5 times is marked failed.",
                               "C3, concurrency] 2 deliveries at once are each sent one time.\n"
                               "- [C4, error] A delivery that fails 5 times is marked failed.")
                .replace("- concurrency: one delivery runs at a time.\n", ""))
        self.assertEqual(tickets.parse(text).item("C3").kind, "concurrency")
        with self.assertRaisesRegex(tickets.TicketError, "C3 is a concurrency item"):
            tickets.parse(text.replace("2 deliveries", "Many deliveries"))

    def test_the_checklist_holds_up_to_ten_items(self):
        extra = "".join(f"- [C{n}] Line {n} is written to the log.\n" for n in range(4, 11))
        text = TICKET.replace("## Not covered", extra + "\n## Not covered", 1)
        self.assertEqual(len(tickets.parse(text).items), 10)
        text = text.replace("## Not covered", "- [C11] One more line.\n\n## Not covered", 1)
        with self.assertRaisesRegex(tickets.TicketError, "3 to 10 items, not 11"):
            tickets.parse(text)

    def test_an_old_ticket_with_no_kinds_names_what_to_add(self):
        text = (TICKET.replace(", happy]", "]").replace(", regression]", "]").replace(", error]", "]")
                .split("## Not covered")[0] + "## Out of scope\nNothing.\n")
        with self.assertRaisesRegex(tickets.TicketError,
                                    r"marks no kinds of test: mark items such as \[C1, must, happy\]"
                                    r" and list kinds that do not apply under ## Not covered"):
            tickets.parse(text)

    def test_item_of_test(self):
        self.assertEqual(tickets.item_of_test("tests.test_t1::test_c12_works"), "C12")
        self.assertIsNone(tickets.item_of_test("tests.test_t1::test_works"))


class JunitTest(unittest.TestCase):
    def test_statuses_and_files(self):
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
            f.write(make_junit({"tests.test_t1::test_a": "passed",
                                "tests.test_t1::test_b": "failed",
                                "tests.test_t1::test_c": "skipped"}))
        found = junit.read(f.name)
        Path(f.name).unlink()
        self.assertEqual(found["tests.test_t1::test_b"].status, "failed")
        self.assertEqual(found["tests.test_t1::test_c"].status, "skipped")
        self.assertTrue(junit.in_files(found["tests.test_t1::test_a"], ["tests/test_t1.py"]))
        self.assertFalse(junit.in_files(found["tests.test_t1::test_a"], ["tests/test_t2.py"]))

    def test_a_go_test_matches_its_file_by_package_and_function(self):
        # gotestsum writes no file attribute, and the import path as the class name.
        pkg = "github.com/acme/app/internal/components"
        result = junit.TestResult(f"{pkg}::TestACause", "passed", "")
        sub = junit.TestResult(f"{pkg}::TestACause/short", "passed", "")
        path = "internal/components/components_test.go"
        defined = {path: {"TestACause"}}
        self.assertTrue(junit.in_files(result, [path], defined))
        self.assertTrue(junit.in_files(sub, [path], defined))
        with self.subTest("the file does not define it"):
            self.assertFalse(junit.in_files(result, [path], {path: {"TestOther"}}))
            self.assertFalse(junit.in_files(result, [path]))
        with self.subTest("another package"):
            other = "internal/logging/cause_test.go"
            self.assertFalse(junit.in_files(result, [other], {other: {"TestACause"}}))
            self.assertFalse(junit.in_files(
                junit.TestResult("github.com/acme/app/x/internal/components2::TestACause",
                                 "passed", ""), [path], defined))
        with self.subTest("a file in the root folder"):
            self.assertFalse(junit.in_files(result, ["a_test.go"], {"a_test.go": {"TestACause"}}))

    def test_go_tests_reads_test_functions(self):
        text = (b"package x\n\nfunc TestMain(m *testing.M) {}\nfunc TestA(t *testing.T) {}\n"
                b"func Testable() {}\nfunc FuzzB(f *testing.F) {}\nfunc helper() {}\n"
                b"func Test_c(t *testing.T) {}\n")
        self.assertEqual(manifest.go_tests(text), {"TestA", "FuzzB", "Test_c"})
        self.assertEqual(manifest.go_tests(None), set())


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)
        self.repo.write(manifest.ticket_path("T-1"), TICKET)
        self.repo.write("tests/test_t1.py", "# tests\n")
        self.commit = self.repo.commit()
        self.ticket = tickets.parse(TICKET)

    def results(self, **status):
        default = {"test_c1_retries": "failed", "test_c2_sent_once": "passed",
                   "test_c3_wait_logged": "failed"}
        default.update(status)
        return {f"tests.test_t1::{n}": junit.TestResult(f"tests.test_t1::{n}", s, "")
                for n, s in default.items()}

    def test_builds_a_manifest(self):
        m = manifest.build(str(self.repo.path), self.commit, self.ticket, self.results())
        self.assertEqual(m["commit"], self.commit)
        self.assertEqual(len(m["tests"]), 3)
        self.assertEqual(m["keeps_behavior"], ["tests.test_t1::test_c2_sent_once"])

    def test_refuses_a_new_test_that_already_passes(self):
        with self.assertRaisesRegex(manifest.FreezeError, "passed before the build"):
            manifest.build(str(self.repo.path), self.commit, self.ticket,
                           self.results(test_c1_retries="passed"))

    def test_refuses_a_must_have_item_with_no_test(self):
        r = self.results()
        del r["tests.test_t1::test_c1_retries"]
        with self.assertRaisesRegex(manifest.FreezeError, "no test: C1"):
            manifest.build(str(self.repo.path), self.commit, self.ticket, r)

    def test_refuses_a_skipped_test(self):
        with self.assertRaisesRegex(manifest.FreezeError, "was skipped"):
            manifest.build(str(self.repo.path), self.commit, self.ticket,
                           self.results(test_c3_wait_logged="skipped"))

    def test_reports_a_changed_frozen_file(self):
        m = manifest.build(str(self.repo.path), self.commit, self.ticket, self.results())
        self.repo.write("tests/test_t1.py", "# weakened\n")
        head = self.repo.commit()
        self.assertEqual(manifest.changed(m, str(self.repo.path), head), ["tests/test_t1.py"])


class TiersTest(unittest.TestCase):
    def setUp(self):
        self.s = settings.parse('{"tiers": {"3": ["src/auth/**"], "4": ["migrations/**"]}}')

    def test_small_change_is_tier_1(self):
        self.assertEqual(tiers.tier(self.s, {"a.py": 20, "b.py": 10})[0], 1)

    def test_large_change_is_tier_2(self):
        self.assertEqual(tiers.tier(self.s, {"a.py": 60})[0], 2)

    def test_path_rules(self):
        self.assertEqual(tiers.tier(self.s, {"src/auth/login.py": 1})[0], 3)
        self.assertEqual(tiers.tier(self.s, {"migrations/0001.sql": 1})[0], 4)
        self.assertEqual(tiers.tier(self.s, {"pytest.ini": 1})[0], 4)

    def test_frozen_files_do_not_count(self):
        self.assertEqual(tiers.tier(self.s, {"src/a.py": 5, "tests/test_t1.py": 500},
                                    frozen={"tests/test_t1.py"})[0], 1)


class ChecksTest(unittest.TestCase):
    """A check runs its hook with pre-commit, in a git repository."""

    def project(self, **commands) -> tuple:
        """(repo, settings): a repository whose pre-commit config holds a local hook
        check-<name> for each command, and the settings that name them."""
        repo = Repo()
        self.addCleanup(repo.close)
        raw = {name: {"run": c} if isinstance(c, str) else c for name, c in commands.items()}
        found, config = hook_checks(raw)
        repo.write(".pre-commit-config.yaml", config)
        repo.commit()
        return repo, settings.parse(json.dumps({"checks": found}))

    def test_runs_checks_and_reads_the_report(self):
        repo, s = self.project(tests={"run": "cp r.xml report.xml", "report": "report.xml"},
                               style="exit 3")
        repo.write("r.xml", make_junit({"t::test_c1_a": "passed"}))
        r = checks.run(s, repo.path)
        self.assertEqual(r.failed_checks(), ["style"])
        self.assertEqual(r.tests["t::test_c1_a"]["status"], "passed")
        self.assertEqual(checks.Results.from_json(r.to_json()).tests, r.tests)

    def test_a_check_runs_its_hook_through_pre_commit_at_the_hook_s_stage(self):
        repo, s = self.project(tests="echo ran by pre-commit; exit 1")
        command, problem = checks.command_of(s["checks"]["tests"], repo.path)
        self.assertEqual((command, problem), (["pre-commit", "run", "check-tests", "--all-files",
                                               "--hook-stage", "pre-push", "--color", "never"],
                                              ""))
        result = checks.run(s, repo.path).checks[0]
        self.assertFalse(result.passed, result.output)
        self.assertIn("ran by pre-commit", result.output)
        self.assertIn("- hook id: check-tests", result.output)

    def test_a_hook_that_is_not_in_the_config_fails_and_says_so(self):
        repo, _ = self.project(tests="true")
        s = settings.parse('{"checks": {"vet": {"hook": "check-vet"}}}')
        result = checks.run(s, repo.path).checks[0]
        self.assertFalse(result.passed)
        self.assertIn("the hook check-vet is not in .pre-commit-config.yaml", result.output)

    def test_the_writing_check_runs_its_own_command(self):
        with tempfile.TemporaryDirectory() as d:
            s = settings.parse('{"checks": {"writing": {"run": "echo plain; exit 1"}}}')
            result = checks.run(s, d).checks[0]
        self.assertFalse(result.passed)
        self.assertIn("plain", result.output)

    def test_project_commands_do_not_see_tollgates_import_path(self):
        import os
        os.environ["PYTHONPATH"] = "/tollgate/code"
        self.addCleanup(os.environ.pop, "PYTHONPATH", None)
        repo, s = self.project(env='test -z "$PYTHONPATH"')
        self.assertEqual(checks.run(s, repo.path).failed_checks(), [])

    def test_project_commands_see_no_token(self):
        import os
        for name in ("GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            os.environ[name] = "secret"
            self.addCleanup(os.environ.pop, name, None)
        repo, s = self.project(env='test -z "$GH_TOKEN" && test -z "$CLAUDE_CODE_OAUTH_TOKEN"')
        self.assertEqual(checks.run(s, repo.path).failed_checks(), [])

    def test_a_check_past_its_time_limit_fails(self):
        repo, s = self.project(slow={"run": "sleep 5", "minutes": 0.01})
        r = checks.run(s, repo.path)
        self.assertTrue(r.checks[0].timed_out)
        self.assertFalse(r.checks[0].passed)


class MissingSeamsTest(unittest.TestCase):
    """Before the build, a check may fail on the new tests only because a seam is not there."""

    GO = ("internal/logging/cause_test.go",)

    def allowed(self, output, files=None, seams=("internal/logging.Cause",)):
        return checks.only_missing_seams(output, list(files or self.GO), list(seams))

    def test_a_go_compile_error_on_a_seam_name_is_allowed(self):
        vet = ("check-vet................Failed\n- hook id: check-vet\n- exit code: 1\n"
               "# example.com/m/internal/logging\n"
               "vet: internal/logging/cause_test.go:12:9: undefined: logging.Cause\n")
        lint = ("internal/logging/cause_test.go:12:9: undefined: logging.Cause (typecheck)\n"
                "internal/logging/cause_test.go:20:9: undefined: logging.Cause (typecheck)\n")
        self.assertTrue(self.allowed(vet))
        self.assertTrue(self.allowed(lint))

    def test_the_go_too_many_errors_line_is_not_an_error_of_its_own(self):
        # staticcheck and golangci-lint end with this line after 10 compile errors.
        errors = "".join(f"internal/logging/cause_test.go:{n}:19: undefined: logging.Cause\n"
                         for n in range(18, 28))
        staticcheck = ("-: # example.com/m/internal/logging_test\n" + errors +
                       "internal/logging/cause_test.go:68:26: too many errors (compile)\n")
        lint = ("internal/logging/async_test.go:1: : # example.com/m/internal/logging_test\n"
                + errors + "internal/logging/cause_test.go:68:26: too many errors\n")
        self.assertTrue(self.allowed(staticcheck))
        self.assertTrue(self.allowed(lint))
        self.assertFalse(self.allowed(
            "internal/logging/cause_test.go:68:26: too many errors (compile)\n"),
            "a line that only says too many errors is no seam error")

    def test_the_golangci_lint_package_header_is_not_an_error_of_its_own(self):
        lint = ("internal/logging/cause_test.go:1: : # example.com/m/internal/logging_test "
                "[example.com/m/internal/logging.test]\n"
                "internal/logging/cause_test.go:66:12: undefined: logging.Cause\n"
                "internal/logging/cause_test.go:139:12: undefined: logging.Cause (typecheck)\n"
                "package logging_test\n1 issues:\n* typecheck: 1\n")
        self.assertTrue(self.allowed(lint))
        self.assertFalse(self.allowed(lint.replace("logging.Cause (typecheck)",
                                                   "strngs.Cut (typecheck)")))

    def test_a_go_method_that_is_a_seam_is_allowed(self):
        out = ("internal/logging/cause_test.go:9:4: l.Cause undefined (type *logging.Logger "
               "has no field or method Cause)\n")
        self.assertTrue(self.allowed(out, seams=["internal/logging.Logger.Cause"]))
        self.assertFalse(self.allowed(out, seams=["internal/logging.Cause"]))

    def test_a_go_field_or_method_of_a_generic_type_that_is_a_seam_is_allowed(self):
        seams = ["internal/flow.Spec.Do", "internal/flow.Tx", "internal/flow.Runner",
                 "internal/flow.Result"]
        files = ["internal/command/register_test.go"]
        field = ("internal/command/register_test.go:52:3: unknown field Do in struct literal "
                 "of type flow.Spec[testParams]\n")
        self.assertTrue(self.allowed(field, files, seams))
        self.assertTrue(self.allowed(field.replace("Spec[testParams]", "Spec"), files, seams))
        self.assertFalse(self.allowed(field.replace("field Do", "field Run"), files, seams))
        self.assertFalse(self.allowed(field.replace("flow.Spec", "other.Spec"), files, seams))
        method = ("internal/command/register_test.go:60:9: s.Do undefined (type "
                  "*flow.Spec[testParams] has no field or method Do)\n")
        self.assertTrue(self.allowed(method, files, seams))
        self.assertTrue(self.allowed(method.replace("[testParams]", ""), files, seams))
        self.assertFalse(self.allowed(method.replace("method Do)", "method Run)"), files, seams))
        self.assertFalse(self.allowed(method.replace("flow.Spec", "flow.Plan"), files, seams))

    def test_a_member_of_a_seam_is_allowed(self):
        # The seam is a type, so the builder adds its fields and methods too, and a test
        # that sets one does not compile on it either.
        seams = ["internal/flow.Spec", "internal/flow.Tx"]
        files = ["internal/command/register_test.go"]
        field = ("internal/command/register_test.go:52:3: unknown field Do in struct literal "
                 "of type flow.Spec[testParams]\n")
        self.assertTrue(self.allowed(field, files, seams), "Do is a member of flow.Spec")
        self.assertTrue(self.allowed(field.replace("flow.Spec", "flow.Tx"), files, seams))
        self.assertFalse(self.allowed(field.replace("flow.Spec", "flow.Plan"), files, seams))
        self.assertFalse(self.allowed(field.replace("flow.Spec", "other.Spec"), files, seams))
        self.assertFalse(self.allowed(field, files, ["internal/flow.Tx"]))
        method = ("internal/command/register_test.go:60:9: s.Do undefined (type "
                  "*flow.Spec[testParams] has no field or method Do)\n")
        self.assertTrue(self.allowed(method, files, seams))
        other = "internal/command/register_test.go:37:56: undefined: flow.Result\n"
        self.assertFalse(self.allowed(other, files, seams), "flow.Result is no seam's member")

    def test_a_bare_name_in_a_test_inside_a_seam_package_is_allowed(self):
        # The seam is a whole package, and the test sits in it, so the compiler names the
        # members it lacks with no package prefix.
        seams = ["internal/api/gen/common", "make gen"]
        files = ["internal/api/gen/common/common_test.go"]
        out = ("internal/api/gen/common/common_test.go:33:9: undefined: Unauthorized\n"
               "internal/api/gen/common/common_test.go:40:2: undefined: StrictServerInterface\n")
        self.assertTrue(self.allowed(out, files, seams))
        self.assertFalse(self.allowed(out, files, ["internal/api/gen/platform"]))
        other = out.replace("internal/api/gen/common/", "internal/api/gen/common/sub/")
        self.assertFalse(self.allowed(other, [f.replace("common/", "common/sub/") for f in files],
                                      seams), "a sub-folder is another package")
        typo = "internal/api/gen/common/common_test.go:5:2: expected ';', found x\n"
        self.assertFalse(self.allowed(typo, files, seams), "a syntax error is no missing name")

    def test_a_name_that_owns_a_seam_is_allowed(self):
        # The seam is a method, so the builder adds its type too, and a test that names
        # the type does not compile on it either.
        seams = ["internal/ports.JobState.Live", "internal/ports.LiveJobStates"]
        files = ["internal/ports/jobs_test.go"]
        go = ("internal/ports/jobs_test.go:9:8: undefined: ports.JobState\n"
              "internal/ports/jobs_test.go:12:9: undefined: ports.LiveJobStates\n")
        self.assertTrue(self.allowed(go, files, seams))
        owner = go.splitlines()[0] + "\n"
        self.assertTrue(self.allowed(owner, files, seams[:1]), "ports.JobState owns the seam")
        self.assertFalse(self.allowed(owner, files, seams[1:]), "no seam is JobState's")
        self.assertFalse(self.allowed(go.replace("ports.JobState\n", "ports.JobStat\n"),
                                      files, seams))
        self.assertFalse(self.allowed(go.replace("ports.JobState\n", "other.JobState\n"),
                                      files, seams))
        mypy = ('tests/test_jobs.py:3: error: Module "app.ports" has no attribute "JobState"'
                "  [attr-defined]\n")
        self.assertTrue(self.allowed(mypy, ["tests/test_jobs.py"], ["app.ports.JobState.live"]))
        self.assertFalse(self.allowed(mypy, ["tests/test_jobs.py"], ["app.ports.Job.live"]))
        rust = ("error[E0412]: cannot find type `JobState` in module `ports`\n"
                " --> tests/jobs.rs:4:22\n")
        self.assertTrue(self.allowed(rust, ["tests/jobs.rs"], ["crate::ports::JobState::live"]))
        self.assertFalse(self.allowed(rust, ["tests/jobs.rs"], ["crate::ports::Job::live"]))

    def test_a_compile_error_on_a_name_that_is_not_a_seam_is_blocking(self):
        self.assertFalse(self.allowed(
            "internal/logging/cause_test.go:12:9: undefined: logging.Caus\n"))
        self.assertFalse(self.allowed(
            "internal/logging/cause_test.go:12:9: undefined: other.Cause\n"))
        self.assertFalse(self.allowed(
            "internal/logging/cause_test.go:3:2: undefined: logging.Cause\n"
            "internal/logging/cause_test.go:4:2: undefined: helper\n"))

    def test_a_lint_error_that_is_not_a_compile_error_is_blocking(self):
        self.assertFalse(self.allowed(
            "internal/logging/cause_test.go:12:9: undefined: logging.Cause (typecheck)\n"
            "internal/logging/cause_test.go:30:2: ineffectual assignment to err (ineffassign)\n"))
        self.assertFalse(self.allowed(
            "internal/logging/cause_test.go:5:1: exported func should have comment (revive)\n"))

    def test_an_output_that_names_no_new_test_file_is_not_allowed_here(self):
        self.assertFalse(self.allowed("internal/logging/log.go:1:1: undefined: logging.Cause\n"))

    def test_typescript_mypy_and_rust_errors_on_a_seam_are_allowed(self):
        ts = ("src/cause.test.ts(3,10): error TS2305: Module '\"./logging\"' has no exported "
              "member 'cause'.\n")
        self.assertTrue(self.allowed(ts, ["src/cause.test.ts"], ["src/logging.cause"]))
        self.assertFalse(self.allowed(ts.replace("TS2305: Module '\"./logging\"' has no "
                                                 "exported member 'cause'",
                                                 "TS2345: Argument of type 'string' is not "
                                                 "assignable"),
                                      ["src/cause.test.ts"], ["src/logging.cause"]))
        mypy = ('tests/test_cause.py:3: error: Module "app.logging" has no attribute "cause"'
                "  [attr-defined]\n")
        self.assertTrue(self.allowed(mypy, ["tests/test_cause.py"], ["app.logging.cause"]))
        self.assertFalse(self.allowed(mypy, ["tests/test_cause.py"], ["app.other.cause"]))
        rust = ("error[E0425]: cannot find function `cause` in module `logging`\n"
                " --> tests/cause.rs:4:22\n  |\n4 |     logging::cause(err);\n")
        self.assertTrue(self.allowed(rust, ["tests/cause.rs"], ["logging::cause"]))
        self.assertFalse(self.allowed(rust.replace("cannot find function `cause` in module "
                                                   "`logging`", "mismatched types"),
                                      ["tests/cause.rs"], ["logging::cause"]))


class PrecommitConfigTest(unittest.TestCase):
    """gate.precommit_config reads the hooks and stages of a config with no YAML library."""

    CONFIG = """# a comment
default_stages: [pre-commit, push]
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0  # a pin
    hooks:
      - id: trailing-whitespace
  - repo: local
    hooks:
    - id: "check-tests"
      name: tests
      entry: make test
      language: system
      stages:
        - manual
        - 'push'
    -   id: check-vet   # go vet
        name: vet
        entry: go vet ./...
        language: system
        stages: [pre-commit]
    - name: lint
      id: check-lint
      entry: make lint
      language: system
"""

    def test_hooks_ids_and_stages(self):
        text = self.CONFIG
        self.assertEqual(pc.hook_ids(text),
                         ["trailing-whitespace", "check-tests", "check-vet", "check-lint"])
        self.assertEqual(pc.default_stages(text), ["pre-commit", "pre-push"])
        self.assertEqual(pc.stages(text, "check-tests"), ["manual", "pre-push"])
        self.assertEqual(pc.stages(text, "check-vet"), ["pre-commit"])
        self.assertEqual(pc.stages(text, "check-lint"), ["pre-commit", "pre-push"])
        self.assertIsNone(pc.stages(text, "check-none"))

    def test_the_gate_runs_a_hook_at_a_push_when_it_can(self):
        text = self.CONFIG
        self.assertEqual(pc.run_stage(text, "check-tests"), "pre-push")
        self.assertEqual(pc.run_stage(text, "check-vet"), "pre-commit")
        self.assertEqual(pc.run_stage(text, "check-lint"), "pre-push")
        self.assertIsNone(pc.run_stage(text, "check-none"))
        plain = "repos:\n- repo: local\n  hooks:\n  - id: a\n    stages: [commit-msg]\n"
        self.assertEqual(pc.run_stage(plain, "a"), "commit-msg")
        self.assertEqual(pc.run_stage("repos:\n- repo: local\n  hooks:\n  - id: b\n", "b"),
                         "pre-commit")

    def test_an_item_ends_before_the_next_item_and_its_trailing_comments(self):
        lines = self.CONFIG.splitlines()
        item = pc.find(self.CONFIG, "check-tests")
        self.assertEqual(lines[item.start], '    - id: "check-tests"')
        self.assertEqual(lines[item.end - 1], "        - 'push'")
        self.assertEqual((item.indent, item.column), (4, 6))
        vet = pc.find(self.CONFIG, "check-vet")
        self.assertEqual((vet.indent, vet.column), (4, 8))

    def test_scalars(self):
        self.assertEqual(pc.scalar('"a \\"b\\" c"  # x'), 'a "b" c')
        self.assertEqual(pc.scalar("'it''s'"), "it's")
        self.assertEqual(pc.scalar("plain  # comment"), "plain")
        self.assertEqual(pc.scalar("# only a comment"), "")
        self.assertEqual(pc.flow_list('["a", b ]  # c'), ["a", "b"])


if __name__ == "__main__":
    unittest.main()

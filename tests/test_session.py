"""Session mode: your own Claude Code session runs each role as a subagent.

The fake role in tests/fake_role.py stands in for the subagent: the test runs it
in the step's folder with the step's prompt, and hands its answer to
`driver.step answer`, as the task skill does with a subagent's answer.
"""

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from driver import roles, session
from gate import state
from tests.driver_fakes import FAKE_ROLE, FakeStore, Sample, driver, sh


def fake_answer(step: dict, mode: str = "pass2") -> str:
    """What the subagent says: the fake role run in the step's folder with its prompt."""
    done = subprocess.run([sys.executable, FAKE_ROLE, *mode.split(), "cost=0.1"],
                          input=step["prompt"],
                          cwd=step["cwd"] if "cwd" in step else step["copy"],
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout)["result"]


def expand(step_fn, project: str, out: dict, mode: str, reverse=False, seen=None) -> dict:
    """Roles that run at once come as {"steps": [...]}. Answer each but one, as parallel
    subagents would, in the order given or reversed, and return the one left, so a test's
    loop answers it as any step. seen gets the agents answered here."""
    if "steps" not in out:
        return out
    order = list(reversed(out["steps"])) if reverse else list(out["steps"])
    for step in order[:-1]:
        code, got = step_fn("answer", "--project", project, "--key", step["key"],
                            "--nonce", step["nonce"], stdin=fake_answer(step, mode))
        assert code == 0 and got.get("saved") == step["key"] and got["waiting"], got
        if seen is not None:
            seen.append(step["agent"])
    return order[-1]


class Case(unittest.TestCase):
    mode = "pass2"          # what the fake roles of steps that run at once answer
    reverse = False         # answer those steps out of the order they came in
    batches = None          # a list that gets the agent:lens of each list of steps
    expand_steps = True     # False: step returns a list of steps as next printed it

    def sample(self, **settings_args):
        s = Sample(**{"runner": "session", **settings_args})
        self.addCleanup(s.close)
        return s

    def call(self, s, role="reviewer", inputs=None, lens="test_fit", copy=None):
        return session.Call(role=role, inputs=inputs or {"x": 1}, lens=lens, command="claude -p",
                            policy=role, copy=str(copy or s.project), project=str(s.project),
                            log="T-1", ticket="T-1", task="retries")


class CallerTest(Case):
    def test_the_background_caller_is_today_s_role_run(self):
        s = self.sample()
        settings = {"stop": {"attempt_minutes": 7}, "isolation": "sandbox",
                    "container_image": "img", "runner": "background"}
        caller = session.caller(settings)
        self.assertIsInstance(caller, session.Background)
        with mock.patch.object(roles, "run", return_value="answer") as run:
            self.assertEqual(caller(self.call(s)), "answer")
        run.assert_called_once_with("claude -p", "reviewer", {"x": 1}, str(s.project), 7,
                                    "test_fit", "sandbox", "img", str(s.project), "reviewer")

    def test_the_session_caller_writes_the_step_and_pauses(self):
        s = self.sample()
        caller = session.caller({"runner": "session"})
        with self.assertRaises(session.Pause) as paused:
            caller(self.call(s))
        step = session.read_step(str(s.project))
        self.assertEqual(step, paused.exception.step)
        self.assertEqual(step["agent"], "tollgate:reviewer")
        self.assertEqual(step["prompt"], roles.prompt("reviewer", {"x": 1}, "test_fit"))
        self.assertEqual(step["key"], session.key("reviewer", "test_fit", {"x": 1}))
        self.assertEqual((step["ticket"], step["task"], step["log"]), ("T-1", "retries", "T-1"))

    def test_a_saved_answer_is_returned_with_no_cost(self):
        s = self.sample()
        caller = session.Session()
        with self.assertRaises(session.Pause) as paused:
            caller(self.call(s))
        session.save_answer(str(s.project), paused.exception.step,
                            'Done.\n\n```json\n{"verdict": "pass"}\n```')
        self.assertIsNone(session.read_step(str(s.project)))
        answer = caller(self.call(s))
        self.assertEqual(answer.data, {"verdict": "pass"})
        self.assertIsNone(answer.cost_usd)
        with self.assertRaises(session.Pause):
            caller(self.call(s, inputs={"x": 2}))

    def test_roles_asked_at_once_wait_together_and_answer_in_the_order_asked(self):
        s = self.sample()
        caller = session.Session()
        calls = [self.call(s, lens="simplicity"), self.call(s, lens="readiness"),
                 self.call(s, lens="fresh_reader")]
        with self.assertRaises(session.Pause):
            caller.many(calls)
        steps = session.read_steps(str(s.project))
        self.assertEqual([st["lens"] for st in steps], ["simplicity", "readiness", "fresh_reader"])
        # Answered out of order: the run still pauses while one of them waits.
        for st, verdict in ((steps[2], "c"), (steps[0], "a")):
            session.save_answer(str(s.project), st, f'```json\n{{"verdict": "{verdict}"}}\n```')
        with self.assertRaises(session.Pause):
            caller.many(calls)
        self.assertEqual([st["lens"] for st in session.read_steps(str(s.project))],
                         ["readiness"])
        session.save_answer(str(s.project), session.read_steps(str(s.project))[0],
                            '```json\n{"verdict": "b"}\n```')
        self.assertEqual([a.data["verdict"] for a in caller.many(calls)], ["a", "b", "c"])
        self.assertEqual(session.read_steps(str(s.project)), [])

    def test_the_background_asks_roles_one_after_another_and_keeps_each_failure(self):
        s = self.sample()
        failed = roles.RoleError("no answer")
        caller = mock.Mock(side_effect=["one", failed, "three"])
        found = session.ask_many(caller, [self.call(s, lens=lens) for lens in "abc"])
        self.assertEqual(found, ["one", failed, "three"])
        self.assertEqual([c.args[0].lens for c in caller.call_args_list], ["a", "b", "c"])

    def test_a_saved_answer_written_by_anyone_but_answer_is_ignored(self):
        s = self.sample()
        caller = session.Session()
        with self.assertRaises(session.Pause) as paused:
            caller(self.call(s))
        step = paused.exception.step
        # A role that got a line into the answers file, with the right key.
        path = session.answers_path(str(s.project), "T-1")
        path.write_text(json.dumps({"event": "answer", "key": step["key"], "work": "",
                                    "text": '```json\n{"verdict": "pass"}\n```'}) + "\n")
        with self.assertRaises(session.Pause):
            caller(self.call(s))
        session.save_answer(str(s.project), step, '```json\n{"verdict": "fail"}\n```')
        self.assertEqual(caller(self.call(s)).data, {"verdict": "fail"})
        # A line changed after answer saved it does not verify either.
        lines = path.read_text().splitlines()
        saved = json.loads(lines[-1])
        saved["text"] = '```json\n{"verdict": "pass"}\n```'
        path.write_text("\n".join(lines[:-1] + [json.dumps(saved)]) + "\n")
        with self.assertRaises(session.Pause):
            caller(self.call(s))

    def test_the_key_names_the_role_the_lens_and_the_inputs(self):
        k = session.key("reviewer", "test_fit", {"a": 1, "b": 2})
        self.assertEqual(k, session.key("reviewer", "test_fit", {"b": 2, "a": 1}))
        self.assertNotEqual(k, session.key("reviewer", "checklist", {"a": 1, "b": 2}))
        self.assertNotEqual(k, session.key("builder", "test_fit", {"a": 1, "b": 2}))
        # The content the role judges is in the key; with none, the key is as before.
        self.assertEqual(k, session.key("reviewer", "test_fit", {"a": 1, "b": 2}, ""))
        self.assertNotEqual(k, session.key("reviewer", "test_fit", {"a": 1, "b": 2}, "t1"))


class WorkTest(Case):
    def test_the_builder_s_work_survives_a_replay(self):
        s = self.sample()
        copy = roles.make_copy(str(s.project), "T-1")
        caller = session.Session()
        call = self.call(s, role="builder", lens=None, copy=copy)
        with self.assertRaises(session.Pause) as paused:
            caller(call)
        Path(copy, "app.py").write_text("changed\n")
        Path(copy, "new.py").write_text("new\n")
        Path(copy, "run_tests.py").unlink()
        session.save_answer(str(s.project), paused.exception.step,
                            '```json\n{"changed": ["app.py"]}\n```')
        self.assertEqual(sh(copy, "git", "status", "--porcelain").count("\n"), 2)
        # The next try starts again from the best try so far, as try_once does.
        sh(copy, "git", "reset", "-q", "--hard", "HEAD")
        sh(copy, "git", "clean", "-q", "-fd")
        self.assertEqual(caller(call).data, {"changed": ["app.py"]})
        self.assertEqual(Path(copy, "app.py").read_text(), "changed\n")
        self.assertEqual(Path(copy, "new.py").read_text(), "new\n")
        self.assertFalse(Path(copy, "run_tests.py").exists())
        self.assertEqual(sh(copy, "git", "diff", "--cached", "--name-only"), "")

    def test_a_replay_puts_back_the_saved_commit_even_if_the_ref_moves(self):
        s = self.sample()
        copy = roles.make_copy(str(s.project), "T-1")
        caller = session.Session()
        call = self.call(s, role="builder", lens=None, copy=copy)
        with self.assertRaises(session.Pause) as paused:
            caller(call)
        Path(copy, "app.py").write_text("saved\n")
        session.save_answer(str(s.project), paused.exception.step, '```json\n{}\n```')
        sh(copy, "git", "reset", "-q", "--hard", "HEAD")
        # A role in the copy points the ref at other work.
        Path(copy, "app.py").write_text("forged\n")
        sh(copy, "git", "update-ref", session.work_ref(paused.exception.step["key"]),
           session.save_work(copy, "other"))
        sh(copy, "git", "reset", "-q", "--hard", "HEAD")
        caller(call)
        self.assertEqual(Path(copy, "app.py").read_text(), "saved\n")

    def test_a_replace_ref_in_the_copy_does_not_change_the_replayed_work(self):
        s = self.sample()
        copy = roles.make_copy(str(s.project), "T-1")
        caller = session.Session()
        call = self.call(s, role="builder", lens=None, copy=copy)
        with self.assertRaises(session.Pause) as paused:
            caller(call)
        Path(copy, "app.py").write_text("saved\n")
        line = session.save_answer(str(s.project), paused.exception.step, '```json\n{}\n```')
        sh(copy, "git", "reset", "-q", "--hard", "HEAD")
        Path(copy, "app.py").write_text("evil\n")
        evil = session.save_work(copy, "evil")
        sh(copy, "git", "replace", line["work"], evil)
        sh(copy, "git", "reset", "-q", "--hard", "HEAD")
        caller(call)
        self.assertEqual(Path(copy, "app.py").read_text(), "saved\n")

    def test_a_hook_the_builder_writes_in_the_copy_does_not_run(self):
        s = self.sample(runner="background")
        d, _ = driver(s)
        d.copy = roles.make_copy(str(s.project), "T-1")
        marker = Path(s.root, "hook-ran")
        hook = Path(d.copy, ".git", "hooks", "pre-commit")
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        Path(d.copy, "app.py").write_text("changed\n")
        d.commit("feat(T-1): try")
        self.assertFalse(marker.exists())

    def test_a_reviewer_s_answer_saves_no_work(self):
        s = self.sample()
        copy = roles.make_copy(str(s.project), "T-1")
        with self.assertRaises(session.Pause) as paused:
            session.Session()(self.call(s, copy=copy))
        line = session.save_answer(str(s.project), paused.exception.step, '```json\n{}\n```')
        self.assertEqual(line["work"], "")


class DriverPauseTest(Case):
    def test_the_driver_pauses_with_5_and_lets_go_of_its_lock(self):
        s = self.sample()
        d, _ = driver(s)
        self.assertEqual(d.run(), 5)
        step = session.read_step(str(s.project))
        self.assertEqual(step["agent"], "tollgate:test_writer")
        self.assertEqual(Path(step["copy"]), Path(d.copy).resolve())
        self.assertFalse(state.path(str(s.project), "run", "T-1.lock").exists())

    def test_a_paused_driver_replays_its_saved_answers_to_the_next_step(self):
        s = self.sample()
        d, _ = driver(s)
        d.run()
        first = session.read_step(str(s.project))
        session.save_answer(str(s.project), first, fake_answer(first))
        d, _ = driver(s)
        self.assertEqual(d.run(), 5)
        second = session.read_step(str(s.project))
        self.assertEqual((second["role"], second["lens"]), ("reviewer", "test_fit"))
        # The test writer's tests are back in the copy for the reviewer.
        self.assertTrue(Path(second["copy"], "tests/test_t1.py").exists())

    FAIL = ('```json\n{"verdict": "fail", "findings": '
            '[{"level": "blocking", "text": "the test is weak"}]}\n```')

    def rewrite_after_a_rejection(self, change: str):
        """The test writer writes tests, the test_fit reviewer fails them, and the test
        writer writes tests of the same names again, with `change` added to the file.
        Return the first review step and the step the next run of the driver pauses at."""
        s = self.sample()
        driver(s)[0].run()
        first = session.read_step(str(s.project))
        session.save_answer(str(s.project), first, fake_answer(first))
        driver(s)[0].run()
        review = session.read_step(str(s.project))
        self.assertEqual((review["role"], review["lens"]), ("reviewer", "test_fit"))
        session.save_answer(str(s.project), review, self.FAIL)
        driver(s)[0].run()
        again = session.read_step(str(s.project))
        self.assertEqual(again["role"], "test_writer")
        text = fake_answer(again)
        path = Path(again["copy"], "tests/test_t1.py")
        path.write_text(path.read_text() + change)
        session.save_answer(str(s.project), again, text)
        self.assertEqual(driver(s)[0].run(), 5)
        return s, review, session.read_step(str(s.project))

    def test_new_tests_of_the_same_names_get_a_new_review(self):
        s, review, step = self.rewrite_after_a_rejection("\n# A stronger test.\n")
        self.assertEqual((step["role"], step["lens"]), ("reviewer", "test_fit"))
        self.assertNotEqual(step["key"], review["key"])
        self.assertEqual(sum(e["event"] == "tests_rejected" for e in s.log()), 1)
        # Its saved answers still replay: the next run waits at the same review.
        self.assertEqual(driver(s)[0].run(), 5)
        self.assertEqual(session.read_step(str(s.project))["key"], step["key"])

    def test_the_same_tests_again_replay_the_saved_review(self):
        s, _, step = self.rewrite_after_a_rejection("")
        self.assertEqual(step["role"], "test_writer")
        self.assertEqual(sum(e["event"] == "tests_rejected" for e in s.log()), 2)

    def test_session_mode_logs_the_cost_as_unknown(self):
        s = self.sample()
        d, _ = driver(s)
        d.run()
        step = session.read_step(str(s.project))
        session.save_answer(str(s.project), step, fake_answer(step))
        driver(s)[0].run()
        written = next(e for e in s.log() if e["event"] == "tests_written")
        self.assertIn("cost_usd", written)
        self.assertIsNone(written["cost_usd"])


class StepTest(Case):
    """driver.step next and answer, as the task skill runs them."""

    def task_sample(self, **settings_args):
        s = self.sample(ticket=False, **settings_args)
        Path(s.project, ".tollgate/requests").mkdir(parents=True)
        Path(s.project, ".tollgate/requests/demo.md").write_text("Add shout and whisper.\n")
        return s, FakeStore(s.project)

    def step(self, store, *argv, stdin=""):
        from driver import step
        out = io.StringIO()
        with redirect_stdout(out):
            code = step.main(list(argv), store=store, stdin=io.StringIO(stdin))
        found = json.loads(out.getvalue())
        project = argv[argv.index("--project") + 1]
        if "steps" in found and self.batches is not None:
            self.batches.append([f"{st['agent']}:{session.find_step(project, st['key'])[0]['lens']}"
                                 for st in found["steps"]])
        if not self.expand_steps:
            return code, found
        return code, expand(lambda *a, **k: self.step(store, *a, **k), project, found,
                            self.mode, self.reverse, getattr(self, "seen", None))

    def next(self, s, store):
        return self.step(store, "next", "--project", str(s.project), "--task", "demo")

    def answer(self, s, store, out, text, key=None, nonce=None):
        return self.step(store, "answer", "--project", str(s.project), "--key",
                         key or out["key"], "--nonce", nonce or out["nonce"], stdin=text)

    def test_next_names_the_subagent_its_prompt_its_folder_and_the_key(self):
        s, store = self.task_sample()
        code, out = self.next(s, store)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(out), ["agent", "cwd", "key", "nonce", "prompt"])
        self.assertEqual(out["agent"], "tollgate:analyst")
        self.assertTrue(out["prompt"].startswith("# Role: analyst"))
        self.assertEqual(Path(out["cwd"]).resolve(),
                         state.path(str(s.project), "builders", "task-demo").resolve())
        # Asked again, next names the same step.
        self.assertEqual(self.next(s, store)[1]["key"], out["key"])

    def test_the_analyst_gets_the_spec_and_its_specified_facts_are_checked(self):
        from tests.fake_role import inputs
        s, store = self.task_sample(spec=["docs/"])
        Path(s.project, "docs").mkdir()
        Path(s.project, "docs/design.md").write_text("# Design\n\n## Retries\n\nThree.\n")
        sh(s.project, "git", "add", "-A")
        sh(s.project, "git", "commit", "-q", "-m", "spec")
        _, out = self.next(s, store)
        self.assertEqual(out["agent"], "tollgate:analyst")
        asked = inputs(out["prompt"])
        self.assertEqual(asked["spec"], ["docs/"])
        text = (f"---\ntask: demo\ncommit: {asked['commit']}\n---\n"
                "## Structure\n- confirmed: greet builds the greeting [app.py::greet]\n"
                "## Entry points\n- confirmed: greet is the entry point [app.py::greet]\n"
                "## Frameworks\n"
                "- specified: a failed send is retried 3 times [docs/design.md::Retries]\n"
                "- specified: a send times out after 5 seconds [docs/design.md::Timeouts]\n"
                "## Similar features\n- assumed: none\n"
                "## Tests\n- confirmed: tests run with run_tests.py [run_tests.py:1]\n"
                "## Standards\n- assumed: none written\n")
        self.answer(s, store, out, "Done.\n\n```json\n" + json.dumps({"analysis": text}) + "\n```")
        written = next(e for e in s.log("task-demo") if e["event"] == "analysis_written")
        self.assertEqual(written["not_found"], ["docs/design.md::Timeouts"])
        self.assertIn("- specified: a failed send is retried 3 times [docs/design.md::Retries]",
                      written["text"])
        self.assertIn("- assumed: a send times out after 5 seconds", written["text"])

    def test_the_analyst_key_is_the_same_with_no_spec_as_before_spec_existed(self):
        from tests.fake_role import inputs
        s, store = self.task_sample()
        _, out = self.next(s, store)
        asked = inputs(out["prompt"])
        self.assertNotIn("spec", asked)
        self.assertEqual(out["key"], session.key("analyst", None, asked))

    def two_tasks(self):
        s, store = self.task_sample()
        Path(s.project, ".tollgate/requests/other.md").write_text("Add shout and whisper.\n")
        return s, store

    def next_of(self, s, store, task):
        return self.step(store, "next", "--project", str(s.project), "--task", task)

    def run_two(self, s, store, order):
        """next and answer two tasks of one project, each waiting at its own step, in
        turns that order gives. Return what each printed last."""
        outs = {t: self.next_of(s, store, t)[1] for t in ("demo", "other")}
        for i in range(400):
            # Turn by turn: one step of one task, then one of the other.
            names = order(outs) if i % 2 == 0 else order(outs)[::-1]
            todo = [t for t in names if not outs[t].get("done")]
            if not todo:
                return outs
            task = todo[0]
            out = outs[task]
            self.assertNotIn("error", out, (task, out))
            if "question" in out:
                options = out["question"]["options"]
                store.answer(out["question"]["n"], "tollgate decide "
                             + ("approve" if "approve" in options else "continue"))
                outs[task] = self.next_of(s, store, task)[1]
            elif "parked" in out:
                self.fail(f"{task} parked: {out}")
            else:
                code, outs[task] = self.answer(s, store, out, fake_answer(out))
                self.assertEqual(code, 0, outs[task])
        self.fail(f"the tasks did not end: {outs}")

    def test_two_tasks_of_one_project_wait_at_once_and_both_finish(self):
        for name, order in (("demo first", lambda outs: ["demo", "other"]),
                            ("other first", lambda outs: ["other", "demo"])):
            with self.subTest(name):
                s, store = self.two_tasks()
                first = {t: self.next_of(s, store, t)[1] for t in ("demo", "other")}
                # Both wait, each at its own step: the second next did not replace the first.
                self.assertEqual(len(session.all_steps(str(s.project))), 2)
                self.assertNotEqual(first["demo"]["key"], first["other"]["key"])
                outs = self.run_two(s, store, order)
                self.assertTrue(outs["demo"]["done"] and outs["other"]["done"], outs)
                for task in ("demo", "other"):
                    self.assertIn("reported", [e["event"] for e in s.log(f"task-{task}")])

    def test_a_key_or_nonce_of_the_other_task_is_refused(self):
        s, store = self.two_tasks()
        demo = self.next_of(s, store, "demo")[1]
        other = self.next_of(s, store, "other")[1]
        code, refused = self.answer(s, store, demo, fake_answer(demo), nonce=other["nonce"])
        self.assertEqual(code, 1)
        self.assertIn("not the ones next printed", refused["error"])
        code, refused = self.answer(s, store, demo, fake_answer(demo), key="0" * 64)
        self.assertEqual(code, 1)
        self.assertEqual(len(session.all_steps(str(s.project))), 2)
        # The right pair is still saved, and the other task's step still waits.
        self.assertEqual(self.answer(s, store, demo, fake_answer(demo))[0], 0)
        self.assertIn(other["key"], [st["key"] for st in session.all_steps(str(s.project))])

    def test_answer_refuses_a_key_that_is_not_the_waiting_step_s(self):
        s, store = self.task_sample()
        _, out = self.next(s, store)
        code, refused = self.answer(s, store, out, fake_answer(out), key="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("not the ones next printed", refused["error"])
        self.assertNotIn(out["key"], refused["error"])
        self.assertEqual(session.read_step(str(s.project))["key"], out["key"])
        # Once answered, the old key is stale.
        code, following = self.answer(s, store, out, fake_answer(out))
        self.assertEqual(code, 0)
        self.assertNotEqual(following["key"], out["key"])
        code, refused = self.answer(s, store, out, fake_answer(out))
        self.assertEqual(code, 1)
        self.assertIn("not the ones next printed", refused["error"])
        self.assertNotIn(following["key"], refused["error"])

    def test_answer_needs_the_nonce_that_only_next_printed(self):
        s, store = self.task_sample()
        _, out = self.next(s, store)
        stored = session.step_path(str(s.project)).read_text()
        self.assertNotIn(out["nonce"], stored)
        code, refused = self.answer(s, store, out, fake_answer(out), nonce="0" * 32)
        self.assertEqual(code, 1)
        self.assertNotIn(out["key"], refused["error"])
        self.assertEqual(self.answer(s, store, out, fake_answer(out))[0], 0)

    def test_a_malformed_step_file_is_an_error_not_a_crash(self):
        s, store = self.task_sample()
        path = session.step_path(str(s.project))
        for text in ("[]", '{"key": "k"}', "not json", '{"role": 1, "key": "k", "log": "x"}'):
            path.write_text(text)
            self.assertIsNone(session.read_step(str(s.project)))
            code, refused = self.answer(s, store, {"key": "k", "nonce": "n"}, "```json\n{}\n```")
            self.assertEqual(code, 1)
            self.assertIn("no step waits", refused["error"])

    def test_a_second_next_while_the_task_runs_leaves_the_step_alone(self):
        import os
        s, store = self.task_sample()
        _, out = self.next(s, store)
        state.path(str(s.project), "run", "task-demo.lock").write_text(str(os.getpid()))
        code, busy = self.next(s, store)
        self.assertEqual(code, 1)
        self.assertIn("another driver", busy["error"])
        self.assertEqual(session.read_step(str(s.project))["key"], out["key"])

    def test_next_writes_the_nonce_only_under_the_run_lock(self):
        import os

        from driver.task import Task
        s, store = self.task_sample()
        self.next(s, store)
        before = session.step_path(str(s.project)).read_text()
        state.path(str(s.project), "run", "task-demo.lock").write_text(str(os.getpid()))
        with mock.patch.object(Task, "run", return_value=5):
            code, busy = self.next(s, store)
        self.assertEqual(code, 1)
        self.assertIn("another driver", busy["error"])
        self.assertEqual(session.step_path(str(s.project)).read_text(), before)

    def test_answer_refuses_an_answer_with_no_final_json_block(self):
        s, store = self.task_sample()
        _, out = self.next(s, store)
        # A subagent that ran out of turns ends with a part of its answer. The first such
        # answer prints the same step again, to run once more.
        code, again = self.answer(s, store, out, "I looked around. All")
        self.assertEqual(code, 0)
        self.assertIn("json block", again["retry"])
        self.assertEqual((again["key"], again["nonce"], again["prompt"], again["agent"]),
                         (out["key"], out["nonce"], out["prompt"], out["agent"]))
        self.assertIsNotNone(session.read_step(str(s.project)))
        code, refused = self.answer(s, store, again, "I looked around. All good.")
        self.assertEqual(code, 1)
        self.assertIn("json block", refused["error"])
        self.assertIsNotNone(session.read_step(str(s.project)))
        # A whole answer to the same step is still saved.
        self.assertEqual(self.answer(s, store, out, fake_answer(out))[0], 0)

    def test_next_refuses_to_start_a_task_that_cannot_pass(self):
        s, store = self.task_sample()
        Path(s.project, ".tollgate/settings.json").write_text('{"nonsense": 1}')
        sh(s.project, "git", "commit", "-q", "-am", "bad settings")
        code, out = self.next(s, store)
        self.assertEqual(code, 1)
        self.assertIn("/tollgate:init", out["error"])

    def test_a_whole_task_runs_through_next_and_answer(self):
        s, store = self.task_sample()
        code, out = self.next(s, store)
        agents, questions = [], 0
        for _ in range(100):
            self.assertEqual(code, 0, out)
            if "question" in out:
                # The person's answer, recorded as the inbox records it.
                questions += 1
                options = out["question"]["options"]
                pick = "approve" if "approve" in options else "continue"
                self.assertIn(pick, options)
                store.answer(out["question"]["n"], f"tollgate decide {pick}")
                code, out = self.next(s, store)
                continue
            if out.get("done"):
                break
            agents.append(out["agent"])
            code, out = self.answer(s, store, out, fake_answer(out))
        self.assertTrue(out.get("done"), out)
        self.assertEqual(questions, 2)   # the plan and the task checkpoints
        self.assertEqual([m[1] for m in store.ready_marks], ["demo-1", "demo-2"])
        self.assertIn("demo-2", out["result"])
        self.assertEqual(out["merged"], [])
        self.assertEqual(agents[0], "tollgate:analyst")
        for role in ("builder", "test_writer", "reviewer"):
            self.assertIn(f"tollgate:{role}", agents)
        self.assertIsNone(session.read_step(str(s.project)))
        report = Path(state.path(str(s.project), "reports", "demo.md")).read_text()
        self.assertIn("Cost: unknown", report)

    def test_the_lenses_come_as_steps_at_once_and_may_answer_in_any_order(self):
        s, store = self.task_sample(tier1={"max_files": 0, "max_lines": 0})   # tier 2
        self.batches, self.reverse = [], True
        out = self.run_task(s, store)
        self.assertTrue(out.get("done"), out)
        plan = ["tollgate:reviewer:simplicity", "tollgate:reviewer:fresh_reader",
                "tollgate:reviewer:readiness"]
        self.assertIn(plan, self.batches)
        # The checklist reviewer of a try and the tier's review lenses run at once too.
        change = [b for b in self.batches if b[0] == "tollgate:reviewer:checklist"]
        self.assertTrue(change, self.batches)
        self.assertTrue(all(len(b) > 1 for b in change), change)

    def test_each_step_of_a_list_has_its_own_key_and_nonce_and_the_last_answer_goes_on(self):
        s, store = self.task_sample()
        self.expand_steps = False
        code, out = self.next(s, store)
        for _ in range(20):
            if "steps" in out:
                break
            self.assertEqual(code, 0, out)
            code, out = self.answer(s, store, out, fake_answer(out))
        first, second, third = out["steps"]
        self.assertEqual(len({st["key"] for st in out["steps"]}), 3)
        self.assertEqual(len({st["nonce"] for st in out["steps"]}), 3)
        # A nonce answers only its own step.
        self.assertEqual(self.answer(s, store, first, "x", nonce=second["nonce"])[0], 1)
        code, got = self.answer(s, store, second, fake_answer(second))
        self.assertEqual((code, got["saved"]), (0, second["key"]))
        self.assertEqual(sorted(got["waiting"]), sorted([first["key"], third["key"]]))
        # next prints only the steps that still wait, each with a new nonce.
        code, again = self.next(s, store)
        self.assertEqual([st["key"] for st in again["steps"]], [first["key"], third["key"]])
        self.assertEqual(self.answer(s, store, first, fake_answer(first))[0], 1)
        rest = {st["key"]: st for st in again["steps"]}
        code, got = self.answer(s, store, rest[third["key"]], fake_answer(third))
        self.assertEqual(got, {"saved": third["key"], "waiting": [first["key"]]})
        code, got = self.answer(s, store, rest[first["key"]], fake_answer(first))
        self.assertEqual(code, 0, got)
        self.assertNotIn("saved", got)
        self.assertNotIn(first["key"], json.dumps(got))

    def run_task(self, s, store, role="pass2", decide=None, rounds=150):
        """next and answer until the task is done or parked. decide answers a question."""
        self.mode = role
        code, out = self.next(s, store)
        for _ in range(rounds):
            self.assertEqual(code, 0, out)
            if "question" in out:
                options = out["question"]["options"]
                pick = ("approve" if "approve" in options else "continue" if "continue" in options
                        else decide(out["question"]))
                store.answer(out["question"]["n"], f"tollgate decide {pick}")
                code, out = self.next(s, store)
                continue
            if out.get("done") or "parked" in out:
                return out
            code, out = self.answer(s, store, out, fake_answer(out, role))
        self.fail(f"the task did not end: {out}")

    @staticmethod
    def style_check():
        """A writing check that fails, naming the new test file, while it holds the mark.
        Its output also holds the time, so it differs on each run."""
        py = sys.executable
        bad = (f"{py} -c \"import sys,time,pathlib;p=pathlib.Path('tests/test_t1.py');"
               "b=p.exists() and 'BADSTYLE' in p.read_text();"
               "print(time.time());print('tests/test_t1.py:9: bad style'*b);sys.exit(b)\"")
        return {"tests": {"run": f"{py} run_tests.py", "report": "report.xml"},
                "writing": {"run": bad}}

    def test_a_replay_logs_each_event_once(self):
        s, store = self.task_sample(checks=self.style_check())
        out = self.run_task(s, store, "pass2 tw_bad=1")
        self.assertTrue(out.get("done"), out)

        def counts(name):
            from collections import Counter
            return Counter(e["event"] for e in s.log(name))
        ticket = counts("demo-1")
        self.assertEqual([e["event"] for e in s.log("demo-1")], [
            "started", "tests_written", "tests_rejected", "tests_written", "tests_reviewed",
            "frozen", "try_started", "builder_said", "try_failed", "try_started",
            "builder_said", "try_passed", "ready", "waiting_for_merge"])
        self.assertEqual(ticket["started"], 1)
        self.assertEqual(ticket["tests_written"], 2)
        self.assertEqual(ticket["tests_rejected"], 1)
        self.assertEqual(ticket["try_started"], 2)
        self.assertEqual(ticket["try_failed"], 1)
        self.assertEqual(ticket["stopped"], 0)
        task = counts("task-demo")
        self.assertEqual(task["started"], 1)
        self.assertEqual(task["plan_reviewed"], 3)
        self.assertEqual(task["analysis_written"], 1)
        for name in ("demo-2", "task-demo"):
            lines = [json.dumps({k: v for k, v in e.items() if k != "at"}, sort_keys=True)
                     for e in s.log(name)]
            self.assertEqual(len(lines), len(set(lines)), name)


    def test_plan_review_stops_after_four_rounds_when_each_fix_changes_the_plan(self):
        # Each answer to a finding changes the plan, so each round's reviewers get new
        # inputs. A replay must put back the plan each saved answer started from, so the
        # rounds count up to the cap and the open findings go to the plan decision.
        s, store = self.task_sample()
        out = self.run_task(s, store, "pass2 planfind=99 revise=1", rounds=80)
        self.assertTrue(out.get("done"), out)
        events = s.log("task-demo")
        reviewed = [e["round"] for e in events if e["event"] == "plan_reviewed"]
        self.assertEqual(reviewed, [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4])
        done = [e for e in events if e["event"] == "plan_review_done"]
        self.assertEqual(len(done), 1)
        self.assertIn("4 rounds with findings", json.dumps(done[0]["open"]))

    def test_a_plan_sent_back_by_the_driver_is_replayed_to_its_new_version(self):
        # The driver sends the first plan back, with no README. The next run must replay
        # the new plan's answer and log it, not take the plan as written already.
        s, store = self.task_sample()
        from tests.driver_fakes import sh
        Path(s.project, ".tollgate/checks").mkdir(parents=True, exist_ok=True)
        Path(s.project, ".tollgate/checks/check_writing.py").write_text("")
        sh(s.project, "git", "add", "-A")
        sh(s.project, "git", "commit", "-q", "-m", "chore(tollgate): add the writing check")
        out = self.run_task(s, store)
        self.assertTrue(out.get("done"), out)
        events = [e["event"] for e in s.log("task-demo")]
        self.assertEqual(events.count("plan_invalid"), 1)
        self.assertEqual(events.count("plan_written"), 2)
        self.assertLess(events.index("plan_invalid"), len(events) - events[::-1].index("plan_written"))
        plan = sh(s.project, "git", "show", "tg-plan/demo:.tollgate/plans/demo.md")
        self.assertIn("create README.md", plan)

    def test_a_plan_that_breaks_a_rule_after_a_review_goes_back_to_the_planner(self):
        # The answer to a plan finding revises the plan with a bad assumption. The next
        # run must send the plan back with the fault, not stop with an error.
        s, store = self.task_sample()
        out = self.run_task(s, store, "pass2 planfind=1 bad_answer=1")
        self.assertTrue(out.get("done"), out)
        invalid = [e for e in s.log("task-demo") if e["event"] == "plan_invalid"]
        self.assertTrue(invalid)
        self.assertIn("must end with a question mark", invalid[0]["problems"][0])
        plan = sh(s.project, "git", "show", "tg-plan/demo:.tollgate/plans/demo.md")
        self.assertNotIn("Nobody looked it up", plan)

    def test_a_ticket_that_breaks_a_rule_goes_back_to_the_planner(self):
        s, store = self.task_sample()
        out = self.run_task(s, store, "pass2 bad_ticket=1")
        self.assertTrue(out.get("done"), out)
        invalid = [e for e in s.log("task-demo") if e["event"] == "plan_invalid"]
        self.assertEqual(len(invalid), 1)
        self.assertIn("Checklist", " ".join(invalid[0]["problems"]))

    def test_a_planner_that_keeps_breaking_the_rule_parks_the_task_with_the_reason(self):
        s, store = self.task_sample()
        out = self.run_task(s, store, "pass2 bad_plan=1")
        self.assertIn("parked", out, out)
        self.assertIn("could not write a plan that passes the checks", out["parked"])
        self.assertIn("must end with a question mark", out["parked"])
        invalid = [e for e in s.log("task-demo") if e["event"] == "plan_invalid"]
        self.assertEqual(len(invalid), 2)

    def test_the_stop_rule_counts_only_the_real_rejections(self):
        # Two real rejections are under the limit, however many times next replays them.
        s, store = self.task_sample(checks=self.style_check())
        count = Path(s.root, "written")
        out = self.run_task(s, store, f"pass2 tw_bad=2 tw_count={count}")
        self.assertTrue(out.get("done"), out)
        events = [e["event"] for e in s.log("demo-1")]
        self.assertEqual(events.count("tests_rejected"), 2)
        self.assertNotIn("stopped", events)

    def assert_tests_written_again_with_the_hint(self, s, out, hint):
        self.assertTrue(out.get("done"), out)
        log = s.log("demo-1")
        events = [e["event"] for e in log]
        self.assertEqual(events.count("tests_rejected"), 3)
        decided = [e for e in log if e["event"] == "advisor_decided"]
        self.assertEqual([(e["decision"], e["hint"]) for e in decided], [("retry", hint)])
        self.assertNotIn("parked", events)
        self.assertLess(events.index("advisor_decided"), events.index("frozen"))

    def test_a_person_s_retry_before_the_lock_writes_the_tests_again(self):
        s, store = self.task_sample(checks=self.style_check())
        asked = []

        def decide(question):
            asked.append(question)
            return "retry fix the style"
        out = self.run_task(s, store, "pass2 tw_bad=hint", decide=decide)
        self.assertEqual(len(asked), 1)
        self.assertIn("retry", asked[0]["options"])
        self.assertIn("the tests were rejected 3 times", asked[0]["body"])
        self.assert_tests_written_again_with_the_hint(s, out, "fix the style")

    def test_the_ai_advisor_s_retry_before_the_lock_writes_the_tests_again(self):
        s, store = self.task_sample(checks=self.style_check(),
                                    advisor=(f"{sys.executable} {FAKE_ROLE} ok",))
        out = self.run_task(s, store, "pass2 tw_bad=hint decision=retry")
        self.assert_tests_written_again_with_the_hint(s, out, "try harder")

    def test_a_parked_ticket_is_named_not_an_answered_question(self):
        s, store = self.task_sample(checks=self.style_check())
        out = self.run_task(s, store, "pass2 tw_bad=hint", decide=lambda q: "park")
        self.assertIn("demo-1: a person chose park", out["parked"])
        self.assertNotIn("question 1", out["parked"])

    def test_a_run_that_no_longer_reaches_its_saved_answer_still_logs_what_is_new(self):
        s, store = self.task_sample()
        _, out = self.next(s, store)                       # the analyst
        _, out = self.answer(s, store, out, fake_answer(out))
        self.assertEqual(out["agent"], "tollgate:builder")  # the plan
        # The request changes, so the plan's question has a new key: the step it paused
        # at is never reached again.
        Path(s.project, ".tollgate/requests/demo.md").write_text("Add shout and whisper, now.\n")
        out = self.run_task(s, store)
        self.assertTrue(out.get("done"), out)
        events = [e["event"] for e in s.log("task-demo")]
        self.assertEqual(events.count("analysis_written"), 1)
        self.assertEqual(events.count("plan_written"), 1)
        self.assertEqual(events.count("plan_reviewed"), 3)
        self.assertIn("plan_locked", events)

    def test_a_question_asked_while_a_stale_replay_holds_the_log_is_still_logged(self):
        # A run that paused at a role leaves its key in the replay note. If the next run
        # never reaches that role, for example after an update of Tollgate changed the
        # path, and stops at a question to a person, the question must still be logged:
        # the run after it reads the answer from the log, and must not ask again.
        s, store = self.task_sample()
        code, out = self.next(s, store)
        for _ in range(20):
            self.assertEqual(code, 0, out)
            if "# Lens: readiness" in out.get("prompt", ""):
                break
            code, out = self.answer(s, store, out, fake_answer(out))
        else:
            self.fail(f"the plan review did not reach the readiness lens: {out}")
        session.save_answer(str(s.project), session.read_step(str(s.project)),
                            fake_answer(out))
        session.replay_path(str(s.project), "task-demo").write_text("a step no run reaches\n")
        asked = []

        def decide(question):
            asked.append(question["n"])
            return "approve"
        code, out = self.next(s, store)
        self.assertIn("question", out, out)
        asked.append(out["question"]["n"])
        store.answer(out["question"]["n"], "tollgate decide approve")
        out = self.run_task(s, store, decide=decide)
        self.assertTrue(out.get("done"), out)
        events = s.log("task-demo")
        self.assertEqual(len(asked), 1, "the plan question was asked again")
        waiting = [e for e in events if e["event"] == "waiting_for_advisor"]
        self.assertEqual([e["options"] for e in waiting].count(["approve", "send_back"]), 1)
        self.assertEqual(len({e["question"] for e in waiting}), len(waiting))
        self.assertEqual([e["event"] for e in events].count("plan_reviewed"), 3)

    def test_a_plan_a_person_sends_back_is_written_again_and_reviewed_before_a_new_decision(self):
        # The person sends the plan back with a hint. The run pauses at the builder, so
        # the next run starts again from the log: it must write the plan with the hint
        # and review it, not ask for a decision on a plan that nobody reviewed.
        s, store = self.task_sample()
        hints, asked = [], []
        code, out = self.next(s, store)
        for _ in range(150):
            self.assertEqual(code, 0, out)
            if "question" in out:
                q = out["question"]
                asked.append(q["n"])
                if "send_back" in q["options"] and len(asked) == 1:
                    store.answer(q["n"], "tollgate decide send_back make it shorter")
                else:
                    pick = "approve" if "approve" in q["options"] else "continue"
                    store.answer(q["n"], f"tollgate decide {pick}")
                code, out = self.next(s, store)
                continue
            if out.get("done") or "parked" in out:
                break
            if out["agent"] == "tollgate:builder" and "make it shorter" in out["prompt"]:
                hints.append(out["key"])
            code, out = self.answer(s, store, out, fake_answer(out))
        self.assertTrue(out.get("done"), out)
        self.assertEqual(len(hints), 1, "the builder got the hint once")
        events = [e["event"] for e in s.log("task-demo")]
        self.assertEqual(events.count("plan_reviewed"), 6)
        self.assertEqual(events.count("plan_review_done"), 2)
        sent = events.index("plan_sent_back")
        self.assertLess(sent, len(events) - events[::-1].index("plan_reviewed"))
        self.assertEqual(len(asked), 3)  # the plan twice, then the task checkpoint

    def test_tests_the_freeze_refused_are_reviewed_again_once_rewritten(self):
        from tests.fake_role import inputs
        s, store = self.task_sample()
        reviewed = []
        code, out = self.next(s, store)
        for _ in range(150):
            self.assertEqual(code, 0, out)
            if "question" in out:
                store.answer(out["question"]["n"], "tollgate decide " + (
                    "approve" if "approve" in out["question"]["options"] else "continue"))
                code, out = self.next(s, store)
                continue
            if out.get("done") or "parked" in out:
                break
            if "# Lens: test fit" in out["prompt"]:
                files = {x["file"] for x in inputs(out["prompt"])["tests"]}
                reviewed.append((out["key"], files))
            code, out = self.answer(s, store, out, fake_answer(out, "pass2 tw_freeze=1"))
        self.assertTrue(out.get("done"), out)
        events = [e["event"] for e in s.log("demo-1")]
        self.assertEqual(events[:8], [
            "started", "tests_written", "tests_reviewed", "tests_rejected", "tests_written",
            "tests_reviewed", "frozen", "try_started"])
        # demo-1's tests are rewritten after the freeze refused them, with the same names,
        # so they get a second review; demo-2's tests get one.
        first = [k for k, files in reviewed if "tests/test_t1.py" in files]
        self.assertEqual(len(first), 2, "the new tests get their own review")
        self.assertEqual(len(set(first)), 2)
        self.assertEqual(len(reviewed), 3)
        self.assertEqual(events.count("frozen"), 1)

    def test_logging_goes_on_once_a_role_has_no_saved_answer(self):
        s, store = self.task_sample()
        session.replay_path(str(s.project), "task-demo").write_text("a step no run reaches\n")
        _, out = self.next(s, store)
        self.assertEqual(session.replay_path(str(s.project), "task-demo").read_text().strip(), out["key"])
        self.answer(s, store, out, fake_answer(out))
        events = [e["event"] for e in s.log("task-demo")]
        self.assertEqual(events.count("analysis_written"), 1)

    def test_the_fix_round_s_plan_is_reviewed_and_locked_before_its_ticket_is_built(self):
        s, store = self.task_sample()
        out = self.run_task(s, store, "pass2 taskmajor=1")
        self.assertTrue(out.get("done"), out)
        events = [(e["event"], e.get("ticket")) for e in s.log("task-demo")]
        names = [e for e, _ in events]
        self.assertEqual(names.count("fix_plan_written"), 1)
        fixed = names.index("fix_plan_written")
        built = events.index(("ticket_done", "demo-3"))
        self.assertLess(fixed, names.index("fix_round"))
        self.assertLess(names.index("fix_round"), built)
        self.assertEqual(names[fixed + 1:built].count("plan_reviewed"), 3)
        self.assertNotIn("plan_reviewed", names[built:])
        rounds = [e.get("round") for e in s.log("task-demo") if e["event"] == "task_reviewed"]
        self.assertEqual(rounds, [0, 1])

class ReadyTest(Case):
    def test_a_task_needs_settings_on_main_that_can_be_read(self):
        s = self.sample()
        self.assertEqual(session.cannot_pass(str(s.project)), "")
        Path(s.project, ".tollgate/settings.json").write_text('{"nonsense": 1}')
        sh(s.project, "git", "commit", "-q", "-am", "bad settings")
        self.assertIn("/tollgate:init", session.cannot_pass(str(s.project)))
        from driver.task import Task
        with self.assertRaisesRegex(session.NotReady, "cannot be read"):
            Task(str(s.project), "demo", "Add shout.", FakeStore(s.project)).run()


ROADMAP = """---
id: road
checklist: T-1
---
## Request
Add shout and whisper.
## Base
{base}
## Skeleton
## Features
{features}
## Not yet specified
Other languages.
"""
BASE_PASSES = [{"name": "builds", "passed": True, "why": ""}]
BASE_FAILS = [{"name": "builds", "passed": False, "why": "no command"}]


class RoadmapStepTest(Case):
    """driver.step next --roadmap: a roadmap runs in session mode, one step at a time."""

    step = StepTest.step
    answer = StepTest.answer

    def roadmap_sample(self, base="- demo: Add shout and whisper.", features=""):
        s = self.sample()   # its ticket T-1 is the roadmap's checklist
        Path(s.project, ".tollgate/roadmaps").mkdir(parents=True)
        Path(s.project, ".tollgate/roadmaps/road.md").write_text(
            ROADMAP.format(base=base, features=features))
        return s, FakeStore(s.project)

    def next_roadmap(self, s, store):
        return self.step(store, "next", "--project", str(s.project), "--roadmap", "road")

    def run_roadmap(self, s, store, rounds=200):
        """next and answer until the roadmap is done or parked. Return (out, agents)."""
        code, out = self.next_roadmap(s, store)
        agents = []
        for _ in range(rounds):
            self.assertEqual(code, 0, out)
            if "question" in out:
                options = out["question"]["options"]
                pick = "approve" if "approve" in options else "continue"
                self.assertIn(pick, options)
                store.answer(out["question"]["n"], f"tollgate decide {pick}")
                code, out = self.next_roadmap(s, store)
                continue
            if out.get("done") or "parked" in out:
                return out, agents
            self.assertEqual(session.read_step(str(s.project))["roadmap"], "road")
            agents.append(out["agent"])
            code, out = self.answer(s, store, out, fake_answer(out))
        self.fail(f"the roadmap did not end: {out}")

    def roadmap_log(self, s):
        return s.log("roadmap-road")

    def test_a_whole_roadmap_runs_through_next_and_answer(self):
        s, store = self.roadmap_sample()
        with mock.patch("setup.base_suite.run", return_value=BASE_PASSES):
            out, agents = self.run_roadmap(s, store)
        self.assertTrue(out.get("done"), out)
        self.assertEqual((out["roadmap"], out["tasks"]), ("road", ["demo"]))
        # The checklist's tests come first, then the task, then the checklist's build.
        self.assertEqual(agents[0], "tollgate:test_writer")
        self.assertIn("tollgate:analyst", agents)
        events = self.roadmap_log(s)
        self.assertEqual(events[0]["event"], "checklist_frozen")
        # A task that waits for a person stops the roadmap with 3, as in background mode;
        # the next run goes on with it.
        self.assertEqual([(e["event"], e.get("code")) for e in events[-3:]],
                         [("task_done", 0), ("base_passed", None), ("roadmap_done", None)])
        self.assertNotIn(5, [e.get("code") for e in events])
        self.assertIsNone(session.read_step(str(s.project)))
        self.assertTrue(Path(out["reports"][0]).exists())

    def test_the_roadmap_goes_on_to_the_next_task_when_one_is_done(self):
        s, store = self.roadmap_sample(features="- more: Add shout and whisper again.")
        with mock.patch("setup.base_suite.run", return_value=BASE_PASSES):
            out, _ = self.run_roadmap(s, store, rounds=400)
        self.assertTrue(out.get("done"), out)
        self.assertEqual(out["tasks"], ["demo", "more"])
        self.assertEqual([e.get("task") for e in self.roadmap_log(s)
                          if e["event"] == "task_done" and e["code"] == 0], ["demo", "more"])

    def test_a_base_suite_that_fails_parks_the_roadmap_before_the_next_stage(self):
        s, store = self.roadmap_sample(base="", features="- demo: Add shout and whisper.")
        with mock.patch("setup.base_suite.run", return_value=BASE_FAILS):
            out, agents = self.run_roadmap(s, store)
        self.assertIn("the base is not ready: builds (no command)", out["parked"])
        self.assertNotIn("tollgate:analyst", agents)   # the task did not start
        self.assertEqual([e["event"] for e in self.roadmap_log(s)],
                         ["checklist_frozen", "roadmap_waiting"])

    def test_next_refuses_a_roadmap_that_is_not_there(self):
        s, store = self.roadmap_sample()
        code, out = self.step(store, "next", "--project", str(s.project), "--roadmap", "nope")
        self.assertEqual(code, 1)
        self.assertIn(".tollgate/roadmaps/nope.md", out["error"])

    def test_a_pause_logs_nothing_in_the_roadmap(self):
        s, store = self.roadmap_sample()
        code, out = self.next_roadmap(s, store)
        self.assertEqual((code, out["agent"]), (0, "tollgate:test_writer"))
        self.assertEqual(self.roadmap_log(s), [])

    def test_the_background_roadmap_still_runs_each_role_as_its_own_process(self):
        from driver import roadmap
        s, store = self.roadmap_sample()
        runners = []

        def fake(kind):
            def make(*args, runner=None, **kwargs):
                runners.append((kind, runner))
                return mock.Mock(run=mock.Mock(return_value=0))
            return make

        with mock.patch.object(roadmap, "Driver", fake("checklist")), \
                mock.patch.object(roadmap, "Task", fake("task")), \
                mock.patch("setup.base_suite.run", return_value=BASE_PASSES):
            self.assertEqual(roadmap.main(["--project", str(s.project), "--roadmap", "road"],
                                          store=store), 0)
        self.assertEqual(runners, [("checklist", "background"), ("task", "background"),
                                   ("checklist", "background")])


if __name__ == "__main__":
    unittest.main()

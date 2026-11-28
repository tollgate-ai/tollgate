"""Fakes for the driver's tests: a sample project on this machine, and a fake store.

The fake store keeps the CONTRACT formats in the state folder, so the real gate
reads them. It runs no isolation of its own.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gate import checks, junit, prepare, state, tickets
from gate import gate as real_gate
from gate import manifest as mf
from gate import review as real_review
from gate import settings as st
from gate.gitio import fingerprint, show_bytes
from gate.store import StoreError
from tests.fakes import RUN_TESTS, TICKET
from tests.helpers import write_checks

FAKE_ROLE = str(Path(__file__).resolve().parent / "fake_role.py")


def sh(cwd, *args) -> str:
    return subprocess.run(list(args), cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def settings(builder="pass2", reviewer="ok", advisor=("person",), **extra):
    py = sys.executable
    role = f"{py} {FAKE_ROLE}"
    stop = {"attempts": 3, "attempt_minutes": 1, "ticket_usd": 10, "task_usd": 60,
            **extra.pop("stop", {})}
    return {
        "checks": {"tests": {"run": f"{py} run_tests.py", "report": "report.xml"}},
        "harness": ["run_tests.py"],
        "require_plan": False,
        "runner": "background",
        "isolation": "sandbox",
        "ai": {"builder": f"{role} {builder}", "reviewer": f"{role} {reviewer}",
               "advisor": list(advisor)},
        "stop": stop,
        **extra,
    }


class Sample:
    """A project with a main branch on this machine, its own state folder and key
    folder, and a git identity. Use it as a context manager."""

    def __init__(self, ticket=True, **settings_args):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        self.project = root / "project"
        gitconfig = root / "gitconfig"
        gitconfig.write_text("[user]\n\temail = t@example.com\n\tname = Test\n"
                             "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n")
        self.env = mock.patch.dict(os.environ, {
            "TOLLGATE_STATE": str(root / "state"), "TOLLGATE_KEYS": str(root / "keys"),
            "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1"})
        self.env.start()
        p = self.project
        p.mkdir()
        sh(p, "git", "init", "-q", "-b", "main")
        (p / "app.py").write_text('def greet(name):\n    return f"hello {name}"\n')
        (p / "run_tests.py").write_text(RUN_TESTS)
        (p / "tests").mkdir()
        (p / "tests/__init__.py").write_text("")
        (p / ".gitignore").write_text("report.xml\n__pycache__/\n")
        (p / ".tollgate").mkdir()
        self.write_settings(**settings_args)
        sh(p, "git", "add", "-A")
        sh(p, "git", "commit", "-q", "-m", "sample")
        if ticket:
            (p / ".tollgate/tickets/T-1").mkdir(parents=True)
            (p / ".tollgate/tickets/T-1/ticket.md").write_text(TICKET)

    def write_settings(self, **settings_args):
        write_checks(self.project, settings(**settings_args))

    def commit_settings(self, **settings_args):
        self.write_settings(**settings_args)
        sh(self.project, "git", "commit", "-q", "-am", "settings")

    def log(self, name="T-1") -> list:
        from driver.log import read
        return read(str(state.path(str(self.project), "logs", f"{name}.jsonl")))

    def events(self, name="T-1") -> list:
        return [e["event"] for e in self.log(name)]

    def close(self):
        self.env.stop()
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeStore:
    """The store's interface, with simple bodies. It reads the project's branches
    tg/<ticket> and tg-plan/<task>, as the real store does."""

    def __init__(self, project):
        self.project = str(project)
        self.ready_marks = []
        self.merged = []
        self.freezes = 0

    def _settings(self):
        return st.load_from_ref(self.project, "main")

    def _write(self, path, record):
        state.write_record(path, record)

    # Locks.

    def freeze(self, ticket: str, start: str | None = None) -> str:
        commit = sh(self.project, "git", "rev-parse", f"refs/heads/tg/{ticket}")
        base = start or sh(self.project, "git", "merge-base", "main", commit)
        self.starts = {**getattr(self, "starts", {}), ticket: start}
        settings = self._settings()
        with tempfile.TemporaryDirectory() as d:
            sh(d, "git", "clone", "-q", self.project, ".")
            sh(d, "git", "checkout", "-q", commit)
            prepare.restore(d, "origin/main")
            results = checks.run(settings, d, ["tests"])
        data = show_bytes(self.project, commit, mf.ticket_path(ticket))
        if data is None:
            raise StoreError(f"{mf.ticket_path(ticket)} is not in {commit}")
        tk = tickets.parse(data.decode())
        found = {i: junit.TestResult(i, r["status"], r.get("file", ""))
                 for i, r in results.tests.items()}
        try:
            built = mf.build(self.project, commit, tk, found, base)
        except mf.FreezeError as err:
            raise StoreError(f"the ticket cannot be frozen: {err}") from None
        files, tests, keeps = built["files"], built["tests"], built["keeps_behavior"]
        path = state.path(self.project, "locks", f"{ticket}.json")
        number = json.loads(path.read_text())["number"] + 1 if path.exists() else 1
        self._write(path, {"ticket": ticket, "base_commit": base, "commit": commit,
                          "manifest": {"files": files, "tests": tests, "keeps_behavior": keeps,
                                       "items": built["items"]},
                          "number": number, "at": "2026-11-22T00:00:00Z"})
        self.freezes += 1
        return str(path)

    def lock_plan(self, task: str, amend: str = "") -> str:
        self.amended = [*getattr(self, "amended", []), amend] if amend else getattr(
            self, "amended", [])
        commit = sh(self.project, "git", "rev-parse", f"refs/heads/tg-plan/{task}")
        names = sh(self.project, "git", "diff", "--name-only", f"main...{commit}").split()
        files = {n: fingerprint(show_bytes(self.project, commit, n)) for n in names}
        path = state.path(self.project, "plans", f"{task}.json")
        self._write(path, {"task": task, "commit": commit, "files": files,
                           "at": "2026-11-22T00:00:00Z"})
        return str(path)

    # Questions.

    def _q(self, n, closed=False):
        return state.path(self.project, "questions", f"{n}.closed.json" if closed else f"{n}.json")

    def ask(self, title, body, options=None) -> int:
        n = len(list(state.path(self.project, "questions").glob("*.json"))) + 1
        self._q(n).write_bytes(state.dumps({"n": n, "title": title, "body": body,
                                            "options": list(options or []), "answer": None}))
        return n

    def question(self, n):
        for closed in (False, True):
            if self._q(n, closed).exists():
                return json.loads(self._q(n, closed).read_text())
        return None

    def answer(self, n, body):
        q = self.question(n)
        decision, _, hint = body.strip().removeprefix("tollgate decide ").partition(" ")
        q["answer"] = {"decision": decision, "hint": hint, "at": "2026-11-22T00:00:00Z"}
        self._q(n).write_bytes(state.dumps(q))

    def open_questions(self):
        found = {}
        for p in state.path(self.project, "questions").glob("*.json"):
            if p.stem.isdigit():
                q = json.loads(p.read_text())
                if q["answer"] is None:
                    found[int(p.stem)] = q
        return found

    def close_question(self, n):
        if not self._q(n).exists():
            raise StoreError(f"question {n} is not open")
        self._q(n).replace(self._q(n, True))

    # Ready changes, passes, merges.

    def ready(self, branch, ticket):
        commit = sh(self.project, "git", "rev-parse", f"refs/heads/{branch}")
        if not self.passed(commit):
            raise StoreError(f"{branch} has no pass record")
        self.ready_marks.append((branch, ticket, commit))

    def record_pass(self, commit, record):
        self._write(state.path(self.project, "passes", f"{commit}.json"), record)

    def passed(self, commit):
        try:
            return state.read_record(state.path(self.project, "passes", f"{commit}.json"))
        except state.RecordError:
            return None

    def approvals(self, commit):
        return []

    def merge(self, commit):
        found = self.passed(commit)
        if not found or found["tier"] >= 3:
            raise StoreError(f"{commit} may not merge")
        self.merged.append(commit)
        return "merge-" + commit[:12]


def gate_api():
    return real_gate


def review_api():
    return real_review


class Refusing:
    """A gate that adds one finding the builder cannot fix."""

    def __init__(self, inner):
        self.inner = inner
        self.GateError = inner.GateError

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def evaluate(self, facts):
        return self.inner.evaluate(facts) + [{"check": "lock", "rule": 1, "file": "",
                                              "message": "the lock does not verify",
                                              "fixable": False}]


def driver(sample: Sample, ticket="T-1", gate=None, **kw):
    from driver.driver import Driver
    store = kw.pop("store", None) or FakeStore(sample.project)
    return Driver(str(sample.project), ticket, store, gate_api=gate or gate_api(),
                  review_api=review_api(), **kw), store


__all__ = ["FAKE_ROLE", "FakeStore", "Sample", "SimpleNamespace", "driver", "settings"]

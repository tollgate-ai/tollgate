"""The store: the part of Tollgate that keeps its state on your machine.

The driver, the gate, and the skills reach the state folder only through this
class. Every method names one step. The project is a git repository on your
machine: the store reads its `main` branch and the branches `tg/<ticket>` and
`tg-plan/<task>`.

The records of the store (locks, plans, passes, approvals) are plain files in the
state folder, which no role may write.

UNFINISHED: `freeze` runs the tests on this machine, in a temporary clone. Running
them in the builder's isolation is the driver stream's work.
"""

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from gate import checks, plantag, prepare, state, tickets
from gate import freeze as fz
from gate import manifest as mf
from gate import plan as pl
from gate import settings as st
from gate.gitio import GitError, commit_of, fingerprint, git, show_bytes, worktree_of

MAIN = "main"
PASS_KEYS = {"commit", "ticket", "tier", "base_commit", "lock_sha256", "checks", "score", "at"}
APPROVAL_KEYS = {"commit", "ticket", "approver", "at"}
DECIDE_PREFIX = "tollgate decide "


class StoreError(RuntimeError):
    """A step of the store failed. The message says why."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, project: str):
        self.project = str(project)
        self.dir = state.state_dir(self.project)

    # Helpers.

    def _main(self) -> str:
        commit = commit_of(self.project, f"refs/heads/{MAIN}")
        if not commit:
            raise StoreError(f"the project has no branch {MAIN}")
        return commit

    def _settings(self) -> dict:
        try:
            return st.load_from_ref(self.project, f"refs/heads/{MAIN}")
        except st.SettingsError as err:
            raise StoreError(f"the settings on {MAIN} cannot be read: {err}") from None

    def _read(self, path: Path, keys: set, commit: str) -> dict | None:
        """The record at path, or None when it is missing, has other keys, or names
        another commit."""
        try:
            found = state.read_record(path)
        except state.RecordError:
            return None
        if set(found) != keys or found.get("commit") != commit:
            return None
        return found

    @staticmethod
    def _commit_name(commit: str) -> str:
        if not commit or not all(c in "0123456789abcdef" for c in commit) or len(commit) < 40:
            raise StoreError(f"not a full commit id: {commit!r}")
        return commit

    # Locks.

    def freeze(self, ticket: str, start: str | None = None) -> str:
        """Run the ticket's tests in a clean copy with main's harness; none may pass.
        Write locks/<ticket>.json. Return the lock's path.

        start is the commit the ticket started from, when not main: in a task that
        holds its merges, a ticket starts from the ready change before it. It must
        have a pass record and be in the ticket's history; the lock records
        it as the base, so the gate counts only this ticket's own change."""
        try:
            fz.check_id(ticket)
            number = fz.next_number(self.project, ticket)
            if number > fz.MAX_FREEZES:
                raise mf.FreezeError(f"ticket {ticket} already has {fz.MAX_FREEZES} freezes")
            commit = commit_of(self.project, f"refs/heads/tg/{ticket}")
            if not commit:
                raise mf.FreezeError(f"the project has no branch tg/{ticket}")
            base = self._main()
            settings = self._settings()
            ticket_file = self._approved_ticket(ticket, commit, base)
            started = self._start(start, commit) if start else base
            with tempfile.TemporaryDirectory() as d:
                results = self._run_tests(d, commit, base, settings, ticket_file)
                failed = any(c.name == "tests" and not c.passed for c in results.checks)
                output = "\n".join(c.output for c in results.checks if c.name == "tests")
                try:
                    try:
                        lock = fz.record(d, ticket, commit, started, settings, results.tests,
                                         number, failed, output)
                    except mf.KeptNotRunError:
                        # A test that keeps behavior shares a compile unit with the new
                        # tests, which do not build yet. It must pass on the code before
                        # the new tests, so the tests run once more at the start.
                        with tempfile.TemporaryDirectory() as s:
                            then = self._run_tests(s, started, base, settings)
                        lock = fz.record(d, ticket, commit, started, settings, results.tests,
                                         number, failed, output, then.tests)
                except mf.FreezeError as err:
                    said = [c.output for c in results.checks if c.output]
                    if results.tests or not said:
                        raise
                    # With no test in the report, the check's own output says why.
                    raise mf.FreezeError(f"{err}. The test check said:\n{said[0]}") from None
        except (mf.FreezeError, pl.PlanError, st.SettingsError, GitError,
                ValueError) as err:
            raise StoreError(f"the ticket cannot be frozen: {err}") from None
        lock["at"] = _now()
        path = fz.lock_path(self.project, ticket)
        if number > 1:
            old = fz.replaced_path(self.project, ticket)
            path.replace(old)
        state.write_record(path, lock)
        return str(path)

    def _run_tests(self, folder: str, commit: str, base: str, settings: dict,
                   ticket_file: tuple = (None, None)) -> checks.Results:
        """Clone the project into folder at commit, put back main's harness and the
        approved ticket file, run the setup commands, and run the test check."""
        git(folder, "clone", "-q", "--no-checkout", self.project, ".")
        git(folder, "checkout", "-q", "--detach", commit)
        prepare.restore(folder, base, *ticket_file)
        for command in settings["setup"]:
            done = subprocess.run(command, shell=True, cwd=folder, capture_output=True,
                                  text=True, check=False, env=checks.project_env())
            if done.returncode:
                raise mf.FreezeError(f"the setup command failed: {command}")
        # UNFINISHED: the tests run here, not in the builder's isolation.
        return checks.run(settings, folder, ["tests"])

    def _start(self, start: str, commit: str) -> str:
        """The full commit a ticket started from. Raise FreezeError unless it passed the
        gate and the ticket's commit builds on it."""
        full = commit_of(self.project, start)
        if not full:
            raise mf.FreezeError(f"the project has no commit {start!r}")
        if full != self._main() and not self.passed(full):
            raise mf.FreezeError(f"the start {full[:12]} has no pass record")
        if subprocess.run(["git", "-C", self.project, "merge-base", "--is-ancestor", full, commit],
                          capture_output=True, check=False).returncode:
            raise mf.FreezeError(f"the ticket's commit does not build on {full[:12]}")
        return full

    def _older_record(self, full: str) -> bool:
        """True when the commit has a pass record of an older Tollgate: one
        with every key but the start."""
        found = self._read(state.path(self.project, "passes", f"{full}.json"),
                           PASS_KEYS - {"base_commit"}, full)
        return bool(found and found.get("ticket"))

    def _check_start(self, found: dict, full: str) -> None:
        """Refuse a ticket unless its lock is there, names the same start as its pass
        record, and that start is on main. A ticket in a task builds on the ready change
        before it, and its tier counts only its own files, so it merges only after that
        change, with that change's own tier and approval. Anything else fails closed."""
        ticket = found["ticket"]
        path = fz.lock_path(self.project, ticket)
        if not path.exists():
            raise StoreError(f"ticket {ticket} has no lock, so its start cannot be checked")
        try:
            lock = state.read_record(path)
        except state.RecordError as err:
            raise StoreError(f"the lock of {ticket} cannot be read: {err}") from None
        start = found.get("base_commit")
        if not start or lock.get("base_commit") != start:
            raise StoreError(f"the pass record of {ticket} names no start, or not its lock's "
                             "start")
        if subprocess.run(["git", "-C", self.project, "merge-base", "--is-ancestor", start, MAIN],
                          capture_output=True, check=False).returncode:
            raise StoreError(f"{full[:12]} builds on {start[:12]}, which is not on {MAIN}; "
                             "merge the ticket before it first")

    def _approved_ticket(self, ticket: str, commit: str, base: str) -> tuple:
        """Return (commit, {"files": {path: fingerprint}}) of the approved ticket file, for
        prepare.restore. The approved file is the one in the plan lock, else main's. Raise
        FreezeError when the ticket's commit holds another text."""
        path = mf.ticket_path(ticket)
        committed = show_bytes(self.project, commit, path)
        if committed is None:
            raise mf.FreezeError(f"{path} is not in commit {commit}")
        plan_id = tickets.parse(committed.decode()).plan
        source, approved = None, None
        if plan_id and pl.ID.match(plan_id) and state.path(self.project, "plans",
                                                          f"{plan_id}.json").exists():
            locked = state.read_record(state.path(self.project, "plans", f"{plan_id}.json"))
            if path in locked["files"]:
                source, approved = locked["commit"], locked["files"][path]
        if approved is None:
            on_main = show_bytes(self.project, base, path)
            if on_main is not None:
                source, approved = base, fingerprint(on_main)
        if approved is not None and fingerprint(committed) != approved:
            raise mf.FreezeError(f"{path} differs from the approved ticket")
        return (source, {"files": {path: approved}}) if source else (None, None)

    def lock_plan(self, task: str, amend: str = "") -> str:
        """Lock the approved plan of a task into plans/<task>.json. Return its path.

        A plan already locked may get a new version, from a new commit: the plan of
        the fix round after the review of the whole task. The new version must keep every
        ticket of the old one, unchanged; it may add tickets. The old record moves to
        plans/replaced/<task>.<version>.json.

        amend names the one locked ticket that the new version may change, after the
        advisor or a person decided `amend` on it. The ticket must stay in the plan, and
        every other ticket must stay unchanged. The record names it under `amended`."""
        if not pl.ID.match(task or ""):
            raise StoreError(f"not a valid task id: {task!r}")
        if amend and not fz.TICKET_ID.match(amend):
            raise StoreError(f"not a valid ticket id: {amend!r}")
        path = state.path(self.project, "plans", f"{task}.json")
        commit = commit_of(self.project, f"refs/heads/tg-plan/{task}")
        if not commit:
            raise StoreError(f"the project has no branch tg-plan/{task}")
        old = None
        if path.exists():
            try:
                old = state.read_record(path)
            except state.RecordError as err:
                raise StoreError(f"the plan lock of task {task} cannot be read: {err}") from None
            if old["commit"] == commit:
                raise StoreError(f"the plan of task {task} is already locked at this commit")
        elif amend:
            raise StoreError(f"the plan of task {task} is not locked, so it has no ticket to amend")
        try:
            found = plantag.record(self.project, task, commit)
        except pl.PlanError as err:
            raise StoreError(f"the plan cannot be locked: {err}") from None
        stray = self._stray_tickets(task, commit)
        if stray:
            raise StoreError(f"the plan cannot be locked: the plan commit holds ticket "
                             f"{', '.join(stray)}, which names plan {task} but is not in its "
                             "header. Drop it from the plan commit, or list it in the header")
        if old is not None:
            plan_file = pl.plan_path(task)
            for name, digest in old["files"].items():
                if name == plan_file or not name.endswith("/ticket.md"):
                    continue
                ticket = Path(name).parent.name
                if ticket == amend and name in found["files"]:
                    continue
                if found["files"].get(name) != digest:
                    raise StoreError(f"the new plan changes ticket {ticket}, which the old "
                                     "plan locked; a new version may only add tickets")
            if amend and mf.ticket_path(amend) not in old["files"]:
                raise StoreError(f"ticket {amend} is not in the locked plan of task {task}")
            if amend:
                found["amended"] = amend
            found["version"] = old.get("version", 1) + 1
            kept = state.path(self.project, "plans", "replaced", f"{task}.{old.get('version', 1)}.json")
            kept.parent.mkdir(parents=True, exist_ok=True)
            path.replace(kept)
        found["at"] = _now()
        state.write_record(path, found)
        return str(path)

    def _stray_tickets(self, task: str, commit: str) -> list:
        """The tickets of a plan commit that name the plan, are not in its header, and
        differ from main or are not on it."""
        plan = pl.parse(show_bytes(self.project, commit, pl.plan_path(task)).decode())
        paths = git(self.project, "ls-tree", "-r", "--name-only", commit,
                    "--", ".tollgate/tickets").splitlines()

        def read(path):
            data = show_bytes(self.project, commit, path)
            if data is None or data == show_bytes(self.project, MAIN, path):
                return None
            return data.decode(errors="replace")
        return pl.stray_tickets(plan, read, paths)

    # Questions for you.

    def _question_path(self, n: int, closed: bool = False) -> Path:
        if not isinstance(n, int) or n < 1:
            raise StoreError(f"not a question number: {n!r}")
        return state.path(self.project, "questions", f"{n}.closed.json" if closed else f"{n}.json")

    def ask(self, title: str, body: str, options: list | None = None) -> int:
        """Write questions/<n>.json. Return n."""
        folder = state.path(self.project, "questions")
        numbers = [int(p.name.split(".")[0]) for p in folder.glob("*.json")
                   if p.name.split(".")[0].isdigit()]
        n = max(numbers, default=0) + 1
        record = {"n": n, "title": title, "body": body, "options": list(options or []),
                  "answer": None}
        while True:
            try:
                fd = os.open(self._question_path(n), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                n += 1
                record["n"] = n
        with os.fdopen(fd, "wb") as f:
            f.write(state.dumps(record))
        return n

    def question(self, n: int) -> dict | None:
        """Question n, open or closed, or None when there is none."""
        for closed in (False, True):
            path = self._question_path(n, closed)
            if path.exists():
                return json.loads(path.read_text())
        return None

    def answer(self, n: int, body: str) -> None:
        """Record your answer to question n. The first line is the decision, then a hint:
        `approve keep the retries short`. A leading `tollgate decide` is allowed."""
        path = self._question_path(n)
        if not path.exists():
            raise StoreError(f"question {n} is not open")
        question = json.loads(path.read_text())
        first = (body or "").strip().splitlines()[:1]
        line = first[0].strip() if first else ""
        line = line[len(DECIDE_PREFIX):].strip() if line.startswith(DECIDE_PREFIX) else line
        decision, _, hint = line.partition(" ")
        if not decision:
            raise StoreError("the answer has no decision on its first line")
        if question["options"] and decision not in question["options"]:
            raise StoreError(f"{decision} is not one of: {', '.join(question['options'])}")
        question["answer"] = {"decision": decision, "hint": hint.strip(), "at": _now()}
        path.write_bytes(state.dumps(question))

    def open_questions(self) -> dict:
        """Return {n: question} for each question with no answer."""
        found = {}
        for path in state.path(self.project, "questions").glob("*.json"):
            if path.stem.isdigit():
                question = json.loads(path.read_text())
                if question["answer"] is None:
                    found[int(path.stem)] = question
        return dict(sorted(found.items()))

    def close_question(self, n: int) -> None:
        """Move question n to questions/<n>.closed.json. It keeps its answer."""
        path = self._question_path(n)
        if not path.exists():
            raise StoreError(f"question {n} is not open")
        path.replace(self._question_path(n, closed=True))

    # Ready changes, passes, approvals, merges.

    def _ready_path(self, ticket: str) -> Path:
        fz.check_id(ticket)
        folder = state.path(self.project, "passes", "ready")
        folder.mkdir(exist_ok=True)
        return folder / f"{ticket}.json"

    def ready(self, branch: str, ticket: str) -> None:
        """Mark a branch as a ready change: it passed the gate and waits to merge.
        Refuse when the branch's last commit has no pass record for the ticket."""
        try:
            path = self._ready_path(ticket)
        except mf.FreezeError as err:
            raise StoreError(str(err)) from None
        commit = commit_of(self.project, f"refs/heads/{branch}")
        if not commit:
            raise StoreError(f"the project has no branch {branch}")
        found = self.passed(commit)
        if not found or found["ticket"] != ticket:
            raise StoreError(f"{branch} at {commit[:12]} has no pass record for {ticket}")
        state.write_record(path, {"branch": branch, "ticket": ticket, "commit": commit,
                                  "at": _now()})

    def record_pass(self, commit: str, record: dict) -> None:
        """Write passes/<commit>.json."""
        self._commit_name(commit)
        record = dict(record)
        record.setdefault("at", _now())
        if set(record) != PASS_KEYS:
            raise StoreError(f"a pass record has the keys {', '.join(sorted(PASS_KEYS))}")
        if record["commit"] != commit:
            raise StoreError(f"the record names {record['commit']}, not {commit}")
        if record["tier"] not in (1, 2, 3, 4):
            raise StoreError(f"not a tier: {record['tier']!r}")
        state.write_record(state.path(self.project, "passes", f"{commit}.json"), record)

    def passed(self, commit: str) -> dict | None:
        """The pass record of a commit, or None."""
        self._commit_name(commit)
        return self._read(state.path(self.project, "passes", f"{commit}.json"),
                          PASS_KEYS, commit)

    def approvals(self, commit: str) -> list:
        """The approvals of a commit."""
        self._commit_name(commit)
        found = self._read(state.path(self.project, "approvals", f"{commit}.json"),
                           APPROVAL_KEYS, commit)
        return [found] if found else []

    @staticmethod
    def _clear_untracked(folder: str, commit: str) -> None:
        """Remove each untracked file in the main worktree that the commit adds with the same
        bytes, such as a ticket you wrote before the task started. git would refuse to
        overwrite it. Refuse, and remove nothing, when one of them differs."""
        untracked = git(folder, "ls-files", "-z", "--others", "--exclude-standard").split("\0")
        incoming = set(git(folder, "ls-tree", "-r", "-z", "--name-only", commit).split("\0"))
        same, differ = [], []
        for path in filter(None, untracked):
            if path not in incoming:
                continue
            data = show_bytes(folder, commit, path)
            (same if data == Path(folder, path).read_bytes() else differ).append(path)
        if differ:
            raise StoreError(f"the {MAIN} worktree has untracked files that the change adds "
                             f"with other content: {', '.join(sorted(differ))}")
        for path in same:
            Path(folder, path).unlink()

    def merge(self, commit: str) -> str:
        """Merge a ready change into main with a merge commit (--no-ff). Refuse without
        a pass record, and for tier 3 and 4 without an approval. Return the merge commit.
        One merge runs at a time in a project: two tasks that run at once wait in turn."""
        import fcntl
        with open(state.path(self.project, "run", "merge.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            try:
                return self._merge(commit)
            finally:
                fcntl.flock(held, fcntl.LOCK_UN)

    def _merge(self, commit: str) -> str:
        full = commit_of(self.project, commit or "")
        if not full:
            raise StoreError(f"the project has no commit {commit!r}")
        found = self.passed(full)
        if not found and self._older_record(full):
            raise StoreError(f"the pass record of {full[:12]} was made by an older Tollgate, "
                             "with no start; the change must pass the gate again")
        if not found:
            # A change made by hand has no ticket, so the gate cannot pass it. You
            # vouch for it with an approval instead. An AI ticket always needs a pass.
            mine = [a for a in self.approvals(full) if not a["ticket"]]
            if not mine:
                raise StoreError(f"{full[:12]} has no pass record, and no approval "
                                 "of a change made by hand")
            found = {"ticket": "", "tier": 4, "lock_sha256": ""}
        tier = found["tier"]
        if tier in (3, 4) and not any(a["ticket"] == found["ticket"] for a in self.approvals(full)):
            raise StoreError(f"{full[:12]} is tier {tier}, so it needs your approval")
        if found["ticket"] and found["lock_sha256"] != fz.lock_sha256(self.project, found["ticket"]):
            raise StoreError(f"the pass record of {full[:12]} is not for the current lock of "
                             f"{found['ticket']}")
        if found["ticket"]:
            self._check_start(found, full)
        folder = worktree_of(self.project, MAIN)
        if not folder:
            raise StoreError(f"no worktree of the project has {MAIN} checked out")
        if git(folder, "status", "--porcelain", "--untracked-files=no").strip():
            raise StoreError(f"the {MAIN} worktree has changes; commit or stash them first")
        if subprocess.run(["git", "-C", folder, "merge-base", "--is-ancestor", full, MAIN],
                          capture_output=True, check=False).returncode == 0:
            raise StoreError(f"{full[:12]} is already in {MAIN}")
        self._clear_untracked(folder, full)
        name = found["ticket"] or "change"
        try:
            git(folder, "merge", "-q", "--no-ff", "--no-edit", "-m",
                f"chore({name}): merge {full[:12]}, which passed the gate at tier {tier}", full)
        except GitError as err:
            subprocess.run(["git", "-C", folder, "merge", "--abort"], capture_output=True,
                           check=False)
            raise StoreError(f"the merge failed: {err}") from None
        for path in state.path(self.project, "passes", "ready").glob("*.json"):
            if json.loads(path.read_text()).get("commit") == full:
                path.unlink()
        return git(folder, "rev-parse", "HEAD").strip()

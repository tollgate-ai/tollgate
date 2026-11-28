"""The driver: build one ticket, and stop when a stop rule says so.

The task, or a roadmap, starts it for each ticket. It runs on your machine and
calls no outside service. The steps:

1. The test writer writes the locked tests in the builder's copy, and a reviewer
   checks them through the test fit lens.
2. The driver fetches them from the copy into the project's branch tg/<ticket>,
   and the store freezes them: it writes and signs the lock.
3. Each try starts from the best try so far. The builder builds, the checks run,
   and the gate judges. A reviewer scores the checklist items that have no test.
4. A try that passes gets a pass record. The driver fetches it into the
   project and marks it as a ready change. With auto_merge on, the store merges
   tier 1 and 2. You merge tier 3 and 4.
5. Before each try, driver/stop.py says go on or stop. A stop goes to the advisor.

All run state lives in the state folder: the copy in builders/<ticket>/, the log
in logs/<ticket>.jsonl, the run lock in run/. A driver that starts again reads
the log and repeats no finished step.

Each role goes through one caller, from driver/session.py: in session mode your
own Claude Code session runs it as a subagent, and in background mode the driver
starts it as its own process. The setting `runner` picks one.

Exit codes: 0 the ticket is a ready change or merged, 3 the ticket waits for the
advisor or is parked, 4 another driver holds the ticket, 5 a role waits for your
session (its step file, under run/steps/, says which), 1 an error.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import diagram
from driver import advisor, history, roles, session, stop
from driver.log import Log, read
from gate import checks, prepare, state, tickets
from gate import gate as gatelib
from gate import manifest as mf
from gate import review as reviewlib
from gate import settings as st
from gate.gitio import SAFE, safe_env
from gate.paths import line_exists
from gate.store import Store, StoreError
from gate.tickets import item_of_test

MAX_REJECTIONS = 2
MAX_AI_DECISIONS = 2
MAX_RELOCKS = 1          # the store allows one replacement lock
OPTIONS = ["retry", "stronger", "split", "park"]
AMEND = "amend"          # an option only for a ticket of a plan: the task amends the plan lock
REPLACEMENT = ("The tests are already locked, and this is the one replacement lock. A check "
               "fails on a locked test, or the builder says a locked test is wrong; the "
               "rejected findings say which. Fix the test so that it fails or passes for the "
               "right reason.")
FILE_LINE = re.compile(r"(?:^|[\s(\"'])([\w./-]+\.\w+):\d+")
BASE = "main"


class Busy(RuntimeError):
    pass


class Stop(Exception):
    """A stop rule fired. The reason goes to the advisor."""


class Parked(Exception):
    pass


# The run lock of a ticket or a task.

class TicketLock:
    def __init__(self, path):
        self.path = str(path)

    def acquire(self):
        """The lock appears whole, with its pid, in one step: a link to a file that
        already holds the pid. Only one process at a time may clear a stale lock,
        and it clears it only if it still names the same dead process."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(3):
            if self._link():
                return self
            pid = _pid_in(self.path)
            if _alive(pid):
                raise Busy(f"another driver holds {self.path}")
            with open(f"{self.path}.guard", "a") as guard:
                fcntl.flock(guard, fcntl.LOCK_EX)
                if os.path.exists(self.path) and _pid_in(self.path) == pid:
                    os.unlink(self.path)  # stale: its process is gone
        raise Busy(f"could not take {self.path}")

    def _link(self) -> bool:
        tmp = f"{self.path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        try:
            os.link(tmp, self.path)
            return True
        except FileExistsError:
            return False
        finally:
            os.unlink(tmp)

    def release(self):
        if _pid_in(self.path) == os.getpid():
            os.unlink(self.path)


def run_lock(project: str, name: str) -> TicketLock:
    return TicketLock(state.path(project, "run", f"{name}.lock"))


def _pid_in(path) -> int:
    try:
        return int(Path(path).read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# The score.

def diagram_guide(ticket_text: str) -> str:
    """For a ticket that names a diagram: the diagram skill's rules and the spec
    formats, so the builder can write a spec. Empty for any other ticket."""
    if "diagram" not in ticket_text.lower():
        return ""
    root = Path(__file__).resolve().parent.parent
    skill = (root / "skills" / "diagram" / "SKILL.md").read_text()
    formats, seen = [], set()
    for kind, (module, label) in diagram.KINDS.items():
        if module in seen:
            continue
        seen.add(module)
        # The scene's format is the package's own docstring.
        doc = diagram.__doc__ if kind == "scene" else diagram.module_of(kind).__doc__
        formats.append(f"## The format of {label}\n\n" + (doc or ""))
    return "\n\n".join([
        ("You write the spec in <folder>/src/<name>.json. Tollgate draws the SVG files from "
         "it after your try, so leave the build command and the SVG files to it."),
        skill, *formats])


DIAGRAM_TYPES = tuple(diagram.KINDS)


def _is_spec(path: Path) -> bool:
    """A diagram spec: a JSON object with a diagram type, or a flow's nodes. A file
    that does not parse counts only inside a folder named for diagrams."""
    try:
        spec = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return path.exists() and "diagram" in path.parent.parent.name
    return isinstance(spec, dict) and (spec.get("type") in DIAGRAM_TYPES or "nodes" in spec)


def draw_diagrams(copy: str) -> tuple:
    """Draw each diagram folder that the builder's try touched: a changed spec in
    <folder>/src/, or an SVG file in a folder that has specs. The builder writes
    the spec; Tollgate's own code draws the SVG files, so they always match it.
    Return (folders, problem); problem is empty when every folder was drawn."""
    from diagram import aws
    from diagram.__main__ import build
    folders = set()
    for line in git(copy, "status", "--porcelain", "-uall").splitlines():
        # "XY path", or "R  old -> new"; the output may come with its first space trimmed.
        path = Path(line.split(None, 1)[-1].split(" -> ")[-1].strip('"'))
        if path.suffix == ".json" and path.parent.name == "src" and _is_spec(Path(copy, path)):
            folders.add(path.parent.parent)
        elif path.suffix == ".svg" and Path(copy, path.parent, "src").is_dir():
            folders.add(path.parent)
    names = sorted(str(f) for f in folders)
    for folder in names:
        try:
            build(str(Path(copy, folder)))
        except (aws.Missing, ValueError, KeyError, TypeError, IndexError, AttributeError,
                json.JSONDecodeError, SystemExit) as err:
            return names, (f"the diagram spec in {folder}/src cannot be drawn: {err}. "
                           "Fix the spec; Tollgate draws the SVG files.")
    return names, ""


def item_of(test_id: str, items: dict | None = None) -> str | None:
    """The item a locked test covers: by the lock's map, or else by its name."""
    return (items or {}).get(test_id) or item_of_test(test_id)


def score(ticket, test_status: dict, reviewed: list, workdir: str,
          items: dict | None = None) -> tuple:
    """Return (score, {item id: passed}). items is the lock's map of test to item.

    An item with tests passes when all its tests pass. Any other item passes when
    the reviewer named a file and a line that exists.
    """
    by_item = {}
    for test_id, status in test_status.items():
        item = item_of(test_id, items)
        if item:
            by_item.setdefault(item, []).append(status == "passed")
    claims = {r.get("item"): r for r in reviewed if isinstance(r, dict)}
    passed = {}
    for item in ticket.items:
        if item.id in by_item:
            passed[item.id] = all(by_item[item.id])
        else:
            c = claims.get(item.id) or {}
            passed[item.id] = bool(c.get("passed")) and line_exists(workdir, c.get("file", ""),
                                                                    c.get("line"))
    value = round(100 * sum(passed.values()) / len(ticket.items)) if ticket.items else 0
    return value, passed


def item_reasons(ticket, test_status: dict, reviewed: list, passed: dict,
                 items: dict | None = None) -> str:
    """Why each failed item failed, for the builder's next try."""
    claims = {r.get("item"): r for r in reviewed if isinstance(r, dict)}
    out = []
    for item in ticket.items:
        if passed.get(item.id, True):
            continue
        tests = sorted(t for t in test_status if item_of(t, items) == item.id)
        if tests:
            failing = [t for t in tests if test_status[t] != "passed"]
            out.append(f"{item.id}: its locked tests fail: {', '.join(failing)}")
            continue
        claim = claims.get(item.id)
        if not claim:
            out.append(f"{item.id}: it has no locked test, and the reviewer did not pass it")
        elif not claim.get("passed"):
            why = claim.get("why") or "no reason given"
            out.append(f"{item.id}: it has no locked test, and the reviewer failed it: {why}")
        else:
            out.append(f"{item.id}: the reviewer passed it, but the line it named does not "
                       f"exist: {claim.get('file', '')}:{claim.get('line', '')}")
    return "; ".join(out)


# Git.

def git(cwd, *args) -> str:
    """git in the project or a copy, with no hooks, no file system monitor, and no
    replace refs, since a role can write in its copy."""
    return subprocess.run(["git", *SAFE, *args], cwd=cwd, check=True, capture_output=True,
                          env=safe_env(),
                          text=True).stdout.strip()


def fetch_branch(project: str, copy: str, branch: str) -> None:
    """Fetch a branch of the copy into the project, under the same name."""
    git(project, "fetch", "-q", os.path.realpath(copy), f"+refs/heads/{branch}:refs/heads/{branch}")


def task_cost(logs_dir, task: str) -> float:
    total = 0.0
    for path in history.logs(logs_dir):
        total += history.cost([e for e in read(str(path)) if e.get("task") == task])
    return round(total, 4)


class Driver:
    """gate_api and review_api default to gate.gate and gate.review. Tests pass fakes."""

    def __init__(self, project: str, ticket_id: str, store=None, ticket_text: str | None = None,
                 gate_api=None, review_api=None, runner: str | None = None, caller=None,
                 base: str | None = None, hold: bool = False, approval_hint: str = ""):
        """base is the commit the ticket starts from, main by default; in a task, the
        ready change of the ticket before it. hold=True leaves the merge to the task:
        the ticket ends at waiting_for_merge, even with auto_merge on. approval_hint is
        what the advisor said when it approved the plan: the test writer and the builder
        get it as `approval_hint`."""
        self.project = os.path.abspath(project)
        self.ticket_id = ticket_id
        self.store = store or Store(self.project)
        self.gate = gate_api or gatelib
        self.review = review_api or reviewlib
        self.base = BASE         # main: the settings and the harness come from here
        self.start = base or BASE
        self.hold = hold
        self.branch = f"tg/{ticket_id}"
        self.logs = state.path(self.project, "logs")
        self.log_path = str(self.logs / f"{ticket_id}.jsonl")
        self.ticket_text = ticket_text or Path(self.project, mf.ticket_path(ticket_id)).read_text()
        self.ticket = tickets.parse(self.ticket_text)
        self.runner = runner    # None: the setting `runner` decides
        self.caller = caller
        # Only when there is one, so the inputs of a ticket with none stay as they were.
        self.approval = {"approval_hint": approval_hint} if approval_hint else {}

    # Helpers.

    def call(self, name, inputs, lens=None, command=None) -> session.Call:
        s = self.settings
        command = command or (s["ai"]["reviewer"] if name == "reviewer" else s["ai"]["builder"])
        return session.Call(name, inputs, lens, command, name, self.copy, self.project,
                            self.ticket_id, self.ticket_id, self.ticket.plan)

    def role(self, name, inputs, lens=None, command=None):
        return self.caller(self.call(name, inputs, lens, command))

    def lock(self) -> dict | None:
        """The ticket's lock as the store wrote it. The driver reads it only to put back
        the locked files and to score."""
        import json
        path = state.path(self.project, "locks", f"{self.ticket_id}.json")
        return json.loads(path.read_text()) if path.exists() else None

    def checks(self, only=None):
        lock = self.lock()
        prepare.restore(self.copy, self.base, lock["commit"] if lock else None,
                        lock["manifest"] if lock else None)
        try:
            for command in self.settings["setup"]:
                subprocess.run(command, shell=True, cwd=self.copy, check=False,
                               capture_output=True)
            return checks.run(self.settings, self.copy, only)
        finally:
            git(self.copy, "reset", "-q", "--hard", "HEAD")
            git(self.copy, "clean", "-q", "-fd")

    def commit(self, message) -> str:
        git(self.copy, "add", "-A")
        git(self.copy, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.copy, "rev-parse", "HEAD")

    def stop_check(self):
        """Raise Stop or Parked when a stop rule fires."""
        verdict = stop.check(read(self.log_path), self.settings,
                             task_cost(self.logs, self.ticket.plan) if self.ticket.plan else 0.0)
        if verdict == stop.GO:
            return
        reason = verdict.removeprefix("stop: ")
        if stop.is_hard(verdict):
            self.log.add("parked", reason=reason)
            raise Parked(reason)
        raise Stop(reason)

    # Steps.

    def write_tests(self, st_: history.Ticket):
        """Go on from the log: the findings of the last rejection, and its count."""
        rejected = st_.rejected
        if st_.rejections > MAX_REJECTIONS:
            raise Stop(f"the tests were rejected {st_.rejections} times")
        if st_.relock_from and not st_.rejections and not st_.tests_done:
            # The replacement builds on the old lock, with none of the builder's code, so
            # its new tests still fail before the build, as the store requires.
            git(self.copy, "checkout", "-q", "-B", self.branch, st_.relock_from)
            git(self.copy, "clean", "-q", "-fd")
        while not st_.tests_done:
            # The round tells two rounds apart, in the log and in session mode's saved answers.
            n = st_.rejections + 1
            inputs = {"ticket": self.ticket_text, "ticket_id": self.ticket_id,
                      "rejected": rejected, "round": n, **self.approval}
            if st_.hint:
                inputs["hint"] = st_.hint
            if st_.relock_from:
                inputs["replacement"] = REPLACEMENT
            stronger = self.settings["ai"]["stronger_builder"] if st_.stronger else ""
            answer = self.role("test_writer", inputs, command=stronger or None)
            wrong = answer.data.get("ticket_wrong")
            self.log.add("tests_written", round=n, cost_usd=answer.cost_usd,
                         tests=answer.data.get("tests"),
                         **({"ticket_wrong": wrong} if wrong else {}))
            review = self.role("reviewer", {"ticket": self.ticket_text,
                               "tests": answer.data.get("tests", [])}, lens="test_fit")
            missing = [i.id for i in self.ticket.items if i.must and not any(
                t.get("item") == i.id for t in answer.data.get("tests", []))]
            findings = review.data.get("findings", []) + [
                {"level": "blocking", "text": f"must-have item {m} has no test"} for m in missing]
            blocking = review.data.get("verdict") == "fail" or any(
                f.get("level") == "blocking" for f in findings)
            if not blocking:
                # The other checks, such as style, run on the new tests now. The builder
                # may not change a locked test, so a failure there must go back here.
                others = [n for n in self.settings["checks"] if n != "tests"]
                found = checks.run(self.settings, self.copy, others) if others else None
                # Only a failure that names one of the new test files is the test
                # writer's to fix. Any other, such as a missing README, waits for the
                # builder: the gate runs every check on each try.
                failed = [c for c in (found.checks if found else []) if not c.passed
                          and any(t in (c.output or "") for t in self.ticket.tests)]
                # A test that calls a seam the builder adds does not compile yet, so a
                # vet, type, or lint check fails on it. That failure waits for the
                # builder too, when it is only about seam names.
                waiting = [c.name for c in failed if checks.only_missing_seams(
                    c.output or "", self.ticket.tests, self.ticket.seams)]
                failed = [c for c in failed if c.name not in waiting]
                if failed:
                    findings = [{"level": "blocking", "text": f"check {c.name} fails on the "
                                 f"new tests:\n{c.output}"} for c in failed]
                else:
                    extra = {"waiting_checks": waiting} if waiting else {}
                    self.log.add("tests_reviewed", cost_usd=review.cost_usd, **extra)
                    st_.tests_done = True
                    break
            st_.rejections += 1
            self.log.add("tests_rejected", round=n, cost_usd=review.cost_usd, findings=findings)
            rejected = st_.rejected = findings
            if wrong:
                # The test writer says the locked ticket itself is wrong. Writing the tests
                # again cannot fix that, so the claim goes to the advisor now.
                raise Stop(f"the tests were rejected {st_.rejections} "
                           f"{'time' if st_.rejections == 1 else 'times'}"
                           + self.claim(answer, "test writer"))
            if st_.rejections > MAX_REJECTIONS:
                raise Stop(f"the tests were rejected {st_.rejections} times")

    def write_map(self) -> None:
        """Write the test map from the test writer's last answer, when a test in it
        keeps its own name: an existing test that covers an item."""
        written = [e for e in read(self.log_path) if e["event"] == "tests_written"]
        tests = [t for t in (written[-1].get("tests") or [] if written else [])
                 if isinstance(t, dict) and all(isinstance(t.get(k), str) and t.get(k)
                                                for k in ("item", "file", "name"))]
        path = Path(self.copy, mf.map_path(self.ticket_id))
        if any(item_of_test(t["name"]) != t["item"] for t in tests):
            path.write_text(json.dumps([{k: t[k] for k in ("item", "file", "name")}
                                        for t in tests], indent=1) + "\n")
        else:
            path.unlink(missing_ok=True)

    def freeze(self, st_: history.Ticket):
        self.write_map()
        commit = self.commit(f"test({self.ticket_id}): the ticket and its locked tests")
        fetch_branch(self.project, self.copy, self.branch)
        try:
            if self.start == self.base:
                self.store.freeze(self.ticket_id)
            else:
                self.store.freeze(self.ticket_id, start=self.start)
        except (StoreError, mf.FreezeError) as err:
            st_.rejections += 1
            st_.tests_done = False
            st_.rejected = [{"level": "blocking", "text": str(err)}]
            self.log.add("tests_rejected", round=st_.rejections, findings=st_.rejected)
            if st_.rejections > MAX_REJECTIONS:
                raise Stop(f"the freeze refused the tests: {err}") from None
            return
        lock = self.lock() or {}
        self.log.add("frozen", commit=lock.get("commit", commit), number=lock.get("number"))
        st_.frozen = lock.get("commit", commit)
        st_.best_commit = st_.best_commit or st_.frozen
        if st_.relock_from:
            self.carry_over(st_)

    def carry_over(self, st_: history.Ticket):
        """After a replacement lock: a commit with the best try's files and the new lock's
        files, with both as parents. The gate needs the lock commit in each try's history."""
        git(self.copy, "checkout", "-q", "-B", self.branch, st_.frozen)
        git(self.copy, "merge", "-q", "--no-ff", "--no-commit", "-s", "ours", st_.best_commit)
        git(self.copy, "read-tree", "-u", "--reset", st_.best_commit)
        files = (self.lock() or {}).get("manifest", {}).get("files", [])
        if files:
            git(self.copy, "checkout", st_.frozen, "--", *files)
        commit = self.commit(f"build({self.ticket_id}): the best try on the replacement lock")
        self.log.add("relocked", commit=commit, lock=st_.frozen, start=st_.best_commit)
        st_.relock_from, st_.best_commit, st_.best_score = "", commit, -1

    def locked_test_fault(self, results, answer) -> list:
        """Findings for the test writer when the try cannot pass with the locked tests as
        they are: each failed check names files, and each is a locked test file; or the
        builder says a locked test is wrong. Empty otherwise: the builder tries again."""
        wrong = answer.data.get("locked_test_wrong")
        if wrong:
            return [{"level": "blocking", "text": f"the builder says a locked test is wrong: "
                     f"{wrong if isinstance(wrong, str) else json.dumps(wrong)}"}]
        failed = [c for c in results.checks if not c.passed and c.name != "tests"]
        if not failed:
            return []
        for c in failed:
            named = {m.group(1) for m in FILE_LINE.finditer(c.output or "")}
            if not named or not named <= set(self.ticket.tests):
                return []
        return [{"level": "blocking", "text": f"check {c.name} fails on a locked test:\n"
                 f"{c.output}"} for c in failed]

    @staticmethod
    def claim(answer, who: str = "builder") -> str:
        """The role's claims, as sentences for the advisor's reason, or "": that a locked
        test is wrong (`locked_test_wrong`), or that the locked ticket or plan is wrong
        (`ticket_wrong`, with what, the evidence, and the change). When the try stops, the
        advisor judges each claim: a retry or a park, an amend of the ticket, or a person
        who fixes the test by hand."""
        out = ""
        for key, what in (("locked_test_wrong", "a locked test is wrong"),
                          ("ticket_wrong", "the locked ticket or plan is wrong")):
            wrong = answer.data.get(key)
            if wrong:
                out += (f". The {who} also says {what}: "
                        + (wrong if isinstance(wrong, str) else json.dumps(wrong)))
        return out

    def send_back(self, st_: history.Ticket, findings: list):
        """Send the tests back to the test writer for the one replacement lock, or park
        the ticket when it was used. The builder cannot change a locked test."""
        if st_.relocks >= MAX_RELOCKS:
            reason = ("a locked test still blocks the build after the one replacement lock: "
                      + "; ".join(f["text"].splitlines()[0] for f in findings)
                      + ". Fix the test by hand, or split the ticket?")
            self.log.add("parked", reason=reason)
            raise Parked(reason)
        self.log.add("tests_sent_back", lock=st_.frozen, findings=findings)
        st_.relock_from, st_.relocks = st_.frozen, st_.relocks + 1
        st_.frozen, st_.tests_done, st_.rejections, st_.rejected = "", False, 0, findings
        st_.fallback, st_.fallback_attempt = "", 0   # its pass record is of the old lock

    def try_once(self, st_: history.Ticket):
        """One try, from the best try so far."""
        n = st_.attempts + 1
        git(self.copy, "reset", "-q", "--hard", st_.best_commit)
        git(self.copy, "clean", "-q", "-fd")
        last = [e for e in read(self.log_path) if e["event"] == "try_failed"]
        inputs = {"ticket": self.ticket_text, "attempt": n, "hint": st_.hint,
                  "diagram_guide": diagram_guide(self.ticket_text),
                  "findings": last[-1].get("findings", []) if last else [],
                  "failed": last[-1].get("failed", []) if last else [],
                  "check_output": last[-1].get("check_output", {}) if last else {},
                  **self.approval}
        self.log.add("try_started", attempt=n, start=st_.best_commit)
        stronger = self.settings["ai"]["stronger_builder"] if st_.stronger else ""
        if st_.stronger and not stronger:
            self.log.add("no_stronger_builder", attempt=n)
        try:
            answer = self.role("builder", inputs, command=stronger or None)
        except roles.RoleTimeout as err:
            self.log.add("try_failed", attempt=n, commit=st_.best_commit, score=-1,
                         timed_out=True, findings=[{"level": "blocking", "text": str(err)}])
            st_.attempts = n
            return
        cost = answer.cost_usd
        said = {k: answer.data.get(k) for k in ("decisions", "concerns", "locked_test_wrong",
                                                "ticket_wrong") if answer.data.get(k)}
        if said:
            self.log.add("builder_said", attempt=n, **said)
        folders, problem = draw_diagrams(self.copy)
        if folders:
            self.log.add("diagrams_drawn", attempt=n, folders=folders, problem=problem)
        if problem:
            self.log.add("try_failed", attempt=n, commit=st_.best_commit, score=-1, cost_usd=cost,
                         findings=[{"level": "blocking", "text": problem}])
            st_.attempts = n
            return
        commit = self.commit(f"feat({self.ticket_id}): try {n}")
        results = self.checks()
        facts = self.gate.collect(self.project, self.copy, self.base, "HEAD", self.ticket_id,
                                  results, start=None if self.start == self.base else self.start)
        findings = self.gate.evaluate(facts)
        tier = self.gate.tier_of(facts)[0]
        lock = facts.lock or self.lock() or {"manifest": {"tests": []}}
        mapped = lock["manifest"].get("items", {})
        locked = {t: r["status"] for t, r in results.tests.items()
                  if t in lock["manifest"]["tests"]}
        failed_tests = sorted(t for t in lock["manifest"]["tests"] if locked.get(t) != "passed")
        key = [list(results.failed_checks()), failed_tests]
        fields = {"attempt": n, "commit": commit, "cost_usd": cost, "tier": tier,
                  "seconds": round(answer.seconds, 1),
                  "check_output": {c.name: c.output for c in results.checks if not c.passed},
                  "failed": [*key[0], *key[1]], "failed_key": key}
        st_.attempts = n
        cannot_fix = [f for f in findings if not f["fixable"] and f["check"] != "review"]
        if cannot_fix:
            self.log.add("try_failed", findings=findings, score=-1, **fields)
            raise Stop("the gate failed on a check the builder cannot fix: "
                       + "; ".join(f["message"] for f in cannot_fix) + self.claim(answer))
        if any(f["fixable"] for f in findings):
            # The score with no review: a lower bound, so the best try can still move.
            value, passed = score(self.ticket, locked, [], self.copy, mapped)
            self.log.add("try_failed", findings=findings, score=value, items=passed, **fields)
            self._keep_if_best(st_, value, commit)
            back = self.locked_test_fault(results, answer)
            if back:
                self.send_back(st_, back)
            return
        # Only a locked test vouches for an item: the builder's own tests do not, so the
        # reviewer judges every item that no locked test covers, as score() counts them.
        tested = sorted({item_of(t, mapped) for t in locked if item_of(t, mapped)})
        diff = self.review.diff(self.copy, st_.frozen, "HEAD")
        # The checklist reviewer and, from tier 2, the review lenses read the change and
        # not one another, so session mode asks them all at once.
        change = self.review.diff(self.copy, self.start, "HEAD") if tier >= 2 else ""
        asked = self.review.lens_inputs(tier, self.ticket_text, change) if tier >= 2 else []
        # `answer` stays the builder's: its claim goes into the stop reason below.
        reviews = session.ask_many(self.caller, [
            self.call("reviewer", {"ticket": self.ticket_text, "tested": tested, "diff": diff},
                      lens="checklist"),
            *(self.call("reviewer", inputs, lens=lens) for lens, inputs in asked)])
        for review in reviews:
            if isinstance(review, roles.RoleError):
                raise review
        checked = reviews[0]
        cost = roles.add_cost(cost, *(r.cost_usd for r in reviews))
        value, passed = score(self.ticket, locked, checked.data.get("items", []), self.copy,
                              mapped)
        blocking = [f for f in checked.data.get("findings", []) if f.get("level") == "blocking"]
        if tier >= 2:
            facts.review_passed, found, _ = self.review.judge(
                tier, change, [(lens, a.data) for (lens, _), a in zip(asked, reviews[1:],
                                                                      strict=True)])
            blocking += [f for f in found if f.get("level") == "blocking"]
            majors = [f for f in found if f.get("level") == "major"]
            findings = self.gate.evaluate(facts)
        else:
            majors = []
        fields.update(cost_usd=roles.add_cost(cost), score=value, items=passed)
        must_failed = [i.id for i in self.ticket.items if i.must and not passed.get(i.id)]
        if majors and not (findings or blocking or must_failed
                           or value < self.settings["pass_score"]) and self.major_try(st_):
            # A major finding of the review should be fixed now, so the builder gets one
            # more try with it. Only once for a ticket, and only while it has a try left.
            # The try passes the gate, so it gets its pass record now and is the fallback:
            # when the next try does not pass, the ticket gets ready with this one, so a
            # major finding alone never parks a ticket.
            try:
                record = self.gate.pass_record(facts, value)
            except self.gate.GateError as err:
                self.log.add("try_failed", findings=[{"level": "blocking", "text": str(err)}],
                             **fields)
                raise Stop(f"the gate refused a pass record: {err}") from None
            self.store.record_pass(facts.head_sha, record)
            self.log.add("try_failed", findings=majors, review_major=True, fallback=commit,
                         **fields)
            st_.fallback, st_.fallback_attempt = commit, n
            self._keep_if_best(st_, value, commit)
            return
        if majors:
            fields["majors"] = majors
        if findings or blocking or must_failed or value < self.settings["pass_score"]:
            reasons = item_reasons(self.ticket, locked, checked.data.get("items", []), passed,
                                   mapped)
            why = blocking or [{"level": "major", "text": f["message"]} for f in findings] or [
                {"level": "major", "text": (f"must-have items failed: {', '.join(must_failed)}"
                 if must_failed else f"score {value} is under {self.settings['pass_score']}")
                 + (f". {reasons}" if reasons else "")}]
            self.log.add("try_failed", findings=why, **fields)
            self._keep_if_best(st_, value, commit)
            back = self.locked_test_fault(results, answer)
            if back and not findings:
                self.send_back(st_, back)
            if findings:
                raise Stop("the review of tier " + str(tier) + " failed: "
                           + "; ".join(f["message"] for f in findings) + self.claim(answer))
            return
        try:
            record = self.gate.pass_record(facts, value)
        except self.gate.GateError as err:
            self.log.add("try_failed", findings=[{"level": "blocking", "text": str(err)}],
                         **fields)
            raise Stop(f"the gate refused a pass record: {err}") from None
        self.store.record_pass(facts.head_sha, record)
        self.log.add("try_passed", **fields)
        st_.passed_commit = commit
        self._keep_if_best(st_, value, commit)

    def use_fallback(self, st_: history.Ticket) -> bool:
        """When the try after a fallback ended and did not pass, the fallback is the
        passed try. Return True when it is used."""
        if st_.passed_commit or not st_.fallback or st_.attempts <= st_.fallback_attempt:
            return False
        st_.passed_commit = st_.fallback
        self.log.add("fallback_used", commit=st_.fallback,
                     reason="the try after a try with only major findings did not pass")
        return True

    def major_try(self, st_: history.Ticket) -> bool:
        """True when a try that only has major review findings goes back to the builder:
        no try of the ticket went back for them yet, and the ticket has a try left after
        this one."""
        events = read(self.log_path)
        if any(e["event"] == "try_failed" and e.get("review_major") for e in events):
            return False
        return stop.tries_left(events, self.settings["stop"]) > 1

    @staticmethod
    def _keep_if_best(st_: history.Ticket, value: int, commit: str):
        if history.better(value, st_.best_score):
            st_.best_score, st_.best_commit = value, commit

    def make_ready(self, st_: history.Ticket):
        git(self.copy, "reset", "-q", "--hard", st_.passed_commit)
        fetch_branch(self.project, self.copy, self.branch)
        self.store.ready(self.branch, self.ticket_id)
        st_.ready = st_.passed_commit
        self.log.add("ready", branch=self.branch, commit=st_.passed_commit)

    def finish(self, st_: history.Ticket) -> int:
        # A person merges tier 3 and 4, and any change whose tier the log does not show.
        tier = history.replay(read(self.log_path)).tier
        if self.hold or not self.settings["auto_merge"] or tier is None or tier >= 3:
            self.log.add("waiting_for_merge", branch=self.branch, commit=st_.ready, tier=tier)
            return 0
        try:
            merge = self.store.merge(st_.ready)
        except StoreError as err:
            # The change stays ready; you merge it once the reason is gone.
            self.log.add("waiting_for_merge", branch=self.branch, commit=st_.ready, tier=tier,
                         reason=f"the store did not merge: {err}")
            return 0
        self.log.add("merged", commit=st_.ready, merge=merge)
        roles.remove_copy(self.project, self.ticket_id)
        return 0

    def options(self) -> list:
        """The decisions on a stopped ticket. `amend` only for a ticket of a plan, since
        the task amends the ticket in the plan lock."""
        return [*OPTIONS[:2], AMEND, *OPTIONS[2:]] if self.ticket.plan else list(OPTIONS)

    def ask_advisor(self, st_: history.Ticket, reason: str, person_only: bool = False) -> bool:
        """Return True to go on with one more try."""
        chain = ["person"] if person_only else self.settings["ai"]["advisor"]
        for entry in chain:
            if entry == "person":
                try:
                    found = advisor.ask_person(
                        self.store, self.log, self.log_path, f"ticket {self.ticket_id}", reason,
                        self.options())
                except advisor.Waiting as waiting:
                    raise Parked(str(waiting)) from None
                return self._decided(st_, found["decision"], found["hint"], "a person")
            if st_.ai_decisions >= MAX_AI_DECISIONS:
                continue
            events = read(self.log_path)
            answer = self.role("advisor", {"ticket": self.ticket_text, "reason": reason,
                               "tries": [e for e in events
                                            if e["event"].startswith("try_")][-4:]},
                               command=entry)
            decision = answer.data.get("decision", "park")
            self.log.add("advisor_decided", by="ai", decision=decision, cost_usd=answer.cost_usd,
                         hint=answer.data.get("hint", ""), reason=answer.data.get("reason", ""))
            st_.ai_decisions += 1
            return self._decided(st_, decision, answer.data.get("hint", ""), "the advisor")
        self.log.add("parked", reason=reason)
        raise Parked(reason)

    def _decided(self, st_, decision: str, hint: str, who: str) -> bool:
        if decision in ("retry", "stronger"):
            # Before the tests are locked, the test writer writes them again with the hint,
            # and the rejections count anew; after, the builder gets one more try.
            st_.hint = hint
            st_.stronger = st_.stronger or decision == "stronger"
            if not st_.frozen:
                st_.rejections, st_.rejected = 0, []
            return True
        if decision == AMEND and AMEND in self.options():
            self.request_amend(st_, hint, who)
        if decision == "split":
            # UNFINISHED: the driver does not split a ticket yet. New tickets would need a
            # new plan lock, so you split it by hand and start a task for the parts.
            reason = (f"{who} chose split: UNFINISHED, parked. Split the ticket by hand into "
                      "smaller tickets")
            self.log.add("parked", reason=reason, decision="split")
            raise Parked(reason)
        self.log.add("parked", reason=f"{who} chose {decision}")
        raise Parked(f"{who} chose {decision}")

    def request_amend(self, st_, change: str, who: str):
        """Park the ticket for the task to amend it in the plan lock, with the change to
        make. After the tests are locked, the amended ticket needs the one replacement
        lock, so a ticket that used it stays parked for a person."""
        if not change.strip():
            reason = f"{who} chose amend with no change to make"
        elif st_.frozen and st_.relocks >= MAX_RELOCKS:
            reason = (f"{who} chose amend, but the ticket used its one replacement lock. "
                      "Fix the ticket by hand, or split it")
        else:
            reason = f"{who} chose amend: the planner changes the ticket: {change.strip()}"
            self.log.add("parked", reason=reason, decision=AMEND, change=change.strip())
            raise Parked(reason)
        self.log.add("parked", reason=reason)
        raise Parked(reason)

    def apply_amend(self, st_: history.Ticket):
        """Take the ticket that the task amended: the ticket file in the copy becomes the
        amended one. After the lock, the copy goes back to the old lock commit first, so
        the same locked tests are locked again with the new ticket; the builder's code
        comes back after the lock (carry_over). No locked test changes here."""
        if st_.relock_from:
            git(self.copy, "checkout", "-q", "-B", self.branch, st_.relock_from)
            git(self.copy, "clean", "-q", "-fd")
        path = Path(self.copy, mf.ticket_path(self.ticket_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.ticket_text)
        self.log.add("amend_applied", relock=bool(st_.relock_from))
        st_.amending = False

    def run(self, until: str | None = None) -> int:
        """until="frozen" stops once the tests are frozen, for a roadmap's checklist."""
        self.until = until
        lock = run_lock(self.project, self.ticket_id)
        lock.acquire()
        try:
            events = read(self.log_path)
            st_ = history.replay(events)
            with Log(self.log_path, ticket=self.ticket_id, task=self.ticket.plan) as self.log:
                self.copy = roles.make_copy(self.project, self.ticket_id)
                # The branch starts once: tests written again after a rejection build on it.
                if not st_.frozen and not any(e["event"] == "tests_written" for e in events):
                    if self.start != self.base:
                        # The ready change this ticket builds on lives in the project.
                        git(self.copy, "fetch", "-q", self.project,
                            "+refs/heads/tg/*:refs/tollgate/tickets/*")
                    git(self.copy, "checkout", "-q", "-B", self.branch, self.start)
                    path = Path(self.copy, mf.ticket_path(self.ticket_id))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(self.ticket_text)
                self.settings = st.load_from_ref(self.copy, self.base)
                self.caller = self.caller or session.caller(self.settings, self.runner)
                in_session = isinstance(self.caller, session.Session)
                if in_session:
                    # Session mode replays the step it stopped in: log each event once.
                    self.log.hold_while(lambda: session.replaying(self.project))
                if not (in_session and st_.state != "not started"):
                    self.log.add("started", attempts_done=st_.attempts)
                question = advisor.pending(self.log_path)
                if question:
                    # Read the answer before doing anything else.
                    self.ask_advisor(st_, question["reason"], person_only=True)
                return self._loop(st_)
        except Parked:
            return 3
        except session.Pause:
            return 5
        finally:
            lock.release()

    def _loop(self, st_: history.Ticket) -> int:
        if st_.amending:
            self.apply_amend(st_)
        while True:
            try:
                while not st_.frozen:
                    self.write_tests(st_)
                    self.freeze(st_)
                if st_.relock_from:
                    self.carry_over(st_)    # a run that stopped after the replacement lock
                if getattr(self, "until", None) == "frozen":
                    return 0
                while not st_.passed_commit and st_.frozen:
                    if self.use_fallback(st_):
                        break
                    self.stop_check()
                    self.try_once(st_)
                if not st_.frozen:
                    continue    # the tests went back to the test writer
                if not st_.ready:
                    self.make_ready(st_)
                return self.finish(st_)
            except Stop as stopped:
                if st_.frozen and self.use_fallback(st_):
                    continue    # the try after the fallback stopped: the fallback is ready
                self.log.add("stopped", reason=str(stopped))
                self.ask_advisor(st_, str(stopped))


def main(argv=None, store=None) -> int:
    parser = argparse.ArgumentParser(prog="tollgate driver", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=".")
    parser.add_argument("--ticket", required=True)
    args = parser.parse_args(argv)
    try:
        return Driver(args.project, args.ticket, store).run()
    except Busy as err:
        print(err, file=sys.stderr)
        return 4
    except (roles.RoleError, tickets.TicketError, st.SettingsError, StoreError, OSError,
            subprocess.CalledProcessError) as err:
        print(f"driver: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

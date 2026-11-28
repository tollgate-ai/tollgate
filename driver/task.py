"""The task loop: analysis, plan, plan review, approval, plan lock, each ticket in
order, then a review of the whole task.

    PYTHONPATH=<tollgate> python3 -m driver.task --project . --task retries --request-file r.txt
    PYTHONPATH=<tollgate> python3 -m driver.task --project . --task retries --brief retries

A brief from /tollgate:brainstorm can be the request: its goals and non-goals set
the scope of the plan, and each question under its Open questions must be an assumption of
the plan, so the advisor answers it before the plan is locked.

The task's own log is logs/task-<task id>.jsonl in the state folder, and its
copy is builders/task-<task id>/. Each ticket keeps its own log. A task that
starts again repeats no finished step. A ticket that waits for the advisor or is
parked does not stop the others: the report says why.

The setting `checkpoints` names where the advisor decides whether the task goes
on: "plan" (the approval of the plan), "ticket" (after each ready ticket), and
"task" (after the review of the whole task). A plan with an assumption, a plan
review with open findings, and a task review that blocks go to the advisor even
when their checkpoint is off.

A task starts only when it can pass: the settings on main are there and readable.
Otherwise it stops at once and says to run /tollgate:init.

In a task the tickets build one on another: each starts from the ready change of
the ticket before it, and none merges until the review of the whole task passes.
Then the tickets merge in plan order, as far as auto merge and their tiers allow.
When the first review of the whole task finds a blocking or major problem, the
task runs one fix round: the builder changes the plan with the findings as its
hint, the plan is reviewed, approved, and locked again, and the new tickets are
built. A second failed review parks the task, and nothing merges, whatever the
advisor says. A ticket that does not end ready stops the tickets after it.

Exit codes: 0 every ticket is a ready change or merged, 3 something waits for the
advisor, 4 another driver holds the task, 5 a role waits for your session (see
driver/session.py), 1 an error.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from driver import advisor, report, roles, session
from driver.driver import BASE, Busy, Driver, Parked, fetch_branch, git, run_lock
from driver.log import Log, read
from gate import analysis as an
from gate import brief as bf
from gate import plan as pl
from gate import review as reviewlib
from gate import settings as st
from gate import state
from gate import tickets as tk
from gate.store import Store, StoreError

PLAN_LENSES = ("simplicity", "fresh_reader", "readiness")
WRITING_CHECK = ".tollgate/checks/check_writing.py"
OWN_WRITING_CHECK = "setup/check_writing.py"   # in Tollgate's own project, which holds the source
MAX_ROUNDS = 4
OPEN_LEVELS = ("blocking", "major")
TASK_TIER = 2   # the review of the whole task reads like a tier 2 review: the review lens only
FIX_ROUNDS = 1  # the fix rounds a task may run after a failed review of the whole task
MAX_AMENDS = 2  # the amends of one ticket in the plan lock
LOCKS = ("plan_locked", "plan_amended")   # the events that lock the plan at a new commit


def analyze(role, log, copy: str, request: str, task_id: str, spec=()) -> None:
    """The analyst reads the copy and answers with the analysis. The driver checks it,
    makes each citation that does not exist an assumption, and keeps the text in the
    log, so no later role can change it. role(name, inputs) asks a role; a brainstorm
    uses this step too. spec lists the spec paths of the settings: the analyst gets
    them, and a specified fact may cite only inside them. With no spec paths the inputs
    have no spec key, so the key of a paused session stays what it was."""
    commit = git(copy, "rev-parse", "HEAD")
    problems = []
    for _ in range(3):
        inputs = {"request": request, "task_id": task_id, "commit": commit,
                  "problems": problems}
        if spec:
            inputs["spec"] = list(spec)
        answer = role("analyst", inputs)
        try:
            found = an.parse(answer.data.get("analysis") or "")
            if (found.task, found.commit) != (task_id, commit):
                raise an.AnalysisError(f"the header must say task: {task_id} and "
                                       f"commit: {commit}")
        except an.AnalysisError as err:
            problems = [str(err)]
            log.add("analysis_invalid", problems=problems, cost_usd=answer.cost_usd)
            continue
        missing = an.verify(found, copy, spec)
        log.add("analysis_written", text=an.render(found), not_found=missing,
                cost_usd=answer.cost_usd)
        return
    raise Parked("the analysis could not be read: " + "; ".join(problems))


class Task:
    def __init__(self, project: str, task_id: str, request: str, store=None, driver_args=None,
                 runner: str | None = None):
        if not pl.ID.match(task_id):
            raise pl.PlanError(f"not a valid task id: {task_id!r}")
        self.project = os.path.abspath(project)
        self.id = task_id
        self.request = request
        self.brief = _brief(request, task_id)
        self.store = store or Store(self.project)
        self.driver_args = driver_args or {}
        self.base = BASE
        self.logs = state.path(self.project, "logs")
        self.log_path = str(self.logs / f"task-{task_id}.jsonl")
        self.ai_decisions = 0
        self.runner = runner    # None: the setting `runner` decides
        self.caller = None

    def call(self, name, inputs, lens=None, command=None) -> session.Call:
        s = self.settings
        command = command or (s["ai"]["reviewer"] if name in ("reviewer", "analyst")
                              else s["ai"]["builder"])
        # The builder writes the plan here, so it may write plans and tickets.
        policy = "planner" if name == "builder" else name
        return session.Call(name, inputs, lens, command, policy, self.copy, self.project,
                            f"task-{self.id}", "", self.id)

    def role(self, name, inputs, lens=None, command=None):
        return self.caller(self.call(name, inputs, lens, command))

    def roles_at_once(self, asked: list) -> list:
        """Ask the roles of [(name, inputs, lens)] that do not depend on one another: at
        once in session mode, one after another in the background. Each element is the
        Answer or the RoleError it raised."""
        if isinstance(self.caller, session.Session):
            return self.caller.many([self.call(name, inputs, lens)
                                     for name, inputs, lens in asked])
        found = []
        for name, inputs, lens in asked:
            try:
                found.append(self.role(name, inputs, lens=lens))
            except roles.RoleError as err:
                found.append(err)
        return found

    def read_copy(self, path):
        p = Path(self.copy, path)
        return p.read_text() if p.exists() else None

    def read_locked(self, path):
        """A file of the plan as the plan lock has it: the tickets are built from the
        approved plan, never from a plan the builder is still writing in the copy."""
        locked = [e for e in read(self.log_path) if e["event"] in LOCKS]
        if not locked:
            return self.read_copy(path)
        from gate.gitio import show_bytes
        data = show_bytes(self.copy, locked[-1]["commit"], path)
        return data.decode() if data is not None else None

    def check_plan(self, amend: str = "") -> list:
        """The plan's problems. amend names the locked ticket that an amend may change."""
        text = self.read_copy(pl.plan_path(self.id))
        if text is None:
            return [f"{pl.plan_path(self.id)} was not written"]
        try:
            plan = pl.parse(text)
        except pl.PlanError as err:
            return [str(err)]
        if plan.id != self.id:
            return [f"the plan names {plan.id}, not {self.id}"]
        used = [t for t in plan.tickets
                if state.path(self.project, "locks", f"{t}.json").exists()
                and not Path(self.logs, f"{t}.jsonl").exists()]
        problems = [f"ticket id {t} is already used by an earlier lock; pick a new id"
                    for t in used]
        return (problems + self.required_files(plan) + self.kept_tickets(plan, amend)
                + self.brief_questions(plan) + pl.check_tickets(plan, self.read_copy))

    def required_files(self, plan) -> list:
        """A file that a check of the project requires, and that the plan leaves missing.
        Today, one: the README that the writing check fails without."""
        has_check = (Path(self.copy, WRITING_CHECK).exists()
                     or (Path(self.copy, OWN_WRITING_CHECK).exists()
                         and Path(self.copy, "setup", "tollgate_checks.py").exists()))
        if (has_check and not Path(self.copy, "README.md").exists()
                and plan.files.get("README.md") != "create"):
            return [("README.md is missing, and the project's writing check fails without it: "
                     "add 'create README.md' to Files, and a checklist item that covers it")]
        return []

    def brief_questions(self, plan) -> list:
        """A task from a brief asks each open question of the brief under Assumptions."""
        if not self.brief:
            return []
        return [f"the plan's Assumptions must ask the brief's open question, word for word: "
                f"{q}" for q in self.brief.open_questions if q not in plan.assumptions]

    def plan_inputs(self, **inputs) -> dict:
        """The builder's inputs for a plan. From a brief, they add its open questions, and
        after a decision on the plan, the decisions."""
        if self.brief:
            inputs["open_questions"] = self.brief.open_questions
        return {**inputs, **self.decision_inputs()}

    def decision_inputs(self) -> dict:
        """{"decisions": [{by, decision, hint}]}: each decision the advisor, a person or
        an AI, made on this task's plan, oldest first, from the log. {} when there is none,
        so the inputs of a plan that no one decided on stay as they were. A hint can settle
        a fact, such as an owner's confirmation, so the roles that write and review the
        plan read it as a source."""
        found = [{"by": e.get("by", ""), "decision": e.get("decision", ""),
                  "hint": e.get("hint", "")}
                 for e in read(self.log_path) if e["event"] == "advisor_decided"
                 and "checkpoint" not in e]
        return {"decisions": found} if found else {}

    def kept_tickets(self, plan, amend: str = "") -> list:
        """A new version of a locked plan keeps each ticket of the old one, unchanged, but
        for the ticket that an amend names, which it must still list."""
        path = state.path(self.project, "plans", f"{self.id}.json")
        if not path.exists():
            return []
        from gate.gitio import fingerprint
        problems = []
        for name, digest in json.loads(path.read_text())["files"].items():
            if not (name.startswith(".tollgate/tickets/") and name.endswith("/ticket.md")):
                continue
            tid = Path(name).parent.name
            text = self.read_copy(name)
            if tid == amend and tid in plan.tickets and text is not None:
                continue
            if tid not in plan.tickets or text is None or fingerprint(text.encode()) != digest:
                problems.append(f"ticket {tid} is locked by the approved plan; keep it, unchanged, "
                                "and add new tickets for the fix")
        return problems

    def analysis_text(self) -> str:
        found = [e for e in read(self.log_path) if e["event"] == "analysis_written"]
        return found[-1]["text"] if found else ""

    # Steps.

    def analyze(self):
        """The analyst reads the copy at main and answers with the analysis."""
        analyze(self.role, self.log, self.copy, self.request, self.id, self.settings["spec"])

    def write_plan(self, problems=None, hint=""):
        answer = self.role("builder", self.plan_inputs(
            mode="plan", task_id=self.id, request=self.request, analysis=self.analysis_text(),
            problems=problems or [], hint=hint))
        self.log.add("plan_written", cost_usd=answer.cost_usd)
        for _ in range(2):
            problems = self.check_plan()
            if not problems:
                return
            self.log.add("plan_invalid", problems=problems)
            answer = self.role("builder", self.plan_inputs(
                mode="plan", task_id=self.id, request=self.request,
                analysis=self.analysis_text(), problems=problems))
            self.log.add("plan_written", cost_usd=answer.cost_usd)
        problems = self.check_plan()
        if problems:
            raise Parked("the planner could not write a plan that passes the checks: "
                         + "; ".join(problems))

    def review_plan(self, fix: str = "") -> list:
        """Up to 4 rounds. Return the findings still open, or [] after a clean round.
        In a fix round, `fix` holds the findings of the review of the whole task: the lenses
        that judge need and readiness read the plan against them too.

        A minor finding never makes a round unclean, but it goes to the builder: with the
        open findings of its round, or, in a round with none, in one answer step of its
        own. When that answer changes the plan or a ticket, the next round reviews the
        change; a later round with only minor findings is clean. In the last round the
        minor findings stay in the log, since no round is left to review a change."""
        open_findings = []
        minors_answered = False
        for round_no in range(1, MAX_ROUNDS + 1):
            problems = self.check_plan()
            if problems:
                # A plan or a ticket that breaks a format rule goes back to the planner.
                # Session mode replays the review from its first round with the plan the
                # copy holds now, so the fault is found here, before any lens reads it.
                self.log.add("plan_invalid", problems=problems)
                self.write_plan(problems)
            text = self.read_copy(pl.plan_path(self.id))
            plan = pl.parse(text)
            tickets = {t: self.read_copy(f".tollgate/tickets/{t}/ticket.md") for t in plan.tickets}
            findings, minors = [], []
            asked = [("reviewer", {"plan": text} if lens == "fresh_reader" else {
                "plan": text, "tickets": tickets, "request": self.request,
                "analysis": self.analysis_text(), **({"fix": fix} if fix else {}),
                **self.decision_inputs()}, lens) for lens in PLAN_LENSES]
            # The lenses read the same plan and not one another: in session mode they run
            # at once, and the round goes on once each has answered.
            answers = self.roles_at_once(asked)
            for (_, inputs, lens), data in zip(asked, answers, strict=True):
                if isinstance(data, roles.RoleError):
                    try:  # one retry, then the round does not count as clean
                        data = self.role("reviewer", inputs, lens=lens)
                    except roles.RoleError as err:
                        findings.append({"lens": lens, "level": "major",
                                         "text": f"the reviewer did not answer: {err}"})
                        continue
                self.log.add("plan_reviewed", lens=lens, round=round_no, cost_usd=data.cost_usd)
                found = [dict(f, lens=lens) for f in data.data.get("findings", [])
                         if isinstance(f, dict)]
                findings += [f for f in found if f.get("level") in OPEN_LEVELS]
                minors += [f for f in found if f.get("level") == "minor"]
            if not findings:
                if minors and not minors_answered and round_no < MAX_ROUNDS:
                    minors_answered = True
                    if self.answer_minors(round_no, minors, text, tickets):
                        continue
                self.log.add("plan_review_clean", round=round_no,
                             **({"minor": minors} if minors else {}))
                return []
            # The minor findings come after the open ones, so an open finding keeps its
            # number, and only an open finding can be escalated.
            answer = self.role("builder", {"mode": "answer", "findings": findings + minors,
                                           **self.locked_inputs()})
            self.keep_locked_tickets(round_no)
            answers = answer.data.get("answers", [])
            escalated = [findings[a["finding"]] for a in answers if a.get("action") == "escalate"
                         and isinstance(a.get("finding"), int) and a["finding"] < len(findings)]
            self.log.add("plan_answered", round=round_no, cost_usd=answer.cost_usd,
                         answers=answers, escalated=len(escalated))
            open_findings = escalated
            if escalated:
                return escalated
            problems = self.check_plan()
            if problems:
                self.log.add("plan_invalid", problems=problems)
                self.write_plan(problems)
        return open_findings or [{"level": "major", "text": f"{MAX_ROUNDS} rounds with findings"}]

    def answer_minors(self, round_no: int, minors: list, text: str, tickets: dict) -> bool:
        """Give the minor findings of a clean round to the builder, who fixes or contests
        each one. Return True when the answer changed the plan or a ticket, so the change
        needs a review round."""
        answer = self.role("builder", {"mode": "answer", "findings": minors,
                                       **self.locked_inputs()})
        self.keep_locked_tickets(round_no)
        self.log.add("plan_answered", round=round_no, cost_usd=answer.cost_usd,
                     answers=answer.data.get("answers", []), escalated=0, minor=len(minors))
        now = self.read_copy(pl.plan_path(self.id))
        try:
            ids = pl.parse(now).tickets if now is not None else []
        except pl.PlanError:
            return True
        return now != text or {t: self.read_copy(f".tollgate/tickets/{t}/ticket.md")
                               for t in ids} != tickets

    def locked_ids(self) -> list:
        """The tickets that the plan's last lock holds, in a fix round; [] before a lock."""
        locked = [e for e in read(self.log_path) if e["event"] in LOCKS]
        return list(locked[-1].get("tickets", [])) if locked else []

    def locked_inputs(self) -> dict:
        """{"locked_tickets": [...]} for an answer step in a fix round, so the builder
        knows which tickets it may not change; {} before any lock."""
        ids = self.locked_ids()
        return {"locked_tickets": ids} if ids else {}

    def keep_locked_tickets(self, round_no: int) -> None:
        """After an answer in a fix round, put back each ticket of the plan lock that the
        answer changed, with a plan_invalid event, so the plan lock does not refuse it
        late."""
        problems = self.kept_tickets_problems()
        if not problems:
            return
        commit = [e for e in read(self.log_path) if e["event"] in LOCKS][-1]["commit"]
        for tid in self.locked_ids():
            git(self.copy, "checkout", commit, "--", f".tollgate/tickets/{tid}")
        self.log.add("plan_invalid", round=round_no, problems=problems)

    def kept_tickets_problems(self) -> list:
        text = self.read_copy(pl.plan_path(self.id))
        try:
            plan = pl.parse(text) if text is not None else None
        except pl.PlanError:
            plan = None
        if plan is None:
            return []
        return self.kept_tickets(plan)

    @staticmethod
    def sent_back_hint(events: list) -> str | None:
        """The hint of a plan sent back after its last review, or None."""
        last = None
        for e in events:
            if e["event"] == "plan_sent_back":
                last = e.get("hint", "")
            elif e["event"] == "plan_review_done":
                last = None
        return last

    def approve(self, open_findings: list) -> dict:
        plan = self.read_copy(pl.plan_path(self.id))
        assumptions = pl.parse(plan).assumptions
        if "plan" not in self.settings["checkpoints"] and not open_findings and not assumptions:
            self.log.add("plan_approved", by="no checkpoint")
            return {"decision": "approve", "hint": ""}
        reason = ("The plan review is clean." if not open_findings
                  else f"{len(open_findings)} findings are open after the review.")
        if assumptions:
            reason += ("\n\nThe plan lists these assumptions. Send it back with your answers, "
                       "or approve it as it is:\n\n" + "\n".join(f"- {a}" for a in assumptions))
        for entry in self.settings["ai"]["advisor"]:
            if entry == "person":
                return advisor.ask_person(self.store, self.log, self.log_path, f"plan {self.id}",
                                          f"{reason}\n\nThe plan:\n\n{plan}",
                                          ["approve", "send_back"])
            if self.ai_decisions >= 2:
                continue
            answer = self.role("advisor", {"plan": plan, "findings": open_findings,
                                           "reason": reason}, command=entry)
            self.ai_decisions += 1
            decision = answer.data.get("decision")
            self.log.add("advisor_decided", by="ai", decision=decision, cost_usd=answer.cost_usd,
                         hint=answer.data.get("hint", ""))
            if decision in ("approve", "send_back"):
                return {"decision": decision, "hint": answer.data.get("hint", "")}
        raise Parked("no advisor decided on the plan")

    def checkpoint(self, name: str, reason: str, inputs: dict, key: str = "") -> None:
        """Ask the advisor whether the task goes on. Return when it does; raise Parked
        when the advisor stops it or a person has not answered yet. A checkpoint does
        not count against the AI advisor's limit of decisions."""
        if self.stopped_at(name, key):
            raise Parked(f"the advisor stopped the task at the {name} checkpoint")
        subject = f"task {self.id}, {name} checkpoint" + (f" after {key}" if key else "")
        decision = None
        for entry in self.settings["ai"]["advisor"]:
            if entry == "person":
                try:
                    decision = advisor.ask_person(self.store, self.log, self.log_path, subject,
                                                  reason, ["continue", "stop"])
                except advisor.Waiting as waiting:
                    raise Parked(str(waiting)) from None
                break
            answer = self.role("advisor", {"checkpoint": name, "reason": reason, **inputs},
                               command=entry)
            decision = {"decision": answer.data.get("decision"),
                        "hint": answer.data.get("hint", "")}
            self.log.add("advisor_decided", by="ai", cost_usd=answer.cost_usd, checkpoint=name,
                         **decision)
            if decision["decision"] in ("continue", "stop"):
                break
            decision = None
        if decision is None:
            raise Parked(f"no advisor decided at the {name} checkpoint")
        if decision["decision"] == "stop":
            self.log.add("checkpoint_stopped", name=name, key=key, hint=decision["hint"])
            raise Parked(f"the advisor stopped the task at the {name} checkpoint")
        self.log.add("checkpoint_passed", name=name, key=key)

    def stopped_at(self, name: str, key: str = "") -> bool:
        """A stop holds: the task stays parked at that checkpoint when it starts again."""
        return any(e["event"] == "checkpoint_stopped" and e.get("name") == name
                   and e.get("key", "") == key for e in read(self.log_path))

    def passed_checkpoint(self, name: str, key: str = "") -> bool:
        return any(e["event"] == "checkpoint_passed" and e.get("name") == name
                   and e.get("key", "") == key for e in read(self.log_path))

    def lock_plan(self, hint: str = ""):
        """Commit the plan and lock it. hint is what the advisor said with `approve`: the
        tickets of this lock are built with it (see approval_hint)."""
        from gate.gitio import show_bytes
        # The analysis goes on the plan branch as the driver checked it.
        path = Path(self.copy, an.analysis_path(self.id))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.analysis_text())
        plan = pl.parse(self.read_copy(pl.plan_path(self.id)))
        dropped = self.drop_stray_tickets(plan)
        if dropped:
            self.log.add("plan_tickets_dropped", tickets=dropped)
        # Only the plan, the tickets its header lists, and the analysis: a ticket the
        # builder wrote and then left out of the plan is no part of what was approved.
        git(self.copy, "add", "-A", "--", pl.plan_path(self.id), an.analysis_path(self.id),
            *(f".tollgate/tickets/{t}" for t in plan.tickets),
            *(f".tollgate/tickets/{t}/ticket.md" for t in dropped
              if show_bytes(self.copy, "HEAD", f".tollgate/tickets/{t}/ticket.md") is not None))
        git(self.copy, "commit", "-q", "--allow-empty", "-m", f"docs({self.id}): the plan")
        fetch_branch(self.project, self.copy, f"tg-plan/{self.id}")
        path = self.store.lock_plan(self.id)
        self.log.add("plan_locked", path=path, commit=git(self.copy, "rev-parse", "HEAD"),
                     tickets=plan.tickets, **({"hint": hint.strip()} if hint.strip() else {}))

    def drop_stray_tickets(self, plan) -> list:
        """Drop from the copy each ticket of this plan that its header does not list: a
        ticket whose `plan:` names this task, and that main does not hold as it is. A ticket
        that is not on main is deleted; one that main holds goes back to main's folder.
        Return their ids. A ticket of another plan stays. The store refuses a plan commit that
        still holds such a ticket."""
        import shutil

        from gate.gitio import show_bytes
        folder = Path(self.copy, ".tollgate/tickets")
        paths = [f".tollgate/tickets/{p.parent.name}/ticket.md"
                 for p in sorted(folder.glob("*/ticket.md"))] if folder.exists() else []

        def read(path):
            data = Path(self.copy, path).read_bytes()
            return None if show_bytes(self.copy, self.base, path) == data else data.decode(
                errors="replace")
        dropped = pl.stray_tickets(plan, read, paths)
        for tid in dropped:
            shutil.rmtree(folder / tid)
            if show_bytes(self.copy, self.base, f".tollgate/tickets/{tid}/ticket.md") is not None:
                # A ticket that main holds was only edited: it goes back to main's folder.
                git(self.copy, "checkout", self.base, "--", f".tollgate/tickets/{tid}")
        return dropped

    def approval_hint(self, tid: str) -> str:
        """The hint given with the approval of the plan lock that first listed the ticket,
        or "". The plan is locked by then, so the hint changes no ticket: it goes to the
        ticket's test writer and builder, who follow it where the ticket allows."""
        for e in read(self.log_path):
            if e["event"] == "plan_locked" and tid in e.get("tickets", []):
                return e.get("hint", "")
        return ""

    def build_tickets(self) -> dict:
        plan = pl.parse(self.read_locked(pl.plan_path(self.id)))
        done = {e["ticket"]: e["code"] for e in read(self.log_path) if e["event"] == "ticket_done"}
        start = None    # each ticket starts from the ready change before it
        for tid in plan.tickets:
            if done.get(tid) == 0:
                self.ticket_checkpoint(tid)
                start = self.ready_commit(tid) or start
                continue
            text = self.read_locked(f".tollgate/tickets/{tid}/ticket.md")
            try:
                code = Driver(self.project, tid, self.store, ticket_text=text,
                              runner=self.runner, base=start, hold=True,
                              approval_hint=self.approval_hint(tid), **self.driver_args).run()
            except Busy:
                code = 4
            except (roles.RoleError, tk.TicketError, StoreError,
                    subprocess.CalledProcessError) as err:
                self.log.add("ticket_error", ticket=tid, error=str(err)[-500:])
                code = 1
            if code == 5:
                # The ticket's role waits for your session; the task waits with it.
                raise session.Pause(session.read_step(self.project) or {"role": "role"})
            if code == 3 and self.amend_wanted(tid):
                # The advisor or a person chose amend: the plan lock gets the amended
                # ticket, and the ticket goes on with it.
                self.amend_ticket(tid, self.amend_wanted(tid))
                return self.build_tickets()
            self.log.add("ticket_done", ticket=tid, code=code)
            done[tid] = code
            if code != 0:
                # Each later ticket builds on this one, so none starts without it.
                break
            self.ticket_checkpoint(tid)
            start = self.ready_commit(tid) or start
        return done

    def amend_wanted(self, tid: str) -> dict | None:
        """The ticket's open request to amend it: its last `parked` event with the decision
        amend, when no `ticket_amended` came after it. None otherwise."""
        wanted = None
        for e in read(str(Path(self.logs, f"{tid}.jsonl"))):
            if e["event"] == "parked":
                wanted = e if e.get("decision") == "amend" else None
            elif e["event"] == "ticket_amended":
                wanted = None
        return wanted

    def amend_ticket(self, tid: str, wanted: dict) -> None:
        """The planner changes the locked ticket, and the plan if it must, in the task's
        copy, by the change the advisor or a person gave. The store locks the plan again
        with only that ticket changed. The task log gets plan_amended with the old and new
        fingerprints of the ticket and the reason; the ticket log gets ticket_amended, so
        its driver takes the new ticket. Locked tests stay as they are: only the plan and
        the ticket change here."""
        done = sum(1 for e in read(self.log_path)
                   if e["event"] == "plan_amended" and e.get("ticket") == tid)
        if done >= MAX_AMENDS:
            raise Parked(f"ticket {tid} was amended {done} times; fix it by hand")
        lock = [e for e in read(self.log_path) if e["event"] in LOCKS][-1]
        path = f".tollgate/tickets/{tid}/ticket.md"
        if git(self.copy, "rev-parse", "HEAD") != lock["commit"]:
            git(self.copy, "checkout", "-q", "-B", f"tg-plan/{self.id}", lock["commit"])
        # The ticket as the lock holds it: in session mode the planner's step replays, and
        # its edits are already in the copy.
        from gate.gitio import fingerprint, show_bytes
        old = show_bytes(self.copy, lock["commit"], path).decode()
        answer = self.role("builder", {
            "mode": "amend", "task_id": self.id, "ticket_id": tid, "ticket": old,
            "plan": self.read_copy(pl.plan_path(self.id)), "change": wanted.get("change", ""),
            "reason": wanted.get("reason", "")})
        problems = self.check_plan(amend=tid)
        if self.read_copy(path) == old:
            problems.append(f"the planner did not change ticket {tid}")
        # Only .tollgate: in a fix round the copy's work tree shows the built change.
        changed = git(self.copy, "ls-files", "-m", "-o", "-d", "--exclude-standard", "--",
                      ".tollgate").splitlines()
        allowed = (pl.plan_path(self.id), path)
        problems += [f"an amend changes only ticket {tid} and the plan, not {name}"
                     for name in sorted(set(changed)) if name not in allowed]
        try:
            if tid not in pl.parse(self.read_copy(pl.plan_path(self.id))).tickets:
                problems.append(f"the amended plan must keep ticket {tid}")
        except (pl.PlanError, TypeError):
            pass    # check_plan names it
        if problems:
            self.log.add("plan_amend_refused", ticket=tid, problems=problems,
                         cost_usd=answer.cost_usd)
            git(self.copy, "checkout", "-q", "-f", lock["commit"], "--", ".tollgate")
            git(self.copy, "clean", "-q", "-fd", "--", ".tollgate")
            raise Parked(f"the amend of ticket {tid} was refused: " + "; ".join(problems))
        git(self.copy, "add", "-A", "--", *allowed)
        git(self.copy, "commit", "-q", "-m", f"docs({self.id}): amend ticket {tid}")
        fetch_branch(self.project, self.copy, f"tg-plan/{self.id}")
        record = self.store.lock_plan(self.id, amend=tid)
        new = self.read_copy(path)
        hashes = {"old": fingerprint(old.encode()), "new": fingerprint(new.encode())}
        self.log.add("plan_amended", ticket=tid, path=record,
                     commit=git(self.copy, "rev-parse", "HEAD"), old_commit=lock["commit"],
                     tickets=lock.get("tickets", []), reason=wanted.get("reason", ""),
                     change=wanted.get("change", ""), cost_usd=answer.cost_usd, **hashes)
        with Log(str(Path(self.logs, f"{tid}.jsonl")), ticket=tid, task=self.id) as log:
            log.add("ticket_amended", change=wanted.get("change", ""), **hashes)

    def ready_commit(self, tid: str) -> str:
        from driver import history
        return history.replay(read(str(Path(self.logs, f"{tid}.jsonl")))).ready

    def ticket_checkpoint(self, tid: str) -> None:
        if "ticket" in self.settings["checkpoints"] and not self.passed_checkpoint("ticket", tid):
            self.checkpoint("ticket", f"Ticket {tid} is a ready change. Go on to the next one?",
                            {"ticket": self.read_locked(f".tollgate/tickets/{tid}/ticket.md")}, tid)

    def fix_rounds(self) -> int:
        return sum(1 for e in read(self.log_path) if e["event"] == "fix_round")

    def review_task(self) -> bool:
        """The review lens reads every ticket's change together, against the plan. Return
        True when the task may merge. A blocking or major finding in the first round
        starts the fix round instead, and returns False. After it, such a finding parks
        the task with no merge. A clean review still meets the task checkpoint."""
        round_no = self.fix_rounds()
        found = [e for e in read(self.log_path)
                 if e["event"] == "task_reviewed" and e.get("round", 0) == round_no]
        if found:
            passed, text = found[-1]["passed"], found[-1]["text"]
            findings = found[-1].get("findings", [])
        else:
            plan = pl.parse(self.read_locked(pl.plan_path(self.id)))
            parts, reviewed = [], []
            for tid in plan.tickets:
                ready = [e for e in read(str(Path(self.logs, f"{tid}.jsonl")))
                         if e["event"] == "ready"]
                lock = state.path(self.project, "locks", f"{tid}.json")
                if ready and lock.exists():
                    # From the commit the ticket started on, so a merged ticket still counts.
                    start = json.loads(lock.read_text())["base_commit"]
                    parts.append(reviewlib.diff(self.project, start, ready[-1]["commit"]))
                    reviewed.append(tid)
            change = "\n".join(parts)[:reviewlib.DIFF_LIMIT]
            cost = []

            def run_role(lens, inputs):
                answer = self.role("reviewer", inputs, lens=lens)
                cost.append(answer.cost_usd)
                return answer.data

            passed, findings, text = reviewlib.review(
                TASK_TIER, self.read_locked(pl.plan_path(self.id)), change, run_role,
                lenses=("review",))
            self.log.add("task_reviewed", round=round_no, passed=passed, findings=findings,
                         text=text, tickets=reviewed, cost_usd=roles.add_cost(*cost))
        open_findings = [f for f in findings if f.get("level") in OPEN_LEVELS]
        if not passed or open_findings:
            if round_no < FIX_ROUNDS:
                self.amend_plan(open_findings or findings)
                return False
            # The merges are held until the review passes: no advisor lets them through.
            raise Parked("the review of the whole task still finds problems after the fix "
                         "round, so nothing merges. Read the report, then fix it by hand or "
                         "start a new task")
        if "task" in self.settings["checkpoints"] and not self.passed_checkpoint("task"):
            self.checkpoint("task", f"The review of the whole task:\n\n{text}", {"review": text})
        return True

    def show_built(self) -> None:
        """Put the change the tickets built in the copy's work tree, from the last ready
        change of the chain, where the next ticket starts. Only the work tree changes,
        and .tollgate/ stays as the plan has it, so the plan branch still holds only the
        plan and its tickets."""
        plan = pl.parse(self.read_locked(pl.plan_path(self.id)))
        ready = [c for c in (self.ready_commit(t) for t in plan.tickets) if c]
        if not ready:
            return
        git(self.copy, "fetch", "-q", self.project, "+refs/heads/tg/*:refs/tollgate/tickets/*")
        git(self.copy, "restore", f"--source={ready[-1]}", "--worktree", "--", ".",
            ":(exclude).tollgate")

    def amend_plan(self, findings: list) -> None:
        """The fix round: the builder changes the plan, with the findings of the review of
        the whole task as its hint; it keeps each locked ticket and adds tickets for the
        fix. Then the plan is reviewed, approved, and locked again. Each part is logged,
        so a task that starts again goes on where it stopped."""
        events = read(self.log_path)
        kinds = {e["event"] for e in events}
        hint = ("The review of the whole task found these problems. Keep every ticket of the "
                "plan as it is, and add the tickets that fix them, each with a locked test:\n"
                + "\n".join(f"- {f.get('level')}: {f.get('text', '')}"
                            + (f" ({f['file']}:{f.get('line', '')})" if f.get("file") else "")
                            for f in findings))
        if "fix_plan_reviewed" not in kinds:
            if "fix_plan_written" not in kinds:
                self.show_built()
                self.write_plan(hint=hint)
                self.log.add("fix_plan_written", findings=findings)
            open_findings = self.review_plan(fix=hint)
            self.log.add("fix_plan_reviewed", open=open_findings)
        else:
            open_findings = [e for e in events if e["event"] == "fix_plan_reviewed"][-1]["open"]
        try:
            decision = self.approve(open_findings)
        except advisor.Waiting as waiting:
            raise Parked(str(waiting)) from None
        if decision["decision"] != "approve":
            self.log.add("plan_sent_back", hint=decision.get("hint", ""))
            raise Parked("the plan of the fix round was sent back")
        self.lock_plan(decision.get("hint", ""))
        self.log.add("fix_round", round=self.fix_rounds() + 1, findings=findings)

    def merge_ready(self) -> list:
        """Merge each ticket's ready change in plan order, once the review of the whole task
        passed. Stop at the first that may not merge: auto merge off, tier 3 or 4, or a
        merge the store refuses. Each later ticket holds the changes before it, so none
        may merge past it. Return the tickets merged."""
        plan = pl.parse(self.read_locked(pl.plan_path(self.id)))
        merged = []
        for tid in plan.tickets:
            path = str(Path(self.logs, f"{tid}.jsonl"))
            events = read(path)
            if any(e["event"] == "merged" for e in events):
                continue
            from driver import history
            t = history.replay(events)
            if not t.ready:
                return merged
            if not self.settings["auto_merge"] or t.tier is None or t.tier >= 3:
                return merged
            with Log(path, ticket=tid, task=self.id) as log:
                try:
                    merge = self.store.merge(t.ready)
                except StoreError as err:
                    log.add("waiting_for_merge", branch=f"tg/{tid}", commit=t.ready, tier=t.tier,
                            reason=f"the store did not merge: {err}")
                    return merged
                log.add("merged", commit=t.ready, merge=merge)
            roles.remove_copy(self.project, tid)
            merged.append(tid)
        return merged

    def run(self) -> int:
        problem = session.cannot_pass(self.project)
        if problem:
            raise session.NotReady(problem)
        lock = run_lock(self.project, f"task-{self.id}")
        lock.acquire()
        try:
            # Only the holder of the run lock clears the step: a second next that finds
            # the task busy leaves the waiting step alone.
            session.clear_step(self.project)
            events = read(self.log_path)
            kinds = {e["event"] for e in events}
            self.ai_decisions = sum(1 for e in events if e["event"] == "advisor_decided"
                                    and e.get("by") == "ai" and "checkpoint" not in e)
            with Log(self.log_path, task=self.id) as self.log:
                self.copy = roles.make_copy(self.project, f"task-{self.id}")
                self.settings = st.load_from_ref(self.copy, self.base)
                self.caller = session.caller(self.settings, self.runner)
                if self.runner is None:
                    self.runner = self.settings["runner"]
                in_session = isinstance(self.caller, session.Session)
                if in_session:
                    # Session mode replays the step it stopped in: log each event once.
                    self.log.hold_while(lambda: session.replaying(self.project))
                if not (in_session and events):
                    self.log.add("started")
                if "plan_locked" not in kinds:
                    if "plan_review_done" not in kinds:
                        # A plan the driver sent back is written again: the last of
                        # plan_written and plan_invalid tells which came last. Session
                        # mode always replays the writing too: its saved answers put back
                        # the plan that each saved review read, so the review's rounds
                        # count up to MAX_ROUNDS. Without it each run would start the
                        # review at round 1 with the newest plan, and never reach the cap.
                        writes = [e["event"] for e in events
                                  if e["event"] in ("plan_written", "plan_invalid")]
                        if not writes or writes[-1] == "plan_invalid" or in_session:
                            if "analysis_written" not in kinds:
                                git(self.copy, "checkout", "-q", "-B", f"tg-plan/{self.id}",
                                    self.base)
                                self.analyze()
                            self.write_plan()
                        open_findings = self.review_plan()
                        self.log.add("plan_review_done", open=open_findings)
                    elif self.sent_back_hint(events) is not None:
                        # A plan sent back after its last review is written again with
                        # the hint and reviewed, before anyone decides on it. A session
                        # run that paused in the builder or a reviewer starts here again.
                        self.write_plan(hint=self.sent_back_hint(events))
                        open_findings = self.review_plan()
                        self.log.add("plan_review_done", open=open_findings)
                    else:
                        open_findings = [e for e in events
                                         if e["event"] == "plan_review_done"][-1]["open"]
                    try:
                        decision = self.approve(open_findings)
                    except advisor.Waiting as waiting:
                        raise Parked(str(waiting)) from None
                    if decision["decision"] != "approve":
                        self.log.add("plan_sent_back", hint=decision.get("hint", ""))
                        self.write_plan(hint=decision.get("hint", ""))
                        self.log.add("plan_review_done", open=self.review_plan())
                        raise Parked("the plan was sent back and needs a new approval")
                    self.lock_plan(decision.get("hint", ""))
                while True:
                    done = self.build_tickets()
                    if not done or not all(c == 0 for c in done.values()):
                        break
                    if self.review_task():
                        self.merge_ready()
                        break
                session.end_replay(self.project)
                path = report.write(self.project, self.id)
                self.log.add("reported", path=path, tickets=done)
                return 0 if all(c == 0 for c in done.values()) else 3
        except session.Pause:
            return 5
        except Parked as parked:
            session.end_replay(self.project)
            with Log(self.log_path, task=self.id) as log:
                log.add("task_waiting", reason=str(parked))
            report.write(self.project, self.id)
            return 3
        finally:
            lock.release()


def _brief(request: str, task_id: str):
    """The brief, when the request is one, or None. A brief in a bad format, or one that
    needs a roadmap, stops the task with what to do."""
    if not bf.is_brief(request):
        return None
    try:
        found = bf.parse(request)
    except bf.BriefError as err:
        raise pl.PlanError(f"the request is a brief, but it is not valid: {err}") from None
    if found.size == "roadmap":
        raise pl.PlanError(f"brief {found.id} says the idea is too big for one task: it needs a "
                           f"roadmap. Write .tollgate/roadmaps/{task_id}.md as step 3 of "
                           "/tollgate:task says, with the brief as its request")
    return found


def main(argv=None, store=None) -> int:
    parser = argparse.ArgumentParser(prog="tollgate task", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=".")
    parser.add_argument("--task", required=True)
    parser.add_argument("--request", help="the request, in your own words")
    parser.add_argument("--request-file")
    parser.add_argument("--brief", help="the id of a brief in .tollgate/briefs/, as the request")
    args = parser.parse_args(argv)
    if args.brief:
        try:
            bf.load(args.project, args.brief)
        except bf.BriefError as err:
            print(f"tollgate task: {err}", file=sys.stderr)
            return 1
        args.request_file = str(Path(args.project, bf.brief_path(args.brief)))
    request = args.request or (Path(args.request_file).read_text() if args.request_file else "")
    if not request.strip():
        print("tollgate task: give --request or --request-file", file=sys.stderr)
        return 1
    try:
        return Task(args.project, args.task, request, store).run()
    except Busy as err:
        print(err, file=sys.stderr)
        return 4
    except (roles.RoleError, pl.PlanError, st.SettingsError, StoreError, OSError,
            subprocess.CalledProcessError) as err:
        print(f"tollgate task: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

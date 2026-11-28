"""Session mode, step by step: your own Claude Code session runs each role.

    PYTHONPATH=<tollgate> python3 -P -m driver.step next --project . --task <task id>
    PYTHONPATH=<tollgate> python3 -P -m driver.step next --project . --brainstorm <id>
    PYTHONPATH=<tollgate> python3 -P -m driver.step next --project . --roadmap <id>
    PYTHONPATH=<tollgate> python3 -P -m driver.step answer --project . --key <key> --nonce <nonce> < answer.md

`next` runs the task's driver in session mode, from its log and the saved
answers, up to the next role. It prints one JSON object:

- a step: {"agent": "tollgate:<role>", "prompt": ..., "cwd": ..., "key": ...,
  "nonce": ...}. Start that subagent in that folder with that prompt, then pipe
  its whole final answer to `answer` with that key and that nonce. The nonce is
  new for each `next`, and the step file keeps only its hash, so only the session
  that ran `next` can answer.
- steps that run at once: {"steps": [step, ...]}, each step as above, with its own
  key and nonce. These are roles that do not depend on one another, such as the
  lenses that review one plan or one change. Start their subagents in parallel and
  answer each one. Each answer but the last prints {"saved": <key>, "waiting":
  [keys still due]}; the last one runs `next` and prints what it prints.
- the end: {"done": true, "result": <done and still to do>, "merged": [tickets],
  "report": <path>}.
- a question for a person: {"question": {"n", "title", "body", "options"}}.
  Record the person's answer with setup.inbox, then run `next` again.
- a ticket parked with no question: {"parked": <the reason>}.
- an error: {"error": <the reason>}, and the command ends with 1.

`answer` refuses a key or a nonce that is not the waiting step's, and never
says what they are; and an answer with no final json block. The first such answer of
a step prints the same step again, with its key and nonce and a `retry` reason, for
a subagent that ran out of turns; the second is an error. A role cannot run
this command: the hook refuses any Tollgate command from a role. It saves the answer, then runs `next` and prints what
`next` prints. The request of the task is .tollgate/requests/<task id>.md.

With --brainstorm, `next` runs a brainstorm instead (see driver/brainstorm.py): its
idea is .tollgate/ideas/<id>.md, and its end is {"done": true, "brief": <path>,
"size": "task" or "roadmap", "open_questions": <count>}. `answer` goes on with the
brainstorm or the task that the waiting step belongs to.

With --roadmap, `next` runs a roadmap in session mode (see driver/roadmap.py): its
file is .tollgate/roadmaps/<id>.md. It freezes the checklist's tests, runs each
task in order, checks the base suite after the base tasks, and goes on to the next
task when one is done. Each step is printed as for a task. The step file notes the
roadmap, so `answer` goes on with the roadmap. Its end is {"done": true,
"roadmap": <id>, "tasks": [task ids], "result": <done and still to do>,
"merged": [tickets], "reports": [paths]}. A base suite that fails, or a task that
does not finish, ends with {"parked": <the reason>} or a question.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from driver import advisor, history, report, roles, session
from driver import roadmap as roadmap_driver
from driver.brainstorm import Brainstorm
from driver.driver import Busy, run_lock
from driver.log import read
from driver.task import Task
from gate import brief as bf
from gate import manifest as mf
from gate import plan as pl
from gate import roadmap as rm
from gate import settings as st
from gate import state, tickets
from gate.store import Store, StoreError


def _request(project: str, task: str) -> str:
    path = Path(project, ".tollgate", "requests", f"{task}.md")
    if not path.exists():
        raise session.NotReady(f"the request is missing: write it to {path}")
    return path.read_text()


def _question(project: str, task: str, store) -> dict | None:
    """The open question of the task or of one of its tickets, or None."""
    logs = state.path(project, "logs")
    for path in [logs / f"task-{task}.jsonl", *history.logs(logs)]:
        events = read(str(path))
        if path.name.startswith("task-") and path.name != f"task-{task}.jsonl":
            continue
        if not path.name.startswith("task-") and not any(e.get("task") == task for e in events):
            continue
        found = history.open_question(events)
        if found:
            q = advisor.question(store, found["question"]) or {}
            return {"n": found["question"], "title": q.get("title", ""),
                    "body": q.get("body", found["reason"]), "options": q.get("options", [])}
    return None


def _merged(project: str, task: str) -> list:
    out = []
    for path in history.logs(state.path(project, "logs")):
        if path.name.startswith(("task-", "roadmap-")):
            continue
        events = read(str(path))
        if any(e["event"] == "merged" and e.get("task") == task for e in events):
            out.append(events[0].get("ticket", path.stem))
    return out


def _printed(project: str, step: dict, nonce: str) -> dict:
    return {"agent": step.get("agent", ""), "prompt": step["prompt"],
            "cwd": step.get("copy") or project, "key": step["key"], "nonce": nonce}


def _paused(project: str, lock_name: str, roadmap: str = "") -> tuple:
    """The steps that wait in the run, each with a new nonce. Return (what to print, exit
    code): one step as it is, or several that can run at once as {"steps": [...]}.
    roadmap names the roadmap the steps belong to, so answer goes on with it."""
    # The nonces go in under the run lock, so a second next cannot interleave.
    lock = run_lock(project, lock_name)
    try:
        lock.acquire()
    except Busy as err:
        return {"error": str(err)}, 1
    try:
        steps = session.read_steps(project)
        if not steps:
            return {"error": "the driver paused, but no step file is there; run next "
                             "again"}, 1
        # The replay put the work of earlier answers back in the copy: a writer that
        # waits again gets back the edits it made before this next.
        session.put_back_waiting_work(project, steps)
        if roadmap:
            session.note_roadmap(project, roadmap)
            steps = session.read_steps(project)
        printed = [_printed(project, step, session.new_nonce(project, step)) for step in steps]
    finally:
        lock.release()
    return (printed[0] if len(printed) == 1 else {"steps": printed}), 0


def _keep(project: str, lock_name: str) -> dict | None:
    """Under the run lock, save the work of each writer that waits in the run, before
    the replay changes its copy. Return the error to print when the run is busy."""
    lock = run_lock(project, lock_name)
    try:
        lock.acquire()
    except Busy as err:
        return {"error": str(err)}
    try:
        session.keep_waiting_work(project)
    finally:
        lock.release()
    return None


def _ended(project: str, out: tuple) -> tuple:
    """A run that ended with no pause and no error waits at no writer any more."""
    if out[1] == 0 and "error" not in out[0]:
        session.drop_kept(str(Path(project).resolve()))
    return out


def next_brainstorm(project: str, brainstorm: str, store=None) -> tuple:
    """Run the brainstorm to its next step. Return (what to print, exit code)."""
    with session.in_slot(session.slot_of(brainstorm=brainstorm)):
        return _ended(project, _next_brainstorm(project, brainstorm, store))


def _next_brainstorm(project: str, brainstorm: str, store=None) -> tuple:
    project = str(Path(project).resolve())
    problem = session.cannot_pass(project)
    if problem:
        return {"error": problem}, 1
    store = store or Store(project)
    path = Path(project, bf.idea_path(brainstorm))
    if not bf.ID.match(brainstorm) or not path.exists():
        return {"error": f"the idea is missing: write it to {bf.idea_path(brainstorm)}"}, 1
    try:
        busy = _keep(project, f"brainstorm-{brainstorm}")
        if busy:
            return busy, 1
        code = Brainstorm(project, brainstorm, path.read_text(), store, runner="session").run()
    except Busy as err:
        return {"error": str(err)}, 1
    except (roles.RoleError, bf.BriefError, st.SettingsError, StoreError, OSError,
            subprocess.CalledProcessError) as err:
        return {"error": str(err)}, 1
    if code == 5:
        return _paused(project, f"brainstorm-{brainstorm}")
    events = read(str(state.path(project, "logs", f"brainstorm-{brainstorm}.jsonl")))
    if code == 0:
        written = [e for e in events if e["event"] == "brief_written"][-1]
        return {"done": True, "brief": str(Path(project, bf.brief_path(brainstorm))),
                "size": written["size"], "open_questions": written["open_questions"]}, 0
    found = history.open_question(events)
    if found:
        q = advisor.question(store, found["question"]) or {}
        return {"question": {"n": found["question"], "title": q.get("title", ""),
                             "body": q.get("body", found["reason"]),
                             "options": q.get("options", [])}}, 0
    waiting = [e for e in events if e["event"] == "brainstorm_waiting"]
    return {"parked": waiting[-1]["reason"] if waiting else "the brainstorm waits"}, 0


def next_step(project: str, task: str, store=None) -> tuple:
    """Run the task to its next step. Return (what to print, exit code)."""
    with session.in_slot(session.slot_of(task=task)):
        return _ended(project, _next_step(project, task, store))


def _next_step(project: str, task: str, store=None) -> tuple:
    project = str(Path(project).resolve())
    problem = session.cannot_pass(project)
    if problem:
        return {"error": problem}, 1
    store = store or Store(project)
    try:
        request = _request(project, task)
        busy = _keep(project, f"task-{task}")
        if busy:
            return busy, 1
        code = Task(project, task, request, store, runner="session").run()
    except Busy as err:
        return {"error": str(err)}, 1
    except (roles.RoleError, pl.PlanError, st.SettingsError, StoreError, OSError,
            subprocess.CalledProcessError) as err:
        return {"error": str(err)}, 1
    if code == 5:
        return _paused(project, f"task-{task}")
    if code == 0:
        return {"done": True, "result": report.brief(project, task),
                "merged": _merged(project, task),
                "report": str(state.path(project, "reports", f"{task}.md"))}, 0
    question = _question(project, task, store)
    if question:
        return {"question": question}, 0
    if code == 3:
        waiting = report.still_waiting(read(str(state.path(project, "logs",
                                                           f"task-{task}.jsonl"))))
        parked = [f"{r['ticket']}: {r['reason']}" for r in report.rows(project, task)
                  if r["state"] == "parked"]
        return {"parked": waiting[-1]["reason"] if waiting else
                "; ".join(parked) or "the task waits"}, 0
    return {"error": f"the driver ended with {code}"}, 1


def _checklist_task(project: str, checklist: str) -> str:
    """The task the checklist ticket's events name: its plan, or else its own id."""
    try:
        text = Path(project, mf.ticket_path(checklist)).read_text()
        return tickets.parse(text).plan or checklist
    except (OSError, tickets.TicketError):
        return checklist


def next_roadmap(project: str, roadmap_id: str, store=None) -> tuple:
    """Run the roadmap in session mode to its next step. Return (what to print, exit code)."""
    with session.in_slot(session.slot_of(roadmap=roadmap_id)):
        return _ended(project, _next_roadmap(project, roadmap_id, store))


def _next_roadmap(project: str, roadmap_id: str, store=None) -> tuple:
    project = str(Path(project).resolve())
    problem = session.cannot_pass(project)
    if problem:
        return {"error": problem}, 1
    path = Path(project, rm.roadmap_path(roadmap_id))
    if not pl.ID.match(roadmap_id) or not path.exists():
        return {"error": f"the roadmap is missing: write it to {rm.roadmap_path(roadmap_id)}"}, 1
    store = store or Store(project)
    try:
        found = rm.parse(path.read_text())
        busy = _keep(project, f"roadmap-{roadmap_id}")
        if busy:
            return busy, 1
        code = roadmap_driver.run(project, roadmap_id, store, runner="session")
    except Busy as err:
        return {"error": str(err)}, 1
    except (roles.RoleError, pl.PlanError, rm.RoadmapError, st.SettingsError, StoreError,
            OSError, subprocess.CalledProcessError) as err:
        return {"error": str(err)}, 1
    if code == roadmap_driver.PAUSED:
        return _paused(project, f"roadmap-{roadmap_id}", roadmap=roadmap_id)
    task_ids = [task_id for _, task_id, _ in found.tasks]
    if code == 0:
        return {"done": True, "roadmap": roadmap_id, "tasks": task_ids,
                "result": report.brief(project),
                "merged": [t for task_id in task_ids for t in _merged(project, task_id)],
                "reports": [str(state.path(project, "reports", f"{task_id}.md"))
                            for task_id in task_ids]}, 0
    events = read(str(state.path(project, "logs", f"roadmap-{roadmap_id}.jsonl")))
    # The task that stopped the roadmap, or else the checklist, may wait for a person.
    stopped = [e["task"] for e in events if e["event"] == "task_done" and e.get("code")]
    for task_id in [*stopped[-1:], _checklist_task(project, found.checklist)]:
        question = _question(project, task_id, store)
        if question:
            return {"question": question}, 0
    waiting = [e for e in events if e["event"] == "roadmap_waiting"]
    reason = waiting[-1].get("reason") if waiting else ""
    return {"parked": reason or "the roadmap waits"}, 0


def answer(project: str, found_key: str, nonce: str, text: str, store=None) -> tuple:
    """Save the answer to the waiting step, then run next. Return (what to print, exit code)."""
    import hmac
    project = str(Path(project).resolve())
    if not session.all_steps(project):
        return {"error": "no step waits for an answer; run next"}, 1
    # The key finds the step, in whichever run waits for it; the nonce proves that this
    # session is the one that ran next. Each step is tried, so the time does not say
    # which keys wait.
    step = None
    for waiting in session.all_steps(project):
        wanted = str(waiting.get("nonce_sha256") or "")
        if (hmac.compare_digest(found_key, waiting["key"]) and wanted
                and hmac.compare_digest(session.digest(nonce), wanted)):
            step = waiting
    if step is None:
        return {"error": "the key or the nonce is not the ones next printed for the step "
                         "that waits. Run next, and answer with what it prints."}, 1
    try:
        session.save_answer(project, step, text)
    except roles.RoleError as err:
        if int(step.get("refused") or 0) < session.MAX_REFUSED:
            # A subagent that ran out of turns ends with a part of its answer. The same
            # step runs once more, with the same key and nonce.
            session.note_refused(project, step)
            return {"agent": step.get("agent", ""), "prompt": step["prompt"],
                    "cwd": step.get("copy") or project, "key": step["key"], "nonce": nonce,
                    "retry": f"the answer was not saved: {err}. The subagent may have run out "
                             "of turns. Start it again; it must end its answer with a "
                             "```json block."}, 0
        return {"error": f"the answer was not saved: {err}. The role must end its answer "
                         "with a ```json block."}, 1
    except (OSError, subprocess.CalledProcessError) as err:
        return {"error": f"the answer was not saved: {err}"}, 1
    others = [s for s in session.all_steps(project)
              if s.get("slot", "") == step.get("slot", "") and s["key"] != step["key"]
              and s.get("_path") != step.get("_path")]
    if others:
        # Steps that run at once: the run goes on once the last of them is answered.
        return {"saved": step["key"], "waiting": [s["key"] for s in others]}, 0
    if isinstance(step.get("roadmap"), str) and step["roadmap"]:
        return next_roadmap(project, step["roadmap"], store)
    if step["log"] == f"brainstorm-{step['task']}":
        return next_brainstorm(project, step["task"], store)
    return next_step(project, step["task"], store)


def main(argv=None, store=None, stdin=None) -> int:
    parser = argparse.ArgumentParser(prog="tollgate step", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    found = sub.add_parser("next", help="run the task to its next step")
    found.add_argument("--project", default=".")
    which = found.add_mutually_exclusive_group(required=True)
    which.add_argument("--task")
    which.add_argument("--brainstorm")
    which.add_argument("--roadmap")
    given = sub.add_parser("answer", help="save the answer to the waiting step (on stdin)")
    given.add_argument("--project", default=".")
    given.add_argument("--key", required=True)
    given.add_argument("--nonce", required=True)
    args = parser.parse_args(argv)
    if args.command == "next" and args.brainstorm:
        out, code = next_brainstorm(args.project, args.brainstorm, store)
    elif args.command == "next" and args.roadmap:
        out, code = next_roadmap(args.project, args.roadmap, store)
    elif args.command == "next":
        out, code = next_step(args.project, args.task, store)
    else:
        out, code = answer(args.project, args.key, args.nonce, (stdin or sys.stdin).read(),
                           store)
    print(json.dumps(out, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())

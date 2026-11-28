"""Who runs a role: the background driver, or your own Claude Code session.

The driver asks for each role through one caller. Python still decides every
step, the gate still scores, and the store still signs and merges; only the
place where the role runs differs.

- Background: the driver starts the role as its own process, in its isolation,
  with driver/roles.py. This is the background driver.
- Session: your own Claude Code session starts the role as one of Tollgate's
  subagents. The caller first looks for a saved answer to the same question:
  the same role, lens, and inputs. If there is one, it returns it. If not, it
  writes a step file, run/steps/<run>/<key>.json in the state folder, and raises
  Pause. <run> names the task, roadmap, or brainstorm that driver.step runs, so
  several of them can wait at once.
  The driver then lets go of its run lock and ends with 5. `driver.step answer`
  saves the answer, and the next run of the driver replays its log and the
  saved answers up to the next step.

Each saved answer carries a code made with the machine secret, and a saved answer
whose code does not verify is ignored. A role cannot read the key folder, so a line a role writes into the answers file counts for nothing.

A role that writes, the builder or the test writer, leaves its work in the
copy. Its answer saves that work as a commit on refs/tollgate/answers/<key> in
the copy, and a replay puts the work back, because each try starts again from
the best try so far.

A run replays the step it stopped in, and each event of that part was logged by
the run before, which went at least as far. So when a run pauses, it notes the
key of the role it paused at in its run's replay note, run/replays/<run>. The next run's logs hold each event
until the run reaches that role again: once it gets that role's answer, or
pauses at any role with no saved answer, it writes each event, whatever the
event holds. A held event is written then, or when its log closes, unless the
log file already has it, so a run that never reaches that role, because the path
changed, still logs what is new, such as a question to a person. A task that
ends without a pause clears the note.
"""

import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from driver import roles
from gate import settings as st
from gate import state
from gate.gitio import SAFE, safe_env

WRITERS = ("builder", "test_writer")
ANSWERS = ".answers.jsonl"
INIT = "Run /tollgate:init in this project first."


class Pause(Exception):
    """A role waits for your session. The step file says which role and with what."""

    def __init__(self, step: dict):
        super().__init__(f"the {step['role']} waits for your session")
        self.step = step


class NotReady(st.SettingsError):
    """A task cannot pass the gate here: the settings on main cannot be read."""


@dataclass
class Call:
    """One role the driver needs. log names the log of the ticket or the task."""
    role: str
    inputs: dict
    lens: str | None
    command: str
    policy: str
    copy: str
    project: str
    log: str
    ticket: str = ""
    task: str = ""
    # What the role judges that its inputs do not hold, such as work_tree(copy) for
    # a reviewer that reads the files of the copy. Only the key holds it.
    content: str = ""


def key(role: str, lens: str | None, inputs: dict, content: str = "") -> str:
    """The name of one question to a role: the same role, lens, inputs, and content give
    the same key. With no content, the key is the one of the role, lens, and inputs."""
    parts = [role, lens or "", inputs] + ([content] if content else [])
    data = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


# The run whose steps this process writes and reads: a task, a roadmap, or a brainstorm,
# such as "task-retries". driver.step sets it, so two runs of one project can each wait
# at their own steps at the same time. "" is a run with no name, as in a direct run.
_SLOT = [""]


@contextmanager
def in_slot(name: str):
    """Write and read the steps and the replay note of the run `name` while inside."""
    old, _SLOT[0] = _SLOT[0], name
    try:
        yield
    finally:
        _SLOT[0] = old


def current_slot() -> str:
    return _SLOT[0]


def slot_of(task: str = "", roadmap: str = "", brainstorm: str = "") -> str:
    """The name of a run, for in_slot."""
    if roadmap:
        return f"roadmap-{roadmap}"
    if brainstorm:
        return f"brainstorm-{brainstorm}"
    return f"task-{task}" if task else ""


def _slot_name(slot: str | None) -> str:
    name = current_slot() if slot is None else slot
    if name and not SLOT_NAME.match(name):
        raise roles.RoleError(f"not a valid run name: {name!r}")
    return name


def replay_path(project: str, slot: str | None = None) -> Path:
    name = _slot_name(slot)
    if not name:
        return state.path(project, "run", "replay")
    folder = state.path(project, "run", "replays")
    folder.mkdir(exist_ok=True)
    return folder / name


def replaying(project: str) -> bool:
    """True while this run replays what the run before it already logged."""
    return replay_path(project).exists()


def end_replay(project: str) -> None:
    replay_path(project).unlink(missing_ok=True)


def _mark_replay(project: str, found_key: str) -> None:
    replay_path(project).write_text(found_key + "\n")


def _replay_key(project: str) -> str:
    try:
        return replay_path(project).read_text().strip()
    except OSError:
        return ""


def steps_dir(project: str, slot: str | None = None) -> Path:
    """The folder of the steps that wait in one run: run/steps/<run>/, one file a step."""
    folder = state.path(project, "run", "steps") / (_slot_name(slot) or "default")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def legacy_step_path(project: str) -> Path:
    """Where an older Tollgate kept the one step of a project. answer still reads it."""
    return state.path(project, "run", "step.json")


def step_path(project: str, found_key: str = "", slot: str | None = None) -> Path:
    """The file of one waiting step, by its key. With no key: the file of the only step
    that waits in the run, or the legacy file."""
    if found_key:
        return steps_dir(project, slot) / f"{found_key}.json"
    found = read_step(project, slot)
    return Path(found["_path"]) if found else legacy_step_path(project)


def answers_path(project: str, log: str) -> Path:
    return state.path(project, "logs", f"{log}{ANSWERS}")


STEP_FIELDS = ("role", "key", "log", "task", "prompt")
SLOT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def _load(path: Path) -> dict | None:
    try:
        found = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict) or not all(isinstance(found.get(f), str) and found.get(f)
                                              for f in STEP_FIELDS if f != "task"):
        return None
    if not isinstance(found.get("task", ""), str) or not isinstance(found.get("copy", ""), str):
        return None
    found["_path"] = str(path)
    return found


def read_steps(project: str, slot: str | None = None) -> list:
    """The steps that wait in one run, in the order the run asked for them."""
    found = [s for s in (_load(p) for p in steps_dir(project, slot).glob("*.json")) if s]
    return sorted(found, key=lambda s: (int(s.get("n") or 0), s["key"]))


def all_steps(project: str) -> list:
    """The steps that wait in every run of the project, and the legacy one."""
    root = state.path(project, "run", "steps")
    found = [s for s in (_load(p) for p in sorted(root.glob("*/*.json"))) if s]
    legacy = _load(legacy_step_path(project))
    return found + ([legacy] if legacy else [])


def read_step(project: str, slot: str | None = None) -> dict | None:
    """The first step that waits in the run, or None. With no run named here or set, it
    is the only step that waits in the project, so an older call still works when one
    step waits."""
    found = read_steps(project, slot)
    if found:
        return found[0]
    if _slot_name(slot) == "":
        every = all_steps(project)
        return every[0] if len(every) == 1 else None
    return None


def find_step(project: str, found_key: str) -> list:
    """Every waiting step with this key, in any run."""
    return [s for s in all_steps(project) if s["key"] == found_key]


def _write_step(project: str, step: dict) -> None:
    path = Path(step["_path"]) if step.get("_path") else step_path(project, step["key"],
                                                                     step.get("slot", ""))
    data = {k: v for k, v in step.items() if k != "_path"}
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1) + "\n")
    os.replace(tmp, path)
    step["_path"] = str(path)


MAX_REFUSED = 1   # the answers with no final json block a step may get before it is an error


def note_refused(project: str, step: dict) -> int:
    """Count one answer refused for the step that waits, in its step file. Return the
    count. A role that runs out of turns ends with a part of its answer and no json
    block; the step then runs once more before the refusal is an error."""
    step["refused"] = int(step.get("refused") or 0) + 1
    _write_step(project, step)
    return step["refused"]


def digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def new_nonce(project: str, step: dict | None = None) -> str:
    """A new nonce for a step that waits, the run's first by default. Only its hash goes
    in the step file; the nonce itself goes to your session in next's output, and answer
    needs it."""
    step = step or read_step(project)
    if step is None:
        raise roles.RoleError("no step waits")
    nonce = secrets.token_hex(16)
    step["nonce_sha256"] = digest(nonce)
    _write_step(project, step)
    return nonce


def note_roadmap(project: str, roadmap: str) -> None:
    """Note in each step that waits in the run that it belongs to this roadmap, so that
    answer goes on with the roadmap, not only with the step's task."""
    found = read_steps(project)
    if not found:
        raise roles.RoleError("no step waits")
    for step in found:
        step["roadmap"] = roadmap
        _write_step(project, step)


def clear_step(project: str, slot: str | None = None) -> None:
    """Remove every step that waits in the run."""
    for step in read_steps(project, slot):
        Path(step["_path"]).unlink(missing_ok=True)
    if _slot_name(slot) == "":
        legacy_step_path(project).unlink(missing_ok=True)


def remove_step(step: dict) -> None:
    Path(step["_path"]).unlink(missing_ok=True)


def _mac(line: dict) -> str:
    """A code over a saved answer, made with the machine secret. A role
    cannot read the key folder, so it cannot make a saved answer that verifies."""
    import hmac
    secret = hashlib.sha256(b"tollgate-answers\0" + state.machine_secret()).digest()
    data = json.dumps([line.get(k) for k in ("key", "role", "lens", "seconds", "work", "text")],
                      separators=(",", ":"))
    return hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()


def _verifies(line: dict) -> bool:
    import hmac
    try:
        return isinstance(line.get("mac"), str) and hmac.compare_digest(line["mac"], _mac(line))
    except OSError:
        return False


def saved(project: str, log: str, found_key: str) -> dict | None:
    """The last saved answer with this key that verifies, or None."""
    from driver.log import read
    found = [a for a in read(str(answers_path(project, log)))
             if a.get("key") == found_key and _verifies(a)]
    return found[-1] if found else None


def _git(cwd: str, *args, env=None) -> str:
    return subprocess.run(["git", *SAFE, *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env=safe_env(env)).stdout.strip()


def work_ref(found_key: str) -> str:
    return f"refs/tollgate/answers/{found_key}"


def work_tree(copy: str) -> str:
    """The git tree of the copy's work tree, with each file git does not ignore, in any
    language. The same files give the same tree. HEAD, the index, and the work tree do
    not change."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp, "index"))}
        _git(copy, "read-tree", "HEAD", env=env)
        _git(copy, "add", "-A", env=env)
        return _git(copy, "write-tree", env=env)


def save_work(copy: str, found_key: str) -> str:
    """Save the copy's work tree as a commit on work_ref(key), with HEAD as its parent.
    HEAD, the index, and the work tree do not change. Return the commit."""
    tree = work_tree(copy)
    commit = _git(copy, "commit-tree", tree, "-p", "HEAD", "-m",
                  f"tollgate: the work of the answer {found_key[:12]}")
    _git(copy, "update-ref", work_ref(found_key), commit)
    return commit


def restore_work(copy: str, commit: str) -> bool:
    """Put the saved work back in the copy's work tree, on top of HEAD. commit is the
    one the saved answer names, not the ref, which a role in the copy could move.
    False when the copy holds no such commit, for example a copy made again."""
    ref = commit
    done = subprocess.run(["git", *SAFE, "rev-parse", "-q", "--verify", f"{ref}^{{tree}}"],
                          cwd=copy, env=safe_env(),
                          capture_output=True, text=True, check=False)
    if done.returncode:
        return False
    _git(copy, "read-tree", "--reset", "-u", ref)
    _git(copy, "reset", "-q")
    return True


def save_answer(project: str, step: dict, text: str) -> dict:
    """Save your session's answer to the step. Raise roles.RoleError when the answer
    has no final json block. The step file goes once the answer is saved."""
    roles.last_block(text)
    work = ""
    if step["role"] in WRITERS and step.get("copy"):
        work = save_work(step["copy"], step["key"])
    line = {"key": step["key"], "role": step["role"], "lens": step.get("lens"),
            "seconds": round(max(0.0, time.time() - float(step.get("asked_at") or time.time())),
                             1), "work": work, "text": text}
    line["mac"] = _mac(line)
    from driver.log import Log
    with Log(str(answers_path(project, step["log"]))) as log:
        log.add("answer", **line)
    remove_step(step)
    return line


class Background:
    """The background driver's caller: the role runs as its own process."""

    def __init__(self, settings: dict):
        self.settings = settings

    def __call__(self, call: Call) -> roles.Answer:
        s = self.settings
        return roles.run(call.command, call.role, call.inputs, call.copy,
                         s["stop"]["attempt_minutes"], call.lens, s["isolation"],
                         s["container_image"], call.project, call.policy)


class Session:
    """Session mode's caller: a saved answer, or a step for your session and a Pause.
    The cost of a role is not known here, so the answer's cost is None."""

    def __call__(self, call: Call) -> roles.Answer:
        return self.many([call])[0]

    def many(self, calls: list) -> list:
        """The answers to roles that do not depend on one another, in the order asked.
        Each that has no saved answer becomes a step, and all of them wait together, so
        your session can run them at once; then Pause. A replay goes on past the first
        of them only once each is answered."""
        answers, waiting = [], []
        for call in calls:
            found_key = key(call.role, call.lens, call.inputs, call.content)
            found = saved(call.project, call.log, found_key)
            if found and (not found.get("work") or restore_work(call.copy, found["work"])):
                if found_key == _replay_key(call.project):
                    end_replay(call.project)    # the run is where the run before it paused
                text = found["text"]
                answers.append(roles.Answer(roles.last_block(text), None,
                                            float(found.get("seconds") or 0), text))
                continue
            waiting.append({
                "agent": f"tollgate:{call.role}", "role": call.role, "policy": call.policy,
                "lens": call.lens, "prompt": roles.prompt(call.role, call.inputs, call.lens),
                "copy": os.path.realpath(call.copy) if call.copy else "", "key": found_key,
                "log": call.log, "ticket": call.ticket, "task": call.task,
                "slot": current_slot(), "n": len(answers) + len(waiting),
                "asked_at": time.time()})
        if waiting:
            project = calls[0].project
            for step in waiting:
                _write_step(project, step)
            _mark_replay(project, waiting[0]["key"])
            raise Pause(waiting[0])
        return answers


def ask_many(caller, calls: list) -> list:
    """Ask roles that do not depend on one another: together in session mode, one after
    another in the background. Each element is the role's Answer, or the RoleError it
    raised, so the caller decides what a failure means."""
    if isinstance(caller, Session):
        return caller.many(calls)
    found = []
    for call in calls:
        try:
            found.append(caller(call))
        except roles.RoleError as err:
            found.append(err)
    return found


def caller(settings: dict, runner: str | None = None):
    """The caller for the runner named, or else for the setting `runner`."""
    return Session() if (runner or settings["runner"]) == "session" else Background(settings)


def cannot_pass(project: str) -> str:
    """Why a task cannot pass the gate here, or "": the settings on main must be there
    and readable."""
    try:
        st.load_from_ref(project, "main")
    except st.SettingsError as err:
        return f"the settings on main cannot be read: {err}. {INIT}"
    return ""

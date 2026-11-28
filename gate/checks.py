"""Run the checks of a project and collect their results.

A check names a hook of the project's .pre-commit-config.yaml, and runs as

    pre-commit run <hook> --all-files --hook-stage <stage>

in the folder of the copy, at the stage the hook runs at (see
gate.precommit_config.run_stage). The project's commands stay in the project's own
config, so the project works without Tollgate. The writing check is Tollgate's own, so
it keeps its command in the settings, under `run`.
"""

import argparse
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from gate import junit
from gate import precommit_config as pc


@dataclass
class CheckResult:
    name: str
    passed: bool
    timed_out: bool = False
    output: str = ""


@dataclass
class Results:
    checks: list = field(default_factory=list)
    tests: dict = field(default_factory=dict)  # test id -> {"status", "file"}

    def failed_checks(self):
        return sorted(c.name for c in self.checks if not c.passed)

    def to_json(self) -> str:
        return json.dumps({"checks": [asdict(c) for c in self.checks], "tests": self.tests}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Results":
        data = json.loads(text)
        return cls([CheckResult(**c) for c in data["checks"]], data["tests"])


# The project's own commands never need a secret. Their code may be written by the AI.
SECRET_ENDINGS = ("_TOKEN", "_API_KEY", "_SECRET", "_PASSWORD")
# Without these, the AI's code cannot reach your SSH agent or Tollgate's key and state.
DROPPED = ("PYTHONPATH", "SSH_AUTH_SOCK", "TOLLGATE_KEYS", "TOLLGATE_STATE")


def project_env() -> dict:
    """The environment for the project's own commands: without Tollgate's import path,
    so Tollgate's modules cannot shadow the project's, and without any secret."""
    return {k: v for k, v in os.environ.items()
            if k not in DROPPED and not k.upper().endswith(SECRET_ENDINGS)}


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


def command_of(check: dict, workdir) -> tuple:
    """(command, problem) for a check. The command is a list for a hook, and a string for
    a shell to run for the writing check. problem says why the check cannot run."""
    if "hook" not in check:
        return check["run"], ""
    config = Path(workdir, pc.CONFIG)
    text = config.read_text(errors="replace") if config.is_file() else ""
    stage = pc.run_stage(text, check["hook"])
    if stage is None:
        return None, f"the hook {check['hook']} is not in {pc.CONFIG}"
    return ["pre-commit", "run", check["hook"], "--all-files", "--hook-stage", stage,
            "--color", "never"], ""


def run(settings: dict, workdir, only=None) -> Results:
    """Run each check in the folder and read each test report it names.

    A check that runs longer than its limit counts as failed.
    """
    results = Results()
    for name, check in settings["checks"].items():
        if only and name not in only:
            continue
        minutes = check.get("minutes", settings["check_minutes"])
        report = Path(workdir, check["report"]) if check.get("report") else None
        if report and report.exists():
            report.unlink()
        command, problem = command_of(check, workdir)
        if problem:
            result = CheckResult(name, False, False, problem)
        else:
            try:
                proc = subprocess.run(
                    command, shell=isinstance(command, str), cwd=workdir, capture_output=True,
                    text=True, check=False, timeout=minutes * 60, env=project_env(),
                )
                result = CheckResult(name, proc.returncode == 0, False,
                                     _tail(proc.stdout + proc.stderr))
            except subprocess.TimeoutExpired:
                result = CheckResult(name, False, True, f"ran longer than {minutes} minutes")
            except OSError as err:
                result = CheckResult(name, False, False, f"the check cannot start: {err}")
        results.checks.append(result)
        if report and report.exists():
            for test_id, test in junit.read(report).items():
                results.tests[test_id] = {"status": test.status, "file": test.file}
    return results


# A compile or type error about a name that is not there yet, in Go, TypeScript, mypy,
# pyright, and Rust. Each pattern gives the parts of the name, the qualifier first. A Go
# generic type drops its type arguments: `flow.Spec[testParams]` is `flow.Spec`.
MISSING_NAME = [(re.compile(p), order) for p, order in (
    (r"undefined: ([\w.]+)", (1,)),                                          # Go
    (r"\S+ undefined \(type \*?([\w.]+)(?:\[.*\])? has no (?:field or )?method (\w+)", (1, 2)),
    (r"unknown field '?(\w+)'? in struct literal of type \*?([\w.]+)", (2, 1)),
    (r"has no exported member (?:named )?'([\w$]+)'", (1,)),                  # TypeScript
    (r"Cannot find name '([\w$]+)'", (1,)),
    (r"Property '([\w$]+)' does not exist on type", (1,)),
    (r'Module "([\w.]+)" has no attribute "(\w+)"', (1, 2)),                  # mypy
    (r'Name "(\w+)" is not defined', (1,)),
    (r'"(\w+)" has no attribute "(\w+)"', (1, 2)),
    (r'"(\w+)" is not a known attribute of module "([\w.]+)"', (2, 1)),       # pyright
    (r'"(\w+)" is unknown import symbol', (1,)),
    (r"cannot find \w+(?: or \w+)? `(\w+)` in (?:module|crate) `([\w:]+)`", (2, 1)),  # Rust
    (r"cannot find \w+(?: or \w+)? `(\w+)`", (1,)),
    (r"unresolved import `([\w:]+)`", (1,)),
    (r"no method named `(\w+)` found", (1,)),
    (r"no function or associated item named `(\w+)` found for \w+ `([\w:]+)`", (2, 1)),
)]
NOT_A_NAME = {"crate", "self", "super"}
# Lines that name a file but are no error of their own: the Go compiler stops after 10
# errors with "too many errors", and golangci-lint heads a package that does not compile
# with "file:1: : # <package>".
NOT_AN_ERROR = re.compile(r":\d+:\d+: too many errors|:\d+: : # \S")


def _parts(text: str) -> list:
    return [p for p in re.findall(r"[A-Za-z_$][\w$]*", text) if p not in NOT_A_NAME]


def _missing_name(line: str) -> list | None:
    for pattern, order in MISSING_NAME:
        found = pattern.search(line)
        if found:
            return [p for g in order for p in _parts(found.group(g))]
    return None


def only_missing_seams(output: str, files: list, seams: list) -> bool:
    """True when each line of a check's output that names one of the files is a compile
    or type error about a name that one of the seams names, such as `undefined:
    logging.Cause` for the seam `internal/logging.Cause`. The name matches when its parts
    end the seam's parts, or end a leading run of them: then it names an owner of the seam,
    such as `ports.JobState` for the seam `internal/ports.JobState.Live`. A Rust error names its file on a later `-->` line, so that line
    counts as the `error` line above it. The Go compiler stops after 10 errors with a line
    `too many errors`, and golangci-lint heads a package that does not compile with a
    line `file:1: : # <package>`. Each names the file but is no error of its own, so it
    is skipped.

    Before the build, a new test that calls a seam the builder adds does not compile, so
    a vet, type, or lint check fails on it. That is the test failing for the right reason.
    Any other error on the files, and an output that names none of them, gives False."""
    lines = output.splitlines()
    seam_parts = [_parts(s) for s in seams]
    named = 0
    for i, line in enumerate(lines):
        if not any(f in line for f in files) or NOT_AN_ERROR.search(line):
            continue
        named += 1
        if line.lstrip().startswith("-->"):
            line = next((x for x in reversed(lines[:i]) if x.lstrip().startswith("error")), "")
        parts = _missing_name(line)
        if not parts or not any(_names_seam_or_owner(parts, s) for s in seam_parts):
            return False
    return named > 0


def _names_seam_or_owner(parts: list, seam: list) -> bool:
    """True when the parts end the seam's parts, or end a leading run of them: `ports.JobState`
    names the owner of the seam `internal/ports.JobState.Live`, which the builder adds too.
    Also true when a leading run of the parts ends the seam's parts: `flow.Spec.Do` names a
    member of the seam `internal/flow.Spec`, and the builder adds the members of a seam."""
    n = len(parts)
    if any(seam[k - n:k] == parts for k in range(n, len(seam) + 1)):
        return True
    return any(parts[:m] == seam[-m:] for m in range(1, min(n, len(seam) + 1)))


def main(argv=None) -> int:
    from gate import settings as st

    parser = argparse.ArgumentParser(prog="tollgate checks", description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", default="origin/main", help="read the settings from here")
    parser.add_argument("--only", action="append", help="run only this check")
    parser.add_argument("--setup", action="store_true", help="run the setup commands first")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    settings = st.load_from_ref(args.repo, args.base)
    if args.setup:
        for command in settings["setup"]:
            subprocess.run(command, shell=True, cwd=args.repo, check=True, env=project_env())
    results = run(settings, args.repo, args.only)
    with open(args.out, "w") as f:
        f.write(results.to_json())
    for check in results.checks:
        print(f"{check.name}: {'passed' if check.passed else 'failed'}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

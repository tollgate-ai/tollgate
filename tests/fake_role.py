"""A fake role for the driver's tests. It follows the role contract with no model.

    python3 tests/fake_role.py <mode> [<option>=<value> ...]

It reads the prompt on standard input, acts as the role named in its first line,
and prints {"result": ..., "total_cost_usd": ...}.

Builder modes: pass2 (fails attempt 1, passes attempt 2), pass1only (passes attempt 1 and
fails each later one), same (fails the same
way every time), different (fails a different test each attempt), slow (runs too
long), github (also changes a file under .github).
Reviewer options: badline (names a line that does not exist), badcite (the analyst cites a
function that does not exist), block=<lens> (that lens finds a blocking problem),
major=<lens> (that lens finds a major problem in each ticket's change),
taskmajor=<n> (the first n reviews of the whole task find a major problem),
c3fail (the checklist reviewer fails C3, with a reason), review_to=<path> (the checklist
reviewer adds its inputs to this file), plan_review_to=<path> (each plan lens adds its lens
and inputs to this file). Like a real reviewer, it judges only the items
that are not in `tested`.
A plan written with a hint from the review of the whole task adds a third ticket, loud.
Test writer options: tw_bad=1 (the first tests it writes break the style check),
tw_bad=<n> tw_count=<path> (the first n do, counted in that file), tw_bad=hint (each
does, until the inputs hold a hint), tw_freeze=1 (the first tests it writes hold a C1
test that passes before the build, so the freeze refuses them), tw_map=1 (C2 is kept by
the existing tests/test_greet.py::test_greet, named in the answer's map; tw_map=weaken
first weakens that test; tw_map=next adds a new test next to it).
Builder options: owntest=1 (the builder adds its own test, named for item C3), wrong=1 (the
builder says a locked test is wrong), ask=1 (the plan lists an assumption), diagram=ok or bad (the try
writes a diagram spec, and an SVG file by hand). Advisor options: plan_decision=<d>,
decision=<d>, checkpoint=<continue or stop>.
Planner options: plan_sees=<path> (adds what app.py holds in its copy to that file),
drop_questions=1 (the plan leaves out the open questions of a brief), bad_answer=1 (the
answer to a plan finding revises the plan with an assumption of two sentences), revise=1 (each
answer to plan findings adds a line to the plan's Design, and from round 2 on the plan
finding is about that change), bad_plan=1 (every
plan has such an assumption), bad_ticket=1 (the first plan has a ticket with no Checklist).
Brainstorm options: questions=<n> or always (the builder asks n questions, then states the
idea; always asks one past the cap), idea_to=<path> (the builder adds each idea input to
that file), brief=bad (the first brief breaks the format) or never, size=roadmap,
idea=skip (the advisor skips each question), statement=add (the advisor adds to the first
statement) or always, idea_review_to=<path> (each brainstorm lens adds its lens and inputs
to that file).
Options: cost=<usd>, die_once=<path> (kill the driver once, on attempt 1),
inputs_to=<path> (the builder adds the inputs of each try to this file, one JSON line each).
"""

import json
import os
import re
import signal
import sys
import time
from pathlib import Path

GREET = 'def greet(name):\n    return f"hello {name}"\n'
SHOUT_OK = '\n\ndef shout(name):\n    """Return the greeting in capitals."""\n    return greet(name).upper()\n'
SHOUT_BAD = '\n\ndef shout(name):\n    """Return the greeting in capitals."""\n    return greet(name)\n'
WHISPER = '\n\ndef whisper(name):\n    """Return the greeting in small letters."""\n    return greet(name).lower()\n'
WHISPER_BAD = '\n\ndef whisper(name):\n    """Return the greeting in small letters."""\n    return greet(name)\n'
GREET_BROKEN = 'def greet(name):\n    return f"hi {name}"\n'
TESTS = '''import unittest

import app


class T(unittest.TestCase):
    def test_c1_shout(self):
        self.assertEqual(app.shout("ann"), "HELLO ANN")

    def test_c2_greet_kept(self):
        self.assertEqual(app.greet("ann"), "hello ann")
'''


TESTS_T3 = '''import unittest

import app


class T(unittest.TestCase):
    def test_c1_loud(self):
        self.assertEqual(app.loud("ann"), "hello ann!")

    def test_c2_greet_kept(self):
        self.assertEqual(app.greet("ann"), "hello ann")
'''

LOUD = '\n\ndef loud(name):\n    """Return the greeting with a mark."""\n    return greet(name) + "!"\n'
LOUD_BAD = '\n\ndef loud(name):\n    """Return the greeting with a mark."""\n    return greet(name)\n'
FIX_HINT = "review of the whole task"


TESTS_T2 = '''import unittest

import app


class T(unittest.TestCase):
    def test_c1_whisper(self):
        self.assertEqual(app.whisper("ANN"), "hello ann")

    def test_c2_greet_kept(self):
        self.assertEqual(app.greet("ann"), "hello ann")
'''

PLAN = """---
id: {task}
tickets: {task}-1, {task}-2
---
## Task
{request}

## Design
Add two functions next to greet.

## Files
modify app.py

## Seams
app.shout, app.whisper

## Reuse
app.greet, unchanged.

## Risks
- An empty name: both functions return the greeting with no name.

## Assumptions
none

## Tickets
- {task}-1: shout returns the greeting in capitals.
- {task}-2: whisper returns the greeting in small letters.
"""

ANALYSIS = """---
task: {task}
commit: {commit}
---
## Structure
- confirmed: one module holds the code [app.py:1]
## Entry points
- confirmed: greet builds the greeting [app.py::greet]
## Frameworks
- assumed: no framework, the standard library only
## Similar features
- confirmed: greet formats one name [app.py:2]{bad}
## Tests
- confirmed: a script runs the tests [run_tests.py:1]
## Standards
- assumed: no written standards
"""

TICKET_T1 = """---
id: {task}-1
plan: {task}
seams: app.shout
tests: tests/test_t1.py
---
## Current behavior
There is no shout.

## Desired behavior
shout returns the greeting in capitals.

## Checklist
- [C1, must, happy] shout("ann") returns "HELLO ANN".
- [C2, must, keeps behavior] greet("ann") still returns "hello ann".
- [C3, error] shout has a docstring that says what an empty name gives.

## Not covered
- edge: the input is one fixed name.
- boundary: the input has no limit.
- regression: no bug is fixed.
- integration: the change stays in one module.
- e2e: there is no screen.
- security: the change reads nothing from a user.
- performance: no time limit is asked for.
- concurrency: nothing runs at the same time.

## Out of scope
Other languages.
"""

TICKET_T2 = """---
id: {task}-2
plan: {task}
seams: app.whisper
tests: tests/test_t2.py
---
## Current behavior
There is no whisper.

## Desired behavior
whisper returns the greeting in small letters.

## Checklist
- [C1, must, happy] whisper("ANN") returns "hello ann".
- [C2, must, keeps behavior] greet("ann") still returns "hello ann".
- [C3, error] whisper has a docstring that says what an empty name gives.

## Not covered
- edge: the input is one fixed name.
- boundary: the input has no limit.
- regression: no bug is fixed.
- integration: the change stays in one module.
- e2e: there is no screen.
- security: the change reads nothing from a user.
- performance: no time limit is asked for.
- concurrency: nothing runs at the same time.

## Out of scope
Other languages.
"""


TICKET_T3 = """---
id: {task}-3
plan: {task}
seams: app.loud
tests: tests/test_t3.py
---
## Current behavior
There is no loud.

## Desired behavior
loud returns the greeting with a mark.

## Checklist
- [C1, must, happy] loud("ann") returns "hello ann!".
- [C2, must, keeps behavior] greet("ann") still returns "hello ann".
- [C3, error] loud has a docstring that says what an empty name gives.

## Not covered
- edge: the input is one fixed name.
- boundary: the input has no limit.
- regression: no bug is fixed.
- integration: the change stays in one module.
- e2e: there is no screen.
- security: the change reads nothing from a user.
- performance: no time limit is asked for.
- concurrency: nothing runs at the same time.

## Out of scope
Other marks.
"""


STATEMENT = "Shout turns a name into a loud greeting. It helps a user who wants a loud hello."

BRIEF = """---
id: {id}
size: {size}
---
## Overview
{statement}

## Analysis
{analysis}

## Goals
- shout("ann") returns "HELLO ANN".

## Non-goals
- Other languages.

## Alternatives weighed
{alternatives}

## Open questions
{questions}
"""


def first_changed_line(diff: str) -> tuple:
    """(file, line) of the first line a diff adds, for a finding inside the change."""
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        m = re.match(r"^@@ -\S+ \+(\d+)", line)
        if path and m:
            return path, int(m.group(1))
    return "app.py", 1


def count_marker(name: str) -> int:
    """Count calls across processes with a file in the working folder."""
    path = Path(name)
    n = int(path.read_text()) + 1 if path.exists() else 1
    path.write_text(str(n))
    return n


def inputs(prompt: str) -> dict:
    return json.loads(re.findall(r"```json\s*\n(.*?)\n```", prompt, re.DOTALL)[-1])


def answer(data: dict, cost: float) -> None:
    print(json.dumps({"result": "Done.\n\n```json\n" + json.dumps(data) + "\n```",
                      "total_cost_usd": cost}))


def main() -> int:
    mode = sys.argv[1]
    opts = dict(a.split("=", 1) for a in sys.argv[2:] if "=" in a)
    cost = float(opts.get("cost", 0.1))
    prompt = sys.stdin.read()
    first = prompt.splitlines()[0]
    data = inputs(prompt)
    if first == "# Role: analyst":
        commit = data["commit"]
        bad = "\n- confirmed: shout already exists [app.py::nope]" if "badcite" in sys.argv else ""
        if "badanalysis" in sys.argv:
            answer({"analysis": "no header"}, cost)
            return 0
        answer({"analysis": ANALYSIS.format(task=data["task_id"], commit=commit, bad=bad)}, cost)
        return 0
    if first == "# Role: test writer":
        Path("tests").mkdir(exist_ok=True)
        if data.get("ticket_id", "").endswith("-3"):
            Path("tests/test_t3.py").write_text(TESTS_T3)
            answer({"tests": [{"item": "C1", "file": "tests/test_t3.py", "name": "test_c1_loud"},
                              {"item": "C2", "file": "tests/test_t3.py",
                               "name": "test_c2_greet_kept"}]}, cost)
            return 0
        if data.get("ticket_id", "").endswith("-2"):
            Path("tests/test_t2.py").write_text(TESTS_T2)
            answer({"tests": [{"item": "C1", "file": "tests/test_t2.py", "name": "test_c1_whisper"},
                              {"item": "C2", "file": "tests/test_t2.py",
                               "name": "test_c2_greet_kept"}]}, cost)
            return 0
        bad = opts.get("tw_bad") == "1" and not data.get("rejected")
        if opts.get("tw_bad", "").isdigit() and opts.get("tw_count"):
            # Bad for the first tw_bad tests it writes, counted in the file tw_count.
            bad = count_marker(opts["tw_count"]) <= int(opts["tw_bad"])
        if opts.get("tw_bad") == "hint":
            bad = not data.get("hint")
        text = TESTS
        if opts.get("tw_freeze") and not data.get("rejected"):
            # A C1 test that already passes, so the freeze refuses it.
            text = TESTS.replace('app.shout("ann"), "HELLO ANN"', 'app.greet("ann"), "hello ann"')
        if opts.get("tw_map"):
            # C2 is kept by tests/test_greet.py::test_greet, which was there before.
            text = text.split("\n    def test_c2_greet_kept")[0] + "\n"
        Path("tests/test_t1.py").write_text(text + ("# BADSTYLE\n" if bad else ""))
        greet = Path("tests/test_greet.py")
        if opts.get("tw_map") == "weaken" and not data.get("rejected"):
            greet.write_text(greet.read_text().replace(
                "self.assertEqual(app.greet('ann'), 'hello ann')", "self.assertTrue(app.greet)"))
        if opts.get("tw_map") == "next" and "test_c4_" not in greet.read_text():
            greet.write_text(greet.read_text() + "\n    def test_c4_greet_has_a_docstring(self):"
                             "\n        self.assertTrue(app.shout.__doc__)\n")
        kept = ({"item": "C2", "file": "tests/test_greet.py", "name": "test_greet"}
                if opts.get("tw_map") else
                {"item": "C2", "file": "tests/test_t1.py", "name": "test_c2_greet_kept"})
        answer({"tests": [{"item": "C1", "file": "tests/test_t1.py", "name": "test_c1_shout"},
                          kept]}, cost)
    elif first == "# Role: builder" and data.get("mode") == "idea":
        if opts.get("idea_to"):
            with open(opts["idea_to"], "a") as f:
                f.write(json.dumps(data) + "\n")
        asked = len(data.get("answers", []))
        want = opts.get("questions", "2")
        if want == "always" or (asked < int(want) and data["questions_left"] > 0):
            answer({"question": f"Who uses shout, in round {asked + 1}?"}, cost)
        else:
            answer({"statement": STATEMENT}, cost)
    elif first == "# Role: builder" and data.get("mode") == "brief":
        lines = []
        for f in data.get("findings", []):
            if f["text"] not in lines:     # the brief merges the lenses: no repeats
                lines.append(f["text"])
        alternatives = [f"- {a['alternative']}: dropped because {a['trade_off']}"
                        for a in data.get("alternatives", [])]
        alternatives = alternatives or ["- Nothing: dropped because the user asked for it."]
        text = BRIEF.format(id=data["id"], size=opts.get("size", "task"),
                            statement=data["statement"],
                            analysis="\n".join(f"- {x}" for x in lines) or "- nothing found.",
                            alternatives="\n".join(alternatives),
                            questions="\n".join(f"- {q}" for q in data["open_questions"])
                            or "none")
        if opts.get("brief") == "never" or (opts.get("brief") == "bad"
                                             and not data.get("problems")):
            text = text.replace("## Goals", "## Aims")
        answer({"brief": text}, cost)
    elif first == "# Role: builder" and data.get("mode") == "plan":
        task = data["task_id"]
        if opts.get("plan_sees"):
            # What the planner's copy holds: app.py, with each function built so far.
            with open(opts["plan_sees"], "a") as f:
                f.write(json.dumps({"hint": data.get("hint", ""),
                                    "app": Path("app.py").read_text()}) + "\n")
        Path(".tollgate/plans").mkdir(parents=True, exist_ok=True)
        plan = PLAN.format(task=task, request=data["request"])
        if data.get("open_questions") and not opts.get("drop_questions"):
            plan = plan.replace("## Assumptions\nnone", "## Assumptions\n" + "\n".join(
                f"- {q}" for q in data["open_questions"]))
        if opts.get("ask") and not data.get("hint"):
            plan = plan.replace("## Assumptions\nnone", "## Assumptions\n- Should whisper keep accents?")
        if opts.get("bad_plan"):
            plan = plan.replace("## Assumptions\nnone",
                                "## Assumptions\n- Is this the newest? Nobody looked it up.")
        if any("README.md" in problem for problem in data.get("problems", [])):
            plan = plan.replace("## Files\nmodify app.py", "## Files\nmodify app.py\ncreate README.md")
        written = [(f"{task}-1", TICKET_T1), (f"{task}-2", TICKET_T2)]
        if FIX_HINT in data.get("hint", ""):
            plan = plan.replace(f"tickets: {task}-1, {task}-2", f"tickets: {task}-1, {task}-2, {task}-3")
            plan = plan.rstrip("\n") + f"\n- {task}-3: loud returns the greeting with a mark.\n"
            written.append((f"{task}-3", TICKET_T3))
        Path(f".tollgate/plans/{task}.md").write_text(plan)
        for tid, text in written:
            Path(f".tollgate/tickets/{tid}").mkdir(parents=True, exist_ok=True)
            body = text.format(task=task)
            if opts.get("bad_ticket") and not data.get("problems") and tid.endswith("-1"):
                body = body.replace("## Checklist", "## Items")   # a section the checker needs
            Path(f".tollgate/tickets/{tid}/ticket.md").write_text(body)
        answer({"plan": f".tollgate/plans/{task}.md", "tickets": [f"{task}-1", f"{task}-2"]},
               cost)
    elif first == "# Role: builder" and data.get("mode") == "answer":
        if opts.get("revise"):
            # Each answer changes the plan, as a real fix does, so the next round's
            # reviewers get new inputs.
            for plan_file in Path(".tollgate/plans").glob("*.md"):
                text = plan_file.read_text()
                plan_file.write_text(text.replace("## Design\n", "## Design\nRevised once more.\n", 1))
        if opts.get("bad_answer"):
            # The revised plan breaks a rule: an assumption of two sentences.
            for plan_file in Path(".tollgate/plans").glob("*.md"):
                text = plan_file.read_text().replace(
                    "## Assumptions\nnone",
                    "## Assumptions\n- Is this the newest? Nobody looked it up.")
                plan_file.write_text(text)
        answer({"answers": [{"finding": i, "action": opts.get("answer", "fix"), "evidence": "x"}
                            for i, _ in enumerate(data.get("findings", []))]}, cost)
    elif first == "# Role: builder":
        n = data["attempt"]
        if opts.get("inputs_to"):
            with open(opts["inputs_to"], "a") as f:
                f.write(json.dumps(data) + "\n")
        die = opts.get("die_once")
        if die and n == 1 and not os.path.exists(die):
            Path(die).write_text("died")
            os.kill(os.getppid(), signal.SIGKILL)
            time.sleep(5)
        if mode == "slow":
            time.sleep(30)
        good = (mode == "pass2" and n >= 2) or mode == "pass1" or (mode == "pass1only"
                                                                    and n == 1)
        if "seams: app.loud" in data.get("ticket", ""):
            kept = Path("app.py").read_text().split("\n\ndef loud(")[0]
            Path("app.py").write_text(kept + (LOUD if good else LOUD_BAD))
        elif "seams: app.whisper" in data.get("ticket", ""):
            # The whisper ticket builds on what main has, which may hold shout already.
            kept = Path("app.py").read_text().split("\n\ndef whisper(")[0]
            Path("app.py").write_text(kept + (WHISPER if good else WHISPER_BAD))
        elif mode == "different" and n % 2 == 0:
            Path("app.py").write_text(GREET_BROKEN + SHOUT_OK)
        else:
            Path("app.py").write_text(GREET + (SHOUT_OK if good else SHOUT_BAD))
        if opts.get("owntest"):
            Path("tests/test_more.py").write_text(
                "import unittest\n\nimport app\n\n\nclass M(unittest.TestCase):\n"
                "    def test_c3_has_a_docstring(self):\n"
                "        self.assertTrue(app.shout.__doc__)\n")
        if mode == "github":
            Path(".github/workflows").mkdir(parents=True, exist_ok=True)
            Path(".github/workflows/evil.yml").write_text("on: push\n")
            Path("app.py").write_text(GREET + SHOUT_OK)
        if opts.get("diagram"):
            # A diagram spec; the driver draws the SVG files. "bad" cannot be drawn.
            Path("docs/diagrams/src").mkdir(parents=True, exist_ok=True)
            spec = {"type": "flow", "title": "Shout", "desc": "One step.",
                    "nodes": [{"id": "a", "text": "Shout\nIn capitals", "col": 0, "row": 0}],
                    "edges": []}
            if opts["diagram"] == "bad":
                spec["nodes"][0].pop("col")
            Path("docs/diagrams/src/shout.json").write_text(json.dumps(spec))
            Path("docs/diagrams/shout.svg").write_text("<svg>drawn by hand</svg>")
            Path("docs/diagrams/notes.txt").write_text(json.dumps(sorted(data)))
        wrong = ("tests/test_t1.py::test_c1_shout: the ticket asks for HELLO ANN, the test is "
                 "right only if greet is unchanged") if opts.get("wrong") else None
        answer({"changed": ["app.py"], "locked_test_wrong": wrong,
                "decisions": ["shout reuses greet"], "concerns": []}, cost)
    elif first == "# Role: reviewer" and "# Lens: test fit" in prompt:
        answer({"verdict": "pass", "findings": [], "items": []}, cost)
    elif first == "# Role: reviewer" and "# Lens: checklist" in prompt:
        if opts.get("review_to"):
            with open(opts["review_to"], "a") as f:
                f.write(json.dumps(data) + "\n")
        lines = Path("app.py").read_text().splitlines()
        line = 999 if "badline" in sys.argv else next(
            i for i, text in enumerate(lines, 1) if '"""' in text)
        item = {"item": "C3", "passed": True, "file": "app.py", "line": line}
        if "c3fail" in sys.argv:
            item = {"item": "C3", "passed": False, "why": "shout has no docstring"}
        items = [] if "C3" in data.get("tested", []) else [item]
        answer({"verdict": "pass", "findings": [], "items": items}, cost)
    elif first == "# Role: reviewer" and any(f"# Lens: {k}" in prompt for k in
                                             ("simplicity", "fresh reader", "readiness")):
        rounds = int(opts.get("planfind", 0))
        lens = prompt.split("# Lens: ", 1)[1].splitlines()[0]
        found = lens == "simplicity" and count_marker(".fake-planfind") <= rounds
        if opts.get("plan_review_to"):
            with open(opts["plan_review_to"], "a") as f:
                f.write(json.dumps({"lens": lens, "inputs": data}) + "\n")
        text = "drop T-2?"
        if opts.get("revise"):
            # A real finding is about the plan it read, so it differs in each round.
            text += f" (revision {data.get('plan', '').count('Revised once more.')})"
        finding = {"level": "major", "text": text}
        if opts.get("revise") and "diff" in data:
            finding["about"] = "change"   # from round 2 on, it is about the revision
        answer({"verdict": "pass", "findings": [finding] if found else [], "items": []}, cost)
    elif first == "# Role: reviewer" and any(f"# Lens: {k}\n" in prompt for k in
                                             ("need", "user", "risk", "shape")):
        lens = prompt.split("# Lens: ", 1)[1].splitlines()[0]
        if opts.get("idea_review_to"):
            with open(opts["idea_review_to"], "a") as f:
                f.write(json.dumps({"lens": lens, "inputs": data}) + "\n")
        text = {"need": "greet already builds the greeting [app.py::greet].",
                "user": "an empty name gives no clear error.",
                "risk": "greet already builds the greeting [app.py::greet].",
                "shape": "a second function to keep in step with greet."}[lens]
        items = ([{"alternative": "A loud flag on greet",
                   "trade_off": "it changes a function that other code calls."}]
                 if lens == "shape" else [])
        answer({"verdict": "pass", "findings": [{"level": "major", "text": text}],
                "items": items}, cost)
    elif first == "# Role: reviewer" and ("# Lens: review" in prompt or "# Lens: security" in prompt):
        lens = "security" if "# Lens: security" in prompt else "review"
        block = opts.get("block") == lens
        whole_task = "\ntickets: " in data.get("ticket", "")
        if whole_task and lens == "review" and opts.get("taskmajor") and \
                count_marker(".fake-taskreview") <= int(opts["taskmajor"]):
            path, line = first_changed_line(data.get("diff", ""))
            answer({"verdict": "pass", "findings": [{"level": "major", "file": path, "line": line,
                                                     "text": "loud is missing"}]}, cost)
            return 0
        if not whole_task and opts.get("major") == lens:
            # A major finding inside the change, on each review of a ticket's try.
            path, line = first_changed_line(data.get("diff", ""))
            answer({"verdict": "pass", "findings": [{"level": "major", "file": path,
                                                     "line": line,
                                                     "text": "an error branch has no test"}]},
                   cost)
            return 0
        answer({"verdict": "fail" if block else "pass", "findings": [
            {"level": "blocking", "file": "app.py", "line": 1, "text": f"{lens} problem"}]
            if block else []}, cost)
    elif first == "# Role: advisor":
        hint = "try harder"
        if data.get("brainstorm") == "question":
            decision = "skip" if opts.get("idea") == "skip" else "answer"
            hint = "" if decision == "skip" else "Ann, who greets her friends."
        elif data.get("brainstorm") == "statement":
            add = opts.get("statement") == "always" or (opts.get("statement") == "add"
                                                         and not data.get("additions"))
            decision = "add" if add else "confirm"
            hint = "It must work for an empty name." if add else ""
        elif "checkpoint" in data:
            decision = opts.get("checkpoint", "continue")
        elif "plan" in data:
            decision = opts.get("plan_decision", "approve")
        else:
            decision = opts.get("decision", "park")
        answer({"decision": decision, "hint": hint, "reason": "test"}, cost)
    else:
        print(f"unknown role: {first}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

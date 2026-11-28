# Tollgate's design

## What Tollgate is

Tollgate is a plugin for Claude Code. It lets an AI write your code, and merges only what works: a change reaches main only when tests that the AI cannot change pass, and its score is 70% or more. Everything runs on your machine.

You give Tollgate a task. Tollgate gives you ready changes, and a report.

**What it talks to.** You type a task in your Claude Code session. Tollgate, on your machine, runs each step of it: in session mode your session runs each role, and in background mode Tollgate starts each role on its own. Every role works with the model. You push main to GitHub, and Tollgate checks each push before it goes.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/c4-context-isometric-dark.svg">
  <img alt="Tollgate in its context, drawn in three dimensions: you, your Claude Code session, the model, and GitHub around Tollgate, with what flows between them" src="diagrams/c4-context-isometric.svg">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/the-lock-dark.svg">
  <img alt="The idea of Tollgate in four steps: write the tests, lock them, the AI builds and can try again, and the tests and reviewers check each try" src="diagrams/the-lock.svg">
</picture>

Think of an exam. The questions are set and sealed before the student starts. The student can take the exam more than once, but the student cannot rewrite the questions or mark the paper.

**Who it is for.** An engineer or a small team that lets an AI write most of each change. It works on one project or on many.

**The problem.** An AI that writes code makes four kinds of mistake. It invents things: a function, a file, a fact. It pads its work. It stops before the work is done. It weakens a test to make the test pass. Written instructions do not stop these mistakes, because the AI can ignore them. A review by the same AI does not stop them either, because it defends its own work.

**The answer.** Tollgate does not ask the AI to behave. The store locks the tests before the AI builds, and signs the lock. The gate, a program with no AI in it, checks each try. Two layers stop a change that skips the gate: Claude Code hooks and policies, and pre-commit in git. The 18 rules in [principles.md](principles.md) say what Tollgate holds to, and why.

**The limit.** Tollgate stops an AI that takes a shortcut. It does not stop code written to attack the test tools themselves. It does not stop a person who skips pre-commit with `--no-verify`.

**How to read this page.** The sections up to "How you use it" explain the tool in plain words. The sections after that are the reference for a person who changes Tollgate's code. [CONTEXT.md](../CONTEXT.md) explains each word once.

## A task from start to finish

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/a-task-dark.svg">
  <img alt="A task from start to finish" src="diagrams/a-task.svg">
</picture>

Before the plan, the analyst reads the project and reports how it is built: each fact confirmed, with a citation to the code, or for something the project lacks, to the folder that would hold it (`[./]` or `[docs/]`), specified, with a citation to a spec doc, or marked assumed. The setting `spec` names the spec docs: files or folders that state the intended design. The analyst reads them first, and a specified citation counts only inside them: `[path]` or `[path::heading]`, where the heading is a markdown heading of the doc. The driver checks every citation, and a finding whose citation does not exist becomes an assumption. A specified fact is not an assumption of the plan, but a plan that differs from one must change that doc, and a plan that adds behavior that no spec doc describes must list a spec doc under Files, so the next analysis finds the feature as a specified fact. The plan writer and the plan reviewers read the analysis. A plan that lists an assumption goes to the advisor before it is locked. After the last ticket, the review lens reads every ticket's change together, against the plan.

Steps 4 to 6 repeat for each ticket. Each ticket starts from the ready change of the ticket before it, and the store checks that this start passed the gate before it locks the tests. No ticket merges until the review of the whole task passes. Then the tickets merge in plan order: with `auto_merge` on, the store merges each tier 1 or 2 change, and stops at the first that it may not merge, since each later ticket holds the ones before it. You merge tiers 3 and 4, and every change when `auto_merge` is off.

### Before a task: the brainstorm

An idea that is not clear yet starts with `/tollgate:brainstorm`, not with a task. The brainstorm writes a brief, and `/tollgate:task --brief <id>` takes the brief as its request. The brainstorm signs nothing and changes no code.

1. **Questions first.** The builder asks one question at a time: the problem, who uses it, the hard limits, how it is done today, what done means, and what is out of scope. The advisor answers each one, or skips it; a skipped question goes under `Open questions` in the brief. The builder may ask at most 5 questions. Then it must state the idea.
2. **Readiness.** The builder states the idea in 2 or 3 sentences. The advisor confirms the statement, or adds what it misses. After 3 additions with no confirmation, the brainstorm parks.
3. **The analysis.** When the project has code, or a path of the setting `spec` exists, the analyst reads it, as for a task, so the lenses read facts, not guesses.
4. **Four lenses.** Four reviewers challenge the idea, each with a fresh context and the same inputs: need, user, risk, and shape. None reads the others' findings. In background mode they run side by side.
5. **The brief.** The builder merges what the lenses found and writes `.tollgate/briefs/<id>.md`: Overview, Analysis, Goals, Non-goals, Alternatives weighed, and Open questions. `gate/brief.py` checks the format, and the builder writes it again with the problem, up to 3 times.

The advisor is the same chain as for a task, so a brainstorm runs to its end with an AI advisor alone. Each question and answer goes in the log, `logs/brainstorm-<id>.jsonl`. In session mode the brainstorm runs through `driver.step next --brainstorm <id>` and `answer`, with the same replay as a task.

In the task, the brief's goals and non-goals set the scope of the plan. Each question under `Open questions` in the brief must be an assumption of the plan, so the advisor answers it before the plan is locked. A brief of size `roadmap` says the idea is too big for one task: the task skill writes a roadmap from it, as in [Large requests](#large-requests), and the task driver refuses it.

### The fix round

When the first review of the whole task finds a blocking or major problem, the task runs one fix round, with no checkpoint first. The builder writes a new version of the plan, with the findings as its hint: it keeps every ticket of the plan unchanged and adds tickets for the fix, each with a locked test. Before the builder writes it, the task's copy gets the change that the tickets built, from the last ready change, in its work tree; the plan's branch still holds only the plan and its tickets. In a new ticket, behavior that is already built is a `keeps behavior` item, whose test passes now; only what the fix adds is a plain must-have item, whose new test fails until the fix is built. The plan is reviewed, approved, and locked again; the store refuses a new version that changes or drops a ticket of the old one, and keeps the old lock in `plans/replaced/`. The builder's answer steps of that review get the locked tickets as `locked_tickets`, and the driver puts back a locked ticket that an answer changed, with a `plan_invalid` event, so the refusal never comes as late as the lock. The new tickets are built, and the whole task is reviewed again. The task always builds its tickets from the plan as its last plan lock has it, never from a plan the builder is still writing in the task's copy. If that review still finds a blocking or major problem, the task parks and nothing merges, whatever the advisor would say: the merges wait until the review passes. A ticket that does not end ready, parked or stopped, stops the tickets after it, since each builds on the one before.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/task-lifecycle-dark.svg">
  <img alt="The life of a task: analysis, plan, approval, the tickets built in order and held, the review of the whole task, one fix round when it finds problems, then the merges in plan order" src="diagrams/task-lifecycle.svg">
</picture>

## How one ticket is built

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/one-ticket-dark.svg">
  <img alt="How one ticket is built" src="diagrams/one-ticket.svg">
</picture>

### The steps

1. The test writer writes one test at a seam for each must-have item, in the builder's copy.
2. A reviewer confirms four things for each test: it fits its item, it can fail, its expected result does not come from the code it tests, and it is the kind of test its item is marked with.
3. The driver fetches the ticket and its tests into the branch `tg/<ticket id>` of your project. The store locks them: it runs the tests once in a clean clone with main's harness, confirms that none passes yet, and signs the lock.
4. The builder builds in its copy, from the best try so far. It writes its own smaller tests as it goes.
5. The driver puts back main's harness and the locked files, runs the checks, and runs the gate. If a locked test or a check fails, the findings go to the builder and step 4 repeats.
6. A reviewer scores the checklist items that have no test. From tier 2, another reviewer reads the change through the review lens. For tier 3 and 4, a third reads it through the security lens. A finding counts only when it points to a line inside the change. A reviewer that answers fail with no blocking finding at all still fails the try. A low score or a blocking finding goes to the builder, and step 4 repeats. A major finding of the review or security lens should be fixed now: a try that has only major findings goes back to the builder with them, once for a ticket, and only while the ticket has a try left after it. That try passes the gate, so the store records its pass first, and it is the fallback: when the next try does not pass, the ticket gets ready with the fallback. After that, such a try passes, and the try's log keeps its major findings under `majors`, so a major finding alone never parks a ticket.
7. The store signs a pass record. The driver fetches the try into `tg/<ticket id>`, and the store marks it as a ready change.
8. With `auto_merge` on, the store merges a tier 1 or 2 change. You merge the rest with `/tollgate:merge`.

### The score

The score is the share of checklist items that passed. An item with a test passes when its tests pass. A reviewer decides each other item and names the file and the line that prove it. The driver confirms that the line exists.

A try passes only with a score of 70 or more. Every must-have item has a locked test, so no score can excuse a failed must-have item. The driver keeps the best try so far and builds the next one from it.

### When the tries stop

The driver stops and asks the advisor when one of these is true:

- The ticket used 3 tries.
- The last try failed the same way as the try before it.
- One try ran for 30 minutes.

The driver parks the ticket when it cost 10 USD, or the task cost 60 USD. No advisor can lift a cost stop. Each number is a setting under `stop`.

### Special cases

- **A test is rejected** by the reviewer in step 2 or by the store in step 3. It goes back to the test writer, at most 2 times, then to the advisor. A `retry` or `stronger` from the advisor then sends the tests back to the test writer with the hint, for up to 2 more rejections.
- **A new test names a seam that is not built yet.** In a compiled or type-checked language, such as Go, TypeScript, Python with mypy, or Rust, a test that calls a new function does not compile until the builder writes it, so a vet, type, or lint check fails on the new tests. The driver does not send the tests back for that failure when each line of it that names a locked test file is a compile or type error about a name that ends one of the ticket's seams, such as `undefined: logging.Cause` for the seam `internal/logging.Cause` (`gate/checks.py::only_missing_seams`). Any other error on the new tests still sends them back. The log notes the waiting checks on `tests_reviewed`, and the gate runs every check on each try, where it must pass.
- **A Go package does not compile before the build.** In Go, that same error stops the whole package of the new test file from compiling. gotestsum then writes no case for its tests: the package is an empty test suite, or one failed `TestMain`, and the output has the line `FAIL <import path> [build failed]`. In a Go report, a test belongs to a locked file when the package's import path ends with the file's folder and the file defines the test's function. The store counts the ticket's tests of that file as failing before the build when all of these hold: the package failed to build, as the failed `TestMain` or the `[build failed]` line, and the report has no other test of that package, each test is defined in the file and was not there at the start, and each line of the test check's output that names a file of that folder is a compile error about a seam's name. The lock names those tests as `<import path>::<name>`, under `tests` and `not_built`. The gate requires each of them to run and pass on each try, as any locked test. A `keeps behavior` test in another package still must pass before the build. Any other compile error refuses the lock.
- **An item keeps existing behavior.** Its test passes before the build. The ticket marks the item `keeps behavior`, and the store accepts that one test as passing.
- **A kept test shares a compile unit with the new tests.** In a compiled language, an old test and the new tests may build as one unit, such as one Go package. While the new tests call a seam that is not there, the old test cannot run, so the report at the ticket's commit has no case for it. The store then runs the tests once more, at the commit the ticket starts from, with main's harness: the code before the new tests. It takes a mapped `keeps behavior` test that did not run from that report, when the test's source is the same at the start and at the ticket's commit, and the rest of its file only gained lines, since a changed helper or TestMain could change what the test checks. It must have passed there. The lock names it under `tests`, `keeps_behavior`, and `kept_at_start`, so the gate requires it to pass after the build. The same source means the same test function, read by the parser of the file's language in `gate/manifest.py::TEST_SOURCES` (Python and Go today); a file of a language with no entry there must be unchanged as a whole. A new language adds a function from the file's bytes to `{test name: source}` there.
- **The tests cannot start before the build.** In a new project the first ticket may write the build file, such as `go.mod`. Until then the test command fails at its setup and writes no report. The store still locks the ticket when each locked test file is new, not in the commit the ticket starts from, the report is missing or lists no test, the test command ended with an error, and no item keeps behavior. The lock then names no test and is marked `failed_at_setup`. After the build, the gate takes the locked tests from the report of each try: the tests of the locked files that the ticket claims. The report must list them, they must cover each must-have item, and each must pass.

A locked test can be wrong. The builder cannot change it, so it says so in `locked_test_wrong`, with the test and the evidence, and the log keeps the claim on `builder_said`. When the try fails, the driver sends the tests back to the test writer with the claim, for the one replacement lock the store allows; a test that still blocks the build after it parks the ticket with a question for a person. When the try stops for another reason, such as a failed review, the claim goes into the advisor's reason, so the advisor judges it.

## What is locked, and who can change it

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/who-holds-the-key-dark.svg">
  <img alt="Who can change what: the AI works in its own copy, and the store keeps the records in the state folder" src="diagrams/who-holds-the-key.svg">
</picture>

### Who can do what

| Actor | Can | Cannot |
| --- | --- | --- |
| Builder and test writer | Edit files in their own copy. Run commands there. | Edit a locked file or the settings. Reach the state folder or the key folder. Push or merge. |
| Reviewer and advisor | Read their own copy. | Edit anything. Run a command. |
| Driver | Run the roles, the checks, and the gate. Fetch work from a copy into your project. | Write a record without the store. Merge without the store. |
| Store | Lock tests. Lock plans and write pass records. Write questions. Merge a ready change. | Write an approval. Merge a change with no pass record, or a tier 3 or 4 change with no approval. |
| You | Answer a question. Approve and merge with `/tollgate:merge`. Change the settings. | Nothing in Tollgate stops a commit you make with `--no-verify`. |

Each role works in a copy of the project in the state folder, `builders/<ticket>/`. The copy has no remote, so nothing in it can push. In background mode with `"isolation": "container"`, a role runs in Docker with only its copy mounted, read-only for a reviewer and the advisor. Only the model access variables go in. The key folder and the rest of the state folder never go in. In session mode, the hook keeps a role in its copy: see [Two ways to run the roles](#two-ways-to-run-the-roles).

## Two ways to run the roles

The driver decides every step, the gate scores every try, and the store signs and merges, in both modes. Only the place where a role runs differs. The setting `"runner"` picks the mode.

- **Session mode**, the default. Your own Claude Code session runs each role as one of Tollgate's subagents, step by step. Reviewers that read the same plan or the same change and not one another run at once, as parallel subagents. Nothing runs when your session is closed, and no Docker image or login inside Docker is needed.
- **Background mode.** The driver starts each role as its own `claude -p` process, in its isolation: Docker, or Claude Code's sandbox. It keeps running after you close Claude Code.

A roadmap runs in the same mode as a task. In session mode, `driver.step next --roadmap <id>` walks it: the checklist's tests, each task in order, the base suite after the base tasks, and the checklist at the end. The step file then notes the roadmap, so `answer` goes on with it. In background mode, `driver.roadmap` runs it as its own process.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/session-ticket-dark.svg">
  <img alt="One ticket in session mode: your session asks the driver for the next step, runs the role it names as a subagent in the copy, and hands back the answer, until the gate passes a try and the store signs its pass record" src="diagrams/session-ticket.svg">
</picture>

### The step file

The driver asks for each role through one caller. In session mode, the caller works out a key for the question: the SHA-256 of the role, the lens, and the inputs. If `logs/<log>.answers.jsonl` in the state folder already holds an answer with that key, the caller returns it. If not, it writes a step file, `run/steps/<run>/<key>.json`: the role, the lens, the full prompt, the copy, the key, and the task and ticket. `<run>` names the task, roadmap, or brainstorm, such as `task-retries`, so several tasks of one project can each wait at their own steps at the same time, each with its own run lock and its own replay note, `run/replays/<run>`. Then the driver lets go of its run lock and ends with 5. The store merges into main one change at a time, so two tasks that merge at once wait in turn.

`python3 -P -m driver.step next --project . --task <task id>` runs the driver up to the next step and prints it: the subagent to start, its prompt, its folder, and the key. `driver.step answer --project . --key <key> --nonce <nonce>` takes the subagent's whole answer on standard input. Each `next` prints a new nonce and keeps only its hash in the step file, so only the session that ran `next` can answer, and the hook refuses a role's plain run of a Tollgate command. These are defense in depth: see [Threat model](#threat-model). `answer` finds the step by its key, in whichever run waits for it, and refuses a key or a nonce that is not a waiting step's, and never prints them, and it refuses an answer with no final json block. A subagent that runs out of turns ends with such a part of its answer, so the first refusal of a step prints the same step again, with its key, its nonce, and a `retry` reason, and the session starts the subagent once more; the second refusal is an error. In session mode, each role's prompt names its turn limit and tells it to stop and answer when about 5 turns are left. It saves the answer, then runs `next` again. `next` also prints the end of the task, or a question for you, which you answer as `/tollgate:inbox` does. With `--roadmap <id>` in place of `--task`, `next` runs a roadmap the same way.

Some roles do not depend on one another: the three lenses of a plan review (simplicity, fresh_reader, readiness), the checklist reviewer of a try with the review lenses of its tier, and the lenses that challenge a brainstorm's idea. The driver asks for them together. Each that has no saved answer gets its own step file, and the driver pauses once for all of them. `next` then prints `{"steps": [...]}`, each step with its own key and nonce, so your session starts their subagents in parallel. `answer` saves each one; while others of the same run wait, it prints `{"saved": <key>, "waiting": [keys]}` and does not run the driver. The last answer runs `next`. The answers reach the driver in the order it asked for them, whatever order they came in, so the result is the same as one at a time. The replay note names the first of them. In background mode the driver asks the same roles one after another.

### The replay

Each `next` starts the driver again, and the driver repeats no finished step: it reads its log, as it does after any restart, and each role it asks again gets its saved answer at once. So a step costs one run of the driver up to the point where it stopped.

A step that was not finished runs again from its start, and logs the same events again up to the role that just answered. The run before went at least that far, so it logged them already. So when a run pauses, it notes the key of the role it paused at in `run/replay`, and the next run's logs write nothing until the run reaches that role again: once it gets that role's answer, or pauses at any role with no saved answer, each event is written. The rule goes by where the run is, not by what an event holds, so a check whose output changes on each run does not break it. A task that ends without a pause clears the note. `started` is logged once for a ticket or a task. So the log holds each event once, and the stop rules, the counts, and the report read only what really happened. The test writer's rounds are numbered, so two rounds that wrote the same tests stay two events. Background mode repeats no role, and its log writes every event.

Each try starts from the best try so far, so the driver resets the copy before the builder's step, which would wipe the builder's edits on a replay. So `answer`, for the builder or the test writer, saves the copy's work tree as a commit on `refs/tollgate/answers/<key>` in the copy. On a replay the caller puts that work back in the copy before it returns the saved answer.

### The copy of a subagent

The builder works in the driver's own copy, in the state folder, with no remote: not in a work tree that Claude Code makes. The subagent carries its type, such as `tollgate:builder`, and no driver names its copy. So the hook reads the copy from the step file, and only when the step is for that same role. No role can write the state folder, so no role can change the step file. A builder that writes a plan gets the planner's rules from the step file too.

### Cost

In session mode the cost of a role is your session's own, and Tollgate cannot see it. The log keeps `cost_usd: null`, not 0, and the report shows the cost as unknown. The two USD stop rules apply only in background mode. Each subagent has a limit of turns instead, in `maxTurns`: the builder the most, then the test writer, and the analyst, the reviewer, and the advisor few.

### Threat model

Session mode stops a role's mistakes and shortcuts, such as editing locked tests, skipping checks, pushing, or touching Tollgate's state, but not a role that deliberately attacks your machine, because roles and the code they write run as you and can reach your signing key. For that, use background mode, which runs roles in a container with no key.

What holds in session mode:

- **Mistakes and shortcuts.** The hook refuses a role's edit of a locked file, of `.tollgate/`, of the copy's `.git` folder, and of the state folder; its read of the key folder and of the state folder outside its copy; a command that names those folders; a push, a merge into main, and `--no-verify`; and a plain run of a Tollgate command that changes its state, such as `driver.step answer`. These rules read the text of a call, so they catch what a role does by mistake or as a shortcut, not what it hides on purpose.
- **Defense in depth.** `answer` needs a nonce that only `next` prints; the step file keeps only its hash. Each saved answer carries a code made with the machine secret, and a replay ignores an answer whose code does not verify. The driver runs git in a copy with no hooks, no file system monitor, and no replace refs, and puts the builder's work back from the commit the saved answer names.
- **The gate and the store.** The gate scores each try with no AI. A ticket's pass record names the commit it started from, and the store and the merge check refuse it until that commit is on main, so each ticket merges with its own tier and approval.

What does not hold: a role, or a test or script it writes, runs as you, so it can reach the state folder, write a record, and get past every rule above. Records are plain files, not signed. Use background mode, with `"isolation": "container"`, for tier 3 and 4 work from a source you do not trust: the state folder and the key folder are never mounted in the container.

### Before a task starts

A task that cannot pass stops at once. `driver.task` and `driver.step next` refuse to start when the settings on main cannot be read, and say to run `/tollgate:init`.

### The gate

The gate uses no AI, so it gives the same answer every time. The driver runs it on your machine after each try. It reads the settings from main, never from the try. A try passes only when all six checks hold:

1. **Lock.** The ticket's lock is in the state folder, and names this ticket. The try is built on the lock's commit. The lock's commit added only tests and the ticket file.
2. **Plan.** A plan lock lists this ticket, with the same ticket text. Tier 1 needs none. The setting `require_plan` turns this check off.
3. **Locked files.** Every locked file has its fingerprint at the try.
4. **Tier.** The gate works out the tier from the files the try changes.
5. **Tests.** Every check passes. Every locked test ran and passed. No locked test was skipped.
6. **Review.** A try of tier 2 or more has a passed review.

Then the score decides: a try needs 70 or more. The builder can fix a failure of checks 3 and 5. A failure of any other check goes to the advisor.

### What the gate proves

The gate proves that the locked tests ran unchanged and passed, and that the try scored 70 or more, on your machine. Its proof is as trustworthy as your machine and its state folder.

## Two layers of enforcement

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/two-layers-dark.svg">
  <img alt="Two layers enforce the rules: Claude Code hooks and permission rules refuse a bad tool call, then pre-commit checks each commit, merge, and push" src="diagrams/two-layers.svg">
</picture>

**Layer 1: Claude Code hooks and policies.** Each role gets a policy: a Claude Code settings file with permission rules. The rules deny an edit of a locked file, of the settings, of the key folder, and of the state folder outside the role's copy. They deny `git push`, `git merge`, and the commands that skip the checks. A deny rule matches a command by its start only, so the hook checks every tool call again. The plugin runs the same hook in your own session in the project.

**Layer 2: pre-commit.** `init` copies Tollgate's checks into `.tollgate/checks/` and adds one hook, `tollgate`, to `.pre-commit-config.yaml`. The config is the project's own: `init` keeps every other repo and hook as it is. One script, `tollgate_checks.py`, runs Tollgate's checks of a git step side by side. The hook `tollgate` runs it, for all four git steps. pre-commit does not tell a hook which step runs it, so the script works the step out: a push when pre-commit gives the name of the remote and git gives no index file, a commit message when the first argument is a message file, a merge when git names the commits it merges, and a commit in any other case. The entry runs the script with `uv run --no-project python`, so it does not depend on which `python3` comes first on the PATH. In Tollgate's own project the entry runs `setup/tollgate_checks.py`, the source, and `.tollgate/checks/` holds no copy. The writing check under `checks` in the settings runs the same way.

| Git step | Check | What it refuses |
| --- | --- | --- |
| Commit | `check_locked.py` | A change to a locked file, or to `.tollgate/` or the pre-commit config |
| Commit | `check_writing.py` | Words to avoid, filler, and long dashes in the staged change. A doc that points at a file or a name the change removes. |
| Commit message | `check_commit_msg.py` | A subject that is not `type(scope): subject`, or a line that credits an AI |
| Merge | `check_merge.py merge` | A merge into main with no pass record, or a tier 3 or 4 merge with no approval |
| Push | `check_merge.py push` | A push to main with a merge that has no pass record, or a commit made on main itself |

The checks of one git step run side by side, and the script prints each check's output and a PASS or FAIL line when the check ends. The setting `parallel` sets how many run at once: `true`, the default, for all of them, a whole number for that many, or `false` for one at a time.

The project's own checks, such as its tests and its lint, are hooks of their own in the config, and pre-commit runs them. The settings name the hook of each check, so the project keeps its checks without Tollgate. `init` writes a check that the settings still keep as a command, under `run`, once as a local hook `check-<name>` that runs before each push, and the settings then name that hook. pre-commit runs the hooks one after another, so the project's checks do not run side by side. To run them side by side, make one hook that runs them all, such as `make -j check`.

A merge may change locked files when the commit it merges passed the gate. Commits from before `init` are not checked.

**Behind both layers: the records.** The store and `check_merge.py` read every record from the state folder, which no role may write. A role that gets past its hook can write there, so use background mode for work you do not trust.

**The limit.** `git commit --no-verify` and `git push --no-verify` skip pre-commit. A person can still change main by hand.

## The reviewers

Every reviewer is the same role with a different lens. The analyst, a separate read-only role, reads the project before the plan.

| Lens | Reads | When | Looks for |
| --- | --- | --- | --- |
| Simplicity | The whole plan and the analysis | Plan review | Is this needed? Does the code already have it? Is anything built for a need nobody has? Does it fit the architecture the analysis found? |
| Fresh reader | The plan alone | Plan review | It writes in its own words what the plan proposes. A part it cannot restate is a blocking finding. |
| Readiness | The plan, the analysis, and the code | Plan review | Does every file and name that the plan says exists really exist? Is any design decision left to the builder? Does the plan rest on an assumption it does not ask about? Does each risk have a checklist item? Does the plan keep each file that a check of the project requires, such as a README? Is each reason under Not covered true? |
| Test fit | A ticket and its tests | Before the lock | The four points in step 2. |
| Checklist | A ticket and the change | Step 6, every tier | Each item with no test. |
| Review | The ticket, the change, the project's own standards | Step 6, from tier 2, and once for the whole task | A bug or a missing requirement. A security hole. A break from the architecture. A wrong assumption. A missing error case. A slower path. A test gap. Needless complexity, though a file that a check of the project requires is never unrequested. |
| Security | The change | Step 6, tier 3 and 4 | Ways to misuse the change. |
| Need | The idea, the answers, and the analysis | Brainstorm | Is it needed? Do the project or a library already do most of it? |
| User | The idea, the answers, and the analysis | Brainstorm | Who uses it? Is it easy to use? Does each error say what to do? |
| Risk | The idea, the answers, and the analysis | Brainstorm | What it costs. How to migrate and roll back. How you debug it when it breaks. |
| Shape | The idea, the answers, and the analysis | Brainstorm | What it costs to keep. The trade-off it takes for granted, and a way around it. |

### Plan review

The builder writes the plan, with its tickets, checklists, must-have marks, and kinds of test. The first three lenses read it in each round. The builder answers each finding in one of three ways:

- **Fix.** The finding is right.
- **Contest.** The finding is wrong, and the builder points to evidence that the reviewer could read.
- **Escalate.** The builder cannot settle it. The advisor decides.

The review ends after one round with no open blocking or major finding. The limit is 4 rounds. A minor finding never keeps a round open, but it can still be a real error, so it goes to the builder too: after the open findings of its round, where it cannot be escalated, or, in a round with no open finding, in one answer step of its own. When that answer changes the plan or a ticket, one more round reviews the change, and a round after it with only minor findings is clean. In round 4 the minor findings only go into the log. Then the advisor approves the plan or sends it back. When round 4 still has open findings, the driver returns them after a line that names the limit, and the question to the advisor or the person lists them.

Round 1 reads the whole plan. Each round after it reviews the last answer, not the whole plan again, because fresh reviewers find new points in text that an earlier round accepted, and a plan would then use its 4 rounds on wording. Each lens gets two more inputs: `earlier_findings`, its own findings of the round before, each with the builder's answer, and `diff`, the unified diff of the plan and its tickets since that round. The lens judges whether each earlier finding is resolved, and whether the change adds a problem, and marks each finding with `about`: `earlier`, `change`, or `unchanged`. A blocking finding always keeps the round open. A major finding keeps it open only when it is about an earlier finding or the change. Any other major finding, on text that did not change, is logged as `plan_findings_demoted` and goes to the builder as a minor finding, so it reopens no round. A round that follows an answer to minor findings only is such a change-only round too. The driver fetches the approved plan into `tg-plan/<task id>`, and the store signs the plan lock. The plan's commit holds only the plan, the analysis, and the tickets its header lists: the driver deletes a ticket that names the plan but that the header no longer lists, such as one the builder wrote in an early round and then dropped, and the store refuses a plan commit that still holds one.

A reviewer that fails to answer gets one retry. After that, the round does not count as clean.

The builder and the lenses that read more than the plan get the decisions made on the plan so far, from the task's log: who decided, the decision, and its hint. A hint can settle a fact, such as an owner's confirmation of a doc change, so a reviewer reads it as a source. The fresh reader reads the plan alone.

## Tiers

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/tiers-dark.svg">
  <img alt="The four tiers: a small fix, a normal change, login and payments, and the test setup and settings" src="diagrams/tiers.svg">
</picture>

No AI picks the tier. Path rules in the settings on main map paths to tiers, and the gate works out the tier from the files a change touches. Locked test files do not count.

| Tier | When | Plan lock | Review | Who merges |
| --- | --- | --- | --- | --- |
| 1 | At most 3 files and 50 lines, and no path rule matches | No | Checklist | The store, when `auto_merge` is on |
| 2 | Every other change | Yes | Checklist, review | The store, when `auto_merge` is on |
| 3 | A path under `tiers.3`. Examples: login, payments, user data | Yes | Checklist, review, security | You, with your approval |
| 4 | Harness, or a path under `tiers.4`. Examples: database changes, production settings, Tollgate's own rules | Yes | Checklist, review, security | You, with your approval |

`auto_merge` is off by default. Then you merge every change.

## The advisor

The driver asks the advisor when a plan needs approval, when the tries stopped, and at each checkpoint. The setting `checkpoints` lists them: `plan` (the approval of the plan), `ticket` (after each ready ticket), and `task` (after the review of the whole task). The default is `["plan", "task"]`. A plan with an assumption, and a task review with a blocking finding, go to the advisor even when their checkpoint is off.

The advisor chain is `ai.advisor` in the settings: a list of commands and the word `person`, asked in order. The default is `["person"]`. For a person, the store writes a question in the state folder, and the ticket waits. The driver moves on to other tickets, and reads the answer when it starts again. You answer with `/tollgate:inbox`.

| Decision | What the driver does |
| --- | --- |
| Approve | Has the store sign the plan lock. A hint given with it goes to the test writer and the builder of each ticket of that lock, as `approval_hint`. The plan is locked, so the hint changes no ticket: the roles follow it where the ticket and the locked tests allow. To change the plan, send it back. |
| Send back | Parks the task. The plan needs a new approval. |
| Retry | Runs one more try, with the advisor's hint. |
| Stronger | Runs one more try with `ai.stronger_builder`. |
| Split | Parks the ticket. The driver does not cut it into new tickets. |
| Park | Parks the ticket. |
| Continue | At a checkpoint: the task goes on. |
| Stop | At a checkpoint: parks the task. |

Three limits:

- No advisor can make the gate pass.
- No advisor can lift a cost stop.
- An AI advisor decides at most 2 times for one ticket. After that, only a person decides. A checkpoint does not count against this limit.

## Large requests

A request that needs more than one plan gets a roadmap: one file with an ordered list of tasks. Example: "build an order service with login and an admin page".

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/large-requests-dark.svg">
  <img alt="A large request in three parts" src="diagrams/large-requests.svg">
</picture>

1. **The base.** Tasks that make the project build, test, and run. Then the base suite must pass: five commands under `base` in the settings. They prove that the checks run, the tests run side by side, the test setup starts and stops, a wrong setting stops the start, and one request writes one structured log line. A command that is not set fails, so each gap becomes a task.
2. **The skeleton.** First, one task for each rule that all code must follow: the shape of an error, the format of the API, the login check, the fields of a log line. Then the thinnest path through the riskiest feature, through every one of those rules.
3. **The features.** Each feature is a task.

Three rules keep the roadmap honest:

- It lists only the features that are clear now. The rest stay under "Not yet specified".
- Shared code is not planned up front. It moves to a shared place when a second feature needs it.
- The roadmap has its own checklist ticket. Its tests are locked before the first task and must pass after the last.

A task that does not finish stops the roadmap, because later tasks build on it.

## One project or many

One project needs nothing but itself. You run `/tollgate:init` inside it.

For many projects, a home is optional: one git repository on your machine. It is not a copy of Tollgate, so an upgrade of Tollgate never touches it.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/home-dark.svg">
  <img alt="A home with three projects" src="diagrams/home.svg">
</picture>

Each project sits inside the home as a git submodule: a link from the home to the project's own repository.

| You type | What happens |
| --- | --- |
| `/tollgate:home` | Creates the home in an empty folder. |
| `/tollgate:home add <path or git address>` | Adds the project as a submodule under `projects/<name>`, and says whether it has Tollgate settings. The home does not track which commit the project is on, so the project's own commits never show the home as changed. |
| `/tollgate:home sync` | Copies the shared lenses into each project, on the branch `tollgate-sync`. You approve and merge each one. |
| `/tollgate:inbox` | Lists every question that waits for you, across all projects. |

The home never weakens a project. Each project keeps its own settings, and the gate reads them from the project's main.

### Learning

`/tollgate:retro` reads the logs of a task and shows numbers, for example the share of tickets that reached the advisor. It writes at most 3 lessons, each as a ticket. A lesson becomes a test, a check, a change to a lens, or a stricter rule.

Tollgate can make a rule stricter. Only you make a rule looser: the settings and the lenses are harness, so a change to them is tier 4 and needs your approval.

## How you use it

You install the plugin in Claude Code once. There is no separate program to install or keep up to date.

```
/plugin marketplace add tollgate-ai/tollgate
/plugin install tollgate@tollgate-ai
```

The machine needs Python 3.11, git, uv, and pre-commit. Background mode with the container isolation also needs Docker.

| You type in Claude Code | What happens |
| --- | --- |
| `/tollgate:analyze` | Reads the project and reports how it is built, with a citation for each fact. It changes no file. |
| `/tollgate:brainstorm` | Turns a rough idea into a brief: questions to the advisor, a statement the advisor confirms, the analysis, and four lenses that challenge the idea. |
| `/tollgate:init` | Sets up one project. It reads the project, asks what it cannot find, writes the settings, and installs pre-commit. |
| `/tollgate:task Add retries to webhook delivery` | Runs a task. With `--brief <id>`, the brief is the request. In session mode your session runs each role, step by step, and the reviewers of one plan or change in parallel. In background mode the driver runs on its own, so you can close Claude Code. |
| `/tollgate:status` | Shows Done and Still to do, and warns when a layer of checks is missing. |
| `/tollgate:inbox` | Lists the questions that wait for you, and records your answer. |
| `/tollgate:merge` | Lists the ready changes, asks you to sign the approval for tier 3 and 4, and merges. |
| `/tollgate:retro` | Shows the numbers for a finished task and writes lessons as tickets. |
| `/tollgate:home` | Works across many projects. |
| `/tollgate:docs` | Writes docs, comments, and commit messages in plain words. |
| `/tollgate:diagram` | Draws a diagram as an SVG file, in light and dark. |

When a task ends, you read the report. It has:

- Whether the task is done, or which tickets wait or are parked, and why.
- Each ticket, with its tier, its tries, its score, and its branch.
- Each decision, with who made it and why.
- The cost and the time.

**A reader who does not write code can stop here.**

## Reference: how it is built

### Parts

| Part | Files | What it does |
| --- | --- | --- |
| State | `gate/state.py` | The paths of the state folder and the key folder, the records, and the machine secret. |
| Store | `gate/store.py`, `gate/freeze.py`, `gate/plantag.py`, `gate/prepare.py` | Lock tests, lock plans, write questions, write pass records, mark ready changes, merge. |
| Gate | `gate/gate.py`, `gate/tiers.py`, `gate/review.py`, `gate/checks.py` | The six checks, the tier, the review from tier 2, and running the checks. |
| Formats | `gate/settings.py`, `gate/analysis.py`, `gate/brief.py`, `gate/tickets.py`, `gate/plan.py`, `gate/roadmap.py`, `gate/manifest.py`, `gate/junit.py` | Read and check each file format. |
| Driver | `driver/driver.py`, `driver/task.py`, `driver/roadmap.py`, `driver/brainstorm.py` | Run one ticket, one task, one roadmap, one brainstorm. |
| Session mode | `driver/session.py`, `driver/step.py` | The caller of each role in each mode, the step file, the saved answers, and the `next` and `answer` commands. |
| Stop and advisor | `driver/stop.py`, `driver/advisor.py` | When to stop, and how to ask. |
| Roles | `driver/roles.py`, `roles/`, `lenses/`, `agents/` | Run one AI session in its copy and its isolation. `agents/` holds each role as a Claude Code subagent, written from its role file by `setup/agents.py`. |
| Log and report | `driver/log.py`, `driver/history.py`, `driver/report.py`, `driver/retro.py` | One line per event. Read the logs back. |
| Hook and policies | `hooks/guard.py`, `hooks/policy.py`, `hooks/hooks.json` | Layer 1. |
| Setup | `setup/init.py`, `setup/precommit.py`, `setup/check_*.py`, `setup/health.py`, `setup/merge.py`, `setup/inbox.py`, `setup/home.py`, `setup/sync_rules.py` | Layer 2, and the scripts behind the skills. |
| Skills | `skills/` | The instructions Claude Code follows for each command you type. |

### What is a skill and what is a script

A skill is instructions, so two runs can differ. A script gives the same result every time. Tollgate uses each where it fits.

| Work | Done by | Why |
| --- | --- | --- |
| Find the project's test commands. Propose the risky paths. Ask you questions. | A skill | It needs judgment. |
| Write the settings keys, the checks, and the pre-commit config. Add a submodule. Sign an approval. Merge. | A setup script that the skill calls | It must come out the same every time, and it can be tested. |
| Run a task for hours. | The driver | An AI session loses its place on a long loop. |
| Decide whether a try passes. | The gate | It must use no AI. |

Each setup script does one step, can run twice without harm, and prints one line for each step.

### How a task starts and keeps running

`/tollgate:task` checks the settings and the two layers, and writes the request to `.tollgate/requests/<task id>.md`. In session mode it then loops on `driver.step next` and `answer` until the task ends or waits for you. In background mode it starts `driver.task` or `driver.roadmap` as its own process, with Python's `subprocess.Popen(..., start_new_session=True)`, which works on Linux and macOS. Then it returns. The driver takes a run lock for each ticket and each task in `run/`, so two drivers cannot build the same ticket. A lock whose process is gone is taken over. `/tollgate:status` reads the logs.

### The state folder

```
~/.local/state/tollgate/<name>-<hash>/      Linux; macOS: ~/Library/Application Support/tollgate/
  locks/<ticket>.json and .sig              a lock
  locks/replaced/<ticket>.json and .sig     the lock a replacement took the place of
  plans/<task>.json and .sig                a plan lock
  passes/<commit>.json and .sig             a pass record
  passes/ready/<ticket>.json and .sig       a ready change
  approvals/<commit>.json and .sig          your approval
  questions/<n>.json                        a question; <n>.closed.json once answered
  builders/<ticket>/                        the builder's copy of the project
  logs/  reports/  lessons/  run/           logs, reports, lessons, run locks
~/.config/tollgate/secret                    the machine secret
```

`<hash>` is the first 10 characters of the SHA-256 of the project's real path. `TOLLGATE_STATE` and `TOLLGATE_KEYS` set the two roots, for tests.

### In a project

```
.tollgate/
  settings.json              committed: the rules, read from main
  checks/                    committed: Tollgate's checks, copied by init
  lenses/                    committed, only for lenses the project changes
  requests/<task>.md         the request, in your words
  ideas/<id>.md              the idea of a brainstorm, in your words
  briefs/<id>.md             the brief that a brainstorm wrote
  analysis/<task>.md         the analysis as the driver checked it, on tg-plan/<task>
  plans/<task>.md            the plan, on tg-plan/<task>
  tickets/<id>/ticket.md     a ticket, on tg/<id>
  roadmaps/<id>.md           a roadmap
.pre-commit-config.yaml      committed: written by init
```

### Records

Each record is JSON with sorted keys and a final newline.

| Record | Fields |
| --- | --- |
| Lock | `ticket`, `base_commit`, `commit`, `manifest` (`files`, `tests`, `keeps_behavior`), `number`, `at` |
| Plan lock | `task`, `commit`, `files`, `at` |
| Pass record | `commit`, `ticket`, `tier`, `lock_sha256`, `checks`, `score`, `at` |
| Approval | `commit`, `ticket`, `approver`, `at` |
| Question | `n`, `title`, `body`, `options`, `answer` (`decision`, `hint`, `at`) |

### A ticket

```
---
id: T-042
plan: webhook-retries
seams: POST /webhooks/deliver
tests: tests/test_webhook_retries.py
---
## Current behavior
A failed delivery is dropped.

## Desired behavior
A failed delivery is tried again up to 5 times, with a longer wait each time.

## Checklist
- [C1, must, happy] A delivery that fails twice and then succeeds is marked delivered.
- [C2, must, error] A delivery that fails 5 times is marked failed.
- [C3, must, keeps behavior, regression] A delivery that succeeds at once is sent one time.
- [C4, boundary] A delivery that fails 4 times and then succeeds is marked delivered.
- [C5, concurrency] 2 deliveries to one address at the same time are each sent one time.

## Not covered
- edge: a delivery has no input that can be empty.
- integration: C1 already goes through the web address and the queue.
- e2e: there is no screen.
- security: the change reads nothing new from a sender.
- performance: no time limit is asked for.

## Out of scope
A screen to retry by hand.
```

A ticket describes behavior and seams. `tests` lists every file the locked tests need, and a file the test map names is one of them too. A new test names its item, such as `test_c1_succeeds_on_third_try`. A `keeps behavior` item names the existing tests that already cover it, as `file::test`, and their files go under `tests`. The test writer points the item at them in its answer, and never renames or edits them. The driver writes that answer as the test map, `.tollgate/tickets/<ticket id>/tests.json`, a list of `{"item", "file", "name"}`, and the store's lock maps each test to its item by the map or by its name. Each mapped test must run, a `keeps behavior` test must pass before the build (at the start, when the new tests stop it from building), and a new test must fail. A test that was in a test file at the ticket's start must stay as it was: the lock refuses one whose source changed, or that is gone or renamed, read test by test by the parser of the file's language (`gate/manifest.py::TEST_SOURCES`); a test file of a language with no parser there must stay unchanged as a whole, and a mapped test that was not there at the start must be named for its item. New tests may go next to the old ones. Only a new test maps to an item by its name: a test that was there at the start, such as an earlier ticket's `test_c1_`, counts only through the map. Each test file and the map are locked whole, and a file none of whose tests maps to an item is refused. The gate also holds a try to the locks of the earlier tickets it builds on: a file that only an earlier ticket locked must be as that lock froze it, and in a file that this ticket's own lock covers too, each test the earlier lock froze must still be there, unchanged. The lock records which test covers which item, and the score reads it. The optional header key `fixes` names the bug that the ticket fixes, in one line.

A checklist has 3 to 10 items. Each item may carry one kind of test: `happy`, `edge`, `boundary`, `error`, `regression`, `integration`, `e2e`, `security`, `performance`, or `concurrency`. The gate works out from the marks which kinds the ticket covers:

- Each of the 10 kinds is marked on an item, or listed under the optional section Not covered, one line for each kind: `- <kind>: <reason>`. The reason cannot be empty. A kind cannot be both.
- A ticket with an item that is not `keeps behavior` needs a `happy` item and an `error` item. A ticket with `fixes` needs a `regression` item. These kinds cannot go under Not covered.
- The text of a `performance` or `concurrency` item holds a number, such as a time, a count, or a size, so that a test can fail.

A ticket that marks no kind fails, and the message says what to add.

### A lock

```json
{
  "at": "2026-11-22T10:14:03Z",
  "base_commit": "<main's commit>",
  "commit": "<the commit of the ticket and its tests>",
  "manifest": {
    "files": {
      ".tollgate/tickets/T-042/ticket.md": "sha256:9b1e...",
      "tests/test_webhook_retries.py": "sha256:c07a..."
    },
    "items": {
      "tests.test_webhook_retries::test_c1_succeeds_on_third_try": "C1",
      "tests.test_webhook_retries::test_c2_fails_after_five": "C2",
      "tests.test_webhook_retries::test_c3_sent_once": "C3"
    },
    "keeps_behavior": ["tests.test_webhook_retries::test_c3_sent_once"],
    "tests": [
      "tests.test_webhook_retries::test_c1_succeeds_on_third_try",
      "tests.test_webhook_retries::test_c2_fails_after_five",
      "tests.test_webhook_retries::test_c3_sent_once"
    ]
  },
  "number": 1,
  "ticket": "T-042"
}
```

### Settings

```json
{
  "checks": {
    "tests": {"hook": "check-tests", "report": "build/junit.xml"},
    "style": {"hook": "check-style"},
    "types": {"hook": "check-types"},
    "secrets": {"hook": "gitleaks"},
    "writing": {"run": "uv run --no-project python .tollgate/checks/check_writing.py"}
  },
  "harness": ["Makefile"],
  "tiers": {
    "3": ["src/auth/**", "src/payments/**"],
    "4": ["migrations/**", "deploy/**"]
  },
  "ai": {
    "builder": "claude -p --model sonnet",
    "reviewer": "claude -p --model opus",
    "advisor": ["claude -p --model opus", "person"],
    "stronger_builder": "claude -p --model opus"
  },
  "runner": "session",
  "isolation": "container",
  "container_image": "my-builder:latest",
  "stop": {"attempts": 3, "attempt_minutes": 30, "ticket_usd": 10, "task_usd": 60},
  "pass_score": 70,
  "require_plan": true,
  "spec": ["docs/design.md", "docs/architecture/"],
  "checkpoints": ["plan", "task"],
  "auto_merge": false
}
```

`.tollgate/settings.json` is a locked file: the locked-file check refuses a commit that changes it, so a role's commit cannot loosen the rules. You change it on main and commit it yourself, from your own terminal, with `git commit --no-verify`, as `init` does. Tollgate's hook refuses `--no-verify` inside Claude Code.

Each check names a hook of the project's `.pre-commit-config.yaml`. The gate runs it in the builder's copy with `pre-commit run <hook> --all-files --hook-stage <stage>`, at a push when the hook runs there, and reads the JUnit report that `report` names. A check may set its own time limit in `minutes`. The writing check is Tollgate's own, so it keeps its command under `run`; no other check may. `setup.health` warns when a check names a hook that the config does not have.

The hooks, the image, and the model names are examples. `"runner"` is `"session"` (the default) or `"background"`; `ai`, `isolation`, `container_image`, and the USD stops count only in background mode. Without `container_image`, a role runs in Docker's own Claude Code image, `docker/sandbox-templates:claude-code`, pinned by digest in `gate/settings.py`. It has git, Python, Node, and Go. Name your own image when the project's tests need more. CI runs `tests/proof_container.py` on the pinned image, and a weekly workflow proposes a newer build once that proof passes on it. `"spec"` is optional: a list of files and folders, relative to the project root, with no `..`. List the folder of decision records in `spec`: the analyst then treats each accepted record as a specified fact, and a record that a newer one supersedes is left out. The built-in harness counts as tier 4 without a line in `tiers`. The settings file rejects an unknown key.

### What the gate returns

`python3 -m gate.gate --ticket <id> --results <file>` ends with 0 for pass, 1 for fail, and 2 when it could not run. It prints one line of JSON for each finding, then the tier:

```json
{"check": "locked_files", "rule": 3, "file": "tests/test_webhook_retries.py", "message": "differs from the lock", "fixable": true}
{"tier": 2, "passed": false}
```

### A line of the log

```json
{"at": "2026-11-22T10:14:03Z", "ticket": "T-042", "event": "try_failed", "attempt": 2, "failed": ["tests"], "cost_usd": 1.84}
```

Each ticket has its own log file, and each task has one. One file has one writer.

### The contract with Claude Code

- The driver runs the command from the settings, for example `claude -p`, with `--output-format json`, `--allowedTools <list>`, and `--settings <the role's policy>`. It sends the prompt on standard input.
- The prompt is the role file, then the lens file, then the inputs as JSON.
- The driver reads `result` and `total_cost_usd` from the output.
- The role ends its answer with one JSON object in a fenced block. The driver reads the last such block.
- A fake role is any command that reads the prompt on standard input and prints the same JSON shape. The tests use fake roles.

## Reference: the rules that Tollgate carries

These rules live in the role files and the lens files. Each rule names what enforces it. A rule that nothing enforces is not in this list.

### A plan

| Rule | Enforced by |
| --- | --- |
| It states the problem, the goals, what it will not do, the design, and the options it rejected, each with a concrete reason. | Simplicity lens |
| It lists every file, marked create, modify, or delete. Every file marked modify or delete exists. | `gate/plan.py` for the marks, the readiness lens for the files |
| It leaves no design decision to the builder: no data shape, no API path, no dependency, no way to handle an error. | Readiness lens |
| It names every seam. It prefers a seam that exists, and the highest one. | Readiness lens |
| It reuses what the code already has, and names it under Reuse. | Simplicity lens |
| It fits the architecture the analysis found, unless the request asks for a new one. | Simplicity lens |
| It names each edge case, failure, and security, speed, or compatibility risk under Risks, and each edge case and failure becomes a checklist item. | Readiness lens |
| It lists each unclear point under Assumptions, asked as a question, and waits for the advisor. | `gate/plan.py` and the driver |
| A reader with no context can restate it. | Fresh reader lens |
| It marks each assumption about an outside system that nobody has checked. | Readiness lens |

### A ticket

| Rule | Enforced by |
| --- | --- |
| It can be shown working alone. It goes through every layer that it needs. | Readiness lens |
| It states current behavior, desired behavior, a checklist, and what is out of scope. | `gate/tickets.py` |
| Each kind of test is marked on an item, or listed under Not covered with a reason. | `gate/tickets.py` |
| It has a `happy` and an `error` item, unless every item keeps behavior, and a `regression` item when it has `fixes`. | `gate/tickets.py` |
| A `performance` or `concurrency` item names a number that a test can check. | `gate/tickets.py` |
| Each must-have item has a locked test. | The store, when it locks the tests |
| Each item names a result that someone can observe. | Test fit lens |
| A change that prepares the way is its own ticket and goes first. | Readiness lens |
| A change that touches every caller of something has three tickets: add the new form, move the callers, remove the old form. | Readiness lens |

### A test

| Rule | Enforced by |
| --- | --- |
| It checks behavior through a seam, not the inside of the code. | Test fit lens |
| Its expected result comes from the ticket or a worked example, not from the code it tests. | Test fit lens |
| It replaces only what is outside the system: other services, time, randomness. | Test fit lens |
| It fails before the build, unless its item keeps behavior. | The store, when it locks the tests |
| A locked test is never edited, skipped, or removed. | The gate's locked files and tests checks, the policy, the hook, and pre-commit |

### A change

| Rule | Enforced by |
| --- | --- |
| It touches only the files that the plan lists. | Review lens |
| It adds no dependency that the plan does not name. | The tier: the dependency list is harness, so the change is tier 4 |
| It does only what the ticket asks. | Review lens |
| It has no stub, no unfinished marker, and no hidden error. | Review lens |
| New logic has a test. | Review lens |
| It follows the project's own written standards. | Review lens |
| It updates the docs that describe what it changes. | Review lens, and `check_writing.py` for links and names |
| A diagram is a spec drawn as SVG, never a Mermaid block. The builder writes the spec, and the driver draws the SVG files after each try. | The driver, and `check_writing.py` |

### A bug fix

A ticket for a bug adds one rule: its first must-have item is a test that fails on the bug. The store already requires this, because a locked test must fail before the build.

### Writing

These rules cover documents, code comments, and commit messages. The review lens reads for them. `check_writing.py` and `check_commit_msg.py` find the ones a script can find.

- Lead with the answer or the action.
- One instruction for each sentence. One topic for each paragraph.
- One word for each thing. The project keeps a word list, and each entry names the words to avoid.
- Everyday words. Explain a technical term once, in a few words.
- No chain of more than three nouns.
- Active voice with a named actor. No long dash.
- Say plainly when code is unfinished, untested, or unverified.
- Describe the thing as it is. Do not tell its history.
- A README answers four questions first: what it is, why it exists, how to start, and when to use it.
- A commit subject has the form `type(scope): subject`. The body says why. The author is the owner of the repository, and no line credits an AI.

### Tollgate's own skills, role files, and lens files

- Every step ends with a condition that says when it is done.
- Each file says what to do, not what to avoid.
- Each rule is stated in one place.
- A skill never does by instruction what a setup script can do.

## Limits and open points

### Out of scope

Product management documents, deployment to production, a hosted service, an AI coding tool other than Claude Code, and defense against code written to attack the test tools.

### Limits

- Tollgate works only with Claude Code.
- A person who skips pre-commit with `--no-verify` can change main.
- A run with no person cannot merge tier 3 or 4: both need your approval.
- The gate trusts the test report the checks write. Code in a change that forges its own results is not caught.
- Every proof is as trustworthy as your machine and its state folder.
- A project can sit in one home only.

### Not verified

- A role obeys its policy inside Claude Code.
- The sandbox stops a write through Python. So the default isolation is the container.
- Session mode with real subagents: that Claude Code honors `maxTurns` in a subagent, and sends the subagent's type to the hook for each Read, Grep, and Glob.
- A role in Docker through the local driver.
- The analyst, the review lens, and the checkpoints with real models.
- A task end to end with real models through the local driver.
- One clean round of three lenses finds enough errors in a plan.
- Every number on this page: 3 tries, 70, 30 minutes, 10 USD, 60 USD, 4 rounds, 2 advisor decisions, 3 files and 50 lines.

### Open

- **The lock run.** The store runs the tests for a lock in a temporary clone on your machine, not in the role's isolation.
- **A replaced lock.** The store allows one, but the driver has no step that asks for it.
- **A split.** The advisor can choose it, but the driver parks the ticket.
- **Tickets at the same time.** Two tickets that touch different files could run side by side. Tollgate has no design for this.

## Credits and licenses

Tollgate copies no code from these projects. It uses their ideas. Where a lens or a rule follows their wording closely, the file says so and the repository keeps both license notices.

| Source | License | What Tollgate takes |
| --- | --- | --- |
| Elephant Goldfish Model, by Google | Apache-2.0 | Reviewers with a fresh context. The paraphrase test. The rule that a plan leaves no decision to the builder. Fix, contest, or escalate as the answers to a finding. |
| The brainstorm skill of the "Elephant Goldfish Model", by Google | Apache-2.0 | The method of `/tollgate:brainstorm`: questions first, a readiness check, fresh reviewers that challenge the idea side by side, then a brief. Tollgate's brainstorm uses none of its code and does not call it. |
| mattpocock-skills, by Matt Pocock | MIT | Seams. Tickets that work alone. The rules for a good test. The three-step path for a wide change. The ticket format. A map that leaves unclear work unplanned. |
| ApexYard, by me2resh | See its repository | One view over many projects, and the lesson that a fork as the home makes every upgrade a merge. |
| Squad OS, by Tollgate's author | MIT | The separate test writer, tiers from path rules, the log, and the numbers for learning. |
| "How to write a good README", an article |  | The four questions that a README answers. |
| The owner's own coding standards |  | The rules for a change and for commits. |

"""What a log says. Each event in a log means one thing, and this module is the
one place that says what. The driver resumes from it; the report, the retro,
and the advisor read it. The log itself is written by driver/log.py.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# The state of a ticket, as a person reads it, from the last event that sets one.
STATES = {
    "merged": "merged", "waiting_for_merge": "ready to merge", "ready": "ready to merge",
    "parked": "parked", "waiting_for_advisor": "waits for a person", "still_waiting":
    "waits for a person", "stopped": "stopped", "try_passed": "building",
    "try_failed": "building", "try_started": "building", "frozen": "frozen",
    "tests_sent_back": "tests written", "relocked": "frozen",
    "tests_reviewed": "tests written", "tests_written": "tests written", "started": "started",
    "ticket_amended": "amended",
}
TRY_ENDS = ("try_failed", "try_passed")
REASONS = ("parked", "waiting_for_advisor", "stopped")


@dataclass
class Ticket:
    """A ticket as its log tells it. The driver changes it as it works, then logs the change."""
    ticket: str = ""
    task: str = ""
    state: str = "not started"
    tests_done: bool = False
    frozen: str = ""           # the lock commit
    rejections: int = 0        # since the tests were last written again on a decision
    rejected: list = field(default_factory=list)   # the findings of the last rejection
    attempts: int = 0          # tries that ended
    best_commit: str = ""
    best_score: int = -1
    passed_commit: str = ""
    tier: int | None = None
    ready: str = ""            # the commit of the ready change
    reason: str = ""
    ai_decisions: int = 0
    hint: str = ""
    stronger: bool = False
    relock_from: str = ""      # the lock commit that a replacement lock builds on, while one waits
    relocks: int = 0           # the tests sent back after the lock: at most one replacement
    fallback: str = ""         # a try that passed but for major findings: it has a pass record
    fallback_attempt: int = 0  # its try number; a later try that does not pass falls back to it
    amending: bool = False     # the plan lock amended the ticket; the driver has not taken it yet
    amend_wanted: bool = False  # amend was chosen and the task has not amended the ticket yet


def better(score: int, best: int) -> bool:
    """A try becomes the best try when it scores higher than the best so far."""
    return score > best


def replay(events: list) -> Ticket:
    """Read the events of one ticket in order, and return where the ticket stands."""
    t = Ticket(ticket=events[0].get("ticket", "") if events else "",
               task=events[0].get("task", "") if events else "")
    for e in events:
        kind = e["event"]
        t.state = STATES.get(kind, t.state)
        if kind in REASONS:
            t.reason = e.get("reason", "")
        if kind == "parked":
            t.amend_wanted = e.get("decision") == "amend"
        if kind == "tests_reviewed":
            t.tests_done, t.rejected = True, []
        elif kind == "tests_rejected":
            # The freeze can refuse tests the reviewer passed: they are written again.
            t.tests_done = False
            t.rejections += 1
            t.rejected = e.get("findings", [])
        elif kind == "tests_sent_back":
            # A check failed on a locked test, or the builder said one is wrong: the test
            # writer writes the tests again for the one replacement lock.
            t.frozen, t.tests_done, t.rejections = "", False, 0
            t.rejected = e.get("findings", [])
            t.relock_from, t.relocks = e.get("lock", ""), t.relocks + 1
            t.fallback, t.fallback_attempt = "", 0   # its pass record is of the old lock
        elif kind == "ticket_amended":
            # The task amended the ticket in the plan lock. Before the lock the test writer
            # writes the tests again; after it, the same tests are locked again with the
            # new ticket, as the one replacement lock.
            t.amending, t.hint, t.amend_wanted = True, e.get("change", ""), False
            if t.frozen:
                t.relock_from, t.relocks = t.frozen, t.relocks + 1
                t.frozen, t.tests_done, t.rejections, t.rejected = "", True, 0, []
                t.fallback, t.fallback_attempt = "", 0
            else:
                t.tests_done, t.rejections, t.rejected = False, 0, []
        elif kind == "amend_applied":
            t.amending = False
        elif kind == "relocked":
            # The build goes on from the best try, merged onto the replacement lock.
            t.relock_from, t.best_commit, t.best_score = "", e.get("commit", ""), -1
        elif kind == "frozen":
            t.frozen = e.get("commit", "")
            t.best_commit = t.best_commit or e.get("commit", "")
        elif kind in TRY_ENDS:
            t.attempts = e.get("attempt", t.attempts + 1)
            t.tier = e.get("tier", t.tier)
            if better(e.get("score", -1), t.best_score):
                t.best_score, t.best_commit = e["score"], e.get("commit", t.best_commit)
            if kind == "try_passed":
                t.passed_commit = e.get("commit", "")
            elif e.get("fallback"):
                t.fallback, t.fallback_attempt = e["fallback"], t.attempts
        elif kind == "fallback_used":
            t.passed_commit = e.get("commit", "")
        elif kind == "ready":
            t.ready = e.get("commit", "")
        elif kind == "advisor_decided":
            t.ai_decisions += 1 if e.get("by") == "ai" else 0
            if e.get("decision") in ("retry", "stronger"):
                t.hint = e.get("hint", "")
                t.stronger = t.stronger or e.get("decision") == "stronger"
                if not t.frozen:
                    # The tests are written again with the hint: the rejections count anew.
                    t.rejections, t.rejected = 0, []
    return t


def open_question(events: list) -> dict | None:
    """The question a person has not answered yet, or None: {"question", "reason"}."""
    answered = {e.get("question") for e in events
                if e["event"] == "advisor_decided" and e.get("by") == "person"}
    for e in reversed(events):
        if e["event"] == "waiting_for_advisor" and e.get("question"):
            return None if e["question"] in answered else {"question": e["question"],
                                                           "reason": e.get("reason", "")}
    return None


def cost(events: list) -> float:
    """What the AI sessions in these events cost, in USD. A cost that is not known counts 0."""
    return round(sum(float(e.get("cost_usd") or 0) for e in events), 4)


def cost_known(events: list) -> bool:
    """False when a role in these events ran in session mode, where its cost is not known."""
    return not any("cost_usd" in e and e["cost_usd"] is None for e in events)


def logs(folder, pattern: str = "*.jsonl") -> list:
    """The logs in a folder, in name order, without the saved answers of session mode."""
    return sorted(p for p in Path(folder).glob(pattern) if not p.name.endswith(".answers.jsonl"))


def minutes(events: list) -> int:
    """Minutes from the first event to the last."""
    stamps = [datetime.strptime(e["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
              for e in events if "at" in e]
    return round((max(stamps) - min(stamps)).total_seconds() / 60) if stamps else 0

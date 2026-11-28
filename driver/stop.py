"""The stop rules: given a ticket's log and the settings, go on or stop.

    check(events, settings, task_spent) -> "go on" | "stop: <reason>"

The driver asks before each try. Five rules, each number from the settings
`stop`:

- tries: the ticket used its tries (`attempts`, 3 by default). Each retry, or
  amend of the ticket, that the advisor grants after the tests are locked adds one.
- same failure: the last try failed the same way as the try before it: the
  same failed checks, or the same failed checklist items at the same score.
- time: the last try ran past `attempt_minutes` (30 by default).
- ticket cost: the ticket cost `ticket_usd` (10 USD by default) or more.
- task cost: the task cost `task_usd` (60 USD by default) or more.

A cost stop is a hard stop: no advisor can grant more money. The two cost
rules apply only in background mode: in session mode the cost of a role is not
known, so the log holds `cost_usd: null` and your own session's limits hold. The others go to
the advisor. The rules on the last try count only for a try after the last
decision of the advisor, so a retry always gets one more try.
"""

from driver import history

GO = "go on"
TRY_ENDS = ("try_failed", "try_passed")
RETRY = ("retry", "stronger", "amend")
COST_STOP = ("the ticket cost", "the task cost")


def check(events: list, settings: dict, task_spent: float = 0.0) -> str:
    usd = settings.get("runner", "background") == "background"
    reason = _reason(events, settings["stop"], task_spent, usd)
    return f"stop: {reason}" if reason else GO


def is_hard(verdict: str) -> bool:
    """True for a cost stop, which the advisor cannot lift."""
    return verdict.removeprefix("stop: ").startswith(COST_STOP)


def _reason(events: list, limits: dict, task_spent: float, usd: bool = True) -> str:
    spent = history.cost(events)
    if usd and spent >= limits["ticket_usd"]:
        return f"the ticket cost {spent} USD; the limit is {limits['ticket_usd']} USD"
    if usd and task_spent >= limits["task_usd"]:
        return f"the task cost {round(task_spent, 4)} USD; the limit is {limits['task_usd']} USD"
    tries = [e for e in events if e["event"] in TRY_ENDS]
    # A retry before the tests are locked writes the tests again; it grants no try.
    locked = next((i for i, e in enumerate(events) if e["event"] == "frozen"), -1)
    decided = [i for i, e in enumerate(events) if i > locked
               and e["event"] == "advisor_decided" and e.get("decision") in RETRY]
    last_decision = decided[-1] if decided else -1
    last_end = max((i for i, e in enumerate(events) if e["event"] in TRY_ENDS), default=-1)
    if tries and last_end > last_decision:
        last = tries[-1]
        n = last.get("attempt", len(tries))
        if last.get("timed_out") or last.get("seconds", 0) >= limits["attempt_minutes"] * 60:
            return f"try {n} ran past {limits['attempt_minutes']} minutes"
        if len(tries) >= 2 and same_failure(tries[-2], last):
            return f"try {n} failed the same way as the try before it: {what_failed(last)}"
    allowed = limits["attempts"] + len(decided)
    if len(tries) >= allowed:
        return f"the ticket used {len(tries)} {'try' if len(tries) == 1 else 'tries'}"
    return ""


def tries_left(events: list, limits: dict) -> int:
    """The tries the ticket may still start: `attempts`, plus one for each retry the
    advisor granted after the tests were locked, less the tries that ended."""
    locked = next((i for i, e in enumerate(events) if e["event"] == "frozen"), -1)
    granted = sum(1 for i, e in enumerate(events) if i > locked
                  and e["event"] == "advisor_decided" and e.get("decision") in RETRY)
    return limits["attempts"] + granted - sum(1 for e in events if e["event"] in TRY_ENDS)


def failed_items(event: dict) -> list:
    return sorted(i for i, ok in (event.get("items") or {}).items() if not ok)


def failed_checks(event: dict) -> list:
    key = event.get("failed_key") or [[], []]
    return [*key[0], *key[1]]


def same_failure(a: dict, b: dict) -> bool:
    """The same failed checks, or the same failed checklist items at the same score."""
    if a["event"] != "try_failed" or b["event"] != "try_failed":
        return False
    if failed_checks(b) and failed_checks(a) == failed_checks(b):
        return True
    score = b.get("score", -1)
    return (score >= 0 and score == a.get("score", -1) and bool(failed_items(b))
            and failed_items(a) == failed_items(b))


def what_failed(event: dict) -> str:
    checks = failed_checks(event)
    if checks:
        return "checks " + ", ".join(checks)
    return f"items {', '.join(failed_items(event))} at score {event.get('score')}"

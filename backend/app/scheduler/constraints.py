from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.resources.registry import TIME_WINDOWS
from app.models import Candidate, RejectedCandidate, Task


def _deadline_allows(candidate: Candidate, task: Task) -> bool:
    if not task.deadline:
        return True

    try:
        deadline = datetime.fromisoformat(task.deadline.replace("Z", "+00:00"))
    except ValueError:
        return False

    window = TIME_WINDOWS.get(candidate.time_window, TIME_WINDOWS["now"])
    finish_time = datetime.now(timezone.utc) + timedelta(
        minutes=window["delay_minutes"],
        milliseconds=candidate.latency_ms,
    )
    return finish_time <= deadline


def rejection_reasons(candidate: Candidate, task: Task) -> list[str]:
    reasons: list[str] = []

    if task.type not in candidate.task_types:
        reasons.append("model_task_incompatible")
    if candidate.accuracy < task.min_accuracy:
        reasons.append("below_min_accuracy")
    if candidate.latency_ms > task.max_latency_ms:
        reasons.append("above_max_latency")
    if candidate.cost_usd > task.cost_limit:
        reasons.append("above_cost_limit")
    if candidate.carbon_g > task.carbon_budget_remaining_g:
        reasons.append("above_remaining_carbon_budget")
    if not _deadline_allows(candidate, task):
        reasons.append("misses_deadline")

    return reasons


def apply_hard_constraints(
    candidates: list[Candidate],
    task: Task,
) -> tuple[list[Candidate], list[RejectedCandidate], dict]:
    feasible: list[Candidate] = []
    rejected: list[RejectedCandidate] = []
    counts = {
        "total": len(candidates),
        "feasible": 0,
        "rejected": 0,
        "rejection_reason_counts": {},
    }

    for candidate in candidates:
        reasons = rejection_reasons(candidate, task)
        if reasons:
            rejected.append(RejectedCandidate(candidate=candidate, reasons=reasons))
            for reason in reasons:
                counts["rejection_reason_counts"][reason] = (
                    counts["rejection_reason_counts"].get(reason, 0) + 1
                )
        else:
            feasible.append(candidate)

    counts["feasible"] = len(feasible)
    counts["rejected"] = len(rejected)
    return feasible, rejected, counts


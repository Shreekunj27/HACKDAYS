from __future__ import annotations

from app.models import Decision, Task
from app.resources.registry import build_candidates
from app.scheduler.constraints import apply_hard_constraints
from app.scheduler.mcdm import balance_breakdown, rank_candidates


def decide_for_task(task: Task) -> Decision:
    candidates = build_candidates(task)
    feasible, rejected, constraint_results = apply_hard_constraints(candidates, task)
    selected, score, weights, normalized_metrics, candidate_scores = rank_candidates(
        feasible,
        task.priority,
    )
    constraint_results["candidate_scores"] = candidate_scores
    constraint_results["balance_metrics"] = balance_breakdown(
        selected,
        normalized_metrics,
        weights,
        score,
    )
    reason = (
        "Selected by constraint-first normalization, entropy-weight blend, TOPSIS ranking, and balance-metric explanation."
        if selected
        else "No feasible candidate remains after hard constraints."
    )

    return Decision(
        selected_candidate=selected,
        score=score,
        weights=weights,
        normalized_metrics=normalized_metrics,
        feasible_candidates=feasible,
        rejected_candidates=rejected,
        constraint_results=constraint_results,
        reason=reason,
    )

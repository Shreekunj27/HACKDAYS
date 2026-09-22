from __future__ import annotations

from app.models import Candidate, Task, new_id, utc_now_iso
from app.scheduler.engine import decide_for_task


def enforce_circuit_breaker(
    workflow_id: str,
    step_id: str,
    task: Task,
    stored_decision: dict | None,
    remaining_carbon_g: float,
) -> tuple[Candidate | None, dict, dict | None]:
    if stored_decision and stored_decision.get("selected_candidate"):
        selected = Candidate.from_payload(stored_decision["selected_candidate"])
        if selected.carbon_g <= remaining_carbon_g:
            return selected, stored_decision, None

        replanned = decide_for_task(task).to_dict()
        replacement = (
            Candidate.from_payload(replanned["selected_candidate"])
            if replanned.get("selected_candidate")
            else None
        )
        event = {
            "event_id": new_id("event"),
            "type": "carbon_circuit_breaker",
            "workflow_id": workflow_id,
            "step_id": step_id,
            "created_at": utc_now_iso(),
            "reason": "stored_selected_candidate_exceeds_remaining_carbon_budget",
            "remaining_carbon_g": round(remaining_carbon_g, 6),
            "blocked_candidate_id": selected.candidate_id,
            "blocked_candidate_carbon_g": selected.carbon_g,
            "replacement_candidate_id": replacement.candidate_id if replacement else None,
            "replacement_candidate_carbon_g": replacement.carbon_g if replacement else None,
        }
        return replacement, replanned, event

    replanned = decide_for_task(task).to_dict()
    replacement = (
        Candidate.from_payload(replanned["selected_candidate"])
        if replanned.get("selected_candidate")
        else None
    )
    return replacement, replanned, None

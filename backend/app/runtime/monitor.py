from __future__ import annotations

from app.models import new_id, utc_now_iso


def apply_environment_change(workflow: dict, payload: dict) -> dict:
    resource_state = workflow.setdefault(
        "resource_state",
        {
            "latency_multiplier": 1.0,
            "carbon_multiplier": 1.0,
            "cost_multiplier": 1.0,
            "change_count": 0,
        },
    )
    for key in ["latency_multiplier", "carbon_multiplier", "cost_multiplier"]:
        if key in payload:
            resource_state[key] = round(float(payload[key]), 4)
    resource_state["change_count"] += 1
    resource_state["updated_at"] = utc_now_iso()

    return {
        "event_id": new_id("event"),
        "type": "environment_change",
        "workflow_id": workflow["workflow_id"],
        "step_id": None,
        "created_at": resource_state["updated_at"],
        "reason": payload.get("reason", "demo_environment_change"),
        "resource_state": dict(resource_state),
    }


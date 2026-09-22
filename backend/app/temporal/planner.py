from __future__ import annotations

from app.carbon.provider import get_carbon_provider
from app.models import Task
from app.resources.registry import TIME_WINDOWS


def temporal_mode() -> str:
    try:
        import temporalio  # noqa: F401
    except Exception:
        return "mock_temporal_plan"
    return "temporal_ready"


def plan_green_window(task: Task, decision: dict, workflow_id: str, step_id: str | None) -> dict:
    selected = decision.get("selected_candidate")
    target_window = selected["time_window"] if selected else "now"
    window = TIME_WINDOWS.get(target_window, TIME_WINDOWS["now"])
    wait_minutes = int(window["delay_minutes"]) if task.delay_tolerant else 0

    cleanest_window = None
    if selected:
        cleanest = get_carbon_provider().find_cleanest_window(selected["location"])
        cleanest_window = cleanest.to_dict()

    return {
        "workflow_id": workflow_id,
        "step_id": step_id,
        "mode": temporal_mode(),
        "wait_required": wait_minutes > 0,
        "wait_minutes": wait_minutes,
        "target_time_window": target_window,
        "selected_candidate_id": selected["candidate_id"] if selected else None,
        "reason": (
            "Delay-tolerant workflow should wait for the selected cleaner energy window."
            if wait_minutes > 0
            else "Selected candidate can run immediately under current constraints."
        ),
        "cleanest_window": cleanest_window,
    }

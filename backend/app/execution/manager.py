from __future__ import annotations

from app.models import Candidate, Task, new_id, utc_now_iso


QUALITY_BY_MODEL = {
    "small-local": -0.01,
    "medium-edge": 0.005,
    "large-cloud": 0.01,
}


def execute_candidate(task: Task, candidate: Candidate) -> dict:
    quality_delta = QUALITY_BY_MODEL.get(candidate.model, 0)
    actual_latency_ms = round(candidate.latency_ms * 1.04)
    actual_cost_usd = round(candidate.cost_usd * 1.01, 6)
    actual_energy_wh = round(candidate.energy_wh * 1.03, 6)
    actual_carbon_g = round(candidate.carbon_g * 1.03, 6)
    actual_quality = round(min(1.0, candidate.accuracy + quality_delta), 4)

    return {
        "run_id": new_id("run"),
        "task_id": task.task_id,
        "start_time": utc_now_iso(),
        "end_time": utc_now_iso(),
        "status": "done",
        "result": f"Simulated {task.type} result for {task.task_id}",
        "selected_candidate_id": candidate.candidate_id,
        "predicted_metrics": {
            "latency_ms": candidate.latency_ms,
            "accuracy": candidate.accuracy,
            "cost_usd": candidate.cost_usd,
            "energy_wh": candidate.energy_wh,
            "carbon_g": candidate.carbon_g,
        },
        "actual_metrics": {
            "latency_ms": actual_latency_ms,
            "quality": actual_quality,
            "cost_usd": actual_cost_usd,
            "energy_wh": actual_energy_wh,
            "carbon_g": actual_carbon_g,
        },
    }

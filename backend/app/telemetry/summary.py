from __future__ import annotations

from app.models import Task


METRIC_KEYS = ["latency_ms", "cost_usd", "energy_wh", "carbon_g"]


def empty_totals() -> dict:
    return {
        "latency_ms": 0,
        "cost_usd": 0.0,
        "energy_wh": 0.0,
        "carbon_g": 0.0,
        "quality": 0.0,
        "steps": 0,
    }


def actual_totals(runs: list[dict]) -> dict:
    totals = empty_totals()
    qualities = []
    for run in runs:
        metrics = run["actual_metrics"]
        totals["latency_ms"] += metrics["latency_ms"]
        totals["cost_usd"] += metrics["cost_usd"]
        totals["energy_wh"] += metrics["energy_wh"]
        totals["carbon_g"] += metrics["carbon_g"]
        qualities.append(metrics["quality"])
    totals["steps"] = len(runs)
    totals["latency_ms"] = round(totals["latency_ms"])
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    totals["energy_wh"] = round(totals["energy_wh"], 6)
    totals["carbon_g"] = round(totals["carbon_g"], 6)
    totals["quality"] = round(sum(qualities) / len(qualities), 4) if qualities else 0.0
    return totals


def _static_baseline_run(task: Task, *, step_index: int = 0) -> dict:
    baseline_candidate = {
        "candidate_id": f"baseline_{task.task_id}_{step_index}",
        "model": "large-cloud",
        "location": "cloud-clean-eu",
        "time_window": "now",
        "task_types": [task.type],
        "latency_ms": 2600,
        "accuracy": 0.96,
        "cost_usd": 0.015,
        "power_watts": 220,
        "duration_seconds": 2.6,
        "carbon_intensity_g_per_kwh": 95,
        "energy_wh": 0.158,
        "carbon_g": 0.015,
    }
    return {
        "run_id": f"baseline_run_{task.task_id}_{step_index}",
        "task_id": task.task_id,
        "status": "baseline_static",
        "result": "baseline_static_execution",
        "predicted_metrics": {
            "latency_ms": baseline_candidate["latency_ms"],
            "cost_usd": baseline_candidate["cost_usd"],
            "energy_wh": baseline_candidate["energy_wh"],
            "carbon_g": baseline_candidate["carbon_g"],
            "quality": baseline_candidate["accuracy"],
        },
        "actual_metrics": {
            "latency_ms": baseline_candidate["latency_ms"],
            "cost_usd": baseline_candidate["cost_usd"],
            "energy_wh": baseline_candidate["energy_wh"],
            "carbon_g": baseline_candidate["carbon_g"],
            "quality": baseline_candidate["accuracy"],
        },
    }


def baseline_workflow_totals(workflow: dict | None) -> dict:
    workflow = workflow or {}
    steps = workflow.get("steps", [])
    if not steps:
        return empty_totals()
    constraints = workflow.get("constraints", {})
    runs = []
    for index, step in enumerate(steps, start=1):
        task = Task.from_payload(
            {
                "task_id": step.get("step_id") or f"baseline_step_{index}",
                "type": step.get("type", "summarize"),
                "input": step.get("input", ""),
            },
            constraints,
        )
        runs.append(_static_baseline_run(task, step_index=index))
    return actual_totals(runs)


def baseline_totals(step_count: int, workflow: dict | None = None) -> dict:
    if workflow is not None:
        return baseline_workflow_totals(workflow)
    return {
        "latency_ms": round(2298 * step_count),
        "cost_usd": round(0.01515 * step_count, 6),
        "energy_wh": round(0.139108 * step_count, 6),
        "carbon_g": round(0.070945 * step_count, 6),
        "quality": 0.97 if step_count else 0.0,
        "steps": step_count,
    }


def compare_totals(baseline: dict, dynamic: dict) -> dict:
    absolute = {}
    percentage = {}
    for key in METRIC_KEYS:
        absolute[key] = round(dynamic[key] - baseline[key], 6)
        percentage[key] = (
            round((absolute[key] / baseline[key]) * 100, 2)
            if baseline[key]
            else 0.0
        )
    return {
        "baseline_totals": baseline,
        "dynamic_totals": dynamic,
        "absolute_difference": absolute,
        "percentage_difference": percentage,
        "quality_check": {
            "baseline_quality": baseline["quality"],
            "dynamic_quality": dynamic["quality"],
            "quality_visible": True,
        },
    }


def predicted_saving_from_decision(decision: dict | None) -> dict:
    if not decision or not decision.get("selected_candidate"):
        return {"carbon_g": 0.0, "energy_wh": 0.0, "cost_usd": 0.0}

    selected = decision["selected_candidate"]
    feasible = decision.get("feasible_candidates", [])
    if not feasible:
        return {"carbon_g": 0.0, "energy_wh": 0.0, "cost_usd": 0.0}

    worst_carbon = max(candidate["carbon_g"] for candidate in feasible)
    worst_energy = max(candidate["energy_wh"] for candidate in feasible)
    worst_cost = max(candidate["cost_usd"] for candidate in feasible)
    return {
        "carbon_g": round(worst_carbon - selected["carbon_g"], 6),
        "energy_wh": round(worst_energy - selected["energy_wh"], 6),
        "cost_usd": round(worst_cost - selected["cost_usd"], 6),
    }


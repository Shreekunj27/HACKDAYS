from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from pydantic import ValidationError

from app.carbon.budget import deduct_carbon, make_budget
from app.carbon.provider import carbon_snapshot
from app.carbon.runtime import enforce_circuit_breaker
from app.execution.manager import execute_candidate
from app.intelligence.service import (
    decide_from_payload,
    runtime_config,
    validation_error_payload,
)
from app.models import Task, new_id, utc_now_iso
from app.scheduler.engine import decide_for_task
from app.schemas import WorkflowSubmitRequest
from app.storage.memory import store
from app.runtime.monitor import apply_environment_change
from app.telemetry.summary import (
    actual_totals,
    baseline_totals,
    baseline_workflow_totals,
    compare_totals,
    predicted_saving_from_decision,
)
from app.temporal.planner import plan_green_window
from app.workflow.engine import build_workflow, current_step, set_step_status


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
LEGACY_DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
DASHBOARD_DIR = FRONTEND_DIR if FRONTEND_DIR.exists() else LEGACY_DASHBOARD_DIR

app = Flask(__name__)


DEFAULT_CONSTRAINTS = {
    "type": "summarize",
    "input": "",
    "min_accuracy": 0.85,
    "max_latency_ms": 3000,
    "priority": "balanced",
    "deadline": None,
    "delay_tolerant": False,
    "cost_limit": 0.02,
    "carbon_budget_remaining_g": 10.0,
}


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "greenpilot-backend"})


@app.get("/")
def dashboard_index():
    return send_from_directory(DASHBOARD_DIR, "index.html")


@app.get("/dashboard/<path:filename>")
def dashboard_asset(filename: str):
    return send_from_directory(DASHBOARD_DIR, filename)


@app.get("/api/contracts")
def contracts():
    return jsonify(
        {
            "task": [
                "task_id",
                "type",
                "input",
                "min_accuracy",
                "max_latency_ms",
                "priority",
                "deadline",
                "delay_tolerant",
                "cost_limit",
                "carbon_budget_remaining_g",
            ],
            "candidate": [
                "candidate_id",
                "model",
                "location",
                "time_window",
                "latency_ms",
                "accuracy",
                "cost_usd",
                "energy_wh",
                "carbon_g",
            ],
            "decision": [
                "selected_candidate",
                "score",
                "weights",
                "normalized_metrics",
                "feasible_candidates",
                "rejected_candidates",
                "constraint_results",
                "reason",
            ],
            "execution": [
                "run_id",
                "task_id",
                "status",
                "result",
                "predicted_metrics",
                "actual_metrics",
            ],
            "runtime_event": [
                "event_id",
                "type",
                "workflow_id",
                "step_id",
                "reason",
                "blocked_candidate_id",
                "replacement_candidate_id",
            ],
        }
    )


@app.get("/api/runtime/config")
def get_runtime_config():
    return jsonify(runtime_config().model_dump())


@app.post("/api/decisions")
def create_decision():
    payload = request.get_json(silent=True) or {}
    try:
        decision = decide_from_payload(payload)
    except ValidationError as exc:
        return jsonify(validation_error_payload(exc)), 400
    return jsonify(decision)


def _workflow_constraints(workflow: dict) -> dict:
    return {**DEFAULT_CONSTRAINTS, **workflow.get("constraints", {})}


def _task_for_step(step: dict, workflow: dict) -> Task:
    constraints = _workflow_constraints(workflow)
    budget = workflow.setdefault(
        "carbon_budget",
        make_budget(float(constraints["carbon_budget_remaining_g"])),
    )
    constraints["carbon_budget_remaining_g"] = budget["remaining_carbon_g"]
    constraints["resource_state"] = workflow.get("resource_state", {})
    return Task.from_payload(step, constraints)


def _apply_carbon_snapshot_to_workflow(workflow: dict, snapshot: dict) -> dict:
    resource_state = workflow.setdefault("resource_state", {})
    resource_state["carbon_provider"] = snapshot["provider"]
    resource_state["carbon_provider_status"] = snapshot["status"]
    resource_state["carbon_intensity_by_location"] = {
        location: region["carbon_intensity_g_per_kwh"]
        for location, region in snapshot["regions"].items()
    }
    resource_state["carbon_snapshot_updated_at"] = snapshot["updated_at"]
    return workflow


def _decision_for_profile(step: dict, workflow: dict, priority: str) -> dict:
    constraints = _workflow_constraints(workflow)
    budget = workflow.setdefault(
        "carbon_budget",
        make_budget(float(constraints["carbon_budget_remaining_g"])),
    )
    constraints["carbon_budget_remaining_g"] = budget["remaining_carbon_g"]
    constraints["priority"] = priority
    constraints["resource_state"] = workflow.get("resource_state", {})
    return decide_for_task(Task.from_payload(step, constraints)).to_dict()


@app.post("/api/workflows")
def submit_workflow():
    payload = request.get_json(silent=True) or {}
    try:
        validated = WorkflowSubmitRequest.model_validate(payload)
        payload = validated.model_dump(exclude_none=True)
    except ValidationError as exc:
        return jsonify(validation_error_payload(exc)), 400
    constraints = {**DEFAULT_CONSTRAINTS, **payload.get("constraints", {})}
    workflow = store.save_workflow(build_workflow(payload))
    workflow["constraints"] = constraints
    workflow["carbon_budget"] = make_budget(float(constraints["carbon_budget_remaining_g"]))
    step = current_step(workflow)

    decision = None
    if step:
        task = _task_for_step(step, workflow)
        decision = decide_for_task(task).to_dict()
        store.save_decision(workflow["workflow_id"], step["step_id"], decision)

    return jsonify(
        {
            "workflow": workflow,
            "current_step": step,
            "decision": decision,
            "checkpoint": {
                "backend": "B1 API contract",
                "scheduler": "S1 hard constraints + S2-S4 MCDM",
                "runtime": "R1 carbon math + R2 budget",
            },
        }
    ), 201


@app.get("/api/workflows/<workflow_id>/temporal-plan")
def get_temporal_plan(workflow_id: str):
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "workflow_not_found"}), 404

    step = current_step(workflow)
    if not step:
        return jsonify({"error": "workflow_complete"}), 409

    task = _task_for_step(step, workflow)
    decision = store.get_decision(workflow_id, step["step_id"]) or decide_for_task(task).to_dict()
    plan = plan_green_window(task, decision, workflow_id, step["step_id"])
    event = {
        "event_id": new_id("event"),
        "type": "temporal_green_window_plan",
        "workflow_id": workflow_id,
        "step_id": step["step_id"],
        "created_at": utc_now_iso(),
        "reason": plan["reason"],
        "mode": plan["mode"],
        "wait_minutes": plan["wait_minutes"],
        "selected_candidate_id": plan["selected_candidate_id"],
    }
    store.append_event(workflow_id, event)
    return jsonify({"plan": plan, "event": event})


@app.get("/api/workflows/<workflow_id>")
def get_workflow(workflow_id: str):
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "workflow_not_found"}), 404
    return jsonify(
        {
            "workflow": workflow,
            "current_step": current_step(workflow),
            "runs": store.get_runs(workflow_id),
            "decisions": store.get_decisions(workflow_id),
            "events": store.get_events(workflow_id),
            "resource_state": workflow.get("resource_state", {}),
        }
    )


@app.post("/api/workflows/<workflow_id>/execute-next")
def execute_next_step(workflow_id: str):
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "workflow_not_found"}), 404

    step = current_step(workflow)
    if not step:
        return jsonify({"workflow": workflow, "message": "workflow_complete"}), 200

    task = _task_for_step(step, workflow)
    stored_decision = store.get_decision(workflow_id, step["step_id"])
    selected_candidate, decision_payload, runtime_event = enforce_circuit_breaker(
        workflow_id=workflow_id,
        step_id=step["step_id"],
        task=task,
        stored_decision=stored_decision,
        remaining_carbon_g=workflow["carbon_budget"]["remaining_carbon_g"],
    )
    if runtime_event:
        store.append_event(workflow_id, runtime_event)
    if not selected_candidate:
        return jsonify({"error": "no_feasible_candidate", "decision": decision_payload}), 409

    set_step_status(workflow, step["step_id"], "running")
    run = execute_candidate(task, selected_candidate)
    store.append_run(workflow_id, run)
    deduct_carbon(workflow["carbon_budget"], run["actual_metrics"]["carbon_g"])
    set_step_status(workflow, step["step_id"], "done")
    store.save_decision(workflow_id, step["step_id"], decision_payload)
    store.save_workflow(workflow)

    next_step = current_step(workflow)
    next_decision = None
    if next_step:
        next_task = _task_for_step(next_step, workflow)
        next_decision = decide_for_task(next_task).to_dict()
        store.save_decision(workflow_id, next_step["step_id"], next_decision)

    return jsonify(
        {
            "workflow": workflow,
            "completed_step": step,
            "run": run,
            "carbon_budget": workflow["carbon_budget"],
            "runtime_event": runtime_event,
            "next_step": next_step,
            "next_decision": next_decision,
            "checkpoint": {
                "backend": "B2 workflow loop + B5 retrieval-ready persistence",
                "scheduler": "S2 normalization + S3 entropy + S4 TOPSIS",
                "runtime": "R2 budget + R3 circuit breaker + R6 telemetry",
            },
        }
    )


@app.post("/api/workflows/<workflow_id>/execute-all")
def execute_all_steps(workflow_id: str):
    outputs = []
    while True:
        workflow = store.get_workflow(workflow_id)
        if not workflow:
            return jsonify({"error": "workflow_not_found"}), 404
        if not current_step(workflow):
            return jsonify(
                {
                    "workflow": workflow,
                    "runs": store.get_runs(workflow_id),
                    "events": store.get_events(workflow_id),
                    "executions": outputs,
                }
            )
        with app.test_request_context():
            response = execute_next_step(workflow_id)
        payload = response.get_json()
        outputs.append(payload)
        if "error" in payload:
            return jsonify(payload), 409


@app.get("/api/workflows/<workflow_id>/telemetry")
def get_telemetry(workflow_id: str):
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "workflow_not_found"}), 404
    return jsonify(
        {
            "workflow_id": workflow_id,
            "carbon_budget": workflow.get("carbon_budget"),
            "runs": store.get_runs(workflow_id),
            "decisions": store.get_decisions(workflow_id),
            "events": store.get_events(workflow_id),
            "summary": actual_totals(store.get_runs(workflow_id)),
        }
    )


@app.get("/api/workflows/<workflow_id>/what-if")
def what_if(workflow_id: str):
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "workflow_not_found"}), 404
    step = current_step(workflow) or (workflow["steps"][-1] if workflow["steps"] else None)
    if not step:
        return jsonify({"error": "workflow_has_no_steps"}), 409

    profiles = {}
    for priority in ["fast", "balanced", "green"]:
        decision = _decision_for_profile(step, workflow, priority)
        selected = decision.get("selected_candidate")
        profiles[priority] = {
            "priority": priority,
            "selected_candidate": selected,
            "score": decision["score"],
            "weights": decision["weights"],
            "predicted_metrics": {
                "latency_ms": selected["latency_ms"] if selected else None,
                "accuracy": selected["accuracy"] if selected else None,
                "cost_usd": selected["cost_usd"] if selected else None,
                "energy_wh": selected["energy_wh"] if selected else None,
                "carbon_g": selected["carbon_g"] if selected else None,
            },
            "reason": decision["reason"],
        }
    return jsonify({"workflow_id": workflow_id, "step": step, "profiles": profiles})


@app.post("/api/workflows/<workflow_id>/runtime/environment")
def update_environment(workflow_id: str):
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "workflow_not_found"}), 404

    payload = request.get_json(silent=True) or {}
    event = apply_environment_change(workflow, payload)
    step = current_step(workflow)
    decision = None
    if step:
        event["step_id"] = step["step_id"]
        decision = decide_for_task(_task_for_step(step, workflow)).to_dict()
        store.save_decision(workflow_id, step["step_id"], decision)
        event["replanned_candidate_id"] = (
            decision["selected_candidate"]["candidate_id"]
            if decision.get("selected_candidate")
            else None
        )
    store.append_event(workflow_id, event)
    store.save_workflow(workflow)
    return jsonify(
        {
            "workflow": workflow,
            "resource_state": workflow.get("resource_state", {}),
            "event": event,
            "decision": decision,
            "checkpoint": {
                "runtime": "R5 replanning trigger",
                "scheduler": "S6 live integration",
            },
        }
    )


@app.get("/api/workflows/<workflow_id>/passport")
def decision_passport(workflow_id: str):
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "workflow_not_found"}), 404
    decisions = store.get_decisions(workflow_id)
    runs = store.get_runs(workflow_id)
    events = store.get_events(workflow_id)
    latest_step_id = next(reversed(decisions), None) if decisions else None
    latest_decision = decisions.get(latest_step_id) if latest_step_id else None
    selected = latest_decision.get("selected_candidate") if latest_decision else None
    rejected = latest_decision.get("rejected_candidates", []) if latest_decision else []

    return jsonify(
        {
            "workflow_id": workflow_id,
            "selected_plan": selected,
            "alternatives": {
                "feasible_count": len(latest_decision.get("feasible_candidates", [])) if latest_decision else 0,
                "rejected_count": len(rejected),
                "rejected_candidates": rejected,
            },
            "estimated_saving": predicted_saving_from_decision(latest_decision),
            "constraints": latest_decision.get("constraint_results") if latest_decision else {},
            "predicted_vs_actual": runs[-1] if runs else None,
            "decision_reason": latest_decision.get("reason") if latest_decision else None,
            "runtime_events": events,
            "carbon_budget": workflow.get("carbon_budget"),
        }
    )


@app.get("/api/workflows/<workflow_id>/dashboard/static-vs-dynamic")
def static_vs_dynamic(workflow_id: str):
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "workflow_not_found"}), 404
    runs = store.get_runs(workflow_id)
    dynamic = actual_totals(runs)
    baseline = baseline_workflow_totals(workflow)
    if not baseline.get("steps"):
        baseline = baseline_totals(len(runs) or len(workflow.get("steps", [])))
    comparison = compare_totals(baseline, dynamic)
    comparison["replans"] = len(
        [event for event in store.get_events(workflow_id) if event["type"] == "environment_change"]
    )
    comparison["circuit_breaker_events"] = len(
        [event for event in store.get_events(workflow_id) if event["type"] == "carbon_circuit_breaker"]
    )
    return jsonify({"workflow_id": workflow_id, **comparison})


@app.get("/api/runtime/carbon")
def get_carbon_snapshot():
    snapshot = store.carbon_snapshot or carbon_snapshot(force_live=False)
    store.carbon_snapshot = snapshot
    return jsonify(snapshot)


@app.post("/api/runtime/carbon/refresh")
def refresh_carbon_snapshot():
    payload = request.get_json(silent=True) or {}
    snapshot = carbon_snapshot(force_live=bool(payload.get("force_live", False)))
    store.carbon_snapshot = snapshot

    updated_workflows = []
    for workflow_id, workflow in store.workflows.items():
        _apply_carbon_snapshot_to_workflow(workflow, snapshot)
        step = current_step(workflow)
        decision = None
        if step:
            decision = decide_for_task(_task_for_step(step, workflow)).to_dict()
            store.save_decision(workflow_id, step["step_id"], decision)
        event = {
            "event_id": new_id("event"),
            "type": "carbon_intensity_refresh",
            "workflow_id": workflow_id,
            "step_id": step["step_id"] if step else None,
            "created_at": utc_now_iso(),
            "reason": "live_carbon_intensity_refresh",
            "provider": snapshot["provider"],
            "provider_status": snapshot["status"],
            "replanned_candidate_id": (
                decision["selected_candidate"]["candidate_id"]
                if decision and decision.get("selected_candidate")
                else None
            ),
        }
        store.append_event(workflow_id, event)
        updated_workflows.append(
            {
                "workflow_id": workflow_id,
                "current_step_id": step["step_id"] if step else None,
                "replanned_candidate_id": event["replanned_candidate_id"],
            }
        )

    return jsonify(
        {
            "snapshot": snapshot,
            "updated_workflows": updated_workflows,
            "checkpoint": {
                "runtime": "live carbon intensity refresh with simulator fallback",
                "scheduler": "replan after carbon refresh",
            },
        }
    )


@app.post("/api/workflows/<workflow_id>/runtime/budget")
def update_runtime_budget(workflow_id: str):
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "workflow_not_found"}), 404

    payload = request.get_json(silent=True) or {}
    if "remaining_carbon_g" not in payload:
        return jsonify({"error": "remaining_carbon_g_required"}), 400

    budget = workflow.setdefault("carbon_budget", make_budget(float(payload["remaining_carbon_g"])))
    budget["remaining_carbon_g"] = round(max(0.0, float(payload["remaining_carbon_g"])), 6)
    budget["used_carbon_g"] = round(
        max(0.0, float(budget["total_carbon_budget_g"]) - budget["remaining_carbon_g"]),
        6,
    )
    event = {
        "event_id": new_id("event"),
        "type": "manual_budget_update",
        "workflow_id": workflow_id,
        "step_id": current_step(workflow)["step_id"] if current_step(workflow) else None,
        "created_at": utc_now_iso(),
        "reason": "demo_runtime_budget_change",
        "remaining_carbon_g": budget["remaining_carbon_g"],
    }
    store.append_event(workflow_id, event)
    store.save_workflow(workflow)
    return jsonify({"workflow": workflow, "carbon_budget": budget, "event": event})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)

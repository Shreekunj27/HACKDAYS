from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app.carbon.budget import deduct_carbon, make_budget
from app.carbon.provider import carbon_snapshot
from app.carbon.runtime import enforce_circuit_breaker
from app.execution.manager import execute_candidate
from app.intelligence.service import decide_from_payload, runtime_config, validation_error_payload
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

app = FastAPI(title="Hackdays GreenPilot Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DASHBOARD_DIR = FRONTEND_DIR / "public" if (FRONTEND_DIR / "public").exists() else FRONTEND_DIR

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


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "GreenPilot backend is connected to Hackdays"}


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "hackdays-greenpilot-backend"}


@app.get("/api/contracts")
def contracts() -> dict[str, list[str]]:
    return {
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
    }


@app.get("/api/runtime/config")
def get_runtime_config() -> dict:
    return runtime_config().model_dump()


@app.post("/api/decisions")
def create_decision(payload: dict) -> dict:
    return decide_from_payload(payload)


@app.get("/api/carbon")
def get_carbon_data() -> dict[str, str]:
    return {"data": "Iceland: 90% Renewable", "source": "mock"}


@app.post("/api/llm")
def get_llm_response(payload: dict | None = None) -> dict[str, str]:
    prompt = (payload or {}).get("prompt", "Hello")
    return {
        "response": f"Mock AI says: This is a safe fallback response for '{prompt}'.",
        "source": "mock",
    }


@app.post("/api/workflows", status_code=201)
def submit_workflow(payload: WorkflowSubmitRequest) -> dict:
    raw_payload = payload.model_dump(exclude_none=True)
    constraints = {**DEFAULT_CONSTRAINTS, **raw_payload.get("constraints", {})}
    workflow = store.save_workflow(build_workflow(raw_payload))
    workflow["constraints"] = constraints
    workflow["carbon_budget"] = make_budget(float(constraints["carbon_budget_remaining_g"]))
    step = current_step(workflow)

    decision = None
    if step:
        task = _task_for_step(step, workflow)
        decision = decide_for_task(task).to_dict()
        store.save_decision(workflow["workflow_id"], step["step_id"], decision)

    return {"workflow": workflow, "current_step": step, "decision": decision}


@app.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")
    return {
        "workflow": workflow,
        "current_step": current_step(workflow),
        "runs": store.get_runs(workflow_id),
        "decisions": store.get_decisions(workflow_id),
        "events": store.get_events(workflow_id),
        "resource_state": workflow.get("resource_state", {}),
    }


@app.get("/api/workflows/{workflow_id}/temporal-plan")
def get_temporal_plan(workflow_id: str) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")
    step = current_step(workflow)
    if not step:
        raise HTTPException(status_code=409, detail="workflow_complete")

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
    return {"plan": plan, "event": event}


@app.post("/api/workflows/{workflow_id}/execute-next")
def execute_next_step(workflow_id: str) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")

    step = current_step(workflow)
    if not step:
        return {"workflow": workflow, "message": "workflow_complete"}

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
        raise HTTPException(status_code=409, detail="no_feasible_candidate")

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

    return {
        "workflow": workflow,
        "completed_step": step,
        "run": run,
        "carbon_budget": workflow["carbon_budget"],
        "runtime_event": runtime_event,
        "next_step": next_step,
        "next_decision": next_decision,
    }


@app.post("/api/workflows/{workflow_id}/execute-all")
def execute_all_steps(workflow_id: str) -> dict:
    outputs = []
    while True:
        workflow = store.get_workflow(workflow_id)
        if not workflow:
            raise HTTPException(status_code=404, detail="workflow_not_found")
        if not current_step(workflow):
            return {"workflow": workflow, "runs": store.get_runs(workflow_id), "events": store.get_events(workflow_id), "executions": outputs}
        payload = execute_next_step(workflow_id)
        outputs.append(payload)


@app.get("/api/workflows/{workflow_id}/telemetry")
def get_telemetry(workflow_id: str) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")
    return {
        "workflow_id": workflow_id,
        "carbon_budget": workflow.get("carbon_budget"),
        "runs": store.get_runs(workflow_id),
        "decisions": store.get_decisions(workflow_id),
        "events": store.get_events(workflow_id),
        "summary": actual_totals(store.get_runs(workflow_id)),
    }


@app.get("/api/workflows/{workflow_id}/what-if")
def what_if(workflow_id: str) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")
    step = current_step(workflow) or (workflow["steps"][-1] if workflow["steps"] else None)
    if not step:
        raise HTTPException(status_code=409, detail="workflow_has_no_steps")

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
    return {"workflow_id": workflow_id, "step": step, "profiles": profiles}


@app.post("/api/workflows/{workflow_id}/runtime/environment")
def update_environment(workflow_id: str) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")

    payload = {}
    event = apply_environment_change(workflow, payload)
    step = current_step(workflow)
    decision = None
    if step:
        event["step_id"] = step["step_id"]
        decision = decide_for_task(_task_for_step(step, workflow)).to_dict()
        store.save_decision(workflow_id, step["step_id"], decision)
        event["replanned_candidate_id"] = decision["selected_candidate"]["candidate_id"] if decision.get("selected_candidate") else None
    store.append_event(workflow_id, event)
    store.save_workflow(workflow)
    return {
        "workflow": workflow,
        "resource_state": workflow.get("resource_state", {}),
        "event": event,
        "decision": decision,
    }


@app.post("/api/workflows/{workflow_id}/runtime/budget")
def update_budget(workflow_id: str, payload: dict | None = None) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")
    payload = payload or {}
    remaining = float(payload.get("remaining_carbon_g", workflow["carbon_budget"]["remaining_carbon_g"]))
    workflow["carbon_budget"]["remaining_carbon_g"] = remaining
    workflow["carbon_budget"]["used_carbon_g"] = workflow["carbon_budget"]["total_carbon_budget_g"] - remaining
    store.save_workflow(workflow)
    return {"workflow": workflow, "carbon_budget": workflow["carbon_budget"]}


@app.get("/api/workflows/{workflow_id}/passport")
def decision_passport(workflow_id: str) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")
    decisions = store.get_decisions(workflow_id)
    runs = store.get_runs(workflow_id)
    events = store.get_events(workflow_id)
    latest_step_id = next(reversed(decisions), None) if decisions else None
    latest_decision = decisions.get(latest_step_id) if latest_step_id else None
    selected = latest_decision.get("selected_candidate") if latest_decision else None
    rejected = latest_decision.get("rejected_candidates", []) if latest_decision else []
    return {
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


@app.get("/api/workflows/{workflow_id}/dashboard/static-vs-dynamic")
def static_vs_dynamic(workflow_id: str) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")
    runs = store.get_runs(workflow_id)
    dynamic = actual_totals(runs)
    baseline = baseline_workflow_totals(workflow)
    if not baseline.get("steps"):
        baseline = baseline_totals(len(runs) or len(workflow.get("steps", [])))
    comparison = compare_totals(baseline, dynamic)
    comparison["replans"] = len([event for event in store.get_events(workflow_id) if event["type"] == "environment_change"])
    comparison["circuit_breaker_events"] = len([event for event in store.get_events(workflow_id) if event["type"] == "carbon_circuit_breaker"])
    return {"workflow_id": workflow_id, **comparison}


@app.get("/api/runtime/carbon")
def get_carbon_snapshot() -> dict:
    snapshot = store.carbon_snapshot or carbon_snapshot(force_live=False)
    store.carbon_snapshot = snapshot
    return snapshot


@app.post("/api/runtime/carbon/refresh")
def refresh_carbon_snapshot() -> dict:
    payload = {}
    snapshot = carbon_snapshot(force_live=bool(payload.get("force_live", False)))
    store.carbon_snapshot = snapshot
    return {"snapshot": snapshot}


def _workflow_constraints(workflow: dict) -> dict:
    return {**DEFAULT_CONSTRAINTS, **workflow.get("constraints", {})}


def _task_for_step(step: dict, workflow: dict) -> Task:
    constraints = _workflow_constraints(workflow)
    budget = workflow.setdefault("carbon_budget", make_budget(float(constraints["carbon_budget_remaining_g"])))
    constraints["carbon_budget_remaining_g"] = budget["remaining_carbon_g"]
    constraints["resource_state"] = workflow.get("resource_state", {})
    return Task.from_payload(step, constraints)


def _apply_carbon_snapshot_to_workflow(workflow: dict, snapshot: dict) -> dict:
    resource_state = workflow.setdefault("resource_state", {})
    resource_state["carbon_provider"] = snapshot["provider"]
    resource_state["carbon_provider_status"] = snapshot["status"]
    resource_state["carbon_intensity_by_location"] = {location: region["carbon_intensity_g_per_kwh"] for location, region in snapshot["regions"].items()}
    resource_state["carbon_snapshot_updated_at"] = snapshot["updated_at"]
    return workflow


def _decision_for_profile(step: dict, workflow: dict, priority: str) -> dict:
    constraints = _workflow_constraints(workflow)
    budget = workflow.setdefault("carbon_budget", make_budget(float(constraints["carbon_budget_remaining_g"])))
    constraints["carbon_budget_remaining_g"] = budget["remaining_carbon_g"]
    constraints["priority"] = priority
    constraints["resource_state"] = workflow.get("resource_state", {})
    return decide_for_task(Task.from_payload(step, constraints)).to_dict()


@app.exception_handler(ValidationError)
def validation_exception_handler(_, exc: ValidationError):
    return validation_error_payload(exc)

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from app.carbon.budget import make_budget
from app.intelligence.service import decide_from_payload, runtime_config
from app.scheduler.engine import decide_for_task
from app.schemas import (
    DecisionResponse,
    RuntimeConfigSchema,
    TaskRequest,
    TemporalPlanResponse,
    WorkflowSubmitRequest,
)
from app.storage.memory import store
from app.temporal.planner import plan_green_window
from app.workflow.engine import build_workflow, current_step
from app.api.server import DEFAULT_CONSTRAINTS, _task_for_step


api = FastAPI(title="GreenPilot Backend Intelligence")


@api.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "greenpilot-fastapi-backend"}


@api.get("/api/runtime/config", response_model=RuntimeConfigSchema)
def get_runtime_config() -> RuntimeConfigSchema:
    return runtime_config()


@api.post("/api/decisions", response_model=DecisionResponse)
def create_decision(task: TaskRequest) -> dict:
    return decide_from_payload(task.model_dump())


@api.post("/api/workflows", status_code=201)
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


@api.get("/api/workflows/{workflow_id}/temporal-plan", response_model=TemporalPlanResponse)
def get_temporal_plan(workflow_id: str) -> dict:
    workflow = store.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="workflow_not_found")
    step = current_step(workflow)
    if not step:
        raise HTTPException(status_code=409, detail="workflow_complete")

    task = _task_for_step(step, workflow)
    decision = store.get_decision(workflow_id, step["step_id"]) or decide_for_task(task).to_dict()
    return plan_green_window(task, decision, workflow_id, step["step_id"])

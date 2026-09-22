from __future__ import annotations

from app.models import WorkflowStep, WorkflowStatus, new_id, utc_now_iso


DEFAULT_STEPS = [
    {
        "name": "Ingest request",
        "type": "summarize",
        "input": "Parse and summarize the incoming sustainability report.",
    },
    {
        "name": "Analyze emissions",
        "type": "analyze",
        "input": "Estimate impact categories and flag high-carbon operations.",
    },
    {
        "name": "Generate response",
        "type": "generate",
        "input": "Create a concise recommendation for the user.",
    },
]


def build_workflow(payload: dict) -> dict:
    workflow_id = new_id("workflow")
    raw_steps = payload.get("steps") or DEFAULT_STEPS
    steps: list[WorkflowStep] = []

    previous_step_id: str | None = None
    for index, raw_step in enumerate(raw_steps, start=1):
        step_id = raw_step.get("step_id") or f"step_{index}"
        depends_on = raw_step.get("depends_on")
        if depends_on is None:
            depends_on = [previous_step_id] if previous_step_id else []
        steps.append(
            WorkflowStep(
                step_id=step_id,
                name=raw_step.get("name", f"Step {index}"),
                type=raw_step.get("type", "summarize"),
                input=raw_step.get("input", ""),
                depends_on=depends_on,
                status="pending",
            )
        )
        previous_step_id = step_id

    return {
        "workflow_id": workflow_id,
        "created_at": utc_now_iso(),
        "status": "pending",
        "constraints": payload.get("constraints", {}),
        "steps": [step.to_dict() for step in steps],
    }


def current_step(workflow: dict) -> dict | None:
    completed = {
        step["step_id"]
        for step in workflow["steps"]
        if step["status"] in {"done"}
    }
    for step in workflow["steps"]:
        if step["status"] == "pending" and all(dep in completed for dep in step["depends_on"]):
            return step
    return None


def set_step_status(workflow: dict, step_id: str, status: WorkflowStatus) -> dict:
    for step in workflow["steps"]:
        if step["step_id"] == step_id:
            step["status"] = status
            break
    workflow["status"] = "done" if all(step["status"] == "done" for step in workflow["steps"]) else "pending"
    return workflow

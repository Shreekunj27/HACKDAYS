from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PriorityProfile = Literal["fast", "balanced", "green", "quality"]
TaskType = Literal["summarize", "classify", "generate", "embed", "analyze"]


class RuntimeConfigSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    mock_mode: bool = Field(default=True)
    openai_configured: bool = Field(default=False)
    watttime_configured: bool = Field(default=False)
    carbon_provider_configured: bool = Field(default=False)
    live_mode_ready: bool = Field(default=False)
    deployment_url: str | None = Field(default=None)
    public_url_verified: bool = Field(default=False)
    storage_backend: str = Field(default="in_memory")


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    type: TaskType = "summarize"
    input: str = ""
    min_accuracy: float = Field(default=0.85, ge=0.0, le=1.0)
    max_latency_ms: int = Field(default=3000, gt=0)
    priority: PriorityProfile = "balanced"
    deadline: str | None = None
    delay_tolerant: bool = False
    cost_limit: float = Field(default=0.02, ge=0.0)
    carbon_budget_remaining_g: float = Field(default=10.0, ge=0.0)
    resource_state: dict[str, Any] = Field(default_factory=dict)


class WorkflowStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str | None = None
    name: str = "Step"
    type: TaskType = "summarize"
    input: str = ""
    depends_on: list[str] | None = None


class WorkflowSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraints: TaskRequest = Field(default_factory=TaskRequest)
    steps: list[WorkflowStepRequest] | None = None


class CandidateSchema(BaseModel):
    candidate_id: str
    model: str
    location: str
    time_window: str
    task_types: list[TaskType]
    latency_ms: int
    accuracy: float
    cost_usd: float
    power_watts: float
    duration_seconds: float
    carbon_intensity_g_per_kwh: float
    energy_wh: float
    carbon_g: float


class RejectedCandidateSchema(BaseModel):
    candidate: CandidateSchema
    reasons: list[str]


class DecisionResponse(BaseModel):
    selected_candidate: CandidateSchema | None
    score: float | None
    weights: dict[str, float]
    normalized_metrics: dict[str, dict[str, float]]
    feasible_candidates: list[CandidateSchema]
    rejected_candidates: list[RejectedCandidateSchema]
    constraint_results: dict[str, Any]
    reason: str
    runtime_config: RuntimeConfigSchema


class TemporalPlanResponse(BaseModel):
    workflow_id: str
    step_id: str | None
    mode: Literal["mock_temporal_plan", "temporal_ready"]
    wait_required: bool
    wait_minutes: int
    target_time_window: str
    selected_candidate_id: str | None
    reason: str
    cleanest_window: dict[str, Any] | None = None

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4


PriorityProfile = Literal["fast", "balanced", "green", "quality"]
TaskType = Literal["summarize", "classify", "generate", "embed", "analyze"]
WorkflowStatus = Literal["pending", "scheduled", "running", "done", "failed"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


@dataclass(frozen=True)
class Task:
    task_id: str
    type: TaskType
    input: str
    min_accuracy: float
    max_latency_ms: int
    priority: PriorityProfile
    deadline: str | None
    delay_tolerant: bool
    cost_limit: float
    carbon_budget_remaining_g: float
    resource_state: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any], defaults: dict[str, Any]) -> "Task":
        return cls(
            task_id=payload.get("task_id") or payload.get("step_id") or new_id("task"),
            type=payload.get("type", defaults.get("type", "summarize")),
            input=payload.get("input", defaults.get("input", "")),
            min_accuracy=float(payload.get("min_accuracy", defaults["min_accuracy"])),
            max_latency_ms=int(payload.get("max_latency_ms", defaults["max_latency_ms"])),
            priority=payload.get("priority", defaults["priority"]),
            deadline=payload.get("deadline", defaults.get("deadline")),
            delay_tolerant=bool(payload.get("delay_tolerant", defaults["delay_tolerant"])),
            cost_limit=float(payload.get("cost_limit", defaults["cost_limit"])),
            carbon_budget_remaining_g=float(
                payload.get("carbon_budget_remaining_g", defaults["carbon_budget_remaining_g"])
            ),
            resource_state=payload.get("resource_state", defaults.get("resource_state", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    name: str
    type: TaskType
    input: str
    depends_on: list[str] = field(default_factory=list)
    status: WorkflowStatus = "pending"

    def to_task(self, defaults: dict[str, Any]) -> Task:
        payload = {
            "task_id": self.step_id,
            "type": self.type,
            "input": self.input,
        }
        return Task.from_payload(payload, defaults)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Candidate:
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

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Candidate":
        return cls(
            candidate_id=payload["candidate_id"],
            model=payload["model"],
            location=payload["location"],
            time_window=payload["time_window"],
            task_types=payload["task_types"],
            latency_ms=int(payload["latency_ms"]),
            accuracy=float(payload["accuracy"]),
            cost_usd=float(payload["cost_usd"]),
            power_watts=float(payload["power_watts"]),
            duration_seconds=float(payload["duration_seconds"]),
            carbon_intensity_g_per_kwh=float(payload["carbon_intensity_g_per_kwh"]),
            energy_wh=float(payload["energy_wh"]),
            carbon_g=float(payload["carbon_g"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RejectedCandidate:
    candidate: Candidate
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "reasons": self.reasons,
        }


@dataclass(frozen=True)
class Decision:
    selected_candidate: Candidate | None
    score: float | None
    weights: dict[str, float]
    normalized_metrics: dict[str, dict[str, float]]
    feasible_candidates: list[Candidate]
    rejected_candidates: list[RejectedCandidate]
    constraint_results: dict[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_candidate": self.selected_candidate.to_dict() if self.selected_candidate else None,
            "score": self.score,
            "weights": self.weights,
            "normalized_metrics": self.normalized_metrics,
            "feasible_candidates": [candidate.to_dict() for candidate in self.feasible_candidates],
            "rejected_candidates": [rejected.to_dict() for rejected in self.rejected_candidates],
            "constraint_results": self.constraint_results,
            "reason": self.reason,
        }

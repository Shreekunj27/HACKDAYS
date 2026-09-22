from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models import Task
from app.schemas import RuntimeConfigSchema, TaskRequest


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_env_value(name: str, *, root_dir: Path | None = None) -> str | None:
    value = os.getenv(name)
    if value not in (None, ""):
        return value.strip()

    env_file = (root_dir or _project_root()) / ".env"
    if not env_file.exists():
        return None

    for line in env_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, raw_value = [part.strip() for part in text.split("=", 1)]
        if key == name:
            return raw_value.strip().strip('"\'') or None
    return None


def runtime_config(*, root_dir: Path | None = None) -> RuntimeConfigSchema:
    mock_value = _read_env_value("MOCK_MODE", root_dir=root_dir)
    mock_mode = mock_value is None or mock_value.strip().lower() not in {"0", "false", "no"}
    openai_configured = bool(_read_env_value("OPENAI_API_KEY", root_dir=root_dir))
    watttime_configured = bool(_read_env_value("WATTTIME_API_KEY", root_dir=root_dir))
    carbon_provider_configured = bool(
        _read_env_value("CARBON_INTENSITY_API_KEY", root_dir=root_dir)
        or _read_env_value("WATTTIME_API_KEY", root_dir=root_dir)
    )
    deployment_url = (
        _read_env_value("PUBLIC_URL", root_dir=root_dir)
        or _read_env_value("DEPLOYMENT_URL", root_dir=root_dir)
        or _read_env_value("APP_URL", root_dir=root_dir)
        or "local-demo"
    )

    return RuntimeConfigSchema(
        mock_mode=mock_mode,
        openai_configured=openai_configured,
        watttime_configured=watttime_configured,
        carbon_provider_configured=carbon_provider_configured,
        live_mode_ready=(
            not mock_mode
            and openai_configured
            and carbon_provider_configured
        ),
        deployment_url=deployment_url,
        public_url_verified=False,
        storage_backend=os.getenv("GREENPILOT_STORAGE_BACKEND", "in_memory"),
    )


def validate_task_payload(payload: dict[str, Any]) -> TaskRequest:
    return TaskRequest.model_validate(payload)


def task_from_request(request_model: TaskRequest) -> Task:
    data = request_model.model_dump()
    return Task.from_payload(data, data)


def decide_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from app.scheduler.engine import decide_for_task

    request_model = validate_task_payload(payload)
    decision = decide_for_task(task_from_request(request_model)).to_dict()
    decision["runtime_config"] = runtime_config().model_dump()
    return decision


def validation_error_payload(exc: ValidationError) -> dict[str, Any]:
    return {
        "error": "validation_error",
        "details": exc.errors(),
    }

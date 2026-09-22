from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.carbon.estimator import with_energy_and_carbon
from app.models import Candidate, Task

if TYPE_CHECKING:
    from app.carbon.provider import BaseCarbonIntensityProvider


MODEL_REGISTRY = {
    "small-local": {
        "task_types": ["summarize", "classify", "embed"],
        "accuracy": 0.82,
        "base_latency_ms": 900,
        "cost_usd": 0.001,
        "power_watts": 35,
    },
    "medium-edge": {
        "task_types": ["summarize", "classify", "generate", "analyze"],
        "accuracy": 0.9,
        "base_latency_ms": 1400,
        "cost_usd": 0.004,
        "power_watts": 70,
    },
    "large-cloud": {
        "task_types": ["summarize", "classify", "generate", "analyze"],
        "accuracy": 0.96,
        "base_latency_ms": 2600,
        "cost_usd": 0.015,
        "power_watts": 220,
    },
}

LOCATION_PROFILES = {
    "local-laptop": {"latency_factor": 1.0, "carbon_intensity_g_per_kwh": 420},
    "edge-delhi": {"latency_factor": 0.85, "carbon_intensity_g_per_kwh": 510},
    "cloud-clean-eu": {"latency_factor": 1.25, "carbon_intensity_g_per_kwh": 95},
}

TIME_WINDOWS = {
    "now": {"latency_factor": 1.0, "carbon_factor": 1.0, "delay_minutes": 0},
    "plus_30_min": {"latency_factor": 1.0, "carbon_factor": 0.78, "delay_minutes": 30},
    "cleaner_window": {"latency_factor": 1.08, "carbon_factor": 0.52, "delay_minutes": 90},
}


def build_candidates(
    task: Task,
    carbon_provider: BaseCarbonIntensityProvider | None = None,
) -> list[Candidate]:
    windows = TIME_WINDOWS if task.delay_tolerant else {"now": TIME_WINDOWS["now"]}
    candidates: list[Candidate] = []
    resource_state = task.resource_state or {}
    latency_multiplier = float(resource_state.get("latency_multiplier", 1.0))
    carbon_multiplier = float(resource_state.get("carbon_multiplier", 1.0))
    cost_multiplier = float(resource_state.get("cost_multiplier", 1.0))
    live_intensities = resource_state.get("carbon_intensity_by_location", {})

    for model_name, model in MODEL_REGISTRY.items():
        for location, location_profile in LOCATION_PROFILES.items():
            for window, window_profile in windows.items():
                latency_ms = round(
                    model["base_latency_ms"]
                    * location_profile["latency_factor"]
                    * window_profile["latency_factor"]
                    * latency_multiplier
                )
                duration_seconds = max(latency_ms / 1000, 0.1)
                
                if location in live_intensities:
                    base_carbon_intensity = float(live_intensities[location])
                elif carbon_provider is not None:
                    base_carbon_intensity = float(carbon_provider.get_intensity(location))
                else:
                    base_carbon_intensity = float(location_profile["carbon_intensity_g_per_kwh"])

                carbon_intensity = base_carbon_intensity * window_profile["carbon_factor"] * carbon_multiplier
                enriched = with_energy_and_carbon(
                    {
                        "power_watts": model["power_watts"],
                        "duration_seconds": duration_seconds,
                        "carbon_intensity_g_per_kwh": carbon_intensity,
                    }
                )
                candidates.append(
                    Candidate(
                        candidate_id=f"{task.task_id}:{model_name}:{location}:{window}",
                        model=model_name,
                        location=location,
                        time_window=window,
                        task_types=list(model["task_types"]),
                        latency_ms=latency_ms,
                        accuracy=model["accuracy"],
                        cost_usd=round(model["cost_usd"] * cost_multiplier, 6),
                        power_watts=enriched["power_watts"],
                        duration_seconds=enriched["duration_seconds"],
                        carbon_intensity_g_per_kwh=enriched["carbon_intensity_g_per_kwh"],
                        energy_wh=enriched["energy_wh"],
                        carbon_g=enriched["carbon_g"],
                    )
                )
    return candidates

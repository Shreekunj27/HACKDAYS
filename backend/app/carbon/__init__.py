"""Carbon and runtime intelligence modules."""

from app.carbon.estimator import (
    CarbonEstimator,
    estimate_carbon_g,
    estimate_energy_wh,
    with_energy_and_carbon,
)
from app.carbon.budget import deduct_carbon, make_budget
from app.carbon.provider import (
    BaseCarbonIntensityProvider,
    CarbonIntensityProvider,
    CarbonIntensityRecord,
    carbon_snapshot,
    get_carbon_provider,
    set_carbon_provider,
    simulated_snapshot,
)

__all__ = [
    "CarbonEstimator",
    "estimate_carbon_g",
    "estimate_energy_wh",
    "with_energy_and_carbon",
    "deduct_carbon",
    "make_budget",
    "enforce_circuit_breaker",
    "BaseCarbonIntensityProvider",
    "CarbonIntensityProvider",
    "CarbonIntensityRecord",
    "carbon_snapshot",
    "get_carbon_provider",
    "set_carbon_provider",
    "simulated_snapshot",
]


def __getattr__(name: str):
    if name == "enforce_circuit_breaker":
        from app.carbon.runtime import enforce_circuit_breaker

        return enforce_circuit_breaker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

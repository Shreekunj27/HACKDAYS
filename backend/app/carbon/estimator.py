from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.carbon.provider import BaseCarbonIntensityProvider


def estimate_energy_wh(power_watts: float, duration_seconds: float) -> float:
    """Energy = power * time, returned in watt-hours."""
    return round(power_watts * (duration_seconds / 3600), 6)


def estimate_carbon_g(energy_wh: float, carbon_intensity_g_per_kwh: float) -> float:
    """Carbon = kWh * grid intensity."""
    energy_kwh = energy_wh / 1000
    return round(energy_kwh * carbon_intensity_g_per_kwh, 6)


def with_energy_and_carbon(metrics: dict) -> dict:
    energy_wh = estimate_energy_wh(metrics["power_watts"], metrics["duration_seconds"])
    carbon_g = estimate_carbon_g(energy_wh, metrics["carbon_intensity_g_per_kwh"])
    return {
        **metrics,
        "energy_wh": energy_wh,
        "carbon_g": carbon_g,
    }


class CarbonEstimator:
    """
    Object-oriented estimator that connects power/time models with CarbonIntensityProvider.
    """

    def __init__(self, provider: Optional[BaseCarbonIntensityProvider] = None):
        self.provider = provider

    def calculate_energy_wh(self, power_watts: float, duration_seconds: float) -> float:
        return estimate_energy_wh(power_watts, duration_seconds)

    def calculate_energy_kwh(self, power_watts: float, duration_seconds: float) -> float:
        return self.calculate_energy_wh(power_watts, duration_seconds) / 1000.0

    def estimate_carbon_g(self, energy_wh: float, carbon_intensity_g_per_kwh: float) -> float:
        return estimate_carbon_g(energy_wh, carbon_intensity_g_per_kwh)

    def estimate_carbon_for_location(
        self,
        power_watts: float,
        duration_seconds: float,
        location: str,
        timestamp: datetime | None = None,
    ) -> float:
        from app.carbon.provider import get_carbon_provider

        provider = self.provider or get_carbon_provider()
        intensity = provider.get_intensity(location, timestamp)
        energy_wh = self.calculate_energy_wh(power_watts, duration_seconds)
        return self.estimate_carbon_g(energy_wh, intensity)

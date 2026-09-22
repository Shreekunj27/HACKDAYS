from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import os
from typing import Any

import requests

from app.models import utc_now_iso
from app.resources.registry import LOCATION_PROFILES


REGION_ALIASES = {
    "local-laptop": "IN-NO",
    "edge-delhi": "IN-NO",
    "cloud-clean-eu": "FR",
}

ELECTRICITY_MAPS_LATEST_URL = "https://api.electricitymaps.com/v4/carbon-intensity/latest"


@dataclass(frozen=True)
class CarbonIntensityRecord:
    """Represents a measured or forecasted carbon intensity reading."""
    region: str
    timestamp: str
    carbon_intensity_g_per_kwh: float
    source: str
    is_forecast: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseCarbonIntensityProvider(ABC):
    """Abstract interface for Carbon Intensity Providers."""

    @abstractmethod
    def get_intensity(self, location: str, timestamp: datetime | None = None) -> float:
        """Return the carbon intensity in gCO2e/kWh for a given location or region."""
        pass

    @abstractmethod
    def get_snapshot(self, force_live: bool = False) -> dict[str, Any]:
        """Return a snapshot dictionary of carbon intensity across all tracked regions."""
        pass

    @abstractmethod
    def get_forecast(
        self,
        location: str,
        start_time: datetime | None = None,
        duration_minutes: int = 120,
        interval_minutes: int = 15,
    ) -> list[CarbonIntensityRecord]:
        """Return forecasted carbon intensity records over a forward time window."""
        pass

    @abstractmethod
    def find_cleanest_window(
        self,
        location: str,
        start_time: datetime | None = None,
        search_window_minutes: int = 120,
        interval_minutes: int = 15,
    ) -> CarbonIntensityRecord:
        """Find the time window with the lowest projected carbon intensity."""
        pass


def _extract_intensity(payload: Any) -> float | None:
    if isinstance(payload, dict):
        for key in [
            "carbonIntensity",
            "carbon_intensity",
            "intensity",
            "value",
            "rating",
        ]:
            if key in payload and isinstance(payload[key], (int, float)):
                return float(payload[key])
        for nested_key in ["data", "current", "result", "results"]:
            if nested_key in payload:
                result = _extract_intensity(payload[nested_key])
                if result is not None:
                    return result
    if isinstance(payload, list) and payload:
        return _extract_intensity(payload[0])
    return None


def _live_region_intensity(base_url: str, api_key: str, region: str, timeout_seconds: float) -> float:
    headers = {"auth-token": api_key}
    response = requests.get(
        base_url,
        params={
            "zone": region,
            "temporalGranularity": "hourly",
            "emissionFactorType": "lifecycle",
        },
        headers=headers,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    intensity = _extract_intensity(response.json())
    if intensity is None:
        raise ValueError("carbon intensity value not found in provider response")
    return round(float(intensity), 6)


class CarbonIntensityProvider(BaseCarbonIntensityProvider):
    """
    GreenPilot Carbon Intensity Provider.
    Provides regional baseline, diurnal variation, and live grid API fetching
    with graceful simulated fallback.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        location_profiles: dict[str, dict[str, Any]] | None = None,
        region_aliases: dict[str, str] | None = None,
        diurnal_amplitude: float = 0.20,
    ):
        self._base_url = base_url
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self.location_profiles = location_profiles or LOCATION_PROFILES
        self.region_aliases = region_aliases or REGION_ALIASES
        self.diurnal_amplitude = diurnal_amplitude

    @property
    def api_key(self) -> str | None:
        return self._api_key if self._api_key is not None else os.getenv("CARBON_INTENSITY_API_KEY")

    @property
    def base_url(self) -> str:
        return self._base_url or os.getenv("CARBON_INTENSITY_API_URL", ELECTRICITY_MAPS_LATEST_URL)

    @property
    def timeout_seconds(self) -> float:
        if self._timeout_seconds is not None:
            return self._timeout_seconds
        return float(os.getenv("CARBON_INTENSITY_TIMEOUT_SECONDS", "4"))

    def _diurnal_multiplier(self, dt: datetime) -> float:
        """
        Calculates diurnal factor based on time of day.
        Trough (solar generation peak) around 13:00 UTC.
        Peak (thermal evening demand) around 20:00 UTC.
        """
        hour_fraction = dt.hour + (dt.minute / 60.0)
        angle = 2.0 * math.pi * (hour_fraction - 13.0) / 24.0
        return 1.0 + (self.diurnal_amplitude * math.sin(angle))

    def get_intensity(self, location: str, timestamp: datetime | None = None) -> float:
        """
        Returns the carbon intensity (gCO2e/kWh) for the specified location.
        Uses live grid if configured, or simulated profile modulated by diurnal curve.
        """
        profile = self.location_profiles.get(location)
        base_intensity = profile["carbon_intensity_g_per_kwh"] if profile else 350.0
        dt = timestamp or datetime.now(timezone.utc)

        # If live API is configured and no specific future timestamp is requested, try live
        api_key = self.api_key
        if api_key and timestamp is None:
            region = self.region_aliases.get(location, location)
            try:
                return _live_region_intensity(
                    base_url=self.base_url,
                    api_key=api_key,
                    region=region,
                    timeout_seconds=self.timeout_seconds,
                )
            except Exception:
                pass

        # Return diurnal-modeled baseline
        multiplier = self._diurnal_multiplier(dt)
        return max(10.0, round(base_intensity * multiplier, 2))

    def get_snapshot(self, force_live: bool = False) -> dict[str, Any]:
        """Returns snapshot across all registered locations."""
        api_key = self.api_key

        if not api_key:
            snapshot = simulated_snapshot()
            snapshot["status"] = "missing_live_config" if force_live else "ok"
            return snapshot

        base_url = self.base_url
        timeout_seconds = self.timeout_seconds
        regions: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}

        for location in self.location_profiles:
            region = self.region_aliases.get(location, location)
            try:
                regions[location] = {
                    "region": region,
                    "carbon_intensity_g_per_kwh": _live_region_intensity(
                        base_url=base_url,
                        api_key=api_key,
                        region=region,
                        timeout_seconds=timeout_seconds,
                    ),
                    "source": "live",
                }
            except Exception as exc:
                errors[location] = str(exc)
                regions[location] = {
                    "region": region,
                    "carbon_intensity_g_per_kwh": self.location_profiles[location]["carbon_intensity_g_per_kwh"],
                    "source": "simulated_fallback",
                }

        return {
            "provider": "electricity_maps" if not errors else "electricity_maps_with_fallback",
            "status": "ok" if not errors else "partial_fallback",
            "updated_at": utc_now_iso(),
            "regions": regions,
            "error": errors or None,
        }

    def get_forecast(
        self,
        location: str,
        start_time: datetime | None = None,
        duration_minutes: int = 120,
        interval_minutes: int = 15,
    ) -> list[CarbonIntensityRecord]:
        start = start_time or datetime.now(timezone.utc)
        steps = max(1, duration_minutes // interval_minutes)
        records: list[CarbonIntensityRecord] = []

        for step in range(steps):
            offset_seconds = step * interval_minutes * 60
            ts = datetime.fromtimestamp(start.timestamp() + offset_seconds, tz=timezone.utc)
            intensity = self.get_intensity(location, ts)
            records.append(
                CarbonIntensityRecord(
                    region=location,
                    timestamp=ts.isoformat(),
                    carbon_intensity_g_per_kwh=intensity,
                    source="diurnal_forecast_model",
                    is_forecast=True,
                )
            )
        return records

    def find_cleanest_window(
        self,
        location: str,
        start_time: datetime | None = None,
        search_window_minutes: int = 120,
        interval_minutes: int = 15,
    ) -> CarbonIntensityRecord:
        forecast = self.get_forecast(location, start_time, search_window_minutes, interval_minutes)
        return min(forecast, key=lambda rec: rec.carbon_intensity_g_per_kwh)


_DEFAULT_PROVIDER: CarbonIntensityProvider | None = None


def get_carbon_provider() -> CarbonIntensityProvider:
    global _DEFAULT_PROVIDER
    if _DEFAULT_PROVIDER is None:
        _DEFAULT_PROVIDER = CarbonIntensityProvider()
    return _DEFAULT_PROVIDER


def set_carbon_provider(provider: CarbonIntensityProvider | None) -> None:
    global _DEFAULT_PROVIDER
    _DEFAULT_PROVIDER = provider


def simulated_snapshot() -> dict[str, Any]:
    regions = {
        location: {
            "region": REGION_ALIASES.get(location, location),
            "carbon_intensity_g_per_kwh": profile["carbon_intensity_g_per_kwh"],
            "source": "simulated",
        }
        for location, profile in LOCATION_PROFILES.items()
    }
    return {
        "provider": "simulated",
        "status": "ok",
        "updated_at": utc_now_iso(),
        "regions": regions,
        "error": None,
    }


def carbon_snapshot(force_live: bool = False) -> dict[str, Any]:
    return get_carbon_provider().get_snapshot(force_live=force_live)

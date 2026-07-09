"""
Hestia.Index — FWI State Manager
==================================

Persists (FFMC, DMC, DC) state per location so that:

  1. **First encounter**: hot-start from N days of historical weather,
     then compute today's FWI in one flow.
  2. **Subsequent encounters**: just apply today's weather to the
     carried-forward state — no API history call needed.

This avoids re-fetching 14 days of archive data for the same hotspot
on every poll cycle. The state is persisted to a JSON file similar to
Vigil's alert_history.json.

Usage:
    from fwi_state_manager import FWIStateManager

    manager = FWIStateManager()
    result = manager.get_fwi(lat=39.0, lon=-105.0, temp=22, rh=45,
                             wind=12, rain=0.0, lat_for_factor=45.0)
    # Returns FWIResult — auto-initializes state if first time.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from fwi_calculator import fwi_daily, FWIResult

logger = logging.getLogger(__name__)

# Default state file location (alongside the Vigil alert history)
_DEFAULT_STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "fwi_state.json"

# Number of days of history to fetch for hot-starting a new location
_DEFAULT_SPINUP_DAYS = 14

# Location key precision (decimal places for lat/lon rounding).
# 2 dp ≈ 1.1 km at the equator — more than enough for FWI purposes and
# prevents state bloat from minor coordinate shifts.
_LOC_PRECISION = 2


def _location_key(lat: float, lon: float) -> str:
    """Build a stable key for a location rounded to 2dp."""
    return f"{lat:.{_LOC_PRECISION}f}_{lon:.{_LOC_PRECISION}f}"


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "locations": {},
    }


# ---------------------------------------------------------------------------
# FWI State Manager
# ---------------------------------------------------------------------------


class FWIStateManager:
    """Manage per-location FWI moisture-code state with persistence.

    Caller responsibilities:
      - Provide today's weather observations at noon local time
      - Optionally call ``ensure_initialized()`` before ``get_fwi()`` to
        hot-start from historical data (requires a function that returns
        a list of historical NoonWeather records).
    """

    def __init__(self, state_path: str | Path | None = None):
        self._path = Path(state_path) if state_path else _DEFAULT_STATE_PATH
        self._state = self._load()

    # -- Persistence -------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return _empty_state()
        try:
            with open(self._path, "r") as fh:
                state = json.load(fh)
            if not isinstance(state, dict):
                return _empty_state()
            state.setdefault("version", 1)
            state.setdefault("locations", {})
            return state
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load FWI state, starting fresh: %s", e)
            return _empty_state()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as fh:
            json.dump(self._state, fh, indent=2, sort_keys=True)
            fh.write("\n")

    # -- State access ------------------------------------------------------

    def has_location(self, lat: float, lon: float) -> bool:
        """Check if we already have spun-up FWI state for this location."""
        key = _location_key(lat, lon)
        return key in self._state["locations"]

    def get_state(self, lat: float, lon: float) -> dict[str, Any] | None:
        """Get the stored state for a location, or None."""
        key = _location_key(lat, lon)
        return self._state["locations"].get(key)

    def set_state(
        self,
        lat: float,
        lon: float,
        ffmc: float,
        dmc: float,
        dc: float,
        last_date: str | None = None,
    ) -> None:
        """Update the stored FWI state for a location."""
        key = _location_key(lat, lon)
        self._state["locations"][key] = {
            "lat": round(lat, _LOC_PRECISION),
            "lon": round(lon, _LOC_PRECISION),
            "ffmc": ffmc,
            "dmc": dmc,
            "dc": dc,
            "last_date": last_date or date.today().isoformat(),
        }

    # -- Spin-up -----------------------------------------------------------

    def ensure_initialized(
        self,
        lat: float,
        lon: float,
        history_provider: callable | None = None,
        lat_for_factor: float | None = None,
    ) -> bool:
        """Hot-start the FWI state for a location if not already done.

        Parameters
        ----------
        lat, lon : float
            Coordinates of the hotspot.
        history_provider : callable, optional
            Function ``(lat, lon) -> list[NoonWeather]`` that returns
            N days of historical noon weather for spin-up. If None and
            the location is new, initialization is skipped (caller must
            handle separately).
        lat_for_factor : float, optional
            Latitude used for day-length factors. Defaults to ``lat``.

        Returns
        -------
        bool
            True if the location was newly initialized, False if it
            already existed or couldn't be initialized.
        """
        if self.has_location(lat, lon):
            return False  # Already spun up

        if history_provider is None:
            logger.info(
                "No history provider for new location %s (%.4f, %.4f) — "
                "skipping spin-up. Use defaults and first live reading.",
                _location_key(lat, lon), lat, lon,
            )
            # Set initial defaults so it doesn't retry the history call
            self.set_state(lat, lon, ffmc=85.0, dmc=6.0, dc=15.0)
            self._save()
            return True

        logger.info(
            "Hot-starting FWI for new location (%.4f, %.4f)...",
            lat, lon,
        )

        try:
            history = history_provider(lat, lon)
        except Exception as e:
            logger.error("History provider failed for %s,%s: %s", lat, lon, e)
            self.set_state(lat, lon, ffmc=85.0, dmc=6.0, dc=15.0)
            self._save()
            return True

        if not history:
            logger.warning("Empty history for %s,%s — using defaults", lat, lon)
            self.set_state(lat, lon, ffmc=85.0, dmc=6.0, dc=15.0)
            self._save()
            return True

        ffmc_p, dmc_p, dc_p = 85.0, 6.0, 15.0
        _lat = lat_for_factor if lat_for_factor is not None else lat

        for hw in history:
            try:
                h_month = int(hw.date.split("-")[1])
            except (IndexError, ValueError):
                h_month = date.today().month

            r = fwi_daily(
                temp=hw.temp_c,
                rh=hw.rh_pct,
                wind=hw.wind_kmh,
                rain=hw.precip_mm,
                prev_ffmc=ffmc_p,
                prev_dmc=dmc_p,
                prev_dc=dc_p,
                lat=_lat,
                month=h_month,
            )
            ffmc_p, dmc_p, dc_p = r.ffmc, r.dmc, r.dc

        self.set_state(lat, lon, ffmc_p, dmc_p, dc_p)
        self._save()
        logger.info(
            "FWI state initialized for %s: FFMC=%.1f DMC=%.1f DC=%.1f "
            "(%d days of history)",
            _location_key(lat, lon), ffmc_p, dmc_p, dc_p, len(history),
        )
        return True

    # -- FWI computation ---------------------------------------------------

    def get_fwi(
        self,
        lat: float,
        lon: float,
        temp: float,
        rh: float,
        wind: float,
        rain: float,
        month: int | None = None,
        lat_for_factor: float | None = None,
    ) -> FWIResult | None:
        """Compute today's FWI using stored (or default) state.

        Parameters
        ----------
        lat, lon : float
            Coordinates (for location lookup and day-length factor).
        temp, rh, wind, rain : float
            Today's noon weather observations (already in °C, %, km/h, mm).
        month : int, optional
            Month number. Auto-detected from today if not given.
        lat_for_factor : float, optional
            Latitude for day-length factor. Defaults to ``lat``.

        Returns
        -------
        FWIResult or None
            None if the location has no initialized state and can't be
            looked up.
        """
        state = self.get_state(lat, lon)
        if state is None:
            logger.warning(
                "No FWI state for (%.4f, %.4f). Call ensure_initialized() first.",
                lat, lon,
            )
            return None

        _month = month or date.today().month
        _lat = lat_for_factor if lat_for_factor is not None else lat

        result = fwi_daily(
            temp=temp,
            rh=rh,
            wind=wind,
            rain=rain,
            prev_ffmc=state["ffmc"],
            prev_dmc=state["dmc"],
            prev_dc=state["dc"],
            lat=_lat,
            month=_month,
        )

        # Persist the updated state for next time
        self.set_state(lat, lon, result.ffmc, result.dmc, result.dc)
        self._save()

        return result

    # -- Bulk operations ---------------------------------------------------

    def drop_location(self, lat: float, lon: float) -> bool:
        """Remove a location from the state store."""
        key = _location_key(lat, lon)
        if key in self._state["locations"]:
            del self._state["locations"][key]
            self._save()
            return True
        return False

    def clear_all(self) -> None:
        """Reset all stored FWI states."""
        self._state = _empty_state()
        self._save()

    def location_count(self) -> int:
        """Number of tracked locations."""
        return len(self._state["locations"])

    @property
    def state_path(self) -> Path:
        return self._path
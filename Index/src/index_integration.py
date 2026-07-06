"""
Hestia.Index → Vigil Integration
=================================

Bridges the FWI calculator, Open-Meteo weather client, and FWI state manager
into Vigil's FIRMS data pipeline. Provides the multi-factor filter for
separating real wildfires from false positives.

Filter rules (applied in order):
  1. FRP > 10 MW      → 🔥 ALWAYS SHOW (confirmed active fire)
  2. confidence = 'h'  → 🔥 ALWAYS SHOW (satellite says high confidence)
  3. remaining (nominal + low FRP) → check ISI:
       ISI >= 2.0 → ⚠️ Show (surface conditions support fire spread)
       ISI < 2.0  → 🔇 Hide (probably ag burn / gas flare)

Usage:
    from index_integration import apply_fwi_filter
    filtered = apply_fwi_filter(fires_dataframe, config)
    # filtered has new columns: ffmc, dmc, dc, isi, bui, fwi, danger_rating, fwi_show
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import Index components (with fallback paths for different run contexts)
# ---------------------------------------------------------------------------

try:
    from weather_client import OpenMeteoClient
    from fwi_state_manager import FWIStateManager
    from fwi_calculator import fwi_daily
except ImportError:
    import sys
    # Add both the Vigil and Index src directories so we can import from
    # either run context (Flask from Vigil, or directly from Index)
    _index_src = Path(__file__).resolve().parent
    if str(_index_src) not in sys.path:
        sys.path.insert(0, str(_index_src))
    from weather_client import OpenMeteoClient
    from fwi_state_manager import FWIStateManager
    from fwi_calculator import fwi_daily


# ---------------------------------------------------------------------------
# FWI danger → marker colour mapping for the Folium map
# ---------------------------------------------------------------------------

_FWI_COLOURS: dict[str, str] = {
    "Low":       "#2ecc71",   # green
    "Moderate":  "#f1c40f",   # yellow
    "High":      "#e67e22",   # orange
    "Very High": "#e74c3c",   # red
    "Extreme":   "#8e44ad",   # purple
}

# Default colour if FWI data unavailable
_COLOUR_UNAVAILABLE = "#555555"  # dark grey


def fwi_danger_colour(danger_rating: str) -> str:
    """Return a hex colour for a given FWI danger rating string."""
    return _FWI_COLOURS.get(danger_rating, _COLOUR_UNAVAILABLE)


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


def should_show(
    frp: float | None,
    confidence: str | None,
    isi: float | None,
) -> tuple[bool, str]:
    """Apply the multi-factor filter to a single fire detection.

    Parameters
    ----------
    frp : float or None
        Fire Radiative Power in MW.
    confidence : str or None
        FIRMS confidence code ('h', 'n', 'l', or None).
    isi : float or None
        Initial Spread Index from the FWI calculator.

    Returns
    -------
    tuple[bool, str]
        (show, reason) — whether to show this dot on the map, and why.
    """
    frp = frp if frp is not None and not pd.isna(frp) else 0.0
    confidence = str(confidence).lower() if confidence else "n"
    isi = isi if isi is not None and not pd.isna(isi) else -1.0

    # If we couldn't compute FWI (weather unavailable), err on the side
    # of showing the dot rather than suppressing a potential fire.
    if isi < 0:
        return True, "fwi_unavailable"

    # Rule 1: High FRP → confirmed active fire
    if frp > 10.0:
        return True, f"high_frp ({frp:.1f} MW)"

    # Rule 2: High satellite confidence
    if confidence == "h":
        return True, "high_confidence"

    # Rule 3: Nominal/low confidence — check if surface supports fire spread
    if isi >= 2.0:
        return True, f"isi_spread ({isi:.1f})"
    else:
        return False, f"isi_too_low ({isi:.1f})"


# ---------------------------------------------------------------------------
# Apply the filter to a FIRMS DataFrame
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cache: re-use FWI results within the same day
# ---------------------------------------------------------------------------

# Weather data for today doesn't change, so we cache the FWI filter output
# keyed by (date, rounded_lat, rounded_lon). The cache lives only for the
# current process — no disk persistence needed.
_FWI_CACHE: dict[str, dict[str, Any]] = {}
_FWI_CACHE_DATE: str = ""  # ISO date string for the cached day


def _fwi_cache_key(lat: float, lon: float) -> str:
    return f"{lat:.2f}_{lon:.2f}"


def _get_cached_fwi(lat: float, lon: float) -> dict[str, Any] | None:
    from datetime import date
    today = date.today().isoformat()
    if _FWI_CACHE_DATE != today:
        _FWI_CACHE.clear()
        return None
    return _FWI_CACHE.get(_fwi_cache_key(lat, lon))


def _set_cached_fwi(lat: float, lon: float, result: dict[str, Any]) -> None:
    from datetime import date
    global _FWI_CACHE_DATE
    _FWI_CACHE_DATE = date.today().isoformat()
    _FWI_CACHE[_fwi_cache_key(lat, lon)] = result


# Global weather client and state manager (persist across poll cycles)
_weather_client: OpenMeteoClient | None = None
_state_manager: FWIStateManager | None = None


def _get_client() -> OpenMeteoClient:
    global _weather_client
    if _weather_client is None:
        _weather_client = OpenMeteoClient()
    return _weather_client


def _get_manager(state_path: str | Path | None = None) -> FWIStateManager:
    global _state_manager
    if _state_manager is None:
        _state_manager = FWIStateManager(state_path=state_path)
    return _state_manager


def apply_fwi_filter(
    fires: pd.DataFrame,
    config: dict[str, Any] | None = None,
    state_path: str | Path | None = None,
) -> pd.DataFrame:
    """Apply the FWI-based filter to a FIRMS DataFrame.

    Steps:
      1. Deduplicate FIRMS rows by rounded (lat, lon) at 2dp.
      2. For each unique location, fetch weather from Open-Meteo and
         compute today's FWI (using persisted state from previous cycles).
      3. Apply ``should_show()`` to each row.
      4. Add FWI columns (ffmc, dmc, dc, isi, bui, fwi, danger_rating,
         fwi_show, fwi_reason) to the DataFrame.

    Parameters
    ----------
    fires : pd.DataFrame
        FIRMS detection data as returned by ``firms_client.fetch_fires()``.
    config : dict, optional
        Configuration dict (currently unused by the filter but kept for
        future integration with configurable thresholds).
    state_path : str or Path, optional
        Path to the FWI state JSON file. Defaults to
        ``Vigil/state/fwi_state.json``.

    Returns
    -------
    pd.DataFrame
        Same as input with new columns added. Rows are NOT removed —
        callers can use the ``fwi_show`` column to decide visibility.
    """
    if fires.empty:
        return fires.copy()

    df = fires.copy()
    client = _get_client()
    manager = _get_manager(state_path)

    # Step 1: Identify unique locations (rounded to 2dp)
    df["_loc_key"] = df.apply(
        lambda r: f"{round(float(r['latitude']), 2):.2f}_{round(float(r['longitude']), 2):.2f}",
        axis=1,
    )
    unique_locs = df["_loc_key"].unique()

    # Step 2: Fetch weather + FWI for each unique location
    fwi_cache: dict[str, dict[str, Any]] = {}

    for loc_key in unique_locs:
        try:
            parts = loc_key.split("_")
            lat = float(parts[0])
            lon = float(parts[1])
        except (ValueError, IndexError):
            logger.warning("Invalid location key: %s", loc_key)
            continue

        # Check day-level cache first
        cached = _get_cached_fwi(lat, lon)
        if cached is not None:
            fwi_cache[loc_key] = dict(cached)  # copy so we can mutate
            continue

        # Get today's weather
        weather = client.get_noon_weather(lat, lon)
        if weather is None:
            logger.debug("No weather data for %s — showing dot unfiltered", loc_key)
            # Can't compute FWI — show the dot with no FWI context
            fwi_cache[loc_key] = {
                "ffmc": None, "dmc": None, "dc": None,
                "isi": None, "bui": None, "fwi": None,
                "danger_rating": "Unknown",
            }
            continue

        # Ensure FWI state is initialized (first time: hot-start from history)
        manager.ensure_initialized(
            lat, lon,
            history_provider=lambda lat2, lon2: client.get_noon_history(lat2, lon2, days=14),
        )

        # Compute today's FWI using persisted state
        month = 7  # July — current month
        try:
            from datetime import date
            month = date.today().month
        except ImportError:
            pass

        result = manager.get_fwi(
            lat=lat, lon=lon,
            temp=weather.temp_c,
            rh=weather.rh_pct,
            wind=weather.wind_kmh,
            rain=weather.precip_mm,
            month=month,
        )

        if result is None:
            # Fall back to defaults
            r = fwi_daily(
                temp=weather.temp_c, rh=weather.rh_pct,
                wind=weather.wind_kmh, rain=weather.precip_mm,
                lat=lat, month=month,
            )
            fwi_entry = {
                "ffmc": r.ffmc, "dmc": r.dmc, "dc": r.dc,
                "isi": r.isi, "bui": r.bui, "fwi": r.fwi,
                "danger_rating": r.danger_rating(),
            }
        else:
            fwi_entry = {
                "ffmc": result.ffmc, "dmc": result.dmc, "dc": result.dc,
                "isi": result.isi, "bui": result.bui, "fwi": result.fwi,
                "danger_rating": result.danger_rating(),
            }

        fwi_cache[loc_key] = fwi_entry
        _set_cached_fwi(lat, lon, fwi_entry)

    # Step 3: Add FWI columns to each row
    df["ffmc"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("ffmc", None))
    df["dmc"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("dmc", None))
    df["dc"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("dc", None))
    df["isi"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("isi", None))
    df["bui"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("bui", None))
    df["fwi"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("fwi", None))
    df["danger_rating"] = df["_loc_key"].map(
        lambda k: fwi_cache.get(k, {}).get("danger_rating", "Unknown")
    )

    # Step 4: Apply the filter to each row
    filter_results = df.apply(
        lambda r: should_show(
            frp=r.get("frp"),
            confidence=r.get("confidence"),
            isi=r.get("isi"),
        ),
        axis=1,
    )
    df["fwi_show"] = filter_results.apply(lambda x: x[0])
    df["fwi_reason"] = filter_results.apply(lambda x: x[1])

    # Clean up
    df.drop(columns=["_loc_key"], inplace=True)

    shown = df["fwi_show"].sum()
    total = len(df)
    logger.info(
        "FWI filter: showing %d/%d detections (%.1f%% shown, %.1f%% filtered)",
        shown, total, 100 * shown / total if total else 0,
        100 * (total - shown) / total if total else 0,
    )

    return df


# ---------------------------------------------------------------------------
# Convenience: update the alert pipeline with FWI context
# ---------------------------------------------------------------------------


def format_fwi_alert_suffix(danger_rating: str, isi: float | None, fwi: float | None) -> str:
    """Build a short FWI context string for alert messages."""
    isi_str = f"{isi:.1f}" if isi is not None else "N/A"
    fwi_str = f"{fwi:.1f}" if fwi is not None else "N/A"
    return f"FWI: {fwi_str} ({danger_rating}) · ISI: {isi_str}"
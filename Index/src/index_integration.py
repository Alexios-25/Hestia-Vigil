"""
Hestia.Index → Vigil Integration
=================================

Bridges the FWI calculator, Open-Meteo weather client, and FWI state manager
into Vigil's FIRMS data pipeline. Provides the multi-factor filter for
separating real wildfires from false positives.

Filter rules (applied in order):
  1. Inside a current NIFC/WFIGS fire perimeter → 🔥 ALWAYS SHOW
  2. FRP > 10 MW      → 🔥 ALWAYS SHOW (confirmed active fire)
  3. confidence = 'h'  → 🔥 ALWAYS SHOW (satellite says high confidence)
    4. cluster_size >= N → ⚠️ Show (multi-pixel wildfire structure)
  5. ISI/FWI/BUI weather rules (optionally require cluster_size >= 2):
       ISI >= 2.0 or FWI >= 20 or BUI >= 40
  6. Otherwise   → 🔇 Hide (probably ag burn / gas flare)

Each detection also gets a 0–100 ``likelihood_score`` and a tier
(Confirmed / High / Medium / Low / Unlikely) based on the signals above.


Usage:
    from index_integration import apply_fwi_filter
    filtered = apply_fwi_filter(fires_dataframe, config)
    # filtered has new columns: ffmc, dmc, dc, isi, bui, fwi, danger_rating,
    # neighbor_count, cluster_size, fwi_show, fwi_reason, likelihood_score,
    # likelihood_tier
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filter thresholds
# ---------------------------------------------------------------------------

_FRP_SHOW_THRESHOLD = 10.0          # MW
_ISI_SHOW_THRESHOLD = 2.0
_FWI_SHOW_THRESHOLD = 20.0          # catch high-danger, low-spread fires
_BUI_SHOW_THRESHOLD = 40.0          # catch fuel-heavy, low-spread fires

# Default path to the NIFC/WFIGS current-perimeter snapshot written by Vigil.
_DEFAULT_PERIMETER_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / "wfigs" / "current.json"
)

# ---------------------------------------------------------------------------
# Import Index components (with fallback paths for different run contexts)
# ---------------------------------------------------------------------------

try:
    from weather_client import OpenMeteoClient
    from fwi_state_manager import FWIStateManager
    from fwi_calculator import fwi_daily
    from spatial_filter import add_cluster_columns, resolve_spatial_config
    from likelihood import score_detection, tier_from_score, resolve_likelihood_config
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
    from spatial_filter import add_cluster_columns, resolve_spatial_config
    from likelihood import score_detection, tier_from_score, resolve_likelihood_config


# ---------------------------------------------------------------------------
# Perimeter loading
# ---------------------------------------------------------------------------


def _load_perimeter_polygons(path: Path | None = None) -> list[Any] | None:
    """Load NIFC/WFIGS perimeters and return a list of prepared shapely polygons.

    Returns None if the snapshot is missing or unreadable. Invalid geometries
    are repaired with ``buffer(0)`` where possible.
    """
    path = Path(path) if path is not None else _DEFAULT_PERIMETER_PATH
    if not path.exists():
        logger.debug("No perimeter snapshot at %s", path)
        return None

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read perimeter snapshot %s: %s", path, exc)
        return None

    prepared: list[Any] = []
    for feature in data.get("features", []):
        geom = feature.get("geometry") or {}
        rings = geom.get("rings") or []
        if not rings:
            continue
        try:
            polygon = Polygon(shell=rings[0], holes=list(rings[1:]))
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            if polygon is not None and not polygon.is_empty:
                prepared.append(prep(polygon))
        except ValueError:
            continue

    return prepared if prepared else None


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
    fwi: float | None = None,
    bui: float | None = None,
    in_perimeter: bool = False,
    cluster_size: int = 1,
    min_cluster_size: int = 2,
    require_cluster_for_fwi: bool = True,
    spatial_enabled: bool = True,
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
    fwi : float or None
        Fire Weather Index.
    bui : float or None
        Buildup Index.
    in_perimeter : bool
        True if the detection falls inside a current NIFC/WFIGS perimeter.
    cluster_size : int
        Number of detections within the spatial radius (including self).
    min_cluster_size : int
        Minimum cluster size to treat as multi-pixel wildfire structure.
    require_cluster_for_fwi : bool
        If True (variant B), ISI/FWI/BUI alone cannot show a singleton.
    spatial_enabled : bool
        If False, skip cluster-based rules entirely.

    Returns
    -------
    tuple[bool, str]
        (show, reason) — whether to show this dot on the map, and why.
    """
    frp = frp if frp is not None and not pd.isna(frp) else 0.0
    confidence = str(confidence).lower() if confidence else "n"
    isi = isi if isi is not None and not pd.isna(isi) else -1.0
    fwi = fwi if fwi is not None and not pd.isna(fwi) else -1.0
    bui = bui if bui is not None and not pd.isna(bui) else -1.0
    try:
        cluster_size = int(cluster_size) if cluster_size is not None and not pd.isna(cluster_size) else 1
    except (TypeError, ValueError):
        cluster_size = 1

    # Rule 1: Known fire perimeter → always show.
    if in_perimeter:
        return True, "inside_perimeter"

    # If we couldn't compute FWI (weather unavailable), err on the side
    # of showing the dot rather than suppressing a potential fire.
    if isi < 0:
        return True, "fwi_unavailable"

    # Rule 2: High FRP → confirmed active fire
    if frp > _FRP_SHOW_THRESHOLD:
        return True, f"high_frp ({frp:.1f} MW)"

    # Rule 3: High satellite confidence
    if confidence == "h":
        return True, "high_confidence"

    # Rule 4: Multi-pixel cluster structure
    if spatial_enabled and cluster_size >= min_cluster_size:
        return True, f"cluster_size ({cluster_size})"

    # Weather rules (ISI / FWI / BUI). Optionally require a tiny cluster
    # so a lone heat pixel can't pass on weather alone (variant B).
    weather_ok = (
        isi >= _ISI_SHOW_THRESHOLD
        or fwi >= _FWI_SHOW_THRESHOLD
        or bui >= _BUI_SHOW_THRESHOLD
    )
    if weather_ok:
        if (
            spatial_enabled
            and require_cluster_for_fwi
            and cluster_size < 2
        ):
            return False, f"singleton_weak ({cluster_size})"
        if isi >= _ISI_SHOW_THRESHOLD:
            return True, f"isi_spread ({isi:.1f})"
        if fwi >= _FWI_SHOW_THRESHOLD:
            return True, f"high_fwi ({fwi:.1f})"
        return True, f"high_bui ({bui:.1f})"

    # Otherwise suppress
    if spatial_enabled and cluster_size < min_cluster_size:
        return False, f"singleton ({cluster_size})"
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
    perimeter_path: str | Path | None = None,
) -> pd.DataFrame:
    """Apply the FWI-based filter to a FIRMS DataFrame.

    Steps:
      1. Deduplicate FIRMS rows by rounded (lat, lon) at 2dp.
      2. For each unique location, fetch weather from Open-Meteo and
         compute today's FWI (using persisted state from previous cycles).
      3. Optionally check detections against the current NIFC/WFIGS
         perimeter snapshot.
      4. Compute spatial neighbor counts / cluster sizes.
      5. Apply ``should_show()`` to each row.
      6. Compute a 0–100 wildfire-likelihood score and tier.
      7. Add FWI + spatial + likelihood columns.

    Parameters
    ----------
    fires : pd.DataFrame
        FIRMS detection data as returned by ``firms_client.fetch_fires()``.
    config : dict, optional
        Configuration dict. Reads optional ``SPATIAL_FILTER`` section.
    state_path : str or Path, optional
        Path to the FWI state JSON file. Defaults to
        ``Index/state/fwi_state.json``.
    perimeter_path : str or Path, optional
        Path to the NIFC/WFIGS perimeter snapshot. Defaults to
        ``Vigil/state/wfigs/current.json``. If the file is missing, the
        perimeter override is skipped.

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
    spatial_cfg = resolve_spatial_config(config)

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

    # Step 3: Load current fire perimeters (if available).
    perimeter_polys = _load_perimeter_polygons(
        Path(perimeter_path) if perimeter_path is not None else None
    )

    # Step 4: Add FWI columns to each row
    df["ffmc"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("ffmc", None))
    df["dmc"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("dmc", None))
    df["dc"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("dc", None))
    df["isi"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("isi", None))
    df["bui"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("bui", None))
    df["fwi"] = df["_loc_key"].map(lambda k: fwi_cache.get(k, {}).get("fwi", None))
    df["danger_rating"] = df["_loc_key"].map(
        lambda k: fwi_cache.get(k, {}).get("danger_rating", "Unknown")
    )

    # Step 5: Check each original detection against current fire perimeters.
    # Per-row check is slightly more work than per-location caching, but it
    # avoids mis-classifying detections that share a rounded loc key but sit
    # on opposite sides of a perimeter boundary.
    if perimeter_polys:
        df["in_perimeter"] = df.apply(
            lambda r: any(
                poly.contains(Point(float(r["longitude"]), float(r["latitude"])))
                for poly in perimeter_polys
            ),
            axis=1,
        )
    else:
        df["in_perimeter"] = False

    # Step 6: Spatial clustering (neighbor counts within radius)
    if spatial_cfg["enabled"]:
        df = add_cluster_columns(df, radius_km=spatial_cfg["radius_km"])
    else:
        df["neighbor_count"] = 0
        df["cluster_size"] = 1

    # Step 7: Apply the filter to each row
    filter_results = df.apply(
        lambda r: should_show(
            frp=r.get("frp"),
            confidence=r.get("confidence"),
            isi=r.get("isi"),
            fwi=r.get("fwi"),
            bui=r.get("bui"),
            in_perimeter=bool(r.get("in_perimeter", False)),
            cluster_size=r.get("cluster_size", 1),
            min_cluster_size=spatial_cfg["min_cluster_size"],
            require_cluster_for_fwi=spatial_cfg["require_cluster_for_fwi"],
            spatial_enabled=spatial_cfg["enabled"],
        ),
        axis=1,
    )
    df["fwi_show"] = filter_results.apply(lambda x: x[0])
    df["fwi_reason"] = filter_results.apply(lambda x: x[1])

    # Step 8: Likelihood score + tier for prioritisation.
    likelihood_cfg = resolve_likelihood_config(config)
    df["likelihood_score"] = df.apply(score_detection, axis=1)
    df["likelihood_tier"] = df.apply(
        lambda r: tier_from_score(
            int(r["likelihood_score"]),
            bool(r.get("in_perimeter", False)),
            bool(r["fwi_show"]),
            likelihood_cfg,
        ),
        axis=1,
    )

    # Clean up
    df.drop(columns=["_loc_key"], inplace=True)

    shown = df["fwi_show"].sum()
    total = len(df)
    logger.info(
        "FWI filter: showing %d/%d detections (%.1f%% shown, %.1f%% filtered) "
        "[spatial enabled=%s R=%.2fkm N=%d require_cluster_for_fwi=%s]",
        shown, total, 100 * shown / total if total else 0,
        100 * (total - shown) / total if total else 0,
        spatial_cfg["enabled"],
        spatial_cfg["radius_km"],
        spatial_cfg["min_cluster_size"],
        spatial_cfg["require_cluster_for_fwi"],
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
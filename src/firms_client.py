"""
Hestia Vigil — NASA FIRMS Data Client

Fetches active fire detection data from the NASA FIRMS API and returns
it as a clean pandas DataFrame.  Designed to be called by the Flask
dashboard and by standalone scripts / tests.

FIRMS API reference:
    https://firms.modaps.eosdis.nasa.gov/api/area/csv/{map_key}/{source}/{bbox}/{day_range}

CSV columns returned by FIRMS:
    latitude, longitude, bright_ti4, scan, track, acq_date, acq_time,
    satellite, instrument, confidence, version, bright_ti5, frp, daynight

Usage:
    from config import load_config
    from firms_client import fetch_fires

    config = load_config("config.yaml")
    df = fetch_fires(config)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config import load_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FIRMS API client
# ---------------------------------------------------------------------------

_FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"


def _build_firms_url(config: dict[str, Any]) -> str:
    """Construct the FIRMS CSV download URL from config values.

    The URL format is:
        {base}/{map_key}/{source}/{bbox}/{day_range}

    Parameters
    ----------
    config : dict
        Must contain keys ``MAP_KEY``, ``SOURCE``, and ``BBOX``.
        ``MAP_KEY`` is normally loaded from the ``NASA_FIRMS_MAP_KEY``
        environment variable by ``config.load_config()``.
        ``BBOX`` is a list of four floats: [min_lon, min_lat, max_lon, max_lat].

    Returns
    -------
    str
        Fully-qualified FIRMS API URL.
    """
    map_key = config["MAP_KEY"]
    source = config["SOURCE"]
    bbox = config["BBOX"]
    # FIRMS expects bbox as "min_lon,min_lat,max_lon,max_lat"
    bbox_str = ",".join(str(v) for v in bbox)
    # day_range: number of days of data to retrieve (1-5)
    day_range = config.get("DAY_RANGE", 1)
    url = f"{_FIRMS_BASE_URL}/{map_key}/{source}/{bbox_str}/{day_range}"
    logger.debug("FIRMS URL: %s", url)
    return url


def fetch_fires(
    config: dict[str, Any] | None = None,
    *,
    path: str | Path | None = None,
    timeout: int = 60,
) -> pd.DataFrame:
    """Fetch active fire detections from NASA FIRMS and return a DataFrame.

    Parameters
    ----------
    config : dict, optional
        Pre-loaded configuration dict.  If ``None``, loads from *path* or
        the default config file.
    path : str or Path, optional
        Path to the YAML config file.  Ignored if *config* is provided.
    timeout : int, optional
        HTTP request timeout in seconds.  Defaults to 60 (FIRMS can be slow
        for large bounding boxes).

    Returns
    -------
    pd.DataFrame
        One row per fire detection.  Columns match the FIRMS CSV schema with
        the addition of a ``confidence_level`` column that maps the raw
        ``confidence`` string (``'h'``, ``'n'``, ``'l'``) to a human-readable
        string (``'high'``, ``'nominal'``, ``'low'``).

    Raises
    ------
    requests.exceptions.RequestException
        On network or HTTP errors.
    ValueError
        If the FIRMS API returns an error message instead of CSV data.
    """
    if config is None:
        config = load_config(path)

    url = _build_firms_url(config)
    logger.info("Fetching FIRMS data from %s", url)

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()

    # FIRMS returns plain-text error messages (e.g. "Invalid MAP_KEY.")
    # when something is wrong — detect this before parsing CSV.
    text = response.text
    if text.startswith("Invalid") or not text.strip():
        raise ValueError(f"FIRMS API error: {text.strip()}")

    # Parse CSV into DataFrame
    from io import StringIO

    df = pd.read_csv(StringIO(text))
    logger.info("FIRMS returned %d fire detections", len(df))

    # Normalize column names: FIRMS uses bright_ti4 / bright_ti5 (Roman numeral 4/5)
    # which is already what we get.  Add a human-readable confidence level.
    confidence_map = {"h": "high", "n": "nominal", "l": "low"}
    df["confidence_level"] = df["confidence"].map(confidence_map).fillna("unknown")

    # Parse acquisition datetime for easier filtering / display
    df["acq_datetime"] = pd.to_datetime(
        df["acq_date"] + " " + df["acq_time"].astype(str).str.zfill(4),
        format="%Y-%m-%d %H%M",
        errors="coerce",
    )

    # Ensure numeric columns are typed correctly
    for col in ("latitude", "longitude", "bright_ti4", "bright_ti5", "frp"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ---------------------------------------------------------------------------
# Convenience: filter by bounding box
# ---------------------------------------------------------------------------

def filter_by_bbox(
    df: pd.DataFrame,
    bbox: list[float],
) -> pd.DataFrame:
    """Filter a fire-detection DataFrame to a geographic bounding box.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain ``latitude`` and ``longitude`` columns.
    bbox : list of float
        ``[min_lon, min_lat, max_lon, max_lat]``

    Returns
    -------
    pd.DataFrame
        Filtered copy of *df*.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    mask = (
        (df["latitude"] >= min_lat)
        & (df["latitude"] <= max_lat)
        & (df["longitude"] >= min_lon)
        & (df["longitude"] <= max_lon)
    )
    return df.loc[mask].copy()


# ---------------------------------------------------------------------------
# CLI entry point (for quick manual testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    cfg = load_config()
    fires = fetch_fires(cfg)
    print(f"\nTotal detections: {len(fires)}")
    print(fires.head(10).to_string(index=False))
    print(f"\nConfidence distribution:")
    print(fires["confidence_level"].value_counts().to_string())

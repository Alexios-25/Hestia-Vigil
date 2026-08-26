"""
Hestia.Index — Spatial clustering for FIRMS hotspot filtering
==============================================================

Counts nearby detections within a radius so multi-pixel wildfire structure
can be distinguished from isolated industrial / ag-burn heat sources.

Uses a pure-NumPy haversine ball query (no scipy required). Suitable for
the ~2–5k detections typical of a continental-US FIRMS pull.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# Earth radius in km
_EARTH_RADIUS_KM = 6371.0


def neighbor_counts(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    radius_km: float,
) -> np.ndarray:
    """Return neighbor count (excluding self) for each point within radius_km.

    Parameters
    ----------
    latitudes, longitudes : array-like of shape (n,)
        Detection coordinates in degrees.
    radius_km : float
        Search radius in kilometres.

    Returns
    -------
    np.ndarray of shape (n,)
        Integer neighbor counts (self not included).
    """
    lats = np.asarray(latitudes, dtype=float)
    lons = np.asarray(longitudes, dtype=float)
    n = len(lats)
    if n == 0:
        return np.array([], dtype=int)
    if n == 1:
        return np.array([0], dtype=int)

    # Convert to radians once
    lat_r = np.radians(lats)
    lon_r = np.radians(lons)

    # Chord length threshold for haversine: 2 * R * sin(d/(2R)) ≈ d for small d
    # Use full haversine pairwise in blocks to keep memory reasonable.
    counts = np.zeros(n, dtype=int)
    # For n≈3000, full NxN is ~9M floats — fine in memory.
    # haversine: a = sin²(Δφ/2) + cos φ1 · cos φ2 · sin²(Δλ/2)
    dlat = lat_r[:, None] - lat_r[None, :]
    dlon = lon_r[:, None] - lon_r[None, :]
    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat_r)[:, None] * np.cos(lat_r)[None, :] * np.sin(dlon / 2.0) ** 2
    )
    dist_km = 2.0 * _EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    # Neighbors within radius, exclude self (diagonal)
    within = dist_km <= radius_km
    np.fill_diagonal(within, False)
    counts = within.sum(axis=1).astype(int)
    return counts


def add_cluster_columns(
    fires: pd.DataFrame,
    radius_km: float = 1.0,
) -> pd.DataFrame:
    """Add ``neighbor_count`` and ``cluster_size`` columns to a FIRMS DataFrame.

    ``cluster_size`` = neighbor_count + 1 (includes self).
    """
    df = fires.copy()
    if df.empty:
        df["neighbor_count"] = pd.Series(dtype=int)
        df["cluster_size"] = pd.Series(dtype=int)
        return df

    counts = neighbor_counts(
        df["latitude"].to_numpy(),
        df["longitude"].to_numpy(),
        radius_km=radius_km,
    )
    df["neighbor_count"] = counts
    df["cluster_size"] = counts + 1
    return df


def resolve_spatial_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read SPATIAL_FILTER settings from config with defaults."""
    cfg = (config or {}).get("SPATIAL_FILTER") or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "radius_km": float(cfg.get("radius_km", 1.0)),
        "min_cluster_size": int(cfg.get("min_cluster_size", 2)),
        # Variant B: weather (ISI/FWI/BUI) alone cannot show a singleton
        "require_cluster_for_fwi": bool(cfg.get("require_cluster_for_fwi", True)),
    }

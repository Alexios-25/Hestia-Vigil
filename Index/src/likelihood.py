"""
Hestia.Index — Wildfire-likelihood scoring for FIRMS detections.

Turns the multi-factor filter (perimeter, FRP, confidence, spatial cluster,
ISI/FWI/BUI) into a single 0–100 score and a human-readable tier. This lets
Vigil separate the "unconfirmed but shown" detections into more- and less-
likely wildfires without changing which ones are filtered out.

Tiers:
  Confirmed  : inside a current NIFC/WFIGS perimeter
  High       : strong multi-signal detection
  Medium     : moderate signal
  Low        : shown only by a single weak rule
  Unlikely   : suppressed by the filter
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def score_detection(row: Any) -> int:
    """Return a 0–100 likelihood score for a single FIRMS detection.

    The score is additive across independent evidence channels. It is capped
    at 100 and never goes below 0.
    """
    score = 0

    # --- Fire Radiative Power ---
    frp = row.get("frp")
    frp = float(frp) if frp is not None and not pd.isna(frp) else 0.0
    if frp > 50.0:
        score += 30
    elif frp > 20.0:
        score += 25
    elif frp > 10.0:
        score += 20
    elif frp > 5.0:
        score += 12
    elif frp > 2.0:
        score += 6
    elif frp > 0.0:
        score += 3

    # --- Satellite confidence ---
    conf = str(row.get("confidence_level", row.get("confidence", ""))).lower()
    if conf in ("high", "h"):
        score += 20
    elif conf in ("nominal", "n"):
        score += 8
    elif conf in ("low", "l"):
        score += 2

    # --- Spatial cluster structure ---
    cluster_size = row.get("cluster_size", 1)
    try:
        cs = int(cluster_size) if cluster_size is not None and not pd.isna(cluster_size) else 1
    except (TypeError, ValueError):
        cs = 1
    if cs >= 5:
        score += 20
    elif cs == 4:
        score += 15
    elif cs == 3:
        score += 12
    elif cs == 2:
        score += 6

    # --- Weather / fuel context ---
    isi = row.get("isi")
    isi = float(isi) if isi is not None and not pd.isna(isi) else -1.0
    if isi >= 5.0:
        score += 15
    elif isi >= 2.0:
        score += 8

    fwi = row.get("fwi")
    fwi = float(fwi) if fwi is not None and not pd.isna(fwi) else -1.0
    if fwi >= 30.0:
        score += 15
    elif fwi >= 20.0:
        score += 8
    elif fwi >= 12.0:
        score += 4

    bui = row.get("bui")
    bui = float(bui) if bui is not None and not pd.isna(bui) else -1.0
    if bui >= 60.0:
        score += 10
    elif bui >= 40.0:
        score += 5

    return max(0, min(int(score), 100))


def tier_from_score(
    score: int,
    in_perimeter: bool,
    fwi_show: bool,
    thresholds: dict[str, int] | None = None,
) -> str:
    """Map a score + filter state to a likelihood tier."""
    th = thresholds or {}
    high = th.get("high_threshold", 65)
    medium = th.get("medium_threshold", 35)

    if in_perimeter:
        return "Confirmed"
    if not fwi_show:
        return "Unlikely"
    if score >= high:
        return "High"
    if score >= medium:
        return "Medium"
    return "Low"


def resolve_likelihood_config(config: dict[str, Any] | None = None) -> dict[str, int]:
    """Read LIKELIHOOD settings from config with defaults."""
    cfg = (config or {}).get("LIKELIHOOD") or {}
    return {
        "high_threshold": int(cfg.get("high_threshold", 65)),
        "medium_threshold": int(cfg.get("medium_threshold", 35)),
    }

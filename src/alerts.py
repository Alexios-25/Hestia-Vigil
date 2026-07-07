"""
Hestia Vigil — Alert detection and de-duplication.

This module identifies new fire detections inside configured watch zones and
persists alert history so the same hotspot does not trigger repeated alerts on
future polling cycles. When FWI data is available from the Index integration,
alert messages now include spread index, fire danger rating, moisture codes,
filter reason, and a Google Maps location link.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = 1
DEFAULT_STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "alert_history.json"


@dataclass(frozen=True)
class Alert:
    """A new alert that should be sent to notification channels."""

    key: str
    source: str
    zone_id: str
    zone_label: str
    latitude: float
    longitude: float
    confidence: str
    confidence_level: str
    frp: float | None
    acq_datetime: Any
    first_seen_utc: str
    last_seen_utc: str
    last_alerted_utc: str
    count: int
    # Optional FWI context (added by index_integration)
    isi: float | None = None
    fwi: float | None = None
    danger_rating: str | None = None
    filter_reason: str | None = None
    ffmc: float | None = None
    dmc: float | None = None
    dc: float | None = None


def _utc_now_iso() -> str:
    return _format_utc(datetime.now(timezone.utc))


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "") -> str:
    if value is None or pd.isna(value):
        return default
    return str(value)


def _date_from_row(row: pd.Series[Any]) -> str:
    acq_date = row.get("acq_date")
    if acq_date is not None and not pd.isna(acq_date):
        return str(acq_date)

    acq_datetime = row.get("acq_datetime")
    if acq_datetime is not None and not pd.isna(acq_datetime):
        if isinstance(acq_datetime, pd.Timestamp):
            return acq_datetime.date().isoformat()
        if isinstance(acq_datetime, datetime):
            return acq_datetime.date().isoformat()
        return str(acq_datetime)[:10]

    return "unknown-date"


def build_detection_key(zone_id: str, fire_row: pd.Series[Any]) -> str:
    """Build a stable de-duplication key for a fire-zone observation.

    The key intentionally ignores minor coordinate shifts by rounding latitude
    and longitude to two decimal places. This is appropriate for VIIRS 375m
    hotspot data and prevents repeated alerts for the same persistent hotspot.
    """
    latitude = _safe_float(fire_row.get("latitude"))
    longitude = _safe_float(fire_row.get("longitude"))
    if latitude is None or longitude is None:
        raise ValueError("Fire detection rows must include numeric latitude and longitude")

    return (
        f"firms:{zone_id}:{_date_from_row(fire_row)}:"
        f"{latitude:.2f},{longitude:.2f}"
    )


def _find_zone(row: pd.Series[Any], config: dict[str, Any]) -> tuple[str, str] | None:
    latitude = _safe_float(row.get("latitude"))
    longitude = _safe_float(row.get("longitude"))
    if latitude is None or longitude is None:
        return None

    watch_zones = config.get("WATCH_ZONES", {}) or {}
    for zone_id, zone_cfg in watch_zones.items():
        bbox = zone_cfg.get("bbox") if isinstance(zone_cfg, dict) else None
        if not isinstance(bbox, list) or len(bbox) != 4:
            logger.warning("Skipping invalid watch zone %s", zone_id)
            continue

        min_lon, min_lat, max_lon, max_lat = bbox
        if min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon:
            label = zone_cfg.get("label", zone_id) if isinstance(zone_cfg, dict) else zone_id
            return zone_id, str(label)

    return None


def _latest_payload(row: pd.Series[Any], zone_id: str, zone_label: str) -> dict[str, Any]:
    """Build the 'latest' payload for a detection, including FWI context if present."""
    payload: dict[str, Any] = {
        "source": "firms",
        "zone_id": zone_id,
        "zone_label": zone_label,
        "latitude": _safe_float(row.get("latitude")),
        "longitude": _safe_float(row.get("longitude")),
        "acq_date": _safe_str(row.get("acq_date")),
        "acq_time": _safe_str(row.get("acq_time")),
        "acq_datetime": _safe_str(row.get("acq_datetime")),
        "confidence": _safe_str(row.get("confidence")),
        "confidence_level": _safe_str(row.get("confidence_level")),
        "frp": _safe_float(row.get("frp")),
        "bright_ti4": _safe_float(row.get("bright_ti4")),
        "satellite": _safe_str(row.get("satellite")),
        "instrument": _safe_str(row.get("instrument")),
    }

    # Include FWI data if the DataFrame has it (added by apply_fwi_filter)
    if "isi" in row and not pd.isna(row.get("isi")):
        payload["isi"] = _safe_float(row.get("isi"))
        payload["fwi"] = _safe_float(row.get("fwi"))
        payload["danger_rating"] = _safe_str(row.get("danger_rating"))
        payload["filter_reason"] = _safe_str(row.get("fwi_reason"))
        payload["ffmc"] = _safe_float(row.get("ffmc"))
        payload["dmc"] = _safe_float(row.get("dmc"))
        payload["dc"] = _safe_float(row.get("dc"))

    return payload


def _record_to_alert(key: str, record: dict[str, Any]) -> Alert:
    latest = record.get("latest", {}) or {}
    return Alert(
        key=key,
        source=_safe_str(record.get("source"), "firms"),
        zone_id=_safe_str(record.get("zone_id")),
        zone_label=_safe_str(record.get("zone_label")),
        latitude=_safe_float(latest.get("latitude"), 0.0) or 0.0,
        longitude=_safe_float(latest.get("longitude"), 0.0) or 0.0,
        confidence=_safe_str(latest.get("confidence")),
        confidence_level=_safe_str(latest.get("confidence_level")),
        frp=_safe_float(latest.get("frp")),
        acq_datetime=_safe_str(latest.get("acq_datetime")) or _safe_str(latest.get("acq_date")),
        first_seen_utc=_safe_str(record.get("first_seen_utc")),
        last_seen_utc=_safe_str(record.get("last_seen_utc")),
        last_alerted_utc=_safe_str(record.get("last_alerted_utc")),
        count=int(record.get("count", 1)),
        # FWI context
        isi=_safe_float(latest.get("isi")),
        fwi=_safe_float(latest.get("fwi")),
        danger_rating=_safe_str(latest.get("danger_rating")) or None,
        filter_reason=_safe_str(latest.get("filter_reason")) or None,
        ffmc=_safe_float(latest.get("ffmc")),
        dmc=_safe_float(latest.get("dmc")),
        dc=_safe_float(latest.get("dc")),
    )


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_SCHEMA_VERSION, "alerts": {}}


def load_alert_state(path: str | Path | None = None) -> dict[str, Any]:
    """Load alert history from disk."""
    state_path = Path(path) if path else DEFAULT_STATE_PATH
    if not state_path.exists():
        return _empty_state()

    with open(state_path, "r") as fh:
        state = json.load(fh)

    if not isinstance(state, dict):
        raise ValueError("Alert state must be a JSON object")

    state.setdefault("version", STATE_SCHEMA_VERSION)
    state.setdefault("alerts", {})
    if not isinstance(state["alerts"], dict):
        raise ValueError("Alert state 'alerts' must be a JSON object")

    return state


def save_alert_state(path: str | Path | None = None, state: dict[str, Any] | None = None) -> Path:
    """Persist alert history to disk."""
    state_path = Path(path) if path else DEFAULT_STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)

    payload = state or _empty_state()
    payload.setdefault("version", STATE_SCHEMA_VERSION)
    payload.setdefault("alerts", {})

    with open(state_path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")

    logger.info("Saved alert history to %s", state_path.resolve())
    return state_path.resolve()


def detect_new_alerts(
    fires: pd.DataFrame,
    config: dict[str, Any],
    state_path: str | Path | None = None,
    *,
    now: datetime | None = None,
) -> list[Alert]:
    """Detect new alerts and update persistent alert history.

    Returns only alerts that should be sent. Existing hotspot-zone observations
    are updated silently to avoid alert fatigue. FWI data from the DataFrame
    is captured in the alert record if present.
    """
    if fires.empty:
        return []

    state = load_alert_state(state_path)
    now_iso = _format_utc(now) if now else _utc_now_iso()
    new_alerts: list[Alert] = []

    for _, row in fires.iterrows():
        zone = _find_zone(row, config)
        if zone is None:
            continue

        zone_id, zone_label = zone
        key = build_detection_key(zone_id, row)
        latest = _latest_payload(row, zone_id, zone_label)

        if key not in state["alerts"]:
            record: dict[str, Any] = {
                "source": "firms",
                "zone_id": zone_id,
                "zone_label": zone_label,
                "first_seen_utc": now_iso,
                "last_seen_utc": now_iso,
                "last_alerted_utc": now_iso,
                "count": 1,
                "latest": latest,
            }
            state["alerts"][key] = record
            new_alerts.append(_record_to_alert(key, record))
            logger.info(
                "New FIRMS alert for zone %s: %s (isi=%s fwi=%s)",
                zone_id, key,
                _safe_str(latest.get("isi"), "N/A"),
                _safe_str(latest.get("fwi"), "N/A"),
            )
            continue

        record = state["alerts"][key]
        record["last_seen_utc"] = now_iso
        record["count"] = int(record.get("count", 1)) + 1
        record["latest"] = latest

    save_alert_state(state_path, state)
    return new_alerts


def list_alerts(
    state_path: str | Path | None = None,
    *,
    limit: int | None = None,
) -> list[Alert]:
    """Return alert records sorted newest-first."""
    state = load_alert_state(state_path)
    records = sorted(
        state["alerts"].items(),
        key=lambda item: item[1].get("last_seen_utc", ""),
        reverse=True,
    )
    if limit is not None:
        records = records[:limit]
    return [_record_to_alert(key, record) for key, record in records]


# ---------------------------------------------------------------------------
# Helper: ISI danger label (replicated from map_builder for independence)
# ---------------------------------------------------------------------------


def _isi_label(isi: float | None) -> str:
    if isi is None:
        return "N/A"
    if isi >= 15:
        return "Extreme"
    if isi >= 8:
        return "High"
    if isi >= 3:
        return "Moderate"
    if isi >= 1:
        return "Low"
    return "Very Low"


# ---------------------------------------------------------------------------
# Alert message formatting
# ---------------------------------------------------------------------------


def format_alert_message(alert: Alert) -> str:
    """Format an alert for Telegram/email.

    When FWI data is available (from the Index integration), the message
    includes ISI spread danger, FWI fire danger, moisture codes, filter
    reason, and a Google Maps link for a clickable map preview.
    """
    frp = "N/A" if alert.frp is None else f"{alert.frp:.2f} MW"
    confidence = alert.confidence_level or alert.confidence or "unknown"

    lines: list[str] = ["🔥 Hestia.Vigil Alert"]
    lines.append(f"Zone: {alert.zone_label}")
    lines.append(f"Status: New FIRMS hotspot")

    # FWI context (if available)
    if alert.isi is not None:
        isi_label = _isi_label(alert.isi)
        fwi_str = f"{alert.fwi:.1f}" if alert.fwi is not None else "N/A"
        danger = alert.danger_rating or "Unknown"
        lines.append("")
        lines.append(f"🌲 Spread (ISI): {isi_label} ({alert.isi:.1f})")
        lines.append(f"🔥 Fire Danger: {danger} (FWI {fwi_str})")
        lines.append(f"📡 Filter: {alert.filter_reason or 'N/A'}")

    lines.append("")
    lines.append(f"Confidence: {confidence}")
    lines.append(f"FRP: {frp}")

    if alert.ffmc is not None:
        lines.append(
            f"Moisture: FFMC {alert.ffmc:.1f} · "
            f"DMC {alert.dmc:.1f} · "
            f"DC {alert.dc:.1f}"
        )

    lines.append(f"Location: {alert.latitude:.5f}, {alert.longitude:.5f}")
    lines.append(f"First seen: {alert.first_seen_utc}")
    lines.append(f"Acquisition: {alert.acq_datetime or 'N/A'}")
    lines.append(f"Alert key: {alert.key}")

    # Google Maps link for Telegram rich preview
    lines.append("")
    lines.append(f"📍 https://www.google.com/maps?q={alert.latitude},{alert.longitude}")

    return "\n".join(lines)
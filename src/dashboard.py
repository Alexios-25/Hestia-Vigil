"""
Hestia Vigil — Flask Dashboard

Serves an interactive fire-detection map and JSON API endpoints.

Routes
------
/                   → Interactive Folium map (HTML)
/api/fires          → JSON array of fire detections
/api/config         → JSON of watch zones and settings
/api/health         → Simple health check

Usage:
    python src/dashboard.py
    # Then open http://127.0.0.1:5001 in your browser
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = Path(__file__).resolve().parent
for path in (str(PROJECT_ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from flask import Flask, Response, jsonify, request

from alerts import list_alerts
from config import load_config
from firms_client import fetch_fires
from map_builder import build_map, save_map

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load config once at startup
CONFIG = load_config()

# Where to cache the generated map HTML between requests
_MAP_CACHE_PATH = Path(__file__).resolve().parent.parent / "static" / "fire_map.html"
_MAP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Persistent alert history used by the dashboard API
_DEFAULT_ALERT_STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "alert_history.json"
_DEFAULT_ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _refresh_map():
    """Fetch fresh data, build the map, and cache it."""
    logger.info("Refreshing fire data and rebuilding map...")
    fires = fetch_fires(CONFIG)
    m = build_map(fires, CONFIG)
    save_map(m, _MAP_CACHE_PATH)
    return m, fires


def _alert_to_dict(alert) -> dict:
    """Convert an Alert dataclass into a JSON-safe dictionary."""
    return {
        "key": alert.key,
        "source": alert.source,
        "zone_id": alert.zone_id,
        "zone_label": alert.zone_label,
        "latitude": alert.latitude,
        "longitude": alert.longitude,
        "confidence": alert.confidence,
        "confidence_level": alert.confidence_level,
        "frp": alert.frp,
        "acq_datetime": alert.acq_datetime,
        "first_seen_utc": alert.first_seen_utc,
        "last_seen_utc": alert.last_seen_utc,
        "last_alerted_utc": alert.last_alerted_utc,
        "count": alert.count,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    """Main dashboard — return the generated Folium document."""
    try:
        m, fires = _refresh_map()
        # Force generation of the full HTML; this includes the layer control.
        html = m.get_root().render()
        # Quick sanity check: if the control isn't in the generated HTML, log it.
        if "L.control.layers" not in html:
            logger.warning("Layer control missing from generated HTML")
        return html
    except Exception as exc:
        logger.exception("Dashboard error")
        return f"<h1>Error</h1><p>{exc}</p>", 500


@app.route("/api/fires")
def api_fires():
    """JSON endpoint — all current fire detections."""
    try:
        fires = fetch_fires(CONFIG)
        # Convert to list of dicts, handling NaN and timestamps
        records = json.loads(
            fires.to_json(orient="records", date_format="iso")
        )
        return jsonify({"count": len(records), "detections": records})
    except Exception as exc:
        logger.exception("API /fires error")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/config")
def api_config():
    """JSON endpoint — watch zones and settings (no API key exposed)."""
    safe_config = {
        "source": CONFIG.get("SOURCE"),
        "bbox": CONFIG.get("BBOX"),
        "poll_interval_minutes": CONFIG.get("POLL_INTERVAL_MINUTES"),
        "watch_zones": CONFIG.get("WATCH_ZONES", {}),
    }
    return jsonify(safe_config)


@app.route("/api/alerts")
def api_alerts():
    """JSON endpoint — persisted alert history without secrets."""
    try:
        limit_raw = request.args.get("limit")
        limit = int(limit_raw) if limit_raw else None
        if limit is not None and limit < 1:
            return jsonify({"error": "limit must be a positive integer"}), 400

        alerts = list_alerts(_DEFAULT_ALERT_STATE_PATH, limit=limit)
        records = [_alert_to_dict(alert) for alert in alerts]
        return jsonify({"count": len(records), "alerts": records})
    except ValueError as exc:
        logger.exception("API /alerts validation error")
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("API /alerts error")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "hestia-vigil"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Hestia.Vigil dashboard on http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=True)

"""Hestia Vigil — Flask Dashboard with FWI Filtering."""

from __future__ import annotations
import logging
import time
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template_string, request
try:
    from .config import load_config
except ImportError:
    from config import load_config
try:
    from .map_builder import build_map, save_map
except ImportError:
    from map_builder import build_map, save_map
try:
    from .firms_client import fetch_fires
except ImportError:
    from firms_client import fetch_fires
try:
    from .alerts import list_alerts
except ImportError:
    from alerts import list_alerts

# ---------------------------------------------------------------------------
# Import the Index integration (FWI filter)
# ---------------------------------------------------------------------------
import sys
_index_src = Path(__file__).resolve().parent.parent / "Index" / "src"
if str(_index_src) not in sys.path:
    sys.path.insert(0, str(_index_src))

try:
    from index_integration import apply_fwi_filter
    _HAS_FWI = True
except ImportError:
    _HAS_FWI = False
    logging.getLogger(__name__).warning(
        "Index integration not available — running without FWI filter"
    )
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = load_config()
MAP_CACHE_PATH = Path("static/fire_map.html")
POLL_INTERVAL = CONFIG.get("POLL_INTERVAL_MINUTES", 60)
# Ensure the static directory exists
MAP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Cached, filtered FIRMS data written by the worker
LATEST_FIRES_PATH = Path(__file__).resolve().parent.parent / "state" / "latest_fires.csv"

# Persistent alert history used by the dashboard API
_DEFAULT_ALERT_STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "alert_history.json"
_DEFAULT_ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _latest_fires_is_fresh(max_age_minutes: int | None = None) -> bool:
    """Check whether the worker's cached FIRMS file is recent enough to use."""
    if not LATEST_FIRES_PATH.exists():
        return False
    max_age = max_age_minutes or max(POLL_INTERVAL * 2, 120)
    age_seconds = time.time() - LATEST_FIRES_PATH.stat().st_mtime
    return age_seconds <= max_age * 60

app = Flask(__name__)

# Map staleness tracking
_last_build: float = 0.0  # timestamp of last map build


def _map_is_stale() -> bool:
    """Check if the cached map needs rebuilding.

    The map is stale when the poll interval has elapsed OR when the worker
    has written a newer filtered FIRMS cache since the map was last built.
    """
    if not MAP_CACHE_PATH.exists():
        return True
    age_seconds = time.time() - _last_build
    if age_seconds > POLL_INTERVAL * 60:
        return True
    if LATEST_FIRES_PATH.exists():
        csv_mtime = LATEST_FIRES_PATH.stat().st_mtime
        map_mtime = MAP_CACHE_PATH.stat().st_mtime
        if csv_mtime > map_mtime:
            return True
    return False


def refresh_map():
    """Rebuild the cached map if it's stale.

    Reads the worker's filtered/tiered FIRMS cache (``state/latest_fires.csv``)
    so the dashboard doesn't repeat the slow Open-Meteo calls. Falls back to a
    live FIRMS pull only when the cache is missing or stale.
    """
    global _last_build

    if not _map_is_stale():
        logger.debug("Map is fresh — using cache")
        return

    logger.info("Refreshing fire data and rebuilding map...")

    # 1. NIFC perimeters — build_map() falls back to a hotspots-only map
    #    (with a warning) if the snapshot is missing.
    geo_path = Path("state/wfigs/current.json")
    if not geo_path.exists():
        logger.warning("No cached GeoJSON at %s — map will show hotspots only", geo_path)

    # 2. Prefer the worker's filtered/tiered FIRMS cache.
    fires_df = None
    if _latest_fires_is_fresh():
        try:
            logger.info("Loading filtered FIRMS cache from %s", LATEST_FIRES_PATH)
            fires_df = pd.read_csv(LATEST_FIRES_PATH)
            logger.info("Loaded %d detections from cache", len(fires_df))
        except Exception as e:
            logger.warning("Failed to load cached FIRMS data: %s", e)
            fires_df = None

    # 3. Fallback to a live FIRMS pull if the cache isn't usable.
    if fires_df is None:
        try:
            logger.info("Fetching FIRMS hotspot data...")
            fires_df = fetch_fires(CONFIG)
            logger.info("FIRMS returned %d hotspot detections", len(fires_df))
        except Exception as e:
            logger.error("Failed to fetch FIRMS data: %s — map will show perimeters only", e)
            fires_df = None

    # 4. Build the map. If the cache was used, likelihood tiers are present
    #    and each tier becomes a toggleable overlay layer.
    m = build_map(fires_df, CONFIG)
    save_map(m, str(MAP_CACHE_PATH))
    _last_build = time.time()


@app.route('/')
def index():
    refresh_map()
    interval_sec = POLL_INTERVAL * 60
    return render_template_string("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Hestia Vigil Dashboard</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                html, body { height: 100%; overflow: hidden; }
                iframe { width: 100vw; height: 100vh; border: none; }
            </style>
            <meta http-equiv="refresh" content="__POLL_INTERVAL__">
        </head>
        <body>
            <iframe src="/map" title="Fire Map"></iframe>
        </body>
        </html>
    """.replace("__POLL_INTERVAL__", str(interval_sec)))


@app.route('/map')
def serve_map():
    # NOTE: /map does NOT call refresh_map() — the shell page (index)
    # already rebuilt the map. This route just serves the cached file.
    # If someone navigates directly to /map, trigger a rebuild check.
    refresh_map()
    return MAP_CACHE_PATH.read_text(), 200, {
        'Content-Type': 'text/html; charset=utf-8'
    }


@app.route('/api/fires')
def api_fires():
    """JSON endpoint — all current fire detections."""
    try:
        fires = fetch_fires(CONFIG)
        import json
        records = json.loads(
            fires.to_json(orient="records", date_format="iso")
        )
        return jsonify({"count": len(records), "detections": records})
    except Exception as exc:
        logger.exception("API /fires error")
        return jsonify({"error": str(exc)}), 500


@app.route('/api/config')
def api_config():
    """JSON endpoint — watch zones and settings (no API key exposed)."""
    safe_config = {
        "source": CONFIG.get("SOURCE"),
        "bbox": CONFIG.get("BBOX"),
        "poll_interval_minutes": CONFIG.get("POLL_INTERVAL_MINUTES"),
        "watch_zones": CONFIG.get("WATCH_ZONES", {}),
    }
    return jsonify(safe_config)


@app.route('/api/alerts')
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


@app.route('/api/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "hestia-vigil"})


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
        "acq_datetime": str(alert.acq_datetime),
        "first_seen_utc": alert.first_seen_utc,
        "last_seen_utc": alert.last_seen_utc,
        "last_alerted_utc": alert.last_alerted_utc,
        "count": alert.count,
    }


if __name__ == "__main__":
    logger.info(
        "Starting Vigil dashboard (poll interval: %d min)",
        POLL_INTERVAL,
    )
    # Fetch the latest NIFC perimeters on startup
    try:
        import json, urllib.request
        url = (
            "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query?where=1%3D1&outFields=*&f=json"
        )
        data = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
        gj = json.loads(data)
        state_dir = Path("state/wfigs")
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "current.json").write_text(json.dumps(gj), encoding="utf-8")
        logger.info("Fetched %d NIFC perimeters", len(gj.get("features", [])))
    except Exception as e:
        logger.warning("Failed to fetch NIFC perimeters on startup: %s", e)
    # Build the map once at startup
    refresh_map()
    app.run(host='0.0.0.0', port=8080, debug=False)
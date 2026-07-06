"""Hestia Vigil — Flask Dashboard with FWI Filtering."""

from __future__ import annotations
import logging
import time
from flask import Flask, render_template_string
from config import load_config
from map_builder import build_map, save_map
from firms_client import fetch_fires
from pathlib import Path

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

app = Flask(__name__)

# Map staleness tracking
_last_build: float = 0.0  # timestamp of last map build


def _map_is_stale() -> bool:
    """Check if the cached map needs rebuilding."""
    if not MAP_CACHE_PATH.exists():
        return True
    age_seconds = time.time() - _last_build
    return age_seconds > POLL_INTERVAL * 60


def refresh_map():
    """Rebuild the cached map if it's stale.

    The map is only rebuilt when the cached file is older than
    ``POLL_INTERVAL_MINUTES``. Multiple rapid requests for the same
    page are served from cache.
    """
    global _last_build

    if not _map_is_stale():
        logger.debug("Map is fresh — using cache")
        return

    logger.info("Refreshing fire data and rebuilding map...")
    fires = fetch_fires(CONFIG)

    if _HAS_FWI and not fires.empty:
        logger.info("Applying FWI filter to %d detections...", len(fires))
        fires = apply_fwi_filter(fires, CONFIG)

    m = build_map(fires, CONFIG)
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


if __name__ == "__main__":
    logger.info(
        "Starting Vigil dashboard (poll interval: %d min)",
        POLL_INTERVAL,
    )
    # Build the map on startup (this is the slow one — ~2 minutes
    # for the first poll with ~250 unique locations)
    refresh_map()
    app.run(host='0.0.0.0', port=8080, debug=False)
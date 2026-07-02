from __future__ import annotations
import logging
from flask import Flask, render_template_string
from config import load_config
from map_builder import build_map, save_map
from firms_client import fetch_fires
from pathlib import Path

# Configuration
CONFIG = load_config()
MAP_CACHE_PATH = Path("static/fire_map.html")

# Ensure the static directory exists
MAP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

logger = logging.getLogger(__name__)

def refresh_map():
    """Fetch fresh data, build the map, and cache it."""
    logger.info("Refreshing fire data and rebuilding map...")
    fires = fetch_fires(CONFIG)
    m = build_map(fires, CONFIG)
    save_map(m, str(MAP_CACHE_PATH))
    return m, fires

@app.route('/')
def index():
    refresh_map()
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
            <meta http-equiv="refresh" content="600">
        </head>
        <body>
            <iframe src="/map" title="Fire Map"></iframe>
        </body>
        </html>
    """)

@app.route('/map')
def serve_map():
    refresh_map()
    return MAP_CACHE_PATH.read_text(), 200, {'Content-Type': 'text/html; charset=utf-8'}

if __name__ == "__main__":
    # Initialize the map once before starting the server
    refresh_map()
    app.run(host='0.0.0.0', port=8080, debug=True)

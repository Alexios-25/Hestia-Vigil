# Hestia.Vigil — NASA FIRMS Fire Dashboard Implementation Plan

> **For Alex & Hermes:** Build `hestia-vigil`, the first module of the Hestia forest fire monitoring system. Pulls near-real-time satellite fire detection data from NASA FIRMS, plots it on an interactive map, and sends alerts for user-defined watch zones.

**Goal:** A working local web dashboard showing active satellite-detected fires on an interactive map, with configurable geographic watch zones and email/Discord alerting for new hotspots.

**Architecture:** A pure-Python app using NASA FIRMS CSV API → pandas for data processing → Folium for interactive maps → a simple Flask web server for the dashboard. Alerts via SMTP (email) or webhook (discord). All configuration via a single `config.yaml`.

**Tech Stack:** Python 3.11, requests, pandas, folium, Flask, PyYAML, APScheduler (for periodic polling)

---

## Project Structure

```
hestia-vigil/
├── config.yaml              # User config: MAP_KEY, watch zones, alert settings
├── requirements.txt         # Python deps
├── README.md
├── src/
│   ├── __init__.py
│   ├── config.py            # Load & validate config.yaml
│   ├── firms.py             # NASA FIRMS API client (fetch + parse CSV)
│   ├── geo.py               # Geospatial helpers (point-in-polygon, bounding box)
│   ├── alerts.py            # Alert dispatch (email SMTP, Discord webhook)
│   ├── dashboard.py         # Flask app serving the Folium map + watch zone UI
│   └── scheduler.py         # APScheduler: periodic FIRMS polling + alert check
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_firms.py
│   ├── test_geo.py
│   └── test_alerts.py
└── templates/
    └── index.html           # Dashboard page template (optional — Flask serves Folium HTML)
```

---

## Task 1: Project Bootstrap

**Objective:** Create project skeleton with dependencies and a working Hello World Flask app.

**Files:**
- Create: `C:/Users/Alex Hollema/hestia-vigil/requirements.txt`
- Create: `C:/Users/Alex Hollema/hestia-vigil/config.yaml`
- Create: `C:/Users/Alex Hollema/hestia-vigil/src/__init__.py`
- Create: `C:/Users/Alex Hollema/hestia-vigil/tests/__init__.py`
- Create: `C:/Users/Alex Hollema/hestia-vigil/src/dashboard.py`

**Step 1: Create `requirements.txt`**

```txt
requests>=2.28
pandas>=2.0
folium>=0.15
flask>=3.0
pyyaml>=6
apscheduler>=3.10
python-dotenv>=1.0
```

**Step 2: Install dependencies**

Run:
```bash
cd C:/Users/Alex\ Hollema/hestia-vigil
pip install -r requirements.txt
```
Expected: All packages installed successfully.

**Step 3: Create `config.yaml`**

```yaml
# NASA FIRMS API Key — get yours free at https://firms.modaps.eosdis.nasa.gov/api/map_key/
MAP_KEY: "YOUR_FIRMS_MAP_KEY_HERE"

# Fire data source (VIIRS_SNPP_NRT = near-real-time, global, good resolution)
# Other options: MODIS_NRT, VIIRS_NOAA20_NRT, LANDSAT_NRT
SOURCE: "VIIRS_SNPP_NRT"

# Bounding box for initial data pull: [min_lon, min_lat, max_lon, max_lat]
# Default: continental US
BBOX: [-125.0, 24.0, -66.0, 50.0]

# How often to poll FIRMS (minutes)
POLL_INTERVAL_MINUTES: 60

# Watch zones — list of named bounding boxes to monitor
WATCH_ZONES:
  colorado_front_range:
    label: "Colorado Front Range"
    bbox: [-106.0, 38.5, -104.5, 40.5]
  michigan_west:
    label: "Western Michigan"
    bbox: [-87.0, 42.5, -85.0, 44.0]

# Alerting — enable at least one
ALERTS:
  discord_webhook:
    enabled: false
    webhook_url: "https://discord.com/api/webhooks/YOUR_WEBHOOK"
  email:
    enabled: false
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    smtp_user: "you@gmail.com"
    # Use an app password, not your Gmail password
    smtp_password: "your_app_password"
    to_address: "you@gmail.com"
```

**Step 4: Create `src/dashboard.py` (minimal Flask stub)**

```python
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "<h1>🔥 Hestia.Vigil</h1><p>FIRMS data loading soon...</p>"

if __name__ == "__main__":
    app.run(debug=True)
```

**Step 5: Verify Flask starts**

Run:
```bash
cd C:/Users/Alex\ Hollema/hestia-vigil
python src/dashboard.py
```
Expected: Flask dev server starts on `http://127.0.0.1:5000/`.

Then visit `http://127.0.0.1:5000/` in your browser and confirm you see "Fire Watch Dashboard".

**Step 6: Stop server (Ctrl+C), then commit**

```bash
cd C:/Users/Alex\ Hollema/hestia-vigil
git init
echo "__pycache__/" > .gitignore
echo "*.pyc" >> .gitignore
echo ".env" >> .gitignore
git add .
git commit -m "feat: project bootstrap with Flask, config, and requirements"
```

---

## Task 2: Config Loader

**Objective:** Module that loads and validates `config.yaml`.

**Files:**
- Create: `C:/Users/Alex Hollema/hestia-vigil/src/config.py`
- Create: `C:/Users/Alex Hollema/hestia-vigil/tests/test_config.py`

**Step 1: Write failing test**

```python
import pytest
import yaml
import os
from src.config import load_config, ConfigError

def test_loads_valid_config(tmp_path):
    cfg_data = {
        "MAP_KEY": "test_key_123",
        "SOURCE": "VIIRS_SNPP_NRT",
        "BBOX": [-125.0, 24.0, -66.0, 50.0],
        "POLL_INTERVAL_MINUTES": 60,
        "WATCH_ZONES": {},
        "ALERTS": {"discord_webhook": {"enabled": False}}
    }
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg_data))

    cfg = load_config(str(cfg_file))
    assert cfg["MAP_KEY"] == "test_key_123"
    assert cfg["SOURCE"] == "VIIRS_SNPP_NRT"

def test_raises_on_missing_map_key(tmp_path):
    cfg_data = {"MAP_KEY": "", "SOURCE": "X", "BBOX": [0,0,0,0], "POLL_INTERVAL_MINUTES": 1, "WATCH_ZONES": {}, "ALERTS": {}}
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(cfg_data))

    with pytest.raises(ConfigError, match="MAP_KEY"):
        load_config(str(cfg_file))
```

**Step 2: Run test to verify failure**

Run:
```bash
cd C:/Users/Alex\ Hollema/hestia-vigil
pip install pytest -q
pytest tests/test_config.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'src'`

**Step 3: Fix import — add `src/` to Python path**

Add a `conftest.py` at `C:/Users/Alex Hollema/hestia-vigil/tests/conftest.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
```

Run `pytest tests/test_config.py -v` again. Expected: FAIL — `AttributeError: module 'src.config' has no attribute 'load_config'`

**Step 4: Implement `src/config.py`**

```python
import os
import yaml

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")

class ConfigError(Exception):
    pass

def load_config(path=None):
    """Load config.yaml, validate required fields, return dict."""
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        raise ConfigError(f"Config file not found: {path}")

    with open(path) as f:
        cfg = yaml.safe_load(f)

    if not isinstance(cfg, dict):
        raise ConfigError("Config file is not a valid YAML mapping")

    # Validate
    required_keys = ["MAP_KEY", "SOURCE", "BBOX"]
    for key in required_keys:
        if key not in cfg:
            raise ConfigError(f"Missing required config key: {key}")

    if not cfg["MAP_KEY"] or cfg["MAP_KEY"].startswith("YOUR_"):
        raise ConfigError("MAP_KEY is not set — get one at https://firms.modaps.eosdis.nasa.gov/api/map_key/")

    if not isinstance(cfg["BBOX"], list) or len(cfg["BBOX"]) != 4:
        raise ConfigError("BBOX must be a list of 4 floats: [min_lon, min_lat, max_lon, max_lat]")

    # Defaults
    cfg.setdefault("POLL_INTERVAL_MINUTES", 60)
    cfg.setdefault("WATCH_ZONES", {})
    cfg.setdefault("ALERTS", {})
    return cfg
```

**Step 5: Run test to verify pass**

Run: `pytest tests/test_config.py -v`
Expected: 2 passed

**Step 6: Commit**

```bash
git add src/config.py tests/test_config.py tests/conftest.py
git commit -m "feat: config loader with validation"
```

---

## Task 3: NASA FIRMS API Client

**Objective:** Fetch MODIS/VIIRS fire detection CSV from FIRMS API and parse into a clean DataFrame.

**Files:**
- Create: `C:/Users/Alex Hollema/hestia-vigil/src/firms.py`
- Create: `C:/Users/Alex Hollema/hestia-vigil/tests/test_firms.py`

**API Reference:**
- URL format: `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{BBOX}/{days}`
- `BBOX` in URL as comma-separated: `min_lon,min_lat,max_lon,max_lat`
- `days`: number of days back (1-10 for NRT)
- Returns CSV with columns: `latitude, longitude, brightness, scan, track, acq_date, acq_time, satellite, instrument, confidence, version, bright_t31, frp, daynight`

**Step 1: Write failing test**

```python
import pytest
from src.firms import fetch_fires, FIRMS_API_URL

def test_fetch_fires_returns_dataframe(monkeypatch):
    """Mock the HTTP call and verify parsing."""
    import pandas as pd
    from io import StringIO

    csv_csv = """latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight
39.5,-105.2,325.1,0.5,0.3,2026-01-15,1842,VIIRS,VIIRS,75,2.0,301.2,12.5,D
40.1,-104.8,310.0,0.4,0.3,2026-01-15,1901,VIIRS,VIIRS,60,2.0,298.5,8.1,D
"""

    class FakeResponse:
        status_code = 200
        text = csv_csv

    import src.firms as firms_mod
    monkeypatch.setattr(firms_mod.requests, "get", lambda *a, **kw: FakeResponse())

    df = fetch_fires(map_key="fake_key", source="VIIRS_SNPP_NRT", bbox=[-106,39,-104,41], days=1)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "latitude" in df.columns
    assert "confidence" in df.columns
    assert df.iloc[0]["latitude"] == 39.5

def test_fetch_fires_raises_on_bad_status(monkeypatch):
    class FakeResponse:
        status_code = 403
        text = "Forbidden"

    import src.firms as firms_mod
    monkeypatch.setattr(firms_mod.requests, "get", lambda *a, **kw: FakeResponse())

    with pytest.raises(Exception, match="403"):
        fetch_fires(map_key="bad_key", source="X", bbox=[0,0,1,1], days=1)
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_firms.py -v`
Expected: FAIL — module not found

**Step 3: Implement `src/firms.py`**

```python
import requests
import pandas as pd
from io import StringIO

FIRMS_API_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{map_key}/{source}/{bbox}/{days}"

def fetch_fires(map_key: str, source: str, bbox: list, days: int = 1) -> pd.DataFrame:
    """
    Fetch fire detection data from NASA FIRMS API.

    Args:
        map_key: NASA FIRMS API key
        source: Sensor source string (e.g. VIIRS_SNPP_NRT, MODIS_NRT)
        bbox: [min_lon, min_lat, max_lon, max_lat]
        days: Number of days to look back (1-10 for NRT)

    Returns:
        DataFrame with fire detection records, or empty DataFrame if no fires found.
    """
    bbox_str = ",".join(str(v) for v in bbox)
    url = FIRMS_API_URL.format(
        map_key=map_key,
        source=source,
        bbox=bbox_str,
        days=days
    )

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    # FIRMS returns a message string when no data fires in the window
    content = resp.text.strip()
    if not content or content.startswith("#") or "error" in content.lower():
        return pd.DataFrame()

    df = pd.read_csv(StringIO(content))

    # Normalize column types
    if "confidence" in df.columns:
        # confidence can be numeric (0-100) or categorical ('l','n','h')
        df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce")

    if "acq_date" in df.columns:
        df["acq_date"] = pd.to_datetime(df["acq_date"], format="%Y-%m-%d", errors="coerce")

    if "latitude" in df.columns:
        df["latitude"] = pd.to_numeric(df["latitude"])
    if "longitude" in df.columns:
        df["longitude"] = pd.to_numeric(df["longitude"])
    if "frp" in df.columns:
        df["frp"] = pd.to_numeric(df["frp"])  # Fire Radiative Power (MW)

    return df
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_firms.py -v`
Expected: 2 passed

**Step 5: Optional — live API test (skip without valid MAP_KEY)**

```bash
# If you have a MAP_KEY set in config.yaml:
python -c "from src.config import load_config; from src.firms import fetch_fires; cfg=load_config(); df=fetch_fires(cfg['MAP_KEY'], cfg['SOURCE'], cfg['BBOX'][:4]); print(f'Found {len(df)} fires'); print(df.head())"
```

**Step 6: Commit**

```bash
git add src/firms.py tests/test_firms.py
git commit -m "feat: NASA FIRMS API client with DataFrame parsing"
```

---

## Task 4: Geospatial Watch Zone Logic

**Objective:** Functions to check which detected fires fall inside user-defined watch zones, and to generate Folium map layers for each zone.

**Files:**
- Create: `C:/Users/Alex Hollema/hestia-vigil/src/geo.py`
- Create: `C:/Users/Alex Hollema/hestia-vigil/tests/test_geo.py`

**Step 1: Write failing test**

```python
import pytest
import pandas as pd
from src.geo import fires_in_zone, build_watch_zone_layer, make_base_map

ZONES = {
    "colorado": {"label": "Colorado", "bbox": [-106.0, 38.5, -104.5, 40.5]},
    "michigan": {"label": "Michigan", "bbox": [-87.0, 42.5, -85.0, 44.0]},
}

def test_fires_in_zone():
    df = pd.DataFrame({
        "latitude": [39.5, 43.0, 35.0],
        "longitude": [-105.2, -86.0, -110.0],
    })
    result = fires_in_zone(df, ZONES["colorado"]["bbox"])
    # Only row 0 is inside Colorado bbox
    assert len(result) == 1
    assert result.iloc[0]["latitude"] == 39.5

def test_fires_in_zone_empty():
    df = pd.DataFrame({"latitude": [35.0], "longitude": [-110.0]})
    result = fires_in_zone(df, ZONES["colorado"]["bbox"])
    assert len(result) == 0

def test_make_base_map():
    m = make_base_map()
    assert m is not None
    # Folium map has a _repr_html_ method
    assert hasattr(m, "_repr_html_")
```

**Step 2: Run test**

Run: `pytest tests/test_geo.py -v`
Expected: FAIL — module not found

**Step 3: Implement `src/geo.py`**

```python
import folium
import pandas as pd

DEFAULT_MAP_CENTER = [39.0, -105.0]  # Centered on Colorado
DEFAULT_ZOOM = 5

def fires_in_zone(fires_df: pd.DataFrame, bbox: list) -> pd.DataFrame:
    """
    Filter fires DataFrame to only those within a bounding box.

    Args:
        fires_df: DataFrame with 'latitude' and 'longitude' columns
        bbox: [min_lon, min_lat, max_lon, max_lat]

    Returns:
        Filtered DataFrame.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    mask = (
        (fires_df["latitude"] >= min_lat) &
        (fires_df["latitude"] <= max_lat) &
        (fires_df["longitude"] >= min_lon) &
        (fires_df["longitude"] <= max_lon)
    )
    return fires_df[mask].copy()

def make_base_map(center=None, zoom=None) -> folium.Map:
    """Create a Folium base map."""
    center = center or DEFAULT_MAP_CENTER
    zoom = zoom or DEFAULT_ZOOM
    return folium.Map(location=center, zoom_start=zoom, tiles="OpenStreetMap")

def build_fire_markers_map(fires_df: pd.DataFrame, zones: dict = None) -> folium.Map:
    """
    Build an interactive Folium map with fire markers.

    Args:
        fires_df: DataFrame with fire detections
        zones: Optional dict of watch zones for overlay

    Returns:
        folium.Map object
    """
    center = [fires_df["latitude"].mean(), fires_df["longitude"].mean()] if len(fires_df) > 0 else DEFAULT_MAP_CENTER
    m = make_base_map(center=center)

    # Watch zone rectangles
    for zone_key, zone_info in (zones or {}).items():
        bbox = zone_info["bbox"]
        min_lon, min_lat, max_lon, max_lat = bbox
        folium.Rectangle(
            bounds=[[min_lat, min_lon], [max_lat, max_lon]],
            popup=zone_info.get("label", zone_key),
            color="red",
            weight=2,
            fill=True,
            fill_opacity=0.05,
        ).add_to(m)

    # Fire markers
    for _, row in fires_df.iterrows():
        color = _confidence_color(row.get("confidence", None))
        popup_html = _fire_popup(row)
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=5,
            color=color,
            fill=True,
            fill_opacity=0.8,
            popup=folium.Popup(popup_html, max_width=250),
        ).add_to(m)

    return m

def _confidence_color(confidence):
    """Return marker color based on confidence."""
    if pd.isna(confidence):
        return "gray"
    if confidence >= 75:
        return "red"
    elif confidence >= 50:
        return "orange"
    else:
        return "yellow"

def _fire_popup(row):
    """Generate HTML popup content for a fire marker."""
    fields = ["acq_date", "acq_time", "satellite", "confidence", "frp", "brightness"]
    parts = []
    for f in fields:
        if f in row and pd.notna(row[f]):
            val = str(row[f])[:10] if f == "acq_date" else row[f]
            parts.append(f"<b>{f}:</b> {val}")
    return "<br>".join(parts) if parts else "Fire detection"
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_geo.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/geo.py tests/test_geo.py
git commit -m "feat: geospatial watch zone filtering and Folium map builder"
```

---

## Task 5: Alert System

**Objective:** Send alerts (Discord webhook and/or email) when new fires are detected inside watch zones.

**Files:**
- Create: `C:/Users/Alex Hollema/hestia-vigil/src/alerts.py`
- Create: `C:/Users/Alex Hollema/hestia-vigil/tests/test_alerts.py`

**Step 1: Write failing test**

```python
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from src.alerts import send_discord_alert, send_email_alert, check_and_alert

def test_send_discord_alert_success():
    with patch("src.alerts.requests.post") as mock_post:
        mock_post.return_value.status_code = 204
        send_discord_alert("https://discord.com/api/webhooks/fake", "Test message")
        mock_post.assert_called_once()

def test_send_email_alert_success():
    with patch("src.alerts.smtplib.SMTP") as mock_smtp:
        mock_conn = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        send_email_alert(
            smtp_host="smtp.test.com",
            smtp_port=587,
            user="a@test.com",
            password="pw",
            to_addr="b@test.com",
            subject="Fire Alert",
            body="Test"
        )
        mock_conn.send_message.assert_called_once()
```

**Step 2: Run test**

Run: `pytest tests/test_alerts.py -v`
Expected: FAIL — module not found

**Step 3: Implement `src/alerts.py`**

```python
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import pandas as pd

def send_discord_alert(webhook_url: str, message: str):
    """Send a message to a Discord webhook."""
    payload = {"content": message}
    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()

def send_email_alert(smtp_host: str, smtp_port: int, user: str, password: str,
                     to_addr: str, subject: str, body: str):
    """Send an email alert via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(user, password)
        server.send_message(msg)

def build_alert_message(zone_label: str, fires_df: pd.DataFrame) -> str:
    """Build a human-readable alert message for fires in a zone."""
    lines = [f"🔥 Fire detected in watch zone: {zone_label}", ""]
    for _, row in fires_df.iterrows():
        lat = row.get("latitude", "?")
        lon = row.get("longitude", "?")
        date = row.get("acq_date", "?")
        conf = row.get("confidence", "?")
        frp = row.get("frp", "?")
        lines.append(f"  • {lat:.4f}, {lon:.4f} | {date} | conf={conf} | FRP={frp}MW")
    lines.append("")
    lines.append(f"Total: {len(fires_df)} detection(s)")
    return "\n".join(lines)

def check_and_alert(fires_df: pd.DataFrame, zones: dict, alerts_cfg: dict):
    """
    Check all watch zones for fires and send enabled alerts.

    Args:
        fires_df: Full fires DataFrame
        zones: Watch zones dict from config
        alerts_cfg: ALERTS section from config
    """
    for zone_key, zone_info in zones.items():
        from src.geo import fires_in_zone
        zone_fires = fires_in_zone(fires_df, zone_info["bbox"])
        if len(zone_fires) == 0:
            continue

        message = build_alert_message(zone_info.get("label", zone_key), zone_fires)

        # Discord
        dc_cfg = alerts_cfg.get("discord_webhook", {})
        if dc_cfg.get("enabled") and dc_cfg.get("webhook_url"):
            send_discord_alert(dc_cfg["webhook_url"], message)

        # Email
        email_cfg = alerts_cfg.get("email", {})
        if email_cfg.get("enabled") and email_cfg.get("smtp_host"):
            send_email_alert(
                smtp_host=email_cfg["smtp_host"],
                smtp_port=email_cfg.get("smtp_port", 587),
                user=email_cfg["smtp_user"],
                password=email_cfg["smtp_password"],
                to_addr=email_cfg["to_address"],
                subject=f"🔥 Fire Alert: {zone_info.get('label', zone_key)}",
                body=message,
            )
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_alerts.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add src/alerts.py tests/test_alerts.py
git commit -m "feat: alert system (Discord + email)"
```

---

## Task 6: Dashboard Integration + Scheduler

**Objective:** Wire everything together — Flask dashboard shows the live map, and a background scheduler periodically polls FIRMS and triggers alerts.

**Files:**
- Modify: `C:/Users/Alex Hollema/hestia-vigil/src/dashboard.py`
- Create: `C:/Users/Alex Hollema/hestia-vigil/src/scheduler.py`

**Step 1: Rewrite `src/dashboard.py`**

```python
import os
import json
from flask import Flask, render_template_string, jsonify
from src.config import load_config
from src.firms import fetch_fires
from src.geo import build_fire_markers_map, fires_in_zone
from src.alerts import check_and_alert

app = Flask(__name__)
app.config["DATA_DIR"] = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(app.config["DATA_DIR"], exist_ok=True)

# In-memory cache of latest fire data
_latest_map_html = "<h1>Loading...</h1>"
_latest_fires_count = 0
_latest_zone_counts = {}

def refresh_data():
    """Fetch latest FIRMS data, update map, check alerts."""
    global _latest_map_html, _latest_fires_count, _latest_zone_counts

    cfg = load_config()
    try:
        df = fetch_fires(cfg["MAP_KEY"], cfg["SOURCE"], cfg["BBOX"], days=1)
    except Exception as e:
        print(f"[FireWatch] FIRMS fetch failed: {e}")
        return

    _latest_fires_count = len(df)

    # Build map
    m = build_fire_markers_map(df, cfg.get("WATCH_ZONES", {}))
    _latest_map_html = m._repr_html_()

    # Count per zone
    zones = cfg.get("WATCH_ZONES", {})
    _latest_zone_counts = {}
    for zk, zi in zones.items():
        zf = fires_in_zone(df, zi["bbox"])
        _latest_zone_counts[zk] = {"label": zi.get("label", zk), "count": len(zf)}

    # Check alerts
    if len(df) > 0 and any(
        v.get("enabled") for v in cfg.get("ALERTS", {}).values()
    ):
        check_and_alert(df, zones, cfg.get("ALERTS", {}))

    # Save latest data as JSON for the API endpoint
    if len(df) > 0:
        out_path = os.path.join(app.config["DATA_DIR"], "latest_fires.json")
        df.to_json(out_path, orient="records", date_format="iso")

@app.route("/")
def index():
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>🔥 Hestia.Vigil</title>
    <style>
        body { font-family: sans-serif; margin: 0; padding: 20px; background: #1a1a2e; color: #eee; }
        h1 { color: #ff6b35; }
        .stats { display: flex; gap: 20px; margin-bottom: 20px; }
        .stat-box { background: #16213e; padding: 15px 25px; border-radius: 8px; }
        .stat-box .num { font-size: 2em; font-weight: bold; color: #ff6b35; }
        .stat-box .label { font-size: 0.85em; color: #aaa; }
        #map-container { border-radius: 8px; overflow: hidden; }
        .refresh-info { color: #666; font-size: 0.8em; margin-top: 10px; }
    </style>
</head>
<body>
    <h1>🔥 Hestia.Vigil</h1>
    <div class="stats">
            <div class="num" id="total-fires">{{ total }}</div>
            <div class="label">Active Detections (24h)</div>
        </div>
        {% for zk, zi in zone_counts.items() %}
        <div class="stat-box">
            <div class="num">{{ zi.count }}</div>
            <div class="label">{{ zi.label }}</div>
        </div>
        {% endfor %}
    </div>
    <div id="map-container">{{ map_html|safe }}</div>
    <p class="refresh-info">Data source: NASA FIRMS ({{ source }}) | Polling every {{ interval }} min</p>
</body>
</html>
""",
    map_html=_latest_map_html,
    total=_latest_fires_count,
    zone_counts=_latest_zone_counts,
    source=load_config().get("SOURCE", "N/A"),
    interval=load_config().get("POLL_INTERVAL_MINUTES", 60),
)

@app.route("/api/fires")
def api_fires():
    """JSON API endpoint for fire data."""
    out_path = os.path.join(app.config["DATA_DIR"], "latest_fires.json")
    if os.path.exists(out_path):
        with open(out_path) as f:
            return jsonify(json.load(f))
    return jsonify([])

@app.route("/api/status")
def api_status():
    return jsonify({
        "total_fires": _latest_fires_count,
        "zones": _latest_zone_counts,
    })

if __name__ == "__main__":
    refresh_data()  # Initial load
    app.run(debug=True, port=5000)
```

**Step 2: Create `src/scheduler.py`**

```python
"""
Standalone scheduler script — run this in a separate terminal to keep
polling FIRMS and sending alerts even when the dashboard isn't open.
"""
import time
from src.config import load_config
from src.firms import fetch_fires
from src.geo import fires_in_zone
from src.alerts import check_and_alert

def run_scheduler():
    cfg = load_config()
    interval = cfg.get("POLL_INTERVAL_MINUTES", 60) * 60  # convert to seconds
    zones = cfg.get("WATCH_ZONES", {})
    alerts = cfg.get("ALERTS", {})

    print(f"[Hestia.Vigil] Scheduler started — polling every {interval//60} min")
    print(f"[Hestia.Vigil] Watching {len(zones)} zone(s)")

    while True:
        try:
            print(f"[Hestia.Vigil] Fetching FIRMS data...")
            df = fetch_fires(cfg["MAP_KEY"], cfg["SOURCE"], cfg["BBOX"], days=1)
            print(f"[Hestia.Vigil] Found {len(df)} fire detections")

            if len(df) > 0:
                check_and_alert(df, zones, alerts)

        except Exception as e:
            print(f"[FireWatch] Error: {e}")

        time.sleep(interval)

if __name__ == "__main__":
    run_scheduler()
```

**Step 3: Test the dashboard**

Run:
```bash
cd C:/Users/Alex\ Hollema/hestia-vigil
python src/dashboard.py
```
Expected: Flask starts, fetches FIRMS data, serves map at `http://127.0.0.1:5000/`.

**Step 4: Commit**

```bash
git add src/dashboard.py src/scheduler.py
git commit -m "feat: Flask dashboard with live map, API endpoints, and background scheduler"
```

---

## Task 7: README + Final Polish

**Objective:** Document the project so you (or anyone) can set it up from scratch.

**Files:**
- Create: `C:/Users/Alex Hollema/hestia-vigil/README.md`

**Step 1: Write README**

```markdown
# 🔥 Hestia.Vigil — NASA FIRMS Fire Detection Dashboard

> *"The eternal watch."* — First module of the [Hestia](docs/fire-project-roadmap.md) forest fire monitoring system.

Near-real-time forest fire monitoring using NASA FIRMS satellite data (MODIS/VIIRS).

## Features
- Fires plotted on an interactive Folium map
- Configurable watch zones with bounding boxes
- Discord webhook and/or email alerts for new detections in watch zones
- JSON API endpoint (`/api/fires`) for downstream use
- Background scheduler for continuous monitoring

## Quick Start

1. **Get a NASA FIRMS API key** (free): https://firms.modaps.eosdis.nasa.gov/api/map_key/

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure** — edit `config.yaml`:
   - Set your `MAP_KEY`
   - Adjust `BBOX` to your region of interest
   - Define `WATCH_ZONES` (named bounding boxes)
   - Enable alerts (Discord webhook or email SMTP)

4. **Run the dashboard**
   ```bash
   python src/dashboard.py
   ```
   Open http://127.0.0.1:5000/

5. **Run the background scheduler** (optional, separate terminal)
   ```bash
   python src/scheduler.py
   ```

## Data Sources
- [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) — Fire Information for Resource Management System
- Sensors: VIIRS (375m resolution), MODIS (1km resolution)
- Update frequency: Near-real-time (within 3 hours of satellite overpass)

## Project Structure
```
hestia-vigil/
├── config.yaml          # Your settings
├── src/
│   ├── config.py        # Config loader
│   ├── firms.py         # NASA FIRMS API client
│   ├── geo.py           # Geospatial + map building
│   ├── alerts.py        # Discord + email alerts
│   ├── dashboard.py     # Flask web app
│   └── scheduler.py     # Background polling
└── tests/               # pytest suite
```

## Next Steps — Hestia Modules
- [ ] **Hestia.Index** — Fire Weather Index calculator (Phase 2)
- [ ] **Hestia.Oracle** — ML fire prediction model (Phase 3)
- [ ] **Hestia.Sight** — Webcam smoke detection CNN (Phase 4)
- [ ] **Hestia.Nodes** — Ground sensor network simulator (Phase 5)
- [ ] **Hestia.Wing** — Drone thermal anomaly monitor (Phase 6)
```

**Step 2: Final commit**

```bash
git add README.md
git commit -m "docs: add README with setup instructions and project overview"
```

---

## Summary

| Task | What it delivers | Est. time |
|------|-----------------|-----------|
| 1 — Bootstrap | Project skeleton, deps, Flask stub | 15 min |
| 2 — Config | YAML config loader with validation | 20 min |
| 3 — FIRMS Client | API fetch + CSV → DataFrame | 25 min |
| 4 — Geo + Map | Watch zone filtering + Folium map | 30 min |
| 5 — Alerts | Discord + email alert dispatch | 25 min |
| 6 — Dashboard | Flask app with live map + scheduler | 30 min |
| 7 — README | Documentation | 10 min |

**Total: ~2.5 hours to a working MVP.**

---

## After the MVP — Where to Go Next

Once the dashboard is running, you can layer on:

1. **Historical analysis** — Pull FIRMS archive data (years back), compute fire frequency heatmaps per zone
2. **ML Prediction** — Use the fire occurrence data + NOAA weather as features for a classifier (ties to your coursework)
3. **Smoke detection CNN** — Scrape webcam feeds, train MobileNetV2 to flag smoke plumes
4. **Sensor network sim** — Agent-based simulation of ground sensors with Bayesian fire belief updating
5. **Drone thermal patrol** — FLIR sensor + autonomous waypoint navigation

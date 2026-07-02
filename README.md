# 🔥 Hestia Vigil

> *"The eternal watch."* — First module of the Hestia forest fire monitoring system.

Near-real-time forest fire monitoring using NASA FIRMS satellite data (VIIRS 375m / MODIS 1km).

## Features

- **Interactive fire map** — Leaflet-based dashboard displaying active satellite fire detections
- **Configurable watch zones** — Get alerted only when hotspots appear in your areas of interest
- **Telegram alerts** — Push notifications sent to your bot when new hotspots are detected
- **Background polling** — Automated FIRMS data fetching on a configurable schedule (default: 60 min)
- **Alert de-duplication** — Same hotspot won't spam you; state is persisted across restarts

## Quick Start

### 1. Get a NASA FIRMS API key (free)
https://firms.modaps.eosdis.nasa.gov/api/map_key/

### 2. Set up secrets
```bash
cp .env.example .env
```

Then fill in `.env`:
- `NASA_FIRMS_MAP_KEY` — your FIRMS API key
- Telegram credentials are already in `config.yaml` (bot token + chat ID)

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the dashboard
```bash
python src/dashboard.py
```
Open **http://127.0.0.1:8080** in your browser.

### 5. Run the background worker (separate terminal)
```bash
python src/vigil_worker.py
```
Polls FIRMS every 60 minutes and sends Telegram alerts for new hotspots in your watch zones.

## Project Structure

```
hestia-vigil/
├── config.yaml              # Your configuration
├── .env                     # Secrets (API keys, tokens)
├── requirements.txt         # Python dependencies
├── README.md
├── src/
│   ├── config.py            # Config loader + .env secret resolution
│   ├── firms_client.py      # NASA FIRMS API client (CSV fetch → DataFrame)
│   ├── map_builder.py       # Folium map generation with watch zones
│   ├── alerts.py            # Hotspot detection, de-duplication, state persistence
│   ├── notifiers.py         # Alert dispatch (Telegram, email)
│   ├── dashboard.py         # Flask web app serving the interactive map
│   └── vigil_worker.py      # Background polling loop
├── state/
│   └── alert_history.json   # Persistent alert state
├── static/                  # Cached map HTML
└── tests/                   # pytest suite
```

## Configuration

All settings live in `config.yaml`:

```yaml
SOURCE: "VIIRS_SNPP_NRT"           # Satellite data source
BBOX: [-125.0, 24.0, -66.0, 50.0]  # Data bounding box (continental US)
POLL_INTERVAL_MINUTES: 60           # How often to check FIRMS

WATCH_ZONES:
  colorado_front_range:
    label: "Colorado Front Range"
    bbox: [-106.0, 38.5, -104.5, 40.5]
  michigan_west:
    label: "Western Michigan"
    bbox: [-87.0, 42.5, -85.0, 44.0]

ALERTS:
  telegram_webhook:
    enabled: true
    webhook_url: "https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>"
```

## Data Sources

- [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) — Fire Information for Resource Management System
- VIIRS 375m resolution, MODIS 1km resolution
- Near-real-time updates (within 3 hours of satellite overpass)

## What's Next — Hestia Phases

- [ ] **Hestia.Index** — Fire Weather Index calculator (Canadian FWI system)
- [ ] **Hestia.Oracle** — ML fire prediction model
- [ ] **Hestia.Sight** — Smoke plume detection CNN from webcam feeds
- [ ] **Hestia.Nodes** — Ground sensor network simulator + Bayesian fusion
- [ ] **Hestia.Wing** — Drone-mounted thermal anomaly patrol
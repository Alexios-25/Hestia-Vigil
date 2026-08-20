---
agent: CodeActAgent
---

# Hestia Vigil

Near-real-time forest fire monitoring dashboard. First module of **Hestia**, a
multi-phase forest fire detection/analysis/prediction system (see
`docs/fire-project-roadmap.md` for the full phase plan: Vigil → Index → Glyph →
Oracle → Sight → Nodes → Wing).

Repo: `git@github.com:Alexios-25/Hestia-Vigil.git` (remote `origin`, branch `main`).

## What this module does

- Pulls near-real-time satellite fire hotspot data from **NASA FIRMS**
  (VIIRS 375m / MODIS 1km) via `src/firms_client.py`.
- Renders an interactive Folium/Leaflet map (`src/map_builder.py`) served by a
  Flask dashboard (`src/dashboard.py`, default `http://127.0.0.1:8080`).
- Runs a background poller (`src/vigil_worker.py`, default every 60 min) that
  checks configured watch zones and fires Telegram alerts via
  `src/notifiers.py` / `src/alerts.py` (de-duplicated, state persisted in
  `state/alert_history.json`).
- Config lives in `config.yaml` (source, bbox, poll interval, watch zones,
  alert channels); secrets in `.env` (`NASA_FIRMS_MAP_KEY`,
  `TELEGRAM_WEBHOOK_URL`) — copy `.env.example` to start.
- Launch scripts: `./launch_vigil.sh` starts the dashboard + worker together.

## Project structure (as of 2026-08-20)

```
Vigil/
├── config.yaml, .env(.example), requirements.txt
├── launch_vigil.sh
├── src/
│   ├── config.py          # config loader + .env secret resolution
│   ├── firms_client.py    # FIRMS API client (CSV -> DataFrame)
│   ├── map_builder.py     # Folium map generation, watch zones, fire perimeters
│   ├── alerts.py          # hotspot detection, de-dup, state persistence
│   ├── notifiers.py       # Telegram/email dispatch
│   ├── dashboard.py       # Flask app serving the map
│   └── vigil_worker.py    # background polling loop
├── Index/                 # Hestia.Index (FWI calculator) — see note below
├── state/, static/, tests/
└── docs/
    ├── fire-project-roadmap.md
    └── plans/2026-06-08-firms-dashboard.md
```

## Important deviations from the original roadmap

- **Hestia.Index (Canadian Fire Weather Index calculator) was merged directly
  into this repo** under `Index/`, rather than staying a separate
  `hestia-index/` project as the roadmap originally described. It now feeds
  FWI context into alert notifications (weather pipeline + a "smart hotspot
  filter" that uses FWI to reduce false positives). See commits
  `8df2c28` (integration) and `d557fe7` (path fix after the move).
- **Phase 3 (Glyph — fire perimeter/incident tracking) work has started
  inside Vigil's own dashboard**, ahead of becoming its own module: NIFC fire
  perimeter polygons (ArcGIS rings) and containment-based coloring were added
  to `map_builder.py` / `dashboard.py` (commit `b17b0a4`), and cached GeoJSON
  is used instead of re-fetching a DataFrame each refresh.

## Recent history & known threads (from prior Hermes agent sessions)

- **Recurring investigation: "Missing Hotspots in Latest FIRMS Data"** — this
  came up across 7 separate sessions (2026-07-15 to 2026-07-16). Root cause
  wasn't conclusively nailed down in those sessions; worth checking whether
  it's actually resolved or still intermittent (FIRMS occasionally has real
  outages, but a filtering/query bug was also suspected).
- **`launch_vigil.sh` permission errors** have come up before
  (`zsh: permission denied: ./launch_vigil.sh` — fix is `chmod +x
  launch_vigil.sh`). If this recurs after a fresh clone/checkout, that's why.
- **Last active thread (2026-07-30, most recent session before this
  handoff):** a persistent mismatch bug when modifying the `folium.Map`
  instantiation in `map_builder.py` led to a full rewrite of that file (to
  fix CSS injection issues and simplify tile handling — this may be a
  DIFFERENT, later fix than the ArcGIS-rings commit above; check `git log
  src/map_builder.py` and `git diff` to see what's actually uncommitted vs.
  committed). The rewrite reportedly generated `test_map.html` successfully,
  but the user was going to manually verify the dashboard themselves — that
  verification's outcome isn't recorded. **Treat this as unverified/possibly
  incomplete.**
- **Uncommitted changes were present in the working tree as of this
  handoff** (diff stat, as of 2026-08-20):
  ```
  config.yaml        |  31 ++++++++-
  launch_vigil.sh    |   0
  requirements.txt   |   3 +-
  src/dashboard.py   |  40 +++++++++---
  src/map_builder.py | 183 +++++++++++++++++++++++++++++++++++------------------
  ```
  plus `test_map.html` untracked (0 bytes at last check — likely stale/
  incomplete test output, not a real deliverable). The 183-line
  `map_builder.py` diff is almost certainly the from-scratch rewrite
  described above, sitting on top of the last committed rendering fix
  (`b17b0a4`). **Check `git status` and `git diff` first thing** — don't
  assume the working tree matches the last commit, and treat this diff as
  unverified/possibly-incomplete work rather than a finished feature.

## Conventions

- Python 3.11, PEP-8, minimal imports (per prior session constraints).
- Flask for the web app, Folium for maps, pandas for FIRMS data handling.
- Tests live in `tests/` (pytest). `.pytest_cache/` present — tests have been
  run before; check they still pass before/after changes.
- Don't expose API keys/secrets in code, logs, or commits — read from `.env`
  via `src/config.py`, and redact if ever encountered in output.

"""
Hestia Vigil — Map Builder

Takes a pandas DataFrame of fire detections (as returned by firms_client.fetch_fires)
and produces an interactive Folium map.  Features:

- Circle markers for each fire, color-coded by confidence (red/orange/yellow)
- Marker radius scaled by FRP (Fire Radiative Power)
- Watch zone overlays drawn as coloured rectangles
- Satellite / day/night layer toggle
- Popup on each marker with details (time, brightness, FRP, confidence)

Usage:
    from map_builder import build_map, save_map
    from firms_client import fetch_fires, load_config

    config = load_config()
    fires = fetch_fires(config)
    m = build_map(fires, config)
    save_map(m, "fire_map.html")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import folium
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Confidence → colour mapping
# ---------------------------------------------------------------------------

_CONFIDENCE_COLOURS = {
    "high": "#d73027",      # red
    "nominal": "#fc8d59",   # orange
    "low": "#fee08b",       # yellow
    "unknown": "#999999",   # grey
}

# FRP (Fire Radiative Power in MW) — used to scale marker radius.
# Typical range: 0.1 – 500+ MW.  We clamp to a sensible visual range.
_MIN_RADIUS = 3
_MAX_RADIUS = 15
_FRP_CAP = 200.0  # MW above which we use max radius


def _frp_to_radius(frp: float | None) -> float:
    """Map FRP (MW) to a circle marker radius in pixels.

    Uses a square-root scale so that large FRP values don't dominate the map.
    """
    if pd.isna(frp) or frp <= 0:
        return _MIN_RADIUS
    import math
    scaled = math.sqrt(min(frp, _FRP_CAP)) / math.sqrt(_FRP_CAP)
    return _MIN_RADIUS + scaled * (_MAX_RADIUS - _MIN_RADIUS)


# ---------------------------------------------------------------------------
# Map construction
# ---------------------------------------------------------------------------

def build_map(
    fires: pd.DataFrame,
    config: dict[str, Any] | None = None,
    *,
    save_path: str | Path | None = None,
) -> folium.Map:
    """Build an interactive Folium map of fire detections.

    Parameters
    ----------
    fires : pd.DataFrame
        Must contain at least ``latitude``, ``longitude``, ``confidence``,
        ``frp``, ``bright_ti4``, ``acq_datetime``, ``confidence_level``.
    config : dict, optional
        Configuration dict (used for watch zones and map centre).  If
        ``None``, map is centred on the mean of the fire coordinates.
    save_path : str or Path, optional
        If provided, save the map HTML to this path.

    Returns
    -------
    folium.Map
        The constructed map object.
    """
    # --- Centre the map ---
    if config and "BBOX" in config:
        bbox = config["BBOX"]
        centre_lat = (bbox[1] + bbox[3]) / 2
        centre_lon = (bbox[0] + bbox[2]) / 2
    elif not fires.empty:
        centre_lat = float(fires["latitude"].mean())
        centre_lon = float(fires["longitude"].mean())
    else:
        centre_lat, centre_lon = 39.0, -98.0  # US centre fallback

    m = folium.Map(
        location=[centre_lat, centre_lon],
        zoom_start=5,
        tiles=None,
        control_scale=True,
    )

    # Add explicit base layers so the layer picker labels stay clean.
    dark_layer = folium.TileLayer(
        tiles="CartoDB dark_matter",
        name="Dark Mode",
        overlay=False,
        control=True,
        show=True,
    )
    dark_layer.add_to(m)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite Imagery",
        overlay=False,
        control=True,
        show=False,
    ).add_to(m)

    # --- Fire marker layer ---
    fire_layer = folium.FeatureGroup(name="Fire Detections", overlay=True, control=True, show=True)

    for _, row in fires.iterrows():
        lat = row.get("latitude")
        lon = row.get("longitude")
        if pd.isna(lat) or pd.isna(lon):
            continue

        confidence = row.get("confidence_level", "unknown")
        colour = _CONFIDENCE_COLOURS.get(confidence, _CONFIDENCE_COLOURS["unknown"])
        radius = _frp_to_radius(row.get("frp"))

        # Build popup content
        acq_time = row.get("acq_datetime")
        acq_str = str(acq_time) if pd.notna(acq_time) else "N/A"
        popup_html = (
            f"<b>🔥 Fire Detection</b><br>"
            f"Confidence: <b>{confidence}</b><br>"
            f"Time (UTC): {acq_str}<br>"
            f"Brightness (K): {row.get('bright_ti4', 'N/A')}<br>"
            f"FRP (MW): {row.get('frp', 'N/A')}<br>"
            f"Satellite: {row.get('satellite', 'N/A')}"
        )

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=colour,
            fill=True,
            fill_color=colour,
            fill_opacity=0.7,
            weight=1,
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=f"{confidence} · {row.get('frp', 'N/A')} MW",
        ).add_to(fire_layer)

    fire_layer.add_to(m)

    # --- Watch zone overlays ---
    if config and "WATCH_ZONES" in config:
        zone_layer = folium.FeatureGroup(name="Watch Zones", overlay=True, control=True, show=True)
        for zone_id, zone_cfg in config["WATCH_ZONES"].items():
            label = zone_cfg.get("label", zone_id)
            zbbox = zone_cfg.get("bbox")
            if not zbbox or len(zbbox) != 4:
                logger.warning("Skipping invalid watch zone %s", zone_id)
                continue
            min_lon, min_lat, max_lon, max_lat = zbbox
            folium.Rectangle(
                bounds=[[min_lat, min_lon], [max_lat, max_lon]],
                color="#00ff88",
                weight=2,
                fill=True,
                fill_color="#00ff88",
                fill_opacity=0.08,
                popup=f"<b>{label}</b><br>Zone: {zone_id}",
                tooltip=label,
            ).add_to(zone_layer)
        zone_layer.add_to(m)

    # --- Layer control (toggle satellite / fires / zones) ---
    folium.LayerControl(collapsed=False).add_to(m)

    # --- Legend ---
    legend_html = """
    <div style="
        position: fixed; bottom: 30px; left: 30px; z-index: 1000;
        background: rgba(0,0,0,0.8); color: #fff; padding: 10px 14px;
        border-radius: 8px; font-size: 13px; font-family: monospace;
        border: 1px solid #444;
    ">
        <b>🔥 Confidence</b><br>
        <span style="color:#d73027;">●</span> High<br>
        <span style="color:#fc8d59;">●</span> Nominal<br>
        <span style="color:#fee08b;">●</span> Low<br>
        <span style="color:#999;">●</span> Unknown<br>
        <hr style="border-color:#444;">
        <span style="color:#00ff88;">■</span> Watch Zone
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # --- Save if requested ---
    if save_path:
        save_map(m, save_path)

    return m


def save_map(m: folium.Map, path: str | Path) -> Path:
    """Save a Folium map to an HTML file.

    Parameters
    ----------
    m : folium.Map
    path : str or Path

    Returns
    -------
    Path
        The absolute path the file was written to.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out))
    logger.info("Map saved to %s", out.resolve())
    return out.resolve()

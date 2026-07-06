"""
Hestia Vigil — Map Builder

Takes a pandas DataFrame of fire detections (as returned by
firms_client.fetch_fires) with optional FWI/ISI columns from the
Index integration, and produces an interactive Folium map.

When ISI data is present:
  - Only rows with fwi_show == True are plotted
  - Markers are colour-coded by ISI (Initial Spread Index)
    which represents the CURRENT surface fire spread potential
  - Popups include full FWI context (FWI + ISI + moisture codes)
  - Legend shows ISI scale

When ISI data is absent (backward-compatible):
  - All rows are plotted
  - Markers colour-coded by FIRMS confidence (legacy)
  - Legend shows confidence scale
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import folium
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ISI → marker colour mapping
#
# ISI is the Initial Spread Index — it measures how fast a fire would
# spread on the surface RIGHT NOW given current fuel moisture and wind.
# This is a better discriminator than FWI (which is dominated by deep
# drought codes that stay high for weeks).
#
# ISI thresholds based on Van Wagner (1987):
#   0-1   = Very low     (wet fuel, near-zero spread)
#   1-3   = Low          (minimal spread)
#   3-8   = Moderate     (notable spread, manageable)
#   8-15  = High         (fast spread, challenging)
#   15+   = Very High+   (extreme spread rates)
# ---------------------------------------------------------------------------


def _isi_to_danger(isi: float) -> str:
    if isi >= 15:
        return "Extreme"
    if isi >= 8:
        return "High"
    if isi >= 3:
        return "Moderate"
    if isi >= 1:
        return "Low"
    return "Very Low"


_ISI_COLOURS = {
    "Extreme":  "#8e44ad",  # purple
    "High":     "#e74c3c",  # red
    "Moderate": "#e67e22",  # orange
    "Low":      "#f1c40f",  # yellow
    "Very Low": "#2ecc71",  # green
}

# Legacy confidence colours (used when no ISI/FWI data)
_CONFIDENCE_COLOURS = {
    "high":    "#d73027",  # red
    "nominal": "#fc8d59",  # orange
    "low":     "#fee08b",  # yellow
    "unknown": "#999999",  # grey
}

# FRP → marker radius scaling
_MIN_RADIUS = 3
_MAX_RADIUS = 15
_FRP_CAP = 200.0


def _frp_to_radius(frp: float | None) -> float:
    """Map FRP (MW) to a circle marker radius (pixels)."""
    if pd.isna(frp) or frp <= 0:
        return _MIN_RADIUS
    import math
    scaled = math.sqrt(min(frp, _FRP_CAP)) / math.sqrt(_FRP_CAP)
    return _MIN_RADIUS + scaled * (_MAX_RADIUS - _MIN_RADIUS)


def _get_colour(row: pd.Series) -> str:
    """Pick marker colour by ISI spread potential, or FIRMS confidence."""
    if "isi" in row and not pd.isna(row.get("isi")):
        danger = _isi_to_danger(float(row.get("isi", 0)))
        return _ISI_COLOURS.get(danger, "#555555")
    confidence = row.get("confidence_level", "unknown")
    return _CONFIDENCE_COLOURS.get(confidence, _CONFIDENCE_COLOURS["unknown"])


def _build_popup(row: pd.Series) -> str:
    """Build the HTML popup content for a fire marker."""
    parts = ["<b>🔥 Fire Detection</b><br>"]

    # FWI context (if available)
    if "fwi" in row and not pd.isna(row.get("fwi")):
        fwi = row.get("fwi", 0)
        isi = row.get("isi", 0)
        danger = row.get("danger_rating", "?")
        isi_danger = _isi_to_danger(float(isi) if not pd.isna(isi) else 0)
        parts.append(f"Spread (ISI): <b>{isi_danger}</b> ({isi:.1f})<br>")
        parts.append(f"FWI: <b>{danger}</b> ({fwi:.1f})<br>")
        parts.append(f"FFMC: {row.get('ffmc', 'N/A'):.1f} · "
                     f"DMC: {row.get('dmc', 'N/A'):.1f} · "
                     f"DC: {row.get('dc', 'N/A'):.1f}<br>")
        parts.append(f"Filter: {row.get('fwi_reason', 'N/A')}<br>")
        parts.append("<hr style='border-color:#444;'>")

    # FIRMS metadata
    frp = row.get("frp", "N/A")
    frp_str = f"{frp:.2f} MW" if not pd.isna(frp) else "N/A"
    confidence = row.get("confidence_level", "unknown")
    parts.append(f"Confidence: <b>{confidence}</b><br>")
    parts.append(f"FRP: {frp_str}<br>")
    acq = row.get("acq_datetime")
    acq_str = str(acq) if pd.notna(acq) else "N/A"
    parts.append(f"Time (UTC): {acq_str}<br>")
    parts.append(f"Brightness (K): {row.get('bright_ti4', 'N/A')}<br>")
    parts.append(f"Satellite: {row.get('satellite', 'N/A')}")

    return "".join(parts)


def _build_legend(has_isi: bool) -> str:
    """Build the HTML legend overlay for the map."""
    if has_isi:
        return """
        <div style="
            position: fixed; bottom: 30px; left: 30px; z-index: 1000;
            background: rgba(0,0,0,0.85); color: #fff; padding: 10px 14px;
            border-radius: 8px; font-size: 13px; font-family: monospace;
            border: 1px solid #444; min-width: 140px;
        ">
            <b>🔥 Spread (ISI)</b><br>
            <span style="color:#2ecc71;">●</span> Very Low (&lt;1)<br>
            <span style="color:#f1c40f;">●</span> Low (1–3)<br>
            <span style="color:#e67e22;">●</span> Moderate (3–8)<br>
            <span style="color:#e74c3c;">●</span> High (8–15)<br>
            <span style="color:#8e44ad;">●</span> Extreme (15+)<br>
            <hr style="border-color:#444;">
            <span style="color:#00ff88;">■</span> Watch Zone
        </div>
        """
    else:
        return """
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
        Must contain ``latitude``, ``longitude``, ``frp``, and either
        ``confidence``/``confidence_level`` (legacy) or ISI/FWI columns
        from ``index_integration.apply_fwi_filter()``.
    config : dict, optional
        Configuration dict for map centre and watch zones.
    save_path : str or Path, optional
        If provided, save the map HTML to this path.

    Returns
    -------
    folium.Map
    """
    # Detect if ISI data is present (from FWI filter)
    has_isi = "isi" in fires.columns

    # Filter to visible detections
    if has_isi and "fwi_show" in fires.columns:
        visible = fires[fires["fwi_show"] == True].copy()
        filtered_count = len(fires) - len(visible)
        if filtered_count > 0:
            logger.info(
                "Hiding %d/%d detections (FWI filter)",
                filtered_count, len(fires),
            )
    else:
        visible = fires
        logger.info("No FWI data — showing all detections (legacy mode)")

    # --- Centre the map ---
    if config and "BBOX" in config:
        bbox = config["BBOX"]
        centre_lat = (bbox[1] + bbox[3]) / 2
        centre_lon = (bbox[0] + bbox[2]) / 2
    elif not visible.empty:
        centre_lat = float(visible["latitude"].mean())
        centre_lon = float(visible["longitude"].mean())
    else:
        centre_lat, centre_lon = 39.0, -115.0  # Western US centre

    m = folium.Map(
        location=[centre_lat, centre_lon],
        zoom_start=5,
        tiles=None,
        control_scale=True,
    )

    # --- Base layers ---
    folium.TileLayer(
        tiles="CartoDB dark_matter",
        name="Dark Mode",
        overlay=False, control=True, show=True,
    ).add_to(m)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
              "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite Imagery",
        overlay=False, control=True, show=False,
    ).add_to(m)

    # --- Fire marker layer ---
    layer_name = "Fire Detections" + (" (ISI Filtered)" if has_isi else "")
    fire_layer = folium.FeatureGroup(
        name=layer_name, overlay=True, control=True, show=True,
    )

    for _, row in visible.iterrows():
        lat = row.get("latitude")
        lon = row.get("longitude")
        if pd.isna(lat) or pd.isna(lon):
            continue

        colour = _get_colour(row)
        radius = _frp_to_radius(row.get("frp"))
        popup_html = _build_popup(row)

        # Tooltip
        if has_isi and "isi" in row and not pd.isna(row.get("isi")):
            isi_danger = _isi_to_danger(float(row.get("isi", 0)))
            frp = row.get("frp", "N/A")
            frp_str = f"{frp:.1f} MW" if not pd.isna(frp) else "N/A"
            tooltip = f"🔥 {isi_danger} spread · {frp_str}"
        else:
            conf = row.get("confidence_level", "?")
            frp = row.get("frp", "N/A")
            frp_str = f"{frp:.1f} MW" if not pd.isna(frp) else "N/A"
            tooltip = f"{conf} · {frp_str}"

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=colour,
            fill=True,
            fill_color=colour,
            fill_opacity=0.7,
            weight=1,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=tooltip,
        ).add_to(fire_layer)

    fire_layer.add_to(m)

    # --- Watch zone overlays ---
    if config and "WATCH_ZONES" in config:
        zone_layer = folium.FeatureGroup(
            name="Watch Zones", overlay=True, control=True, show=True,
        )
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

    # --- Layer control ---
    folium.LayerControl(collapsed=False).add_to(m)

    # --- Legend ---
    legend_html = _build_legend(has_isi)
    m.get_root().html.add_child(folium.Element(legend_html))

    # --- Save if requested ---
    if save_path:
        save_map(m, save_path)

    return m


def save_map(m: folium.Map, path: str | Path) -> Path:
    """Save a Folium map to an HTML file."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out))
    logger.info("Map saved to %s", out.resolve())
    return out.resolve()
"""
Hestia Vigil – Map builder: NIFC fire perimeters + FIRMS hotspots.

Loads the latest NIFC WFIGS perimeter snapshot (``state/wfigs/current.json``,
an ArcGIS FeatureSet whose geometries use ``rings``) and builds a Folium map:

- Fire perimeters, colored by containment percentage.
- FIRMS hotspot detections: hotspots that fall inside an existing perimeter
  are drawn as bright "confirmed" markers; all other detections are drawn
  in a dimmer, toggleable "unconfirmed" layer so new fires are never
  silently hidden.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Sequence

import folium
import pandas as pd
from shapely.geometry import Point, Polygon

logger = logging.getLogger(__name__)

# Path to the NIFC WFIGS perimeter snapshot written by the dashboard at startup
WFIGS_STATE_PATH = Path("state/wfigs/current.json")

# Esri World Imagery satellite tiles (toggleable alternative to dark mode)
ESRI_WORLD_IMAGERY_TILES = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

# FRP → marker radius scaling
_MIN_RADIUS = 3
_MAX_RADIUS = 15
_FRP_CAP = 200.0

# Containment colour mapping
_CONTAINMENT_COLOURS = {
    "Uncontained": "#d73027",  # red
    "Partially Contained": "#e67e22",  # orange
    "Contained": "#2ecc71",  # green
}

# Hotspot likelihood tier colours
_TIER_COLOURS = {
    "Confirmed": "#ff4500",   # bright orange-red
    "High": "#ff8c00",        # dark orange
    "Medium": "#f1c40f",      # yellow
    "Low": "#9e9e9e",         # grey
    "Unlikely": "#444444",    # dark grey
}

# Perimeter simplification tolerance in degrees (~0.001° ≈ 100 m).  The raw
# WFIGS rings total ~750k vertices, which produces a ~29 MB HTML document;
# simplifying keeps the visual output identical at dashboard zoom levels
# while staying far below the 375 m VIIRS pixel resolution.
_SIMPLIFY_TOLERANCE_DEG = 0.001


# ---------------------------------------------------------------------------
# Perimeter snapshot loading
# ---------------------------------------------------------------------------

def _load_perimeter_features(path: Path | None = None) -> List[Dict[str, Any]]:
    """Return feature dicts from the snapshot; empty list when unavailable."""
    path = Path(path) if path is not None else WFIGS_STATE_PATH
    if not path.exists():
        logger.warning("No perimeter snapshot at %s — showing hotspots only", path)
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            gj = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read perimeter snapshot %s: %s", path, exc)
        return []
    return gj.get("features", [])


# ---------------------------------------------------------------------------
# Perimeter helpers — accept both ArcGIS FeatureSet and GeoJSON features
# ---------------------------------------------------------------------------

def _feature_rings(feature: Dict[str, Any]) -> List[List[List[float]]]:
    """Return the feature's rings as lists of [lon, lat] pairs.

    - ArcGIS FeatureSet: ``geometry.rings`` (already ring-based).
    - GeoJSON Polygon/MultiPolygon: converted to rings (exterior + holes).
    """
    geom = feature.get("geometry") or {}
    if "rings" in geom:
        return [r for r in geom["rings"] if len(r) >= 4]
    if "coordinates" not in geom:
        return []
    if geom.get("type") == "Polygon":
        return [r for r in geom["coordinates"] if len(r) >= 4]
    if geom.get("type") == "MultiPolygon":
        rings: List[List[List[float]]] = []
        for poly in geom["coordinates"]:
            rings.extend(r for r in poly if len(r) >= 4)
        return rings
    return []


def _feature_props(feature: Dict[str, Any]) -> Dict[str, Any]:
    return feature.get("attributes") or feature.get("properties") or {}


def _perimeter_name(props: Dict[str, Any]) -> str:
    return (
        props.get("poly_IncidentName")
        or props.get("attr_IncidentName")
        or props.get("fire_name")
        or "Unnamed Fire"
    )


def _containment(props: Dict[str, Any]) -> tuple[float | None, str]:
    """Return (percent_contained, status_label) for a perimeter."""
    raw = props.get("attr_PercentContained")
    pct: float | None = None
    try:
        if raw is not None:
            pct = float(raw)
    except (TypeError, ValueError):
        pct = None
    if pct is None:
        return None, "Uncontained"
    if pct >= 90:
        return pct, "Contained"
    if pct >= 50:
        return pct, "Partially Contained"
    return pct, "Uncontained"


def _rings_to_polygon(rings: Sequence[Sequence[Sequence[float]]]) -> Polygon | None:
    """Build a shapely Polygon: first ring exterior, remaining rings holes."""
    if not rings:
        return None
    try:
        return Polygon(shell=rings[0], holes=list(rings[1:]))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Hotspot helpers
# ---------------------------------------------------------------------------

def _fmt(value: Any, fmt: str = "{}") -> str:
    """Format a (possibly NaN/None) pandas value for display."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    try:
        if pd.isna(value):
            return "N/A"
    except (TypeError, ValueError):
        pass
    return fmt.format(value)


def _frp_to_radius(frp: Any) -> float:
    """Map FRP (MW) to a circle marker radius (pixels)."""
    if frp is None or (isinstance(frp, float) and pd.isna(frp)) or frp <= 0:
        return _MIN_RADIUS
    import math
    scaled = math.sqrt(min(frp, _FRP_CAP)) / math.sqrt(_FRP_CAP)
    return _MIN_RADIUS + scaled * (_MAX_RADIUS - _MIN_RADIUS)


def _hotspot_popup(row: pd.Series) -> str:
    lat = float(row["latitude"])
    lon = float(row["longitude"])
    conf = _fmt(row.get("confidence_level"), "{} confidence")
    frp = _fmt(row.get("frp"), "FRP: {:.1f}")
    acq = _fmt(row.get("acq_datetime"))
    tier = _fmt(row.get("likelihood_tier"), "Tier: {}")
    score = _fmt(row.get("likelihood_score"), "Likelihood: {}%")
    reason = _fmt(row.get("fwi_reason"), "Reason: {}")
    return (
        f"<b>Hotspot detection</b><br>"
        f"{html.escape(tier)}<br>"
        f"{html.escape(score)}<br>"
        f"{html.escape(reason)}<br>"
        f"{html.escape(conf)}<br>"
        f"{html.escape(frp)}<br>Acquired: {html.escape(acq)}<br>"
        f"Lat/Lon: {lat:.4f}, {lon:.4f}<br>"
        f"<a href='https://www.google.com/maps?q={lat},{lon}' target='_blank'>Google Maps</a>"
    )


# ---------------------------------------------------------------------------
# Public API – build_map and save_map
# ---------------------------------------------------------------------------

def build_map(df: Any, config: Dict[str, Any], dark_mode: bool = True) -> folium.Map:
    """Return a Folium map with NIFC perimeters and FIRMS hotspots.

    Parameters
    ----------
    df : pd.DataFrame or None
        FIRMS fire detections (columns ``latitude``/``longitude`` etc., as
        returned by ``firms_client.fetch_fires``).  Hotspots inside a
        perimeter are drawn as "confirmed"; the rest go to a dimmer
        "unconfirmed" layer.  ``None`` renders perimeters only.
    config : dict
        Configuration dictionary – ``BBOX`` (map centering) and
        ``WATCH_ZONES`` (green rectangle overlays).
    dark_mode : bool, default True
        When ``True`` (default), CartoDB dark_matter tiles are shown;
        otherwise Esri World Imagery satellite tiles are shown.  Both are
        available as toggleable base layers.
    """

    # Load the latest perimeter snapshot
    features = _load_perimeter_features()

    # Center the map on the bbox provided in config
    bbox = config.get("BBOX", [0, 0, 0, 0])
    center_lat = (bbox[1] + bbox[3]) / 2
    center_lon = (bbox[0] + bbox[2]) / 2

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=5,
        tiles=None,
        control_scale=True,
    )

    # --- Base layers ---
    folium.TileLayer(
        tiles="CartoDB dark_matter",
        name="Dark Mode",
        overlay=False, control=True, show=dark_mode,
    ).add_to(m)

    folium.TileLayer(
        tiles=ESRI_WORLD_IMAGERY_TILES,
        attr="Esri World Imagery",
        name="Satellite Imagery",
        overlay=False, control=True, show=not dark_mode,
    ).add_to(m)

    # ------------------------------------------------------------------
    # Fire perimeters
    # ------------------------------------------------------------------
    perimeter_polygons: List[Polygon] = []

    for feature in features:
        rings = _feature_rings(feature)
        if not rings:
            continue
        props = _feature_props(feature)
        name = _perimeter_name(props)
        pct, status = _containment(props)
        colour = _CONTAINMENT_COLOURS[status]

        polygon = _rings_to_polygon(rings)
        if polygon is not None and not polygon.is_empty:
            simple = polygon.simplify(_SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
            if not simple.is_empty:
                polygon = simple
            perimeter_polygons.append(polygon)
            # Leaflet polygons accept [exterior, hole, ...] rings as [lat, lon]
            locations = [[[y, x] for x, y in polygon.exterior.coords]]
            for interior in polygon.interiors:
                locations.append([[y, x] for x, y in interior.coords])
        else:
            # Degenerate geometry — render raw rings, skip containment tests
            locations = [[[c[1], c[0]] for c in ring] for ring in rings]
        pct_str = f"{pct:.0f}%" if pct is not None else "unknown"
        popup = f"<b>{html.escape(str(name))}</b><br>Status: {status} ({pct_str})"
        acres = props.get("poly_GISAcres") or props.get("attr_CalculatedAcres")
        try:
            popup += f"<br>{float(acres):,.0f} acres"
        except (TypeError, ValueError):
            pass
        folium.Polygon(
            locations=locations,
            color=colour,
            weight=2,
            fill=True,
            fill_color=colour,
            fill_opacity=0.15,
            popup=popup,
        ).add_to(m)

    # ------------------------------------------------------------------
    # FIRMS hotspots by likelihood tier
    # ------------------------------------------------------------------
    tier_groups: Dict[str, folium.FeatureGroup] = {}
    tier_counts: Dict[str, int] = {}

    has_tiers = df is not None and "likelihood_tier" in df.columns
    if df is not None and len(df) > 0:
        for _, row in df.iterrows():
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if pd.isna(lat) or pd.isna(lon):
                continue

            # Determine tier
            if has_tiers:
                tier = str(row.get("likelihood_tier", "Low"))
            else:
                # Fallback for raw FIRMS data without likelihood columns
                point = Point(lon, lat)
                tier = "Confirmed" if any(poly.contains(point) for poly in perimeter_polygons) else "Low"

            colour = _TIER_COLOURS.get(tier, _TIER_COLOURS["Low"])
            radius = _frp_to_radius(row.get("frp"))
            conf = row.get("confidence_level", "unknown")
            tooltip = f"{tier} · {conf} · FRP {row.get('frp', 'N/A')}"

            if tier not in tier_groups:
                # "Unlikely" suppressed detections are hidden by default but
                # can be toggled on via the layer control.
                show = tier != "Unlikely"
                tier_groups[tier] = folium.FeatureGroup(name=f"{tier} hotspots", show=show)
                tier_counts[tier] = 0

            folium.CircleMarker(
                location=[lat, lon],
                radius=radius,
                color=colour,
                weight=1,
                fill=True,
                fill_color=colour,
                fill_opacity=0.7,
                popup=_hotspot_popup(row),
                tooltip=tooltip,
            ).add_to(tier_groups[tier])
            tier_counts[tier] += 1

        logger.info(
            "Plotted %d hotspots by tier: %s",
            sum(tier_counts.values()),
            ", ".join(f"{k}={v}" for k, v in sorted(tier_counts.items())),
        )

    # Add tier groups in a stable order
    for tier in ("Confirmed", "High", "Medium", "Low", "Unlikely"):
        if tier in tier_groups:
            tier_groups[tier].add_to(m)

    # ------------------------------------------------------------------
    # Watch zone overlays
    # ------------------------------------------------------------------
    zone_layer = folium.FeatureGroup(name="Watch Zones")
    for zone_id, zone_cfg in (config.get("WATCH_ZONES") or {}).items():
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
            popup=f"<b>{html.escape(label)}</b><br>Zone: {html.escape(zone_id)}",
            tooltip=label,
        ).add_to(zone_layer)
    zone_layer.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # --- Legend ---
    m.get_root().html.add_child(folium.Element(_build_legend()))
    return m


def _build_legend() -> str:
    """Build the HTML legend overlay for the map (dark theme, monospace)."""
    return """
        <div style="
            position: fixed; bottom: 30px; left: 30px; z-index: 1000;
            background: rgba(0,0,0,0.85); color: #fff; padding: 10px 14px;
            border-radius: 8px; font-size: 13px; font-family: monospace;
            border: 1px solid #444; min-width: 160px;
        ">
            <b>🔥 Vigil</b><br>
            <span style="color:#ff4500;">●</span> Confirmed<br>
            <span style="color:#ff8c00;">●</span> High likelihood<br>
            <span style="color:#f1c40f;">●</span> Medium likelihood<br>
            <span style="color:#9e9e9e;">●</span> Low likelihood<br>
            <span style="color:#444444;">●</span> Unlikely<br>
            <hr style="border-color:#444;">
            <b>Perimeter containment</b><br>
            <span style="color:#d73027;">●</span> Uncontained<br>
            <span style="color:#e67e22;">●</span> Partial<br>
            <span style="color:#2ecc71;">●</span> Contained<br>
            <hr style="border-color:#444;">
            <span style="color:#00ff88;">■</span> Watch Zone
        </div>
    """


def save_map(map_obj: folium.Map, path: str | Path) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    map_obj.save(str(out_path))

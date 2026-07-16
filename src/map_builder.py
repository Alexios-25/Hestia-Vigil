import logging
from pathlib import Path
import json
from typing import Any, Dict

import folium

logger = logging.getLogger(__name__)
_CONTAINMENT_COLOURS = {
    "Uncontained": "#d73027",
    "Partially Contained": "#e67e22",
    "Contained": "#2ecc71",
}

def _load_geojson() -> dict:
    path = Path("state/wfigs/current.json")
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def build_map(df: Any, config: Dict[str, Any]) -> folium.Map:
    bbox = config.get("BBOX", [0, 0, 0, 0])
    center_lat = (bbox[1] + bbox[3]) / 2
    center_lon = (bbox[0] + bbox[2]) / 2
    m = folium.Map(location=[center_lat, center_lon], zoom_start=4)

    gj = _load_geojson()
    for feature in gj.get("features", []):
        geom = feature["geometry"]
        props = feature.get("properties", {})

        pct = None
        if "attr_PercentContained" in props:
            try:
                pct = float(props["attr_PercentContained"])
            except Exception:
                pct = None
        if pct is not None:
            if pct >= 90:
                colour = _CONTAINMENT_COLOURS["Contained"]
            elif pct >= 50:
                colour = _CONTAINMENT_COLOURS["Partially Contained"]
            else:
                colour = _CONTAINMENT_COLOURS["Uncontained"]
        else:
            colour = _CONTAINMENT_COLOURS["Uncontained"]

        if "rings" in geom:
            for ring in geom["rings"]:
                coords = [[c[1], c[0]] for c in ring]
                folium.Polygon(
                    locations=coords,
                    color=colour,
                    weight=2,
                    fill=True,
                    fill_color=colour,
                    fill_opacity=0.15,
                    popup=f"<b>{props.get('fire_name', 'Unnamed Fire')}</b>\nStatus: {pct if pct is not None else 'N/A'}",
                ).add_to(m)
        elif "coordinates" in geom:
            if geom["type"] == "Polygon":
                coords = [[c[1], c[0]] for c in geom["coordinates"][0]]
                folium.Polygon(
                    locations=coords,
                    color=colour,
                    weight=2,
                    fill=True,
                    fill_color=colour,
                    fill_opacity=0.15,
                    popup=f"<b>{props.get('fire_name', 'Unnamed Fire')}</b>\nStatus: {pct if pct is not None else 'N/A'}",
                ).add_to(m)
            elif geom["type"] == "MultiPolygon":
                for poly in geom["coordinates"]:
                    coords = [[c[1], c[0]] for c in poly[0]]
                    folium.Polygon(
                        locations=coords,
                        color=colour,
                        weight=2,
                        fill=True,
                        fill_color=colour,
                        fill_opacity=0.15,
                        popup=f"<b>{props.get('fire_name', 'Unnamed Fire')}</b>\nStatus: {pct if pct is not None else 'N/A'}",
                    ).add_to(m)
        else:
            logger.warning("Unsupported geometry format in feature %s", feature.get('id'))

    folium.LayerControl().add_to(m)
    return m

def save_map(map_obj: folium.Map, path: str | Path) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    map_obj.save(str(out_path))

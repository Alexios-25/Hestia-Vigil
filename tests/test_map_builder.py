"""
Tests for the map builder: ArcGIS perimeter parsing + hotspot plotting.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd  # noqa: E402

import map_builder  # noqa: E402


def _arcgis_feature(
    name: str = "Test Fire",
    pct: float | None = None,
    lon_min: float = -107.0,
    lat_min: float = 38.0,
    lon_max: float = -106.0,
    lat_max: float = 39.0,
) -> dict:
    """Build a minimal ArcGIS FeatureSet feature with a square ring."""
    ring = [
        [lon_min, lat_min],
        [lon_max, lat_min],
        [lon_max, lat_max],
        [lon_min, lat_max],
        [lon_min, lat_min],
    ]
    attrs: dict = {"poly_IncidentName": name}
    if pct is not None:
        attrs["attr_PercentContained"] = pct
    return {"attributes": attrs, "geometry": {"rings": [ring]}}


class TestBuildMap(unittest.TestCase):
    def setUp(self):
        self.config = {"BBOX": [-125.0, 24.0, -66.0, 50.0]}

    def _render(self, df, features) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            snap = Path(tmpdir) / "current.json"
            snap.write_text(json.dumps({"features": features}))
            with patch.object(map_builder, "WFIGS_STATE_PATH", snap):
                m = map_builder.build_map(df, self.config)
        return m.get_root().render()

    def test_confirmed_and_unconfirmed_hotspots(self):
        df = pd.DataFrame(
            [
                {"latitude": 38.5, "longitude": -106.5, "confidence_level": "high", "frp": 10.0},
                {"latitude": 45.0, "longitude": -100.0, "confidence_level": "low", "frp": 2.0},
            ]
        )
        html_out = self._render(df, [_arcgis_feature()])
        self.assertIn("Confirmed hotspots", html_out)
        self.assertIn("Unconfirmed detections", html_out)
        self.assertIn("#ff4500", html_out)  # confirmed marker colour
        self.assertIn("#9e9e9e", html_out)  # unconfirmed marker colour
        self.assertIn("Hotspot detection", html_out)

    def test_layer_control_renders(self):
        html_out = self._render(None, [])
        self.assertIn("L.control.layers", html_out)

    def test_containment_colours(self):
        html_out = self._render(None, [_arcgis_feature(pct=95)])
        self.assertIn("#2ecc71", html_out)  # contained
        html_out = self._render(None, [_arcgis_feature(pct=10)])
        self.assertIn("#d73027", html_out)  # uncontained

    def test_perimeter_name_and_acres_in_popup(self):
        feature = _arcgis_feature(name="El Rito Creek", pct=95)
        feature["attributes"]["poly_GISAcres"] = 1234
        html_out = self._render(None, [feature])
        self.assertIn("El Rito Creek", html_out)
        self.assertIn("1,234 acres", html_out)

    def test_missing_snapshot_renders_hotspots_only(self):
        df = pd.DataFrame(
            [{"latitude": 45.0, "longitude": -100.0, "confidence_level": "low", "frp": 2.0}]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "nope.json"
            with patch.object(map_builder, "WFIGS_STATE_PATH", missing):
                m = map_builder.build_map(df, self.config)
        html_out = m.get_root().render()
        self.assertIn("Unconfirmed detections", html_out)
        self.assertIn("#9e9e9e", html_out)

    def test_none_df_renders_perimeters_only(self):
        html_out = self._render(None, [_arcgis_feature(name="Solo Fire")])
        self.assertIn("Solo Fire", html_out)
        self.assertIn("L.control.layers", html_out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

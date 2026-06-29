"""
Unit tests for src/firms_client.py

Uses unittest.mock to patch requests.get so we never hit the real NASA API
during tests.  Covers:
- Config loading
- CSV parsing and column normalization
- Error handling (API errors, empty responses, network failures)
- Bounding box filtering
- CLI entry point
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import yaml

# Ensure src/ is importable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import load_config
from firms_client import (
    fetch_fires,
    filter_by_bbox,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,"
    "satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"
    "39.65504,-104.70329,296.11,0.49,0.40,2026-06-10,0920,"
    "N,VIIRS,n,2.0NRT,281.05,0.39,N\n"
    "39.80336,-104.94584,300.66,0.47,0.40,2026-06-10,0920,"
    "N,VIIRS,n,2.0NRT,289.43,1.19,N\n"
    "42.39546,-83.55927,304.45,0.38,0.36,2026-06-10,0737,"
    "N,VIIRS,h,2.0NRT,287.66,0.83,N\n"
    "25.00000,-90.00000,290.00,0.40,0.40,2026-06-10,0800,"
    "N,VIIRS,l,2.0NRT,275.00,0.20,N"
)

SAMPLE_CONFIG = {
    "SOURCE": "VIIRS_SNPP_NRT",
    "BBOX": [-125.0, 24.0, -66.0, 50.0],
    "POLL_INTERVAL_MINUTES": 60,
    "WATCH_ZONES": {
        "colorado_front_range": {
            "label": "Colorado Front Range",
            "bbox": [-106.0, 38.5, -104.5, 40.5],
        },
    },
}

SAMPLE_CONFIG_WITH_MAP_KEY = {
    **SAMPLE_CONFIG,
    "MAP_KEY": "test_key_123",
}


def _write_temp_config(config: dict) -> Path:
    """Write config dict to a temporary YAML file and return its path."""
    tmp = Path(tempfile.mkdtemp()) / "test_config.yaml"
    with open(tmp, "w") as f:
        yaml.dump(config, f)
    return tmp


def _mock_response(text: str, status_code: int = 200) -> MagicMock:
    """Create a mock requests.Response-like object."""
    mock = MagicMock()
    mock.text = text
    mock.status_code = status_code
    mock.raise_for_status = MagicMock()
    if status_code >= 400:
        mock.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return mock


# ---------------------------------------------------------------------------
# Tests: load_config
# ---------------------------------------------------------------------------

class TestLoadConfig(unittest.TestCase):
    def test_loads_valid_yaml(self):
        path = _write_temp_config(SAMPLE_CONFIG)
        with patch.dict(os.environ, {"NASA_FIRMS_MAP_KEY": "env_key_123"}, clear=False):
            config = load_config(path)
        self.assertEqual(config["MAP_KEY"], "env_key_123")
        self.assertEqual(config["SOURCE"], "VIIRS_SNPP_NRT")
        self.assertEqual(len(config["BBOX"]), 4)

    def test_env_secret_overrides_legacy_config_key(self):
        path = _write_temp_config(SAMPLE_CONFIG_WITH_MAP_KEY)
        with patch.dict(os.environ, {"NASA_FIRMS_MAP_KEY": "env_key_123"}, clear=False):
            config = load_config(path)
        self.assertEqual(config["MAP_KEY"], "env_key_123")

    def test_missing_firms_key_raises(self):
        path = _write_temp_config(SAMPLE_CONFIG)
        with patch.dict(os.environ, {}, clear=True), patch("config._ENV_FILE_PATH", Path("/nonexistent/.env")):
            with self.assertRaises(ValueError):
                load_config(path)

    def test_resolves_alert_credentials_from_env(self):
        alert_config = {
            **SAMPLE_CONFIG,
            "ALERTS": {
                "discord_webhook": {
                    "enabled": True,
                    "webhook_env": "DISCORD_WEBHOOK_URL",
                },
                "email": {
                    "enabled": True,
                    "smtp_host": "smtp.gmail.com",
                    "smtp_port": 587,
                    "smtp_user_env": "EMAIL_USER",
                    "smtp_password_env": "EMAIL_APP_PASSWORD",
                    "to_address": "you@gmail.com",
                },
            },
        }
        path = _write_temp_config(alert_config)
        with patch.dict(
            os.environ,
            {
                "NASA_FIRMS_MAP_KEY": "env_key_123",
                "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test",
                "EMAIL_USER": "you@gmail.com",
                "EMAIL_APP_PASSWORD": "secret-password",
            },
            clear=False,
        ):
            config = load_config(path)

        alerts = config["ALERTS"]
        self.assertEqual(alerts["discord_webhook"]["webhook_url"], "https://discord.com/api/webhooks/test")
        self.assertEqual(alerts["email"]["smtp_user"], "you@gmail.com")
        self.assertEqual(alerts["email"]["smtp_password"], "secret-password")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")


# ---------------------------------------------------------------------------
# Tests: fetch_fires
# ---------------------------------------------------------------------------

class TestFetchFires(unittest.TestCase):

    @patch("firms_client.requests.get")
    def test_returns_dataframe_with_expected_columns(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_CSV)
        df = fetch_fires(SAMPLE_CONFIG_WITH_MAP_KEY)

        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 4)
        # Core columns from FIRMS
        for col in ("latitude", "longitude", "bright_ti4", "confidence",
                     "frp", "satellite"):
            self.assertIn(col, df.columns)
        # Added by our client
        self.assertIn("confidence_level", df.columns)
        self.assertIn("acq_datetime", df.columns)

    @patch("firms_client.requests.get")
    def test_confidence_mapping(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_CSV)
        df = fetch_fires(SAMPLE_CONFIG_WITH_MAP_KEY)

        # Row 0: 'n' → 'nominal'
        self.assertEqual(df.iloc[0]["confidence_level"], "nominal")
        # Row 2: 'h' → 'high'
        self.assertEqual(df.iloc[2]["confidence_level"], "high")
        # Row 3: 'l' → 'low'
        self.assertEqual(df.iloc[3]["confidence_level"], "low")

    @patch("firms_client.requests.get")
    def test_numeric_columns_are_typed(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_CSV)
        df = fetch_fires(SAMPLE_CONFIG_WITH_MAP_KEY)

        self.assertTrue(pd.api.types.is_numeric_dtype(df["latitude"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(df["longitude"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(df["bright_ti4"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(df["frp"]))

    @patch("firms_client.requests.get")
    def test_invalid_api_response_raises_value_error(self, mock_get):
        mock_get.return_value = _mock_response("Invalid MAP_KEY.")
        with self.assertRaises(ValueError) as ctx:
            fetch_fires(SAMPLE_CONFIG_WITH_MAP_KEY)
        self.assertIn("Invalid", str(ctx.exception))

    @patch("firms_client.requests.get")
    def test_empty_response_raises_value_error(self, mock_get):
        mock_get.return_value = _mock_response("   ")
        with self.assertRaises(ValueError):
            fetch_fires(SAMPLE_CONFIG_WITH_MAP_KEY)

    @patch("firms_client.requests.get")
    def test_network_error_propagates(self, mock_get):
        mock_get.side_effect = Exception("Connection timed out")
        with self.assertRaises(Exception) as ctx:
            fetch_fires(SAMPLE_CONFIG_WITH_MAP_KEY)
        self.assertIn("Connection timed out", str(ctx.exception))

    @patch("firms_client.requests.get")
    def test_uses_config_day_range(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_CSV)
        cfg = {**SAMPLE_CONFIG_WITH_MAP_KEY, "DAY_RANGE": 3}
        fetch_fires(cfg)
        # Verify the URL contains /3/ for day_range
        call_url = mock_get.call_args[0][0]
        self.assertTrue(call_url.rstrip("/").endswith("/3"))


# ---------------------------------------------------------------------------
# Tests: filter_by_bbox
# ---------------------------------------------------------------------------

class TestFilterByBbox(unittest.TestCase):
    def test_filters_correctly(self):
        df = pd.DataFrame({
            "latitude": [39.0, 42.0, 25.0, 45.0],
            "longitude": [-105.0, -83.5, -90.0, -73.0],
        })
        # Colorado-ish bbox
        filtered = filter_by_bbox(df, [-106.0, 38.5, -104.5, 40.5])
        self.assertEqual(len(filtered), 1)
        self.assertAlmostEqual(filtered.iloc[0]["latitude"], 39.0)

    def test_returns_copy(self):
        df = pd.DataFrame({
            "latitude": [39.0],
            "longitude": [-105.0],
        })
        filtered = filter_by_bbox(df, [-106.0, 38.5, -104.5, 40.5])
        # Mutating the filtered df should not affect the original
        filtered["new_col"] = 1
        self.assertNotIn("new_col", df.columns)

    def test_empty_result(self):
        df = pd.DataFrame({
            "latitude": [25.0],
            "longitude": [-90.0],
        })
        filtered = filter_by_bbox(df, [-106.0, 38.5, -104.5, 40.5])
        self.assertEqual(len(filtered), 0)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)

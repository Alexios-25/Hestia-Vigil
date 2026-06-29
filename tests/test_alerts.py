"""
Unit tests for src/alerts.py.

Covers the core de-duplication behavior:
- first detection in a watch zone creates one alert
- repeated detections for the same hotspot-zone pair do not create new alerts
- alert history persists across restarts
- distinct hotspots in the same zone create distinct alerts
- detections outside all watch zones are ignored
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alerts import (  # noqa: E402
    Alert,
    build_detection_key,
    detect_new_alerts,
    format_alert_message,
    list_alerts,
    load_alert_state,
)


SAMPLE_CONFIG = {
    "WATCH_ZONES": {
        "colorado_front_range": {
            "label": "Colorado Front Range",
            "bbox": [-106.0, 38.5, -104.5, 40.5],
        },
        "michigan_west": {
            "label": "Western Michigan",
            "bbox": [-87.0, 42.5, -85.0, 44.0],
        },
    }
}

NOW = datetime(2026, 6, 17, 14, 30, tzinfo=timezone.utc)


def _fire_row(
    latitude: float,
    longitude: float,
    *,
    acq_date: str = "2026-06-17",
    confidence: str = "n",
    confidence_level: str = "nominal",
    frp: float = 12.5,
) -> dict:
    return {
        "latitude": latitude,
        "longitude": longitude,
        "acq_date": acq_date,
        "confidence": confidence,
        "confidence_level": confidence_level,
        "frp": frp,
        "satellite": "N",
        "instrument": "VIIRS",
    }


def _fires(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


class TestBuildDetectionKey(unittest.TestCase):
    def test_builds_stable_key_from_zone_date_and_rounded_location(self):
        row = _fire_row(39.65504, -104.70329)
        key = build_detection_key("colorado_front_range", row)

        self.assertEqual(key, "firms:colorado_front_range:2026-06-17:39.66,-104.70")

    def test_small_coordinate_shifts_do_not_create_new_keys(self):
        row_a = _fire_row(39.65504, -104.70329)
        row_b = _fire_row(39.65600, -104.70400)

        self.assertEqual(
            build_detection_key("colorado_front_range", row_a),
            build_detection_key("colorado_front_range", row_b),
        )


class TestDetectNewAlerts(unittest.TestCase):
    def test_first_detection_creates_one_alert_and_persists_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "alert_history.json"
            alerts = detect_new_alerts(
                _fires(_fire_row(39.65504, -104.70329)),
                SAMPLE_CONFIG,
                state_path,
                now=NOW,
            )

            self.assertEqual(len(alerts), 1)
            self.assertIsInstance(alerts[0], Alert)
            self.assertEqual(alerts[0].zone_id, "colorado_front_range")
            self.assertEqual(alerts[0].key, "firms:colorado_front_range:2026-06-17:39.66,-104.70")

            state = load_alert_state(state_path)
            self.assertIn(alerts[0].key, state["alerts"])
            self.assertEqual(state["alerts"][alerts[0].key]["count"], 1)
            self.assertEqual(state["alerts"][alerts[0].key]["last_alerted_utc"], "2026-06-17T14:30:00Z")

    def test_duplicate_detection_does_not_create_new_alert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "alert_history.json"
            row = _fire_row(39.65504, -104.70329)

            first_alerts = detect_new_alerts(_fires(row), SAMPLE_CONFIG, state_path, now=NOW)
            second_alerts = detect_new_alerts(
                _fires(row),
                SAMPLE_CONFIG,
                state_path,
                now=NOW + timedelta(hours=1),
            )

            self.assertEqual(len(first_alerts), 1)
            self.assertEqual(len(second_alerts), 0)

            state = load_alert_state(state_path)
            record = state["alerts"][first_alerts[0].key]
            self.assertEqual(record["count"], 2)
            self.assertEqual(record["last_seen_utc"], "2026-06-17T15:30:00Z")
            self.assertEqual(record["last_alerted_utc"], "2026-06-17T14:30:00Z")

    def test_distinct_hotspots_in_same_zone_create_distinct_alerts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "alert_history.json"

            alerts = detect_new_alerts(
                _fires(
                    _fire_row(39.65504, -104.70329),
                    _fire_row(39.70000, -104.80000),
                ),
                SAMPLE_CONFIG,
                state_path,
                now=NOW,
            )

            self.assertEqual(len(alerts), 2)
            self.assertEqual(alerts[0].key, "firms:colorado_front_range:2026-06-17:39.66,-104.70")
            self.assertEqual(alerts[1].key, "firms:colorado_front_range:2026-06-17:39.70,-104.80")

    def test_detection_outside_all_watch_zones_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "alert_history.json"

            alerts = detect_new_alerts(
                _fires(_fire_row(45.0, -100.0)),
                SAMPLE_CONFIG,
                state_path,
                now=NOW,
            )

            self.assertEqual(alerts, [])
            self.assertEqual(load_alert_state(state_path)["alerts"], {})

    def test_alert_history_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "alert_history.json"
            row = _fire_row(39.65504, -104.70329)

            first_alerts = detect_new_alerts(_fires(row), SAMPLE_CONFIG, state_path, now=NOW)
            second_alerts = detect_new_alerts(_fires(row), SAMPLE_CONFIG, state_path, now=NOW + timedelta(minutes=30))

            self.assertEqual(len(first_alerts), 1)
            self.assertEqual(len(second_alerts), 0)

            # Simulate a fresh process by loading state from disk before the next detection.
            load_alert_state(state_path)
            third_alerts = detect_new_alerts(_fires(row), SAMPLE_CONFIG, state_path, now=NOW + timedelta(hours=2))

            self.assertEqual(third_alerts, [])
            self.assertEqual(load_alert_state(state_path)["alerts"][first_alerts[0].key]["count"], 3)


class TestAlertFormattingAndListing(unittest.TestCase):
    def test_format_alert_message_includes_useful_summary_without_secrets(self):
        alert = Alert(
            key="firms:colorado_front_range:2026-06-17:39.66,-104.70",
            source="firms",
            zone_id="colorado_front_range",
            zone_label="Colorado Front Range",
            latitude=39.65504,
            longitude=-104.70329,
            confidence="n",
            confidence_level="nominal",
            frp=12.5,
            acq_datetime="2026-06-17 0920",
            first_seen_utc="2026-06-17T14:30:00Z",
            last_seen_utc="2026-06-17T15:30:00Z",
            last_alerted_utc="2026-06-17T14:30:00Z",
            count=2,
        )

        message = format_alert_message(alert)

        self.assertIn("Hestia.Vigil Alert", message)
        self.assertIn("Colorado Front Range", message)
        self.assertIn("39.65504, -104.70329", message)
        self.assertNotIn("MAP_KEY", message)

    def test_list_alerts_returns_most_recent_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "alert_history.json"

            detect_new_alerts(
                _fires(_fire_row(39.65504, -104.70329)),
                SAMPLE_CONFIG,
                state_path,
                now=NOW,
            )
            detect_new_alerts(
                _fires(_fire_row(43.0, -86.0)),
                SAMPLE_CONFIG,
                state_path,
                now=NOW + timedelta(minutes=5),
            )

            alerts = list_alerts(state_path)

            self.assertEqual([alert.zone_id for alert in alerts], ["michigan_west", "colorado_front_range"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

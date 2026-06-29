"""
Integration tests for the Flask dashboard endpoints.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dashboard import app  # noqa: E402


class TestDashboardAlertEndpoints(unittest.TestCase):
    def test_api_alerts_returns_empty_history_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "alert_history.json"
            with patch("dashboard._DEFAULT_ALERT_STATE_PATH", state_path):
                with app.test_client() as client:
                    response = client.get("/api/alerts")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["count"], 0)
            self.assertEqual(payload["alerts"], [])

            body = response.get_data(as_text=True)
            self.assertNotIn("MAP_KEY", body)
            self.assertNotIn("NASA_FIRMS_MAP_KEY", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)

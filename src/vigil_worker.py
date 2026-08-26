"""
Hestia Vigil — Background Worker and Scheduler

Polls FIRMS, applies the FWI filter, detects new alerts, and dispatches
notifications — only for hotspots that pass the filter.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import pandas as pd

from config import load_config
from firms_client import fetch_fires
from alerts import detect_new_alerts
from notifiers import dispatch_notifications

# Import Index integration
_index_src = Path(__file__).resolve().parent.parent / "Index" / "src"
if str(_index_src) not in sys.path:
    sys.path.insert(0, str(_index_src))

try:
    from index_integration import apply_fwi_filter
    _HAS_FWI = True
except ImportError:
    _HAS_FWI = False
    logging.getLogger(__name__).warning(
        "Index integration not available — running without FWI filter"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "alert_history.json"
LATEST_FIRES_PATH = Path(__file__).resolve().parent.parent / "state" / "latest_fires.csv"


def _save_filtered_fires(fires: pd.DataFrame, path: Path) -> None:
    """Persist the filtered/tiered FIRMS DataFrame for the dashboard.

    The dashboard reads this file to avoid repeating the slow Open-Meteo
    weather calls on every page load.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Convert any non-JSON-friendly columns to strings for safe CSV round-trip.
    out = fires.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].astype(str)
    out.to_csv(path, index=False)
    logger.info("Saved %d filtered detections to %s", len(out), path)


def poll_and_alert():
    """Main loop: poll FIRMS → filter by FWI → detect new alerts → notify."""
    config = load_config()
    poll_interval = config.get("POLL_INTERVAL_MINUTES", 60)

    logger.info("Starting Vigil worker loop (poll_interval: %d minutes)", poll_interval)

    while True:
        try:
            logger.info("Polling FIRMS for new detections...")
            fires = fetch_fires(config)

            if _HAS_FWI and not fires.empty:
                fires = apply_fwi_filter(fires, config)

            # Persist the full filtered/tiered dataset for the dashboard.
            _save_filtered_fires(fires, LATEST_FIRES_PATH)

            # Filter: only process hotspots that pass the FWI filter
            if "fwi_show" in fires.columns:
                fires_to_process = fires[fires["fwi_show"] == True]
                filtered_count = len(fires) - len(fires_to_process)
                if filtered_count > 0:
                    logger.info(
                        "FWI filter suppressed %d false positives",
                        filtered_count,
                    )
            else:
                fires_to_process = fires

            new_alerts = detect_new_alerts(
                fires_to_process,
                config,
                state_path=str(STATE_PATH),
            )

            if new_alerts:
                logger.info(
                    "Detected %d new alerts. Dispatching notifications...",
                    len(new_alerts),
                )
                dispatch_notifications(new_alerts, config)
            else:
                logger.info("No new alerts detected.")

        except Exception as e:
            logger.error("Error in worker loop: %s", e, exc_info=True)

        logger.info("Sleeping for %d minutes...", poll_interval)
        time.sleep(poll_interval * 60)


if __name__ == "__main__":
    poll_and_alert()
"""
Hestia Vigil — Background Worker and Scheduler
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime
from typing import Any

from config import load_config
from firms_client import fetch_fires
from alerts import detect_new_alerts
from notifiers import dispatch_notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

def poll_and_alert():
    """
    The main loop for the Vigil worker.
    Polls FIRMS data, detects new alerts, and dispatches notifications.
    """
    config = load_config()
    poll_interval = config.get("POLL_INTERVAL_MINUTES", 60)
    state_path = "/Users/AlexHollema/Hestia/Vigil/state/alert_history.json"
    
    logger.info("Starting Vigil worker loop (poll_interval: %d minutes)", poll_interval)
    
    while True:
        try:
            logger.info("Polling FIRMS for new detections...")
            fires = fetch_fires(config)
            
            new_alerts = detect_new_alerts(
                fires, 
                config, 
                state_path=state_path
            )
            
            if new_alerts:
                logger.info("Detected %d new alerts. Dispatching notifications...", len(new_alerts))
                dispatch_notifications(new_alerts, config)
            else:
                logger.info("No new alerts detected.")
                
        except Exception as e:
            logger.error("Error in worker loop: %s", e, exc_info=True)
        
        logger.info("Sleeping for %d minutes...", poll_interval)
        time.sleep(poll_interval * 60)

if __name__ == "__main__":
    # In a production environment, this would be managed by a process manager like systemd 
    # or a dedicated scheduler (e.g. APScheduler, Celery).
    # For this implementation, we use a simple time.sleep loop.
    poll_and_alert()

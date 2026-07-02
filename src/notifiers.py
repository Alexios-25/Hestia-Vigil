"""
Hestia Vigil — Alert Notification System
"""

from __future__ import annotations

import logging
import smtplib
import requests
from email.message import EmailMessage
from typing import List, Any

from config import load_config
from alerts import Alert, format_alert_message

logger = logging.getLogger(__name__)

def send_telegram_notifications(alerts: List[Alert], config: dict[str, Any]) -> None:
    """Sends notifications to a Telegram webhook."""
    alerts_cfg = config.get("ALERTS", {}).get("telegram_webhook", {})
    enabled = alerts_cfg.get("enabled", False)
    webhook_env_key = alerts_cfg.get("webhook_env")
    webhook_url = alerts_cfg.get("webhook_url")

    if not webhook_url and webhook_env_key:
        import os
        webhook_url = os.getenv(webhook_env_key)

    if not enabled or not webhook_url:
        logger.debug("Telegram notifications disabled or webhook URL missing.")
        return

    for alert in alerts:
        message = format_alert_message(alert)
        try:
            response = requests.post(webhook_url, json={"text": message}, timeout=10)
            response.raise_for_status()
            logger.info("Sent Telegram notification for alert: %s", alert.key)
        except Exception as e:
            logger.error("Failed to send Telegram notification: %s", e)

def send_email_notifications(alerts: List[Alert], config: dict[str, Any]) -> None:
    """Sends notifications via SMTP."""
    alerts_cfg = config.get("ALERTS", {}).get("email", {})
    enabled = alerts_cfg.get("enabled", False)

    if not enabled:
        logger.debug("Email notifications disabled.")
        return

    smtp_host = alerts_cfg.get("smtp_host")
    smtp_port = alerts_cfg.get("smtp_port")
    smtp_user = alerts_cfg.get("smtp_user")
    smtp_pass = alerts_cfg.get("smtp_password")
    to_address = alerts_cfg.get("to_address")

    if not all([smtp_host, smtp_user, smtp_pass, to_address]):
        logger.error("Email config missing one or more required fields: host, user, pass, or to_address")
        return

    for alert in alerts:
        message = EmailMessage()
        message["From"] = smtp_user
        message["To"] = to_address
        message["Subject"] = f"🔥 Hestia.Vigil Alert: {alert.zone_label}"
        message.set_content(format_alert_message(alert))

        try:
            with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(message)
            logger.info("Sent email notification for alert: %s", alert.key)
        except Exception as e:
            logger.error("Failed to send email notification: %s", e)

def dispatch_notifications(alerts: List[Alert], config: dict[str, Any]) -> None:
    """Dispatches notifications to all enabled channels."""
    if not alerts:
        return

    send_telegram_notifications(alerts, config)
    send_email_notifications(alerts, config)

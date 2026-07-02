"""
Hestia Vigil — Configuration and secret loading.

Keeps non-secret project settings in config.yaml and loads private values from
environment variables, normally provided by the local .env file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"
_ENV_FILE_PATH = _PROJECT_ROOT / ".env"


def _is_placeholder(value: Any) -> bool:
    """Return True if a config value is empty or an obvious placeholder."""
    if value is None:
        return True

    text = str(value).strip()
    if not text:
        return True

    lowered = text.lower()
    return (
        text.startswith("YOUR_")
        or lowered in {"placeholder", "changeme", "change_me", "todo", "none"}
        or lowered.endswith("your_webhook")
        or lowered.endswith("your_app_password")
    )


def _secret_from_config_or_env(
    config: dict[str, Any],
    yaml_key: str,
    env_var: str,
    description: str,
) -> str:
    """Resolve a secret from env first, then legacy config, then raise."""
    env_value = os.getenv(env_var)
    if env_value and not _is_placeholder(env_value):
        return env_value.strip()

    config_value = config.get(yaml_key)
    if config_value and not _is_placeholder(config_value):
        logger.warning(
            "%s was found in config.yaml. Prefer setting %s in .env instead.",
            yaml_key,
            env_var,
        )
        return str(config_value).strip()

    raise ValueError(
        f"{env_var} is required for {description}. "
        f"Set it in .env, or temporarily keep {yaml_key} in config.yaml."
    )


def _resolve_alert_secrets(config: dict[str, Any]) -> None:
    """Populate alert credential fields from configured environment variables."""
    alerts = config.setdefault("ALERTS", {})
    if not isinstance(alerts, dict):
        return

    # Telegram webhook — resolve webhook_env if set
    telegram = alerts.setdefault("telegram_webhook", {})
    if isinstance(telegram, dict) and telegram.get("webhook_env"):
        telegram["webhook_url"] = os.getenv(telegram["webhook_env"], "")

    # Discord webhook (legacy — kept for compatibility)
    discord = alerts.setdefault("discord_webhook", {})
    if isinstance(discord, dict) and discord.get("webhook_env"):
        discord["webhook_url"] = os.getenv(discord["webhook_env"], "")

    email = alerts.setdefault("email", {})
    if isinstance(email, dict):
        if email.get("smtp_user_env"):
            email["smtp_user"] = os.getenv(email["smtp_user_env"], "")
        if email.get("smtp_password_env"):
            email["smtp_password"] = os.getenv(email["smtp_password_env"], "")


def _validate_config(config: dict[str, Any]) -> None:
    """Validate core Vigil settings after secrets are resolved."""
    required = ["SOURCE", "BBOX"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Missing required config key(s): {', '.join(missing)}")

    bbox = config["BBOX"]
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("BBOX must be a list of four values: [min_lon, min_lat, max_lon, max_lat]")

    poll_interval = config.get("POLL_INTERVAL_MINUTES", 60)
    try:
        if int(poll_interval) <= 0:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("POLL_INTERVAL_MINUTES must be a positive integer") from exc


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load Vigil config.yaml and resolve secrets from .env/environment.

    Parameters
    ----------
    path : str or Path, optional
        Path to a YAML config file. Defaults to ``<project_root>/config.yaml``.

    Returns
    -------
    dict
        Parsed YAML configuration with secret values resolved into the same
        keys that the rest of Vigil expects.

    Raises
    ------
    FileNotFoundError
        If the config file does not exist.
    ValueError
        If required settings or secrets are missing.
    yaml.YAMLError
        If the file is not valid YAML.
    """
    load_dotenv(_ENV_FILE_PATH)

    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    logger.info("Loading config from %s", config_path)

    with open(config_path, "r") as fh:
        config = yaml.safe_load(fh) or {}

    if not isinstance(config, dict):
        raise ValueError("config.yaml must contain a YAML mapping")

    config["MAP_KEY"] = _secret_from_config_or_env(
        config,
        "MAP_KEY",
        "NASA_FIRMS_MAP_KEY",
        "the NASA FIRMS API client",
    )
    _resolve_alert_secrets(config)
    _validate_config(config)

    return config

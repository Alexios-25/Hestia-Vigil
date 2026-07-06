"""
Hestia.Index — Open-Meteo Weather Client
=========================================

Wraps the Open-Meteo forecast and archive APIs to return exactly the
weather variables needed by the FWI calculator — no unit conversions
required since Open-Meteo natively returns °C, %, km/h, and mm.

Two API endpoints are used:

  - **Forecast API** (api.open-meteo.com/v1/forecast)
    Live forecast data for the current day. We extract the noon-hour
    readings for temperature, humidity, wind speed, and the daily
    precipitation_sum for the 24-hour accumulation.

  - **Archive API** (archive-api.open-meteo.com/v1/archive)
    Historical ERA5-based hourly data back to 1940. Used to "hot-start"
    the FWI state (FFMC/DMC/DC) by running N days of sequential FWI
    calculations on historical weather before combining with live data.

Usage:
    from weather_client import OpenMeteoClient

    client = OpenMeteoClient()
    weather = client.get_noon_weather(lat=39.0, lon=-105.0)

    # For FWI spin-up:
    history = client.get_noon_history(lat=39.0, lon=-105.0, days=14)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import time
import random
from threading import Lock

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter (respect Open-Meteo free tier: 600 calls/min)
# ---------------------------------------------------------------------------

_RATE_LIMIT_LOCK = Lock()
_RATE_WINDOW: list[float] = []  # timestamps of recent API calls
_RATE_MAX_PER_MIN = 500  # stay safely under 600/min


def _check_rate_limit() -> None:
    """Block until we can make an API call without exceeding the rate limit.

    Simple sliding-window rate limiter. Keeps track of call timestamps
    and sleeps if we'd exceed ``_RATE_MAX_PER_MIN`` calls in the last 60s.
    """
    global _RATE_WINDOW
    with _RATE_LIMIT_LOCK:
        now = time.monotonic()
        # Prune timestamps older than 60 seconds
        cutoff = now - 60.0
        _RATE_WINDOW = [t for t in _RATE_WINDOW if t > cutoff]

        if len(_RATE_WINDOW) >= _RATE_MAX_PER_MIN:
            # Sleep until the oldest timestamp falls out of the window
            sleep_for = max(0.0, _RATE_WINDOW[0] + 60.0 - now)
            if sleep_for > 0:
                logger.debug(
                    "Rate limit: sleeping %.1fs (%d calls in last 60s)",
                    sleep_for, len(_RATE_WINDOW),
                )
                time.sleep(sleep_for)
            # Re-prune after sleeping
            now = time.monotonic()
            cutoff = now - 60.0
            _RATE_WINDOW = [t for t in _RATE_WINDOW if t > cutoff]

        _RATE_WINDOW.append(now)


def _rate_limited_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    max_retries: int = 3,
) -> requests.Response | None:
    """Make a rate-limited HTTP GET with retry on 503/429.

    Returns the response on success, or None if all retries exhausted.
    """
    for attempt in range(max_retries):
        _check_rate_limit()
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code in (429, 503):
                wait = 2.0 ** attempt + random.uniform(0, 1.0)
                logger.warning(
                    "API rate-limited (HTTP %d). Retrying in %.1fs...",
                    resp.status_code, wait,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.Timeout:
            logger.warning("Timeout on attempt %d/%d", attempt + 1, max_retries)
            if attempt < max_retries - 1:
                time.sleep(1.0)
        except requests.RequestException as e:
            logger.error("Request failed: %s", e)
            return None
    return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoonWeather:
    """Weather observations at noon local time for a single day.

    All fields match FWI input requirements directly — no unit conversion.
    """

    date: str           # ISO date string (e.g. "2026-07-02")
    temp_c: float       # Temperature (°C)
    rh_pct: float       # Relative humidity (%)
    wind_kmh: float     # Wind speed (km/h)
    precip_mm: float    # 24-hour precipitation accumulation (mm)


# ---------------------------------------------------------------------------
# API base URLs
# ---------------------------------------------------------------------------

_FORECAST_BASE = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_BASE = "https://archive-api.open-meteo.com/v1/archive"

# Default headers — no API key needed for the free tier
_HEADERS = {
    "User-Agent": "Hestia.Vigil/1.0 (wildfire-monitoring; contact@hestia.dev)",
    "Accept": "application/json",
}

# Timeout for HTTP requests (Open-Meteo is generally fast)
_REQUEST_TIMEOUT = 15


def _midnight_noon_for_tz(lat: float, lon: float) -> tuple[str, str, str]:
    """Return (today_str, noon_hour, timezone) for the given coordinates.

    We use the America/Denver timezone as default since Hestia currently
    focuses on the western US. The noon hour is either 12 or 13 depending
    on whether daylight saving is in effect — we always fetch both and
    let the caller pick the right one.

    Parameters
    ----------
    lat, lon : float
        Coordinates of the watch zone centre.

    Returns
    -------
    tuple[str, str, str]
        (today_date_string, "12,13" for noon hours, timezone_name)
    """
    # For now, pin to Mountain Time since Hestia focuses on western US.
    # In a future version, auto-detect timezone from coordinates using
    # the Open-Meteo Geocoding API or a local timezone database.
    tz_name = "America/Denver"

    today = date.today()
    today_str = today.isoformat()

    # Fetch both 12:00 and 13:00 (the correct noon depends on DST).
    # The caller can inspect the timestamps to pick the right one.
    noon_hours = "12,13"

    return today_str, noon_hours, tz_name


def _parse_noon_weather(
    hourly: dict[str, list],
    daily: dict[str, list] | None,
    noon_hour: str,
) -> NoonWeather | None:
    """Extract the noon-hour weather from Open-Meteo's hourly response.

    Parameters
    ----------
    hourly : dict
        Open-Meteo hourly response dict with keys ``time``,
        ``temperature_2m``, ``relative_humidity_2m``, ``wind_speed_10m``,
        ``precipitation``.
    daily : dict or None
        Open-Meteo daily response dict with key ``precipitation_sum``.
        Falls back to summing hourly precip if not available.
    noon_hour : str
        The noon hour string to match (e.g. "12" or "13").

    Returns
    -------
    NoonWeather or None
        None if the noon data point is missing.
    """
    times = hourly.get("time", [])

    # Find the index whose time column contains noon
    target_suffix = f"T{noon_hour}:00"
    noon_idx = None
    for i, t in enumerate(times):
        if target_suffix in t:
            noon_idx = i
            break

    if noon_idx is None:
        logger.warning("No noon data point (%s) found in hourly response", noon_hour)
        return None

    date_str = times[noon_idx][:10]

    temp = hourly["temperature_2m"][noon_idx]
    rh = hourly["relative_humidity_2m"][noon_idx]
    wind = hourly["wind_speed_10m"][noon_idx]
    hourly_precip = hourly["precipitation"][noon_idx]

    # 24-hour precipitation: prefer daily precipitation_sum
    precip = None
    if daily and "precipitation_sum" in daily:
        daily_times = daily.get("time", [])
        daily_precip = daily.get("precipitation_sum", [])
        for dt, dp in zip(daily_times, daily_precip):
            if dt == date_str:
                precip = dp
                break

    # Fallback: sum hourly precip for the last 24 hours
    if precip is None:
        start = max(0, noon_idx - 24)
        precip = sum(hourly["precipitation"][start:noon_idx + 1])

    return NoonWeather(
        date=date_str,
        temp_c=float(temp),
        rh_pct=float(rh),
        wind_kmh=float(wind),
        precip_mm=float(precip) if precip is not None else 0.0,
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OpenMeteoClient:
    """Fetch weather data from Open-Meteo for FWI calculation.

    All methods return data in units compatible with ``fwi_daily()``:
    °C, %, km/h, mm. No unit conversion needed.
    """

    def __init__(self, timeout: int = _REQUEST_TIMEOUT):
        self._timeout = timeout

    # -- Public API --------------------------------------------------------

    def get_noon_weather(
        self, lat: float, lon: float,
    ) -> NoonWeather | None:
        """Fetch today's noon weather for a location.

        This is the primary method used during live polling. It makes ONE
        API call to the forecast endpoint.

        Parameters
        ----------
        lat, lon : float
            Coordinates.

        Returns
        -------
        NoonWeather or None
            The noon-hour weather, or None on error.
        """
        today_str, noon_hours, tz = _midnight_noon_for_tz(lat, lon)
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
            "daily": "precipitation_sum",
            "timezone": tz,
            "forecast_days": 1,
        }

        try:
            resp = _rate_limited_get(
                _FORECAST_BASE,
                params=params,
                headers=_HEADERS,
                timeout=self._timeout,
            )
            if resp is None:
                return None
            data = resp.json()
        except requests.RequestException as e:
            logger.error("Open-Meteo forecast request failed for %s,%s: %s", lat, lon, e)
            return None

        hourly = data.get("hourly", {})
        daily = data.get("daily", {})

        # Try 12:00 first; fall back to 13:00 (DST offset)
        for hour in ("12", "13"):
            result = _parse_noon_weather(hourly, daily, hour)
            if result is not None:
                return result

        logger.warning("No noon data returned for %s,%s", lat, lon)
        return None

    def get_noon_history(
        self, lat: float, lon: float, days: int = 14,
    ) -> list[NoonWeather]:
        """Fetch historical noon weather for the past N days.

        Used to "hot-start" the FWI state for a new location by feeding
        sequential weather records through the FWI calculator.

        Makes ONE API call to the archive endpoint regardless of the
        number of days requested.

        Parameters
        ----------
        lat, lon : float
            Coordinates.
        days : int
            Number of past days to fetch. 14 is typical for FWI spin-up.

        Returns
        -------
        list[NoonWeather]
            One entry per day, oldest first. Empty on error.
        """
        today = date.today()
        start = today - timedelta(days=days)
        _, noon_hours, tz = _midnight_noon_for_tz(lat, lon)

        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": today.isoformat(),
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
            "daily": "precipitation_sum",
            "timezone": tz,
        }

        try:
            resp = _rate_limited_get(
                _ARCHIVE_BASE,
                params=params,
                headers=_HEADERS,
                timeout=self._timeout,
            )
            if resp is None:
                return []
            data = resp.json()
        except requests.RequestException as e:
            logger.error(
                "Open-Meteo archive request failed for %s,%s: %s", lat, lon, e
            )
            return []

        hourly = data.get("hourly", {})
        daily = data.get("daily", {})

        results: list[NoonWeather] = []

        # Collect unique noon timestamps across the date range
        # (each day has a 12:00 and/or 13:00 slot)
        times = hourly.get("time", [])
        noon_indices: dict[str, int] = {}
        for i, t in enumerate(times):
            if "T12:00" in t or "T13:00" in t:
                day_key = t[:10]
                # Prefer 12:00 over 13:00 (standard time vs DST)
                if day_key not in noon_indices or "T12:00" in t:
                    noon_indices[day_key] = i

        for day_key, idx in sorted(noon_indices.items()):
            temp = hourly["temperature_2m"][idx]
            rh = hourly["relative_humidity_2m"][idx]
            wind = hourly["wind_speed_10m"][idx]
            hourly_precip = hourly["precipitation"][idx]

            # Prefer daily precipitation_sum
            precip = None
            if daily and "precipitation_sum" in daily:
                daily_times = daily.get("time", [])
                daily_precip = daily.get("precipitation_sum", [])
                for dt, dp in zip(daily_times, daily_precip):
                    if dt == day_key:
                        precip = dp
                        break

            if precip is None:
                # Fallback: sum 24h around this noon slot
                start = max(0, idx - 12)
                end = min(len(times), idx + 12)
                precip = sum(hourly["precipitation"][start:end])

            results.append(NoonWeather(
                date=day_key,
                temp_c=float(temp),
                rh_pct=float(rh),
                wind_kmh=float(wind),
                precip_mm=float(precip) if precip is not None else 0.0,
            ))

        return results

    def get_total_precip_24h(
        self, lat: float, lon: float,
    ) -> float | None:
        """Quick helper: get just the 24-hour precipitation total."""
        weather = self.get_noon_weather(lat, lon)
        if weather is None:
            return None
        return weather.precip_mm


# ---------------------------------------------------------------------------
# Convenience: single-shot weather → FWI
# ---------------------------------------------------------------------------


def fwi_from_location(
    lat: float,
    lon: float,
    client: OpenMeteoClient | None = None,
    history_days: int = 14,
    month: int | None = None,
) -> dict[str, Any] | None:
    """Fetch weather for a location and compute today's FWI in one call.

    This is the highest-level convenience function — pass a lat/lon, get
    back the full FWI result plus today's weather.

    Parameters
    ----------
    lat, lon : float
        Coordinates.
    client : OpenMeteoClient, optional
        Reusable client instance. Creates a new one if not provided.
    history_days : int
        Days of history used to hot-start the FWI state. Default 14.
    month : int, optional
        Month number (1-12). Auto-detected from today's date if not given.

    Returns
    -------
    dict or None
        ``{weather: NoonWeather, fwi: dict, danger: str}``
        or None on error.
    """
    # Late import to avoid circular dependency
    from fwi_calculator import fwi_daily, FWIResult  # noqa: E402

    client = client or OpenMeteoClient()
    today_weather = client.get_noon_weather(lat, lon)
    if today_weather is None:
        return None

    # Spin up FWI state from history
    history = client.get_noon_history(lat, lon, days=history_days)
    curr_month = month or date.today().month

    ffmc_p = 85.0
    dmc_p = 6.0
    dc_p = 15.0

    for hw in history:
        # Estimate month from the historical date
        try:
            h_month = int(hw.date.split("-")[1])
        except (IndexError, ValueError):
            h_month = curr_month

        r = fwi_daily(
            temp=hw.temp_c,
            rh=hw.rh_pct,
            wind=hw.wind_kmh,
            rain=hw.precip_mm,
            prev_ffmc=ffmc_p,
            prev_dmc=dmc_p,
            prev_dc=dc_p,
            lat=lat,
            month=h_month,
        )
        ffmc_p, dmc_p, dc_p = r.ffmc, r.dmc, r.dc

    # Today's FWI
    result = fwi_daily(
        temp=today_weather.temp_c,
        rh=today_weather.rh_pct,
        wind=today_weather.wind_kmh,
        rain=today_weather.precip_mm,
        prev_ffmc=ffmc_p,
        prev_dmc=dmc_p,
        prev_dc=dc_p,
        lat=lat,
        month=curr_month,
    )

    return {
        "weather": {
            "date": today_weather.date,
            "temp_c": today_weather.temp_c,
            "rh_pct": today_weather.rh_pct,
            "wind_kmh": today_weather.wind_kmh,
            "precip_mm": today_weather.precip_mm,
        },
        "ffmc": result.ffmc,
        "dmc": result.dmc,
        "dc": result.dc,
        "isi": result.isi,
        "bui": result.bui,
        "fwi": result.fwi,
        "danger": result.danger_rating(),
    }
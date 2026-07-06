"""
Hestia.Index — Canadian Fire Weather Index (FWI) Calculator
=============================================================

A pure-Python implementation of the six-component Canadian Forest Fire
Weather Index (FWI) System, as published in:

    Van Wagner, C.E. (1987). Development and Structure of the Canadian
    Forest Fire Weather Index System. Forestry Technical Report 35.
    Canadian Forestry Service, Ottawa, ON.

The system takes weather observations (noon local time) and produces
numeric indexes that rate fire danger. Each component builds on the
previous ones:

    ┌─────────────────────────────────────────────────────┐
    │                  FWI (Fire Weather Index)           │
    │                     ┌─────┴─────┐                   │
    │                   ISI          BUI                  │
    │                    │         ┌──┴──┐                │
    │                  FFMC      DMC    DC                │
    │                 (surface)  (duff) (deep)            │
    └─────────────────────────────────────────────────────┘
    Inputs:  Temp (°C) · RH (%) · Wind (km/h) · Rain (mm)

Usage:
    from fwi_calculator import fwi_daily
    result = fwi_daily(temp=25.0, rh=35, wind=15, rain=0.0,
                       prev_ffmc=85.0, prev_dmc=6.0, prev_dc=15.0,
                       lat=45.0, month=7)
    # result = {'ffmc': ..., 'dmc': ..., 'dc': ..., 'isi': ...,
    #           'bui': ..., 'fwi': ...}
"""

from __future__ import annotations

import math
from typing import Any

# =========================================================================
# INTERNAL MOISTURE SCALE
# =========================================================================
#
# The FFMC uses an internal "moisture-equivalent" variable (m) that ranges
# from ~250 (saturated) down to 0 (bone-dry). The 0-101 code we output is
# a transformation:
#
#   m = 147.2 * (101 - FFMC) / (59.5 + FFMC)
#   FFMC = 59.5 * (250 - m) / (147.2 + m)
#
# When FFMC = 101 (bone-dry):  m = 0
# When FFMC = 0   (saturated): m ≈ 250
# When FFMC = 85  (typical):   m ≈ 16.3
#
# ALL internal calculations (rain wetting, equilibrium, drying) operate
# on the m-scale. Only the final output is converted back to 0-101.

def _ffmc_to_m(ffmc: float) -> float:
    """Convert an FFMC code value (0-101) to moisture-equivalent m.

    High FFMC = dry = low m. Low FFMC = wet = high m.
    """
    return 147.2 * (101.0 - ffmc) / (59.5 + ffmc)


def _m_to_ffmc(m: float) -> float:
    """Convert moisture-equivalent m back to the 0-101 FFMC code.

    Clamps the result to [0, 101].
    """
    ffmc = 59.5 * (250.0 - m) / (147.2 + m)
    return max(0.0, min(ffmc, 101.0))


# =========================================================================
# CONSTANTS: Day-length adjustment factors
# =========================================================================
#
# These latitude-and-month tables account for the varying number of daylight
# hours across the fire season. DMC and DC each have their own table because
# their drying depends differently on day length.
#
# Source: Van Wagner (1987), Tables 2 & 3.

_LAT_TABLES = [
    # (min_lat, max_lat, DMC_monthly, DC_monthly)
    # Each row is [Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec]
    (0, 20,  [-1.6, -1.6, -1.6, -0.8, 1.4, 4.2, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6],
            [-0.8, -0.6, 0.1, 0.8, 1.5, 1.7, 1.8, 1.7, 1.5, 0.9, 0.2, -0.5]),
    (20, 30, [-1.6, -1.6, -1.6, -0.8, 1.4, 4.2, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6],
            [-0.8, -0.6, 0.1, 0.8, 1.5, 1.7, 1.8, 1.7, 1.5, 0.9, 0.2, -0.5]),
    (30, 40, [-3.2, -3.2, -3.2, -1.6, 2.8, 6.8, 10.1, 7.2, 4.0, 0.0, -3.2, -3.2],
            [-1.6, -1.3, 0.1, 1.6, 3.1, 3.5, 3.8, 3.5, 3.1, 1.7, 0.3, -1.0]),
    (40, 50, [-3.2, -3.2, -3.2, -1.6, 2.8, 6.8, 10.1, 7.2, 4.0, 0.0, -3.2, -3.2],
            [-1.6, -1.3, 0.1, 1.6, 3.1, 3.5, 3.8, 3.5, 3.1, 1.7, 0.3, -1.0]),
    (50, 60, [-3.2, -3.2, -3.2, -1.6, 2.8, 6.8, 10.1, 7.2, 4.0, 0.0, -3.2, -3.2],
            [-1.6, -1.3, 0.1, 1.6, 3.1, 3.5, 3.8, 3.5, 3.1, 1.7, 0.3, -1.0]),
    (60, 90, [-3.2, -3.2, -3.2, -1.6, 2.8, 6.8, 10.1, 7.2, 4.0, 0.0, -3.2, -3.2],
            [-1.6, -1.3, 0.1, 1.6, 3.1, 3.5, 3.8, 3.5, 3.1, 1.7, 0.3, -1.0]),
]


def _day_length_factor(lat: float, month: int) -> tuple[float, float]:
    """Return (dmc_factor, dc_factor) for a given latitude and month (1-12)."""
    idx = month - 1
    abs_lat = abs(lat)
    for lo, hi, dmc_f, dc_f in _LAT_TABLES:
        if lo <= abs_lat < hi:
            return dmc_f[idx], dc_f[idx]
    last = _LAT_TABLES[-1]
    return last[2][idx], last[3][idx]


# =========================================================================
# COMPONENT 1: FFMC — Fine Fuel Moisture Code
# =========================================================================
#
# Represents the moisture content of surface litter (~1-2 cm deep).
# Range: 0 (saturated) to 101 (bone dry).
#
# The FFMC is the most responsive component — it reacts within hours to
# rain and drying. Internally, all physics work on the moisture-equivalent
# m-scale (~0 to ~250), then transform back to the 0-101 output code.


def ffmc(
    temp: float,
    rh: float,
    wind: float,
    rain: float,
    prev_ffmc: float,
) -> float:
    """Compute the Fine Fuel Moisture Code.

    Parameters
    ----------
    temp : float
        Noon temperature in °C.
    rh : float
        Noon relative humidity in %.
    wind : float
        Noon wind speed in km/h (clamped to [0, 200]).
    rain : float
        24-hour precipitation in mm.
    prev_ffmc : float
        Previous day's FFMC (0-101). Typical fire-season start: 85.

    Returns
    -------
    float
        Current day's FFMC (0-101). Higher = drier = more fire-prone.

    Notes
    -----
    The calculation has three phases:
    1. Convert prev_ffmc (0-101) → internal moisture-equivalent m (0-250)
    2. Apply rain wetting on m (if rain > 0.5 mm)
    3. Dry the fuel on m toward the equilibrium moisture content
    4. Convert m back → FFMC code (0-101)
    """
    wind = max(0.0, min(wind, 200.0))
    rh = max(0.0, min(rh, 100.0))
    rain = max(0.0, rain)

    # --- Step 1: Convert previous FFMC to moisture-equivalent m ---
    m_o = _ffmc_to_m(prev_ffmc)

    # --- Step 2: Rain wetting (m-scale) ---
    if rain > 0.5:
        r_f = rain - 0.5  # effective rain after canopy interception
        # The wetting rate depends on how dry the fuel is and the
        # amount of rain. 42.5 and 6.93 are empirical constants from
        # Van Wagner (1987).
        term = m_o + 42.5 * r_f * math.exp(-100.0 / (251.0 - m_o)) * (
            1.0 - math.exp(-6.93 / r_f)
        )
        m_o = min(term, 250.0)  # cap at saturation

    # --- Step 3: Equilibrium moisture content (m-scale) ---
    # Equilibrium is the moisture level the fuel would reach if exposed
    # to the current temperature and humidity indefinitely.
    if m_o <= 150:
        ed = (0.942 * (rh ** 0.679)
              + 11.0 * math.exp((rh - 100.0) / 10.0)
              + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh)))
    else:
        # Wet-fuel regime — different equilibrium constants
        ed = (0.618 * (rh ** 0.753)
              + 10.0 * math.exp((rh - 100.0) / 10.0)
              + 0.18 * (21.1 - temp) * (1.0 - math.exp(-0.115 * rh)))

    # --- Step 4: Drying/wetting toward equilibrium ---
    # The drying rate (ko) depends on temperature, humidity, and wind.
    ko = 0.424 * (1.0 - (rh / 100.0) ** 1.7) * (
        0.0694 * math.sqrt(wind) * (1.0 - (rh / 100.0) ** 8)
    )
    ko *= 0.581 * math.exp(0.0365 * temp)

    # m approaches ed at rate ko
    m_o = m_o - ko * (m_o - ed)

    # --- Step 5: Convert back to FFMC code ---
    return _m_to_ffmc(m_o)


# =========================================================================
# COMPONENT 2: DMC — Duff Moisture Code
# =========================================================================
#
# Represents moisture in the duff layer (~5-10 cm deep). Dries slower
# than FFMC because it's deeper and more sheltered. Range: 0+ (no upper
# bound).


def dmc(
    temp: float,
    rh: float,
    rain: float,
    prev_dmc: float,
    lat: float,
    month: int,
) -> float:
    """Compute the Duff Moisture Code.

    Parameters
    ----------
    temp : float
        Noon temperature in °C.
    rh : float
        Noon relative humidity in %.
    rain : float
        24-hour precipitation in mm.
    prev_dmc : float
        Previous day's DMC. Typical spring start: 6.0.
    lat : float
        Latitude in degrees.
    month : int
        Month (1-12).

    Returns
    -------
    float
        Current day's DMC. Higher = drier duff = more fire-prone.
    """
    temp = max(-1.1, temp)  # no drying below this threshold
    rh = max(0.0, min(rh, 100.0))
    rain = max(0.0, rain)

    mo = prev_dmc
    le_dmc, _ = _day_length_factor(lat, month)

    # --- Rain effect ---
    # Rain ADDS moisture to the duff, which REDUCES the DMC (drives it
    # toward 0 = saturated). The Van Wagner equations are often written
    # as DMC = P + Δ, where Δ is the moisture-accounting term — but on
    # the DMC dryness scale (higher = drier), this must be a subtraction.
    if rain > 1.5:
        r_e = 0.92 * rain - 1.27  # effective rain after litter interception
        mo -= 100.0 * r_e / (48.77 + 3.94 * r_e)

    # --- Drying ---
    # Drying INCREASES DMC (makes it drier). The rate depends on
    # temperature, humidity deficit, and day length.
    if temp > -1.1:
        r = (1.894 * (temp + 1.1)
             * (100.0 - rh)
             * le_dmc
             * 0.0001)
        mo += r

    return max(0.0, mo)


# =========================================================================
# COMPONENT 3: DC — Drought Code
# =========================================================================
#
# Represents moisture in the deep organic layer (~10-20 cm). This is the
# slowest-changing component — reflects long-term drought. Range: 0+.


def dc(
    temp: float,
    rain: float,
    prev_dc: float,
    lat: float,
    month: int,
) -> float:
    """Compute the Drought Code.

    Parameters
    ----------
    temp : float
        Noon temperature in °C.
    rain : float
        24-hour precipitation in mm.
    prev_dc : float
        Previous day's DC. Typical spring start: 15.0.
    lat : float
        Latitude in degrees.
    month : int
        Month (1-12).

    Returns
    -------
    float
        Current day's DC. Higher = deeper drought = more fuel available.
    """
    temp = max(0.0, temp)
    rain = max(0.0, rain)

    mo = prev_dc
    _, le_dc = _day_length_factor(lat, month)

    # --- Rain effect ---
    # Rain ADDS moisture to the deep layer, REDUCING DC (driving it
    # toward 0 = saturated). The deep layer has a higher interception
    # threshold (2.8 mm) and a lower rain efficiency (0.83) than the
    # duff layer.
    if rain > 2.8:
        r_e = 0.83 * rain - 1.27
        mo -= 400.0 * math.log(1.0 + 3.937 * r_e / 400.0)

    # --- Drying ---
    # DC drying is driven only by temperature and day-length factor.
    # The "effective evapotranspiration" V represents potential water loss.
    if temp > 0.0:
        v = 0.36 * (temp + 2.8) + le_dc
        v = max(v, 0.0)
        mo += v / 2.0

    return max(0.0, mo)


# =========================================================================
# COMPONENT 4: ISI — Initial Spread Index
# =========================================================================
#
# Combines FFMC (fuel dryness) with wind to estimate fire spread rate
# immediately after ignition. No slope, no fuel-quantity effects — those
# come from BUI.
#
# Source: Van Wagner (1987), Section 4.1, Equations 20-23.


def isi(wind: float, ffmc_val: float) -> float:
    """Compute the Initial Spread Index.

    Parameters
    ----------
    wind : float
        Noon wind speed in km/h (10 m height).
    ffmc_val : float
        Current FFMC value (0-101). Drier = higher = faster spread.

    Returns
    -------
    float
        ISI — expected rate of fire spread. Higher = faster.
    """
    wind = max(0.0, min(wind, 200.0))

    # CRITICAL: The ISI equations operate on the moisture-equivalent value
    # (``m``), NOT the 0-101 FFMC code. The ``m`` value ranges from ~250
    # (saturated) to 0 (bone dry); the FFMC code transforms this to a
    # 0-101 scale. Converting back keeps the exponential term
    # exp(-0.1386 * m) in a reasonable range.
    #
    # When FFMC = 85 (typical dry-season start):
    #   m ≈ 16.3 → exp(-0.1386 * 16.3) = exp(-2.26) = 0.1045  ✓
    # vs. using the FFMC code directly:
    #   exp(-0.1386 * 85) = exp(-11.78) = 7.1e-6  ✗ (wrong by 4 orders of mag)
    m = _ffmc_to_m(ffmc_val)

    # Fuel-moisture function f(F): converts fuel moisture to a spread-rate
    # factor. Drier (lower m) → faster spread.
    # (Equation 20-21 in Van Wagner 1987)
    f_F = (91.9 * math.exp(-0.1386 * m)
           * (1.0 + m ** 1.5 / 79.9))

    # Wind function f(W): wind increases spread exponentially.
    # 0.05039 is the empirically-determined wind coefficient.
    # (Equation 22 in Van Wagner 1987)
    f_W = math.exp(0.05039 * wind)

    # ISI = 0.208 * f(F) * f(W)
    # 0.208 is the overall scaling constant.
    # (Equation 23 in Van Wagner 1987)
    return 0.208 * f_F * f_W


# =========================================================================
# COMPONENT 5: BUI — Buildup Index
# =========================================================================
#
# Combines DMC and DC to estimate the total amount of fuel available for
# combustion. High BUI = deep-burning, sustained fire.
#
# Source: Van Wagner (1987), Section 4.2, Equations 24-25.


def bui(dmc_val: float, dc_val: float) -> float:
    """Compute the Buildup Index.

    Parameters
    ----------
    dmc_val : float
        Current DMC.
    dc_val : float
        Current DC.

    Returns
    -------
    float
        BUI — total fuel available to burn.
    """
    if dmc_val <= 0 and dc_val <= 0:
        return 0.0

    if dmc_val <= 0.4 * dc_val:
        # DMC is small relative to DC — duff moisture dominates
        p = 0.8 * dmc_val * dc_val / (dmc_val + 0.4 * dc_val)
    else:
        # DMC is significant — both layers contribute
        p = dmc_val - (1.0 - 0.8 * dc_val / (dmc_val + 0.4 * dc_val)) * (
            dmc_val - dc_val
        )

    return max(0.0, p)


# =========================================================================
# COMPONENT 6: FWI — Fire Weather Index
# =========================================================================
#
# The final index — overall fire danger. Combines ISI (spread rate) with
# BUI (fuel available). This is the headline number you see on fire danger
# signs.
#
# Danger rating scale (common interpretation):
#   0-5    = Low
#   5-12   = Moderate
#   12-22  = High
#   22-37  = Very High
#   37+    = Extreme
#
# Source: Van Wagner (1987), Section 4.3, Equations 26-28.


def fwi_index(isi_val: float, bui_val: float) -> float:
    """Compute the Fire Weather Index.

    Parameters
    ----------
    isi_val : float
        Current ISI (spread rate).
    bui_val : float
        Current BUI (fuel quantity).

    Returns
    -------
    float
        FWI — overall fire danger. Higher = more dangerous.
    """
    if bui_val <= 0 and isi_val <= 0:
        return 0.0

    # f(D) damps BUI — beyond a point, extra fuel doesn't increase FWI
    # linearly because large fires self-limit. The 11.5 and 0.9 constants
    # tune this damping curve.
    # (Equation 26)
    f_D = bui_val - 11.5 * bui_val / (bui_val + 0.9)

    # (Equation 27-28)
    if f_D <= 0:
        return isi_val - 1.0 * isi_val / (isi_val + 3.0)
    else:
        return f_D + isi_val - 1.0 * isi_val / (isi_val + 3.0)


# =========================================================================
# PUBLIC API
# =========================================================================


class FWIResult:
    """Holds the six FWI component values for a single day."""

    __slots__ = ("ffmc", "dmc", "dc", "isi", "bui", "fwi")

    def __init__(self, ffmc: float, dmc: float, dc: float,
                 isi: float, bui: float, fwi: float):
        self.ffmc = ffmc
        self.dmc = dmc
        self.dc = dc
        self.isi = isi
        self.bui = bui
        self.fwi = fwi

    def danger_rating(self) -> str:
        """Return a human-readable danger rating for the FWI value."""
        if self.fwi >= 37:
            return "Extreme"
        elif self.fwi >= 22:
            return "Very High"
        elif self.fwi >= 12:
            return "High"
        elif self.fwi >= 5:
            return "Moderate"
        return "Low"

    def as_dict(self) -> dict[str, float]:
        return {
            "ffmc": self.ffmc, "dmc": self.dmc, "dc": self.dc,
            "isi": self.isi, "bui": self.bui, "fwi": self.fwi,
        }

    def __repr__(self) -> str:
        return (
            f"FWIResult(ffmc={self.ffmc:.2f}, dmc={self.dmc:.2f}, "
            f"dc={self.dc:.2f}, isi={self.isi:.2f}, "
            f"bui={self.bui:.2f}, fwi={self.fwi:.2f})"
        )


def fwi_daily(
    temp: float,
    rh: float,
    wind: float,
    rain: float,
    prev_ffmc: float = 85.0,
    prev_dmc: float = 6.0,
    prev_dc: float = 15.0,
    lat: float = 45.0,
    month: int = 7,
) -> FWIResult:
    """Compute the full six-component FWI for a single day.

    Each day builds on the previous day's moisture-code values. This is
    the main entry point.

    Parameters
    ----------
    temp : float
        Noon temperature in °C.
    rh : float
        Noon relative humidity in % (0-100).
    wind : float
        Noon wind speed in km/h.
    rain : float
        24-hour precipitation in mm.
    prev_ffmc : float
        Previous day's FFMC. Default 85.0 (typical start-of-season).
    prev_dmc : float
        Previous day's DMC. Default 6.0.
    prev_dc : float
        Previous day's DC. Default 15.0.
    lat : float
        Latitude in degrees. Default 45.0 (e.g., Michigan).
    month : int
        Month (1-12). Default 7 (July).

    Returns
    -------
    FWIResult
        All six component values.
    """
    ffmc_val = ffmc(temp, rh, wind, rain, prev_ffmc)
    dmc_val = dmc(temp, rh, rain, prev_dmc, lat, month)
    dc_val = dc(temp, rain, prev_dc, lat, month)
    isi_val = isi(wind, ffmc_val)
    bui_val = bui(dmc_val, dc_val)
    fwi_val = fwi_index(isi_val, bui_val)

    return FWIResult(
        ffmc=round(ffmc_val, 3),
        dmc=round(dmc_val, 3),
        dc=round(dc_val, 3),
        isi=round(isi_val, 3),
        bui=round(bui_val, 3),
        fwi=round(fwi_val, 3),
    )


def fwi_series(
    weather_records: list[dict[str, Any]],
    lat: float = 45.0,
    month_start: int = 4,
    initial_ffmc: float = 85.0,
    initial_dmc: float = 6.0,
    initial_dc: float = 15.0,
) -> list[FWIResult]:
    """Compute FWI for consecutive days. Each day feeds into the next."""
    results: list[FWIResult] = []
    ffmc_p: float = initial_ffmc
    dmc_p: float = initial_dmc
    dc_p: float = initial_dc

    for i, rec in enumerate(weather_records):
        month = rec.get("month", (month_start + i - 1) % 12 + 1)
        r = fwi_daily(
            temp=rec["temp"], rh=rec["rh"], wind=rec["wind"],
            rain=rec["rain"],
            prev_ffmc=ffmc_p, prev_dmc=dmc_p, prev_dc=dc_p,
            lat=lat, month=month,
        )
        results.append(r)
        ffmc_p, dmc_p, dc_p = r.ffmc, r.dmc, r.dc

    return results
"""Unit tests for the FWI Calculator.

Covers each of the six components individually and the full fwi_daily()
function. Test values are cross-checked against the canonical Van Wagner
(1987) reference where possible, and against the physical expectations
(rain → wetter, dry spell → higher FWI, etc.).
"""

from __future__ import annotations

import sys
import math
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fwi_calculator import (
    ffmc, dmc, dc, isi, bui, fwi_index,
    fwi_daily, _ffmc_to_m, _m_to_ffmc, _day_length_factor,
)

# =========================================================================
# Helper: check that a value is within tolerance of expected
# =========================================================================

_TOL = 1e-3


def assert_approx(tc: unittest.TestCase, actual: float, expected: float,
                  msg: str = "") -> None:
    tc.assertAlmostEqual(actual, expected, delta=_TOL, msg=msg)


# =========================================================================
# Tests: Internal conversion helpers
# =========================================================================


class TestInternalConversions(unittest.TestCase):
    def test_ffmc_to_m_bone_dry(self):
        """FFMC=101 (bone dry) → m=0"""
        m = _ffmc_to_m(101.0)
        assert_approx(self, m, 0.0)

    def test_ffmc_to_m_saturated(self):
        """FFMC=0 (saturated) → m≈250"""
        m = _ffmc_to_m(0.0)
        # Exact value: 147.2 * 101 / 59.5
        self.assertAlmostEqual(m, 249.869, delta=0.01,
                               msg="FFMC=0 should map to m≈250")

    def test_ffmc_to_m_typical(self):
        """FFMC=85 (dry-season start) → m≈16.3"""
        m = _ffmc_to_m(85.0)
        assert_approx(self, m, 16.299, "FFMC=85 → m≈16.3")

    def test_m_to_ffmc_roundtrip(self):
        """Conversion is approximately invertible (empirical, not exact)."""
        for ffmc_in in [5, 15, 40, 65, 85, 95, 101]:
            m = _ffmc_to_m(ffmc_in)
            ffmc_out = _m_to_ffmc(m)
            self.assertAlmostEqual(ffmc_out, float(ffmc_in), delta=0.1,
                                   msg=f"Roundtrip FFMC={ffmc_in}")

    def test_day_length_factor_michigan_july(self):
        """Lat=45°, month=7 → DMC factor=10.1, DC factor=3.8"""
        le_dmc, le_dc = _day_length_factor(45.0, 7)
        assert_approx(self, le_dmc, 10.1)
        assert_approx(self, le_dc, 3.8)

    def test_day_length_factor_michigan_april(self):
        """Lat=45°, month=4 → DMC factor=-1.6, DC factor=1.6"""
        le_dmc, le_dc = _day_length_factor(45.0, 4)
        assert_approx(self, le_dmc, -1.6)
        assert_approx(self, le_dc, 1.6)


# =========================================================================
# Tests: FFMC (Component 1)
# =========================================================================


class TestFFMC(unittest.TestCase):
    """Fine Fuel Moisture Code — 0 (saturated) to 101 (bone dry)."""

    def test_no_rain_mild_drying(self):
        """Mild day with no rain → FFMC increases slightly (dries)."""
        result = ffmc(temp=22, rh=45, wind=12, rain=0.0, prev_ffmc=85.0)
        self.assertGreater(result, 85.0)
        assert_approx(self, result, 85.389)

    def test_hot_dry_windy(self):
        """Hot, dry, windy → FFMC increases significantly (dries fast)."""
        result = ffmc(temp=35, rh=15, wind=30, rain=0.0, prev_ffmc=85.0)
        self.assertGreater(result, 87.0)
        assert_approx(self, result, 88.682)

    def test_heavy_rain(self):
        """Heavy rain → FFMC drops sharply (wets)."""
        result = ffmc(temp=18, rh=90, wind=5, rain=25.0, prev_ffmc=85.0)
        self.assertLess(result, 30.0)
        assert_approx(self, result, 12.133)

    def test_light_rain_under_threshold(self):
        """Rain ≤ 0.5 mm → no wetting effect (canopy intercepts)."""
        result_no_rain = ffmc(temp=25, rh=40, wind=10, rain=0.0, prev_ffmc=85.0)
        result_light = ffmc(temp=25, rh=40, wind=10, rain=0.5, prev_ffmc=85.0)
        assert_approx(self, result_no_rain, result_light)

    def test_ffmc_respects_range(self):
        """FFMC is clamped to [0, 101]."""
        self.assertGreaterEqual(ffmc(0, 0, 0, 0, 0), 0.0)
        self.assertLessEqual(ffmc(50, 0, 100, 0, 101), 101.0)

    def test_rain_saturates(self):
        """Extreme rain → FFMC near 0 (very wet, < 10)."""
        result = ffmc(temp=10, rh=100, wind=0, rain=100.0, prev_ffmc=85.0)
        self.assertLess(result, 10.0)


# =========================================================================
# Tests: DMC (Component 2)
# =========================================================================


class TestDMC(unittest.TestCase):
    """Duff Moisture Code — 0 (saturated) upward (drier)."""

    def test_no_rain_mild_drying(self):
        """Mild day with no rain → DMC increases slightly (dries)."""
        result = dmc(temp=22, rh=45, rain=0.0, prev_dmc=6.0, lat=45, month=7)
        # Drying: 1.894 * (22+1.1) * (100-45) * 10.1 * 0.0001 ≈ 2.43
        self.assertGreater(result, 8.0)

    def test_heavy_rain_saturates(self):
        """Heavy rain → DMC approaches 0 (saturated)."""
        result = dmc(temp=18, rh=90, rain=25.0, prev_dmc=6.0, lat=45, month=7)
        self.assertEqual(result, 0.0)

    def test_drying_after_rain(self):
        """After rain saturation, a hot day starts drying the duff."""
        # Start from saturated (0), give it a hot dry day
        result = dmc(temp=32, rh=25, rain=0.0, prev_dmc=0.0, lat=45, month=7)
        self.assertGreater(result, 0.0)


# =========================================================================
# Tests: DC (Component 3)
# =========================================================================


class TestDC(unittest.TestCase):
    """Drought Code — 0 (saturated) upward (drier)."""

    def test_no_rain_drying(self):
        """No rain, warm day → DC increases (dries/deepens drought)."""
        result = dc(temp=22, rain=0.0, prev_dc=15.0, lat=45, month=7)
        self.assertGreater(result, 15.0)

    def test_heavy_rain_saturates(self):
        """Heavy rain → DC approaches 0 (fully saturated)."""
        result = dc(temp=18, rain=25.0, prev_dc=15.0, lat=45, month=7)
        self.assertEqual(result, 0.0)

    def test_cold_drying(self):
        """Cold temps → minimal DC drying."""
        # Near freezing: drying should be very small
        result = dc(temp=1, rain=0.0, prev_dc=15.0, lat=45, month=7)
        self.assertLess(result, 20.0)


# =========================================================================
# Tests: ISI (Component 4)
# =========================================================================


class TestISI(unittest.TestCase):
    """Initial Spread Index — higher = faster spread."""

    def test_wet_fuel_low_isi(self):
        """Wet fuel (FFMC≈20) → ISI near 0 regardless of wind."""
        result = isi(wind=30, ffmc_val=20.0)
        self.assertLess(result, 0.1)

    def test_dry_fuel_high_isi(self):
        """Dry fuel (FFMC≈90) + wind → ISI > 10."""
        result = isi(wind=20, ffmc_val=90.0)
        self.assertGreater(result, 10.0)

    def test_wind_increases_isi(self):
        """Higher wind → higher ISI (same fuel moisture)."""
        low_wind = isi(wind=5, ffmc_val=85.0)
        high_wind = isi(wind=30, ffmc_val=85.0)
        self.assertGreater(high_wind, low_wind)

    def test_drier_fuel_higher_isi(self):
        """Drier fuel → higher ISI (same wind)."""
        wet = isi(wind=10, ffmc_val=70.0)
        dry = isi(wind=10, ffmc_val=90.0)
        self.assertGreater(dry, wet)


# =========================================================================
# Tests: BUI (Component 5)
# =========================================================================


class TestBUI(unittest.TestCase):
    """Buildup Index — higher = more fuel available."""

    def test_both_zero(self):
        self.assertEqual(bui(0, 0), 0.0)

    def test_bui_increases_with_dmc_and_dc(self):
        self.assertGreater(bui(20, 40), 0.0)
        self.assertGreater(bui(40, 80), bui(20, 40))

    def test_bui_when_dmc_small(self):
        """DMC << DC → BUI damps toward DMC."""
        b = bui(1, 100)
        self.assertGreater(b, 0.0)
        self.assertLess(b, 50)


# =========================================================================
# Tests: FWI (Component 6)
# =========================================================================


class TestFWI(unittest.TestCase):
    """Fire Weather Index — higher = greater fire danger."""

    def test_both_zero(self):
        self.assertEqual(fwi_index(0, 0), 0.0)

    def test_fwi_increases_with_isi(self):
        """Higher ISI → higher FWI (same BUI)."""
        self.assertGreater(fwi_index(10, 20), fwi_index(5, 20))

    def test_fwi_increases_with_bui(self):
        """Higher BUI → higher FWI (same ISI)."""
        self.assertGreater(fwi_index(5, 40), fwi_index(5, 20))


# =========================================================================
# Integration tests: fwi_daily
# =========================================================================


class TestFWIDaily(unittest.TestCase):
    """Full FWI pipeline — end-to-end scenarios."""

    def test_mild_day(self):
        r = fwi_daily(temp=22, rh=45, wind=12, rain=0.0, lat=45, month=7)
        self.assertEqual(r.danger_rating(), "Moderate")
        assert_approx(self, r.ffmc, 85.389)
        self.assertGreater(r.isi, 5.0)
        self.assertGreater(r.fwi, 5.0)

    def test_extreme_day(self):
        r = fwi_daily(temp=35, rh=15, wind=30, rain=0.0, lat=45, month=7)
        self.assertIn(r.danger_rating(), ("Very High", "Extreme"))
        assert_approx(self, r.ffmc, 88.682)
        self.assertGreater(r.isi, 20.0)
        self.assertGreater(r.fwi, 20.0)

    def test_rainy_day(self):
        r = fwi_daily(temp=18, rh=90, wind=5, rain=25.0, lat=45, month=7)
        self.assertEqual(r.danger_rating(), "Low")
        self.assertLess(r.ffmc, 30.0)
        self.assertEqual(r.dmc, 0.0)
        self.assertEqual(r.dc, 0.0)

    def test_dry_spell_builds(self):
        """Three consecutive hot, dry days → FWI escalates."""
        r1 = fwi_daily(temp=25, rh=40, wind=10, rain=0.0, lat=45, month=7)
        r2 = fwi_daily(temp=32, rh=25, wind=15, rain=0.0, lat=45, month=7,
                       prev_ffmc=r1.ffmc, prev_dmc=r1.dmc, prev_dc=r1.dc)
        r3 = fwi_daily(temp=38, rh=12, wind=25, rain=0.0, lat=45, month=7,
                       prev_ffmc=r2.ffmc, prev_dmc=r2.dmc, prev_dc=r2.dc)
        self.assertGreater(r3.fwi, r2.fwi)
        self.assertGreater(r2.fwi, r1.fwi)

    def test_danger_rating_thresholds(self):
        """FWI danger rating thresholds are correct."""
        # Low: 0-5
        r = fwi_daily(temp=10, rh=80, wind=5, rain=5.0, lat=45, month=4)
        self.assertEqual(r.danger_rating(), "Low")

        # Moderate: 5-12
        r = fwi_daily(temp=22, rh=45, wind=12, rain=0.0, lat=45, month=7)
        self.assertEqual(r.danger_rating(), "Moderate")

        # High: 12-22
        # Extreme: 22+
        r = fwi_daily(temp=35, rh=15, wind=30, rain=0.0, lat=45, month=7)
        self.assertGreaterEqual(r.fwi, 22.0)

    def test_as_dict(self):
        r = fwi_daily(temp=22, rh=45, wind=12, rain=0.0, lat=45, month=7)
        d = r.as_dict()
        self.assertEqual(set(d.keys()),
                         {"ffmc", "dmc", "dc", "isi", "bui", "fwi"})
        self.assertIsInstance(d["ffmc"], float)


# =========================================================================
# Run
# =========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
"""
astronacci.py — Astronacci Time Trading engine (Python port)
Source: BOBB MARKET ANALYST v3.0 prompt, Step 2 (Astronacci Time Trading,
framework by Prof. Dr. Gema Goeyardi).

Sub-modules:
  A. Lunar Cycle — moon phase + bias window (New/Full Moon +-2 days = reversal window)
  B. Planetary Aspects — major aspect calendar (2026, as specified in the prompt)
  C. Fibonacci Time Projection — reversal date projection from a swing's time range
  D. Astronacci Confluence Score (0-5)

ACCURACY NOTE (read before relying on this in production):
  Real planetary-aspect computation requires an ephemeris library (skyfield /
  pyephem) with downloadable kernel data, which isn't available in this build
  environment (network disabled). More importantly, the source prompt itself
  already specifies a FIXED "Key Planetary Events 2026" calendar and an
  explicit New/Full Moon date list for 2026 — this module encodes those
  tables directly rather than computing live ephemeris.
    - Lunar phase: 2026 dates are exact (from the prompt). Other years fall
      back to a pure-Python synodic-month estimate (~1 day accuracy).
    - Planetary aspects: Mercury/Venus retrograde windows are exact (from the
      prompt). "Saturn square Uranus" and "Jupiter masuk Gemini" were given
      without precise day-ranges in the source — these use broad placeholder
      windows and should be replaced with exact transit dates from a proper
      astrology ephemeris (e.g. astro.com) if precision matters here.
  Update NEW_MOON_2026 / FULL_MOON_2026 / PLANETARY_EVENTS_2026 for 2027+.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict


# ════════════════════════════════════════════════════════════
# A. LUNAR CYCLE
# ════════════════════════════════════════════════════════════

NEW_MOON_2026 = [
    dt.date(2026, 1, 6), dt.date(2026, 2, 5), dt.date(2026, 3, 7),
    dt.date(2026, 4, 5), dt.date(2026, 5, 5), dt.date(2026, 6, 3),
    dt.date(2026, 7, 3), dt.date(2026, 8, 1), dt.date(2026, 8, 30),
    dt.date(2026, 9, 29), dt.date(2026, 10, 28), dt.date(2026, 11, 27),
    dt.date(2026, 12, 27),
]
FULL_MOON_2026 = [
    dt.date(2026, 1, 21), dt.date(2026, 2, 20), dt.date(2026, 3, 22),
    dt.date(2026, 4, 20), dt.date(2026, 5, 20), dt.date(2026, 6, 19),
    dt.date(2026, 7, 18), dt.date(2026, 8, 17), dt.date(2026, 9, 15),
    dt.date(2026, 10, 14), dt.date(2026, 11, 13), dt.date(2026, 12, 12),
]

SYNODIC_MONTH = 29.530588853  # mean days, used only as fallback
_REF_NEW_MOON = dt.datetime(2000, 1, 6, 18, 14)


def _nearest(dates: List[dt.date], target: dt.date) -> Tuple[Optional[dt.date], int]:
    if not dates:
        return None, 999
    best = min(dates, key=lambda d: abs((d - target).days))
    return best, (target - best).days


def _synodic_age_days(target: dt.date) -> float:
    delta = (dt.datetime.combine(target, dt.time(12, 0)) - _REF_NEW_MOON).total_seconds() / 86400
    return delta % SYNODIC_MONTH


@dataclass
class LunarPhase:
    date: dt.date
    phase_name: str
    bias: str                   # free-text bias description
    in_reversal_window: bool      # within +-2 days of New or Full Moon
    nearest_new_moon: Optional[dt.date]
    nearest_full_moon: Optional[dt.date]
    days_to_nearest_event: int
    source: str                    # "2026 calendar" or "synodic estimate"


def get_lunar_phase(target: dt.date) -> LunarPhase:
    if target.year == 2026:
        nm, nm_off = _nearest(NEW_MOON_2026, target)
        fm, fm_off = _nearest(FULL_MOON_2026, target)
        source = "2026 calendar"
        past_new_moons = [d for d in NEW_MOON_2026 if d <= target]
        if past_new_moons:
            cycle_pos = (target - max(past_new_moons)).days
        else:
            cycle_pos = (SYNODIC_MONTH + nm_off) % SYNODIC_MONTH
    else:
        nm = fm = None
        cycle_pos = _synodic_age_days(target)
        source = "synodic estimate"

    near_new = cycle_pos <= 2 or cycle_pos >= (SYNODIC_MONTH - 2)
    near_full = abs(cycle_pos - SYNODIC_MONTH / 2) <= 2

    if near_new:
        phase_name, bias = "New Moon", "Neutral (watch breakout direction)"
    elif near_full:
        phase_name, bias = "Full Moon", "Bearish (reversal watch)"
    elif cycle_pos < SYNODIC_MONTH / 2:
        phase_name = "Waxing Crescent/First Quarter" if cycle_pos < SYNODIC_MONTH / 4 else "Waxing Gibbous"
        bias = "Bullish (markup phase)"
    else:
        phase_name = "Waning Gibbous" if cycle_pos < 3 * SYNODIC_MONTH / 4 else "Waning/Dark Moon"
        bias = "Bearish (distribution phase)"

    nearest_event_days = min(abs(cycle_pos), abs(cycle_pos - SYNODIC_MONTH), abs(cycle_pos - SYNODIC_MONTH / 2))

    return LunarPhase(
        date=target, phase_name=phase_name, bias=bias,
        in_reversal_window=(near_new or near_full),
        nearest_new_moon=nm, nearest_full_moon=fm,
        days_to_nearest_event=int(round(nearest_event_days)),
        source=source,
    )


# ════════════════════════════════════════════════════════════
# B. PLANETARY ASPECTS — static 2026 calendar (per prompt Step 2B)
# ════════════════════════════════════════════════════════════

@dataclass
class PlanetaryEvent:
    name: str
    start: dt.date
    end: dt.date
    impact: str
    precise_dates: bool = True   # False = placeholder window, see module docstring


PLANETARY_EVENTS_2026: List[PlanetaryEvent] = [
    PlanetaryEvent("Mercury Retrograde", dt.date(2026, 3, 15), dt.date(2026, 4, 7),
                    "Ketidakpastian, false breakout risk", precise_dates=True),
    PlanetaryEvent("Mercury Retrograde", dt.date(2026, 7, 18), dt.date(2026, 8, 11),
                    "Ketidakpastian, false breakout risk", precise_dates=True),
    PlanetaryEvent("Mercury Retrograde", dt.date(2026, 11, 10), dt.date(2026, 12, 1),
                    "Ketidakpastian, false breakout risk", precise_dates=True),
    PlanetaryEvent("Venus Retrograde", dt.date(2026, 7, 1), dt.date(2026, 9, 30),
                    "XAUUSD & komoditas berpotensi volatile", precise_dates=True),
    PlanetaryEvent("Saturn square Uranus", dt.date(2026, 1, 1), dt.date(2026, 12, 31),
                    "Tekanan struktural market jangka menengah (placeholder window)",
                    precise_dates=False),
    PlanetaryEvent("Jupiter masuk Gemini", dt.date(2026, 6, 1), dt.date(2026, 12, 31),
                    "Volatilitas crypto naik (placeholder window)", precise_dates=False),
]


def active_planetary_events(target: dt.date) -> List[PlanetaryEvent]:
    return [e for e in PLANETARY_EVENTS_2026 if e.start <= target <= e.end]


def has_major_aspect(target: dt.date) -> bool:
    """'Major/dated' aspect = Mercury or Venus retrograde windows (have exact
    dates from the prompt). Saturn/Jupiter background transits are excluded
    here since their windows are placeholders, not dated signals."""
    return any(e.precise_dates for e in active_planetary_events(target))


def is_retrograde_caution(target: dt.date) -> bool:
    return any("Retrograde" in e.name for e in active_planetary_events(target))


# ════════════════════════════════════════════════════════════
# C. FIBONACCI TIME PROJECTION
# ════════════════════════════════════════════════════════════

FIB_TIME_RATIOS: Dict[float, str] = {
    0.382: "minor",
    0.618: "medium",
    1.000: "major",
    1.618: "extended",
}


@dataclass
class FibTimeProjection:
    swing_start: dt.date
    swing_end: dt.date
    range_days: int
    projections: Dict[float, dt.date]

    def active_window(self, target: dt.date, tolerance_days: int = 2) -> Optional[float]:
        for ratio, pdate in self.projections.items():
            if abs((pdate - target).days) <= tolerance_days:
                return ratio
        return None


def project_fib_time(swing_start: dt.date, swing_end: dt.date) -> FibTimeProjection:
    """Projects reversal dates forward from `swing_end`, per prompt Step 2C."""
    range_days = (swing_end - swing_start).days
    projections = {
        ratio: swing_end + dt.timedelta(days=round(range_days * ratio))
        for ratio in FIB_TIME_RATIOS
    }
    return FibTimeProjection(swing_start, swing_end, range_days, projections)


# ════════════════════════════════════════════════════════════
# D. ASTRONACCI CONFLUENCE SCORE (Step 2D, max 5)
# ════════════════════════════════════════════════════════════

@dataclass
class AstronacciScore:
    lunar_aligned: bool
    moon_window: bool
    fib_time_active: bool
    planetary_major: bool
    retrograde_caution: bool
    total: int
    label: str   # STRONG / MODERATE / WEAK


def score_astronacci(target: dt.date, market_bias: str,
                      fib_time: Optional[FibTimeProjection] = None) -> AstronacciScore:
    """market_bias: 'Bullish' or 'Bearish' (compared against the lunar bias text)."""
    lunar = get_lunar_phase(target)
    lunar_aligned = market_bias.strip().lower() in lunar.bias.lower()
    moon_window = lunar.in_reversal_window
    fib_active = (fib_time.active_window(target) is not None) if fib_time else False
    planetary_major = has_major_aspect(target)
    retro = is_retrograde_caution(target)

    total = sum([lunar_aligned, moon_window, fib_active, planetary_major, retro])
    label = "STRONG" if total >= 4 else ("MODERATE" if total >= 2 else "WEAK")

    return AstronacciScore(
        lunar_aligned=lunar_aligned, moon_window=moon_window,
        fib_time_active=fib_active, planetary_major=planetary_major,
        retrograde_caution=retro, total=total, label=label,
    )

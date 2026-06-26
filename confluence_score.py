"""
confluence_score.py — Master Confluence Scoring (Step 8, max 14 points)
Source: BOBB MARKET ANALYST v3.0 prompt

FAKTOR                              SCORE
HTF Bias aligned (Weekly/Daily)      +2
Elliott Wave direction clear         +2
Fibonacci Price di OTE/PRZ zone      +2
Chart Pattern konfirmasi             +1
Harmonic Pattern PRZ hit             +1
Divergence terdeteksi (RSI/MACD)     +1
H4 CHoCH / BOS ICT structure         +1
Kill Zone aktif saat entry           +1
Fibonacci Time window aktif          +1
Lunar Cycle aligned dengan bias      +1
Planetary Aspect major aktif         +1
TOTAL MAKSIMUM                       +14

Score >= 10  -> HIGH conviction   — Full size entry
Score 6-9    -> MEDIUM conviction — Half size entry
Score 3-5    -> LOW conviction    — Skip atau micro size
Score < 3    -> NO SETUP          — "Skip dulu bro"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class ConfluenceInputs:
    htf_bias_aligned: bool = False            # +2
    elliott_wave_clear: bool = False           # +2
    fib_ote_or_prz: bool = False                # +2
    chart_pattern_confirmed: bool = False        # +1
    harmonic_prz_hit: bool = False                # +1
    divergence_detected: bool = False              # +1
    h4_choch_bos: bool = False                       # +1
    kill_zone_active: bool = False                    # +1
    fib_time_window_active: bool = False               # +1
    lunar_cycle_aligned: bool = False                   # +1
    planetary_aspect_major: bool = False                 # +1


_WEIGHTS: Dict[str, int] = {
    "htf_bias_aligned": 2,
    "elliott_wave_clear": 2,
    "fib_ote_or_prz": 2,
    "chart_pattern_confirmed": 1,
    "harmonic_prz_hit": 1,
    "divergence_detected": 1,
    "h4_choch_bos": 1,
    "kill_zone_active": 1,
    "fib_time_window_active": 1,
    "lunar_cycle_aligned": 1,
    "planetary_aspect_major": 1,
}
MAX_TOTAL = sum(_WEIGHTS.values())  # 14


@dataclass
class ConfluenceResult:
    breakdown: Dict[str, int]
    total: int
    max_total: int
    conviction: str    # HIGH / MEDIUM / LOW / SKIP
    emoji: str
    size: str            # Full / Half / Skip atau micro / Skip


def score(inputs: ConfluenceInputs) -> ConfluenceResult:
    breakdown: Dict[str, int] = {}
    total = 0
    for field_name, weight in _WEIGHTS.items():
        hit = getattr(inputs, field_name)
        pts = weight if hit else 0
        breakdown[field_name] = pts
        total += pts

    if total >= 10:
        conviction, emoji, size = "HIGH", "🟢", "Full"
    elif total >= 6:
        conviction, emoji, size = "MEDIUM", "🟡", "Half"
    elif total >= 3:
        conviction, emoji, size = "LOW", "🟠", "Skip atau micro"
    else:
        conviction, emoji, size = "SKIP", "🔴", "Skip"

    return ConfluenceResult(
        breakdown=breakdown, total=total, max_total=MAX_TOTAL,
        conviction=conviction, emoji=emoji, size=size,
    )

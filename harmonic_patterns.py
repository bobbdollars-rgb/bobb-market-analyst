"""
harmonic_patterns.py — Harmonic XABCD pattern scanner (Python port)
Ported from: "Harmonics IQ Scanner [TradingIQ]" (Pine Script v6, Mozilla Public
License 2.0, (c) martin_4x). Attribution preserved per license:
https://mozilla.org/MPL/2.0/

Detects: Gartley, Crab, Deep Crab, Bat, Butterfly, Shark, Cypher, NenStar,
plus their Anti-pattern counterparts, and Navarro 200.

Uses a 5-pivot ZigZag (X-A-B-C-D) and validates Fibonacci ratios between
legs (XA, AB, BC, CD) against each pattern's published ratio table, with
a configurable error tolerance (default +-10%, matching the original
`errorPercent` input).

NOT ported (UI-only in the original): multi-symbol scanner table, alert
text formatting, ZigZag line/label drawing. The pivot-detection and
ratio-matching math — the part that actually decides "is this a pattern" —
is preserved 1:1.

Run one instance per ZigZag sensitivity to reproduce the original's three
parallel layers (defaults there: length 5 / 10 / 20).

Input: pandas DataFrame ['high','low'] ascending, one timeframe per call.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class PivotPoint:
    price: float
    bar_index: int
    direction: int   # 1 = high-side pivot, -1 = low-side pivot


@dataclass
class HarmonicMatch:
    name: str
    direction: str        # "bullish" (PRZ expects reversal up) or "bearish"
    X: float; A: float; B: float; C: float; D: float
    xab: float
    abc: float
    bcd: float
    xad: float
    bar_index: int          # bar of D (pattern completion / PRZ)
    is_anti: bool = False


# ════════════════════════════════════════════════════════════
# PIVOT DETECTION — TradingIQ style (highest/lowest-bars based ZigZag)
# ════════════════════════════════════════════════════════════

def find_pivots(df: pd.DataFrame, length: int, max_pivots: int = 200) -> List[PivotPoint]:
    high, low = df["high"], df["low"]
    pivots: List[PivotPoint] = []
    direction = 0

    for i in range(length, len(df)):
        window_h = high.iloc[i - length : i + 1]
        window_l = low.iloc[i - length : i + 1]
        is_ph = high.iloc[i] == window_h.max()
        is_pl = low.iloc[i] == window_l.min()

        if is_ph and not is_pl:
            cur_dir = 1
        elif is_pl and not is_ph:
            cur_dir = -1
        else:
            cur_dir = direction if direction != 0 else 0

        if cur_dir == 0:
            continue

        price = high.iloc[i] if cur_dir == 1 else low.iloc[i]

        if cur_dir != direction:
            pivots.append(PivotPoint(price, i, cur_dir))
        elif pivots:
            better = (price > pivots[-1].price) if cur_dir == 1 else (price < pivots[-1].price)
            if better:
                pivots[-1] = PivotPoint(price, i, cur_dir)

        direction = cur_dir
        if len(pivots) > max_pivots:
            pivots.pop(0)

    return pivots


def _ratio(numer: float, denom: float) -> float:
    return abs(numer) / abs(denom) if denom != 0 else np.nan


def _in_range(value: float, lo: float, hi: float, err_min: float, err_max: float) -> bool:
    if np.isnan(value):
        return False
    return (lo * err_min) <= value <= (hi * err_max)


# ════════════════════════════════════════════════════════════
# PATTERN RATIO TABLES — (xab_range, abc_range, bcd_range, xad_range)
# Ranges are (low, high); None means "not constrained" for that leg.
# Taken directly from the original Pine source's if/else-if chain.
# ════════════════════════════════════════════════════════════

RangeT = Optional[Tuple[float, float]]

_STANDARD_PATTERNS: dict[str, Tuple[RangeT, RangeT, RangeT, RangeT]] = {
    "Gartley":   ((0.618, 0.618), (0.382, 0.886), (1.13, 1.618), (0.786, 0.786)),
    "Crab":      ((0.382, 0.618), (0.382, 0.886), (2.618, 3.618), (1.618, 1.618)),
    "Deep Crab": ((0.886, 0.886), (0.382, 0.886), (2.00, 3.618), (1.618, 1.618)),
    "Bat":       ((0.382, 0.50), (0.382, 0.886), (1.618, 2.618), (0.886, 0.886)),
    "Butterfly": ((0.786, 0.786), (0.382, 0.886), (1.618, 2.24), (1.272, 1.41)),
    "NenStar":   ((0.382, 0.618), (1.414, 2.140), (1.272, 2.0), (1.272, 1.272)),
    "Shark":     (None, (1.13, 1.618), (1.618, 2.24), (0.886, 1.13)),
    "Cypher":    ((0.382, 0.618), (1.13, 1.414), (1.272, 2.00), (0.786, 0.786)),
}

_ANTI_PATTERNS: dict[str, Tuple[RangeT, RangeT, RangeT, RangeT]] = {
    "Anti Gartley":   ((0.618, 0.786), (1.127, 2.618), (1.618, 1.618), (1.272, 1.272)),
    "Anti Crab":      ((0.276, 0.446), (1.128, 2.618), (1.618, 2.618), (0.618, 0.618)),
    "Anti NenStar":   ((0.5, 0.786), (0.467, 0.707), (1.618, 2.618), (0.786, 0.786)),
    "Anti Bat":       ((0.382, 0.618), (1.128, 2.618), (2.0, 2.618), (1.128, 1.128)),
    "Anti Butterfly": ((0.382, 0.618), (1.127, 2.618), (1.272, 1.272), (0.618, 0.786)),
    "Anti Shark":     ((0.446, 0.618), (0.618, 0.886), (1.618, 2.618), (1.13, 1.13)),
    "Anti Cypher":    ((0.5, 0.786), (0.467, 0.707), (1.618, 2.618), (1.272, 1.272)),
    "Navarro200":     ((0.382, 0.786), (0.886, 1.127), (0.886, 3.618), (0.886, 1.272)),
}

# Priority order matters: the original uses an if/elif chain, first match wins.
_STANDARD_ORDER = ["Gartley", "Crab", "Deep Crab", "Bat", "Butterfly",
                    "Shark", "Cypher", "NenStar"]
_ANTI_ORDER = ["Anti Gartley", "Anti Crab", "Anti NenStar", "Anti Bat",
               "Anti Butterfly", "Anti Shark", "Anti Cypher", "Navarro200"]


def _match(table: dict, order: list, xab, abc, bcd, xad, err_min, err_max) -> Optional[str]:
    for name in order:
        xr, ar, br, dr = table[name]
        ok_xab = True if xr is None else _in_range(xab, xr[0], xr[1], err_min, err_max)
        if not ok_xab:
            continue
        if not _in_range(abc, ar[0], ar[1], err_min, err_max):
            continue
        if not _in_range(bcd, br[0], br[1], err_min, err_max):
            continue
        if not _in_range(xad, dr[0], dr[1], err_min, err_max):
            continue
        return name
    return None


# ════════════════════════════════════════════════════════════
# SCANNER
# ════════════════════════════════════════════════════════════

def scan_harmonics(df: pd.DataFrame, length: int = 10, error_pct: float = 10.0) -> List[HarmonicMatch]:
    """Scans the most recent 5 pivots (X-A-B-C-D) for a harmonic pattern match.
    Mirrors DetectHarmonicPattern() in the TradingIQ source. Returns at most
    one match (standard pattern takes priority over anti-pattern, matching
    the original's if/else-if chain)."""
    err_min = (100 - error_pct) / 100
    err_max = (100 + error_pct) / 100

    pivots = find_pivots(df, length)
    if len(pivots) < 5:
        return []

    D, C, B, A, X = pivots[-1], pivots[-2], pivots[-3], pivots[-4], pivots[-5]

    xab = _ratio(B.price - A.price, X.price - A.price)
    abc = _ratio(C.price - B.price, A.price - B.price)
    bcd = _ratio(D.price - C.price, B.price - C.price)
    xad = _ratio(D.price - A.price, X.price - A.price)

    high_pt = max(X.price, A.price, B.price, C.price, D.price)
    low_pt = min(X.price, A.price, B.price, C.price, D.price)
    if not (low_pt < B.price < high_pt):
        return []

    direction = "bullish" if C.price > D.price else "bearish"

    def make(name, is_anti):
        return HarmonicMatch(
            name=name, direction=direction,
            X=X.price, A=A.price, B=B.price, C=C.price, D=D.price,
            xab=xab, abc=abc, bcd=bcd, xad=xad,
            bar_index=D.bar_index, is_anti=is_anti,
        )

    name = _match(_STANDARD_PATTERNS, _STANDARD_ORDER, xab, abc, bcd, xad, err_min, err_max)
    if name:
        return [make(name, is_anti=False)]

    name = _match(_ANTI_PATTERNS, _ANTI_ORDER, xab, abc, bcd, xad, err_min, err_max)
    if name:
        return [make(name, is_anti=True)]

    return []


def scan_multi_sensitivity(df: pd.DataFrame, lengths: List[int] = (5, 10, 20),
                            error_pct: float = 10.0) -> dict:
    """Convenience: runs the scanner across multiple ZigZag sensitivities at
    once (matching the original's 3 parallel ZigZag layers) and returns a
    dict keyed by sensitivity length."""
    return {ln: scan_harmonics(df, length=ln, error_pct=error_pct) for ln in lengths}

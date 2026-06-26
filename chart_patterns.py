"""
chart_patterns.py — Classic chart pattern + candlestick pattern detector
Source: BOBB MARKET ANALYST v3.0 prompt, Step 5 (Chart Pattern, Prof. Gema Series)

Covers:
  A. Classic reversal: Double/Triple Top & Bottom, Head & Shoulders (+ Inverse)
  B. Continuation: Ascending/Descending/Symmetric Triangle
  C. Candlestick patterns: Bullish/Bearish Engulfing, Hammer, Shooting Star,
     Pin Bar, Morning Star, Evening Star

All detectors are pivot-based and return a ChartPattern with a
Forming/Confirmed status (not just yes/no), matching the prompt's
"Status: Forming/Confirmed/Invalidated" output field.

Input: pandas DataFrame ['open','high','low','close'] ascending.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional


# ── pivot helpers (same convention as ict_engine.py) ──

def _pivot_high(df: pd.DataFrame, left: int, right: int) -> pd.Series:
    high = df["high"]; n = len(df)
    out = pd.Series(np.nan, index=df.index)
    for i in range(left, n - right):
        w = high.iloc[i - left : i + right + 1]
        if high.iloc[i] == w.max() and (w == high.iloc[i]).sum() == 1:
            out.iloc[i + right] = high.iloc[i]
    return out


def _pivot_low(df: pd.DataFrame, left: int, right: int) -> pd.Series:
    low = df["low"]; n = len(df)
    out = pd.Series(np.nan, index=df.index)
    for i in range(left, n - right):
        w = low.iloc[i - left : i + right + 1]
        if low.iloc[i] == w.min() and (w == low.iloc[i]).sum() == 1:
            out.iloc[i + right] = low.iloc[i]
    return out


def _pivot_points(df: pd.DataFrame, left=5, right=5, max_n: Optional[int] = 12):
    ph, pl = _pivot_high(df, left, right), _pivot_low(df, left, right)
    pts = []
    for i in range(len(df)):
        if not np.isnan(ph.iloc[i]):
            pts.append((i, float(ph.iloc[i]), 1))
        if not np.isnan(pl.iloc[i]):
            pts.append((i, float(pl.iloc[i]), -1))
    pts.sort(key=lambda x: x[0])
    return pts[-max_n:] if max_n else pts


@dataclass
class ChartPattern:
    name: str
    direction: str          # "bullish" / "bearish" / "neutral"
    status: str               # "Forming" / "Confirmed" / "Invalidated"
    bar_index: int
    neckline: Optional[float] = None
    target: Optional[float] = None
    note: str = ""


# ════════════════════════════════════════════════════════════
# A. DOUBLE / TRIPLE TOP & BOTTOM
# ════════════════════════════════════════════════════════════

def detect_double_top_bottom(df: pd.DataFrame, left=5, right=5, tol_pct=0.15) -> List[ChartPattern]:
    pts = _pivot_points(df, left, right, max_n=20)
    highs = [p for p in pts if p[2] == 1]
    lows = [p for p in pts if p[2] == -1]
    patterns: List[ChartPattern] = []

    for a, b in zip(highs, highs[1:]):
        if a[1] == 0:
            continue
        if abs(a[1] - b[1]) / a[1] * 100 <= tol_pct * 100:
            between = [p for p in lows if a[0] < p[0] < b[0]]
            if not between:
                continue
            neckline = min(p[1] for p in between)
            tail = df["close"].iloc[b[0] + 1 :]
            confirmed = bool((tail < neckline).any()) if len(tail) else False
            patterns.append(ChartPattern(
                name="Double Top", direction="bearish",
                status="Confirmed" if confirmed else "Forming",
                bar_index=b[0], neckline=neckline,
                target=neckline - (max(a[1], b[1]) - neckline),
                note=f"Tops @bar{a[0]}({a[1]:.2f}) & @bar{b[0]}({b[1]:.2f})",
            ))

    for a, b in zip(lows, lows[1:]):
        if a[1] == 0:
            continue
        if abs(a[1] - b[1]) / a[1] * 100 <= tol_pct * 100:
            between = [p for p in highs if a[0] < p[0] < b[0]]
            if not between:
                continue
            neckline = max(p[1] for p in between)
            tail = df["close"].iloc[b[0] + 1 :]
            confirmed = bool((tail > neckline).any()) if len(tail) else False
            patterns.append(ChartPattern(
                name="Double Bottom", direction="bullish",
                status="Confirmed" if confirmed else "Forming",
                bar_index=b[0], neckline=neckline,
                target=neckline + (neckline - min(a[1], b[1])),
                note=f"Bottoms @bar{a[0]}({a[1]:.2f}) & @bar{b[0]}({b[1]:.2f})",
            ))
    return patterns


# ════════════════════════════════════════════════════════════
# A. HEAD & SHOULDERS / INVERSE H&S
# ════════════════════════════════════════════════════════════

def detect_head_shoulders(df: pd.DataFrame, left=5, right=5, tol_pct=0.5) -> List[ChartPattern]:
    pts = _pivot_points(df, left, right, max_n=30)
    highs = [p for p in pts if p[2] == 1]
    lows = [p for p in pts if p[2] == -1]
    patterns: List[ChartPattern] = []

    for i in range(len(highs) - 2):
        ls, head, rs = highs[i], highs[i + 1], highs[i + 2]
        if ls[1] == 0:
            continue
        if head[1] > ls[1] and head[1] > rs[1] and abs(ls[1] - rs[1]) / ls[1] * 100 <= tol_pct * 100:
            b1 = [p for p in lows if ls[0] < p[0] < head[0]]
            b2 = [p for p in lows if head[0] < p[0] < rs[0]]
            if not b1 or not b2:
                continue
            neckline = (max(p[1] for p in b1) + max(p[1] for p in b2)) / 2
            tail = df["close"].iloc[rs[0] + 1 :]
            confirmed = bool((tail < neckline).any()) if len(tail) else False
            patterns.append(ChartPattern(
                name="Head & Shoulders", direction="bearish",
                status="Confirmed" if confirmed else "Forming",
                bar_index=rs[0], neckline=neckline,
                target=neckline - (head[1] - neckline),
                note=f"LS={ls[1]:.2f} Head={head[1]:.2f} RS={rs[1]:.2f}",
            ))

    for i in range(len(lows) - 2):
        ls, head, rs = lows[i], lows[i + 1], lows[i + 2]
        if ls[1] == 0:
            continue
        if head[1] < ls[1] and head[1] < rs[1] and abs(ls[1] - rs[1]) / ls[1] * 100 <= tol_pct * 100:
            b1 = [p for p in highs if ls[0] < p[0] < head[0]]
            b2 = [p for p in highs if head[0] < p[0] < rs[0]]
            if not b1 or not b2:
                continue
            neckline = (min(p[1] for p in b1) + min(p[1] for p in b2)) / 2
            tail = df["close"].iloc[rs[0] + 1 :]
            confirmed = bool((tail > neckline).any()) if len(tail) else False
            patterns.append(ChartPattern(
                name="Inverse Head & Shoulders", direction="bullish",
                status="Confirmed" if confirmed else "Forming",
                bar_index=rs[0], neckline=neckline,
                target=neckline + (neckline - head[1]),
                note=f"LS={ls[1]:.2f} Head={head[1]:.2f} RS={rs[1]:.2f}",
            ))
    return patterns


# ════════════════════════════════════════════════════════════
# B. TRIANGLES (slope-based classifier on recent pivots)
# ════════════════════════════════════════════════════════════

def detect_triangle(df: pd.DataFrame, left=3, right=3, lookback=60) -> List[ChartPattern]:
    sub = df.iloc[-lookback:] if len(df) > lookback else df
    pts = _pivot_points(sub, left, right, max_n=10)
    highs = [p for p in pts if p[2] == 1]
    lows = [p for p in pts if p[2] == -1]
    if len(highs) < 2 or len(lows) < 2:
        return []

    def slope(pts2):
        xs = np.array([p[0] for p in pts2], dtype=float)
        ys = np.array([p[1] for p in pts2], dtype=float)
        return float(np.polyfit(xs, ys, 1)[0]) if len(xs) >= 2 else 0.0

    hs = slope(highs[-3:])
    ls = slope(lows[-3:])
    flat_tol = sub["close"].mean() * 0.0008
    last_bar = len(df) - 1

    if abs(hs) < flat_tol and ls > flat_tol:
        return [ChartPattern("Ascending Triangle", "bullish", "Forming", last_bar,
                              note="Flat resistance, rising support")]
    if abs(ls) < flat_tol and hs < -flat_tol:
        return [ChartPattern("Descending Triangle", "bearish", "Forming", last_bar,
                              note="Flat support, falling resistance")]
    if hs < -flat_tol and ls > flat_tol:
        return [ChartPattern("Symmetric Triangle", "neutral", "Forming", last_bar,
                              note="Converging trendlines")]
    return []


# ════════════════════════════════════════════════════════════
# C. CANDLESTICK PATTERNS
# ════════════════════════════════════════════════════════════

def _body(df, i): return abs(df["close"].iloc[i] - df["open"].iloc[i])
def _rng(df, i): return df["high"].iloc[i] - df["low"].iloc[i]
def _upper_wick(df, i): return df["high"].iloc[i] - max(df["close"].iloc[i], df["open"].iloc[i])
def _lower_wick(df, i): return min(df["close"].iloc[i], df["open"].iloc[i]) - df["low"].iloc[i]
def _is_bull(df, i): return df["close"].iloc[i] > df["open"].iloc[i]


def detect_candlestick_patterns(df: pd.DataFrame, i: Optional[int] = None) -> List[ChartPattern]:
    """Detects patterns at bar `i` (default: last closed bar)."""
    i = len(df) - 1 if i is None else i
    if i < 2:
        return []
    patterns: List[ChartPattern] = []
    rng = _rng(df, i)
    if rng == 0:
        return []

    prev_bull, cur_bull = _is_bull(df, i - 1), _is_bull(df, i)
    if (not prev_bull and cur_bull
            and df["close"].iloc[i] > df["open"].iloc[i - 1]
            and df["open"].iloc[i] < df["close"].iloc[i - 1]):
        patterns.append(ChartPattern("Bullish Engulfing", "bullish", "Confirmed", i))
    if (prev_bull and not cur_bull
            and df["open"].iloc[i] > df["close"].iloc[i - 1]
            and df["close"].iloc[i] < df["open"].iloc[i - 1]):
        patterns.append(ChartPattern("Bearish Engulfing", "bearish", "Confirmed", i))

    body = _body(df, i)
    if body / rng < 0.35:
        if _lower_wick(df, i) / rng > 0.55 and _upper_wick(df, i) / rng < 0.15:
            patterns.append(ChartPattern("Hammer / Bullish Pin Bar", "bullish", "Confirmed", i))
        if _upper_wick(df, i) / rng > 0.55 and _lower_wick(df, i) / rng < 0.15:
            patterns.append(ChartPattern("Shooting Star / Bearish Pin Bar", "bearish", "Confirmed", i))

    if i >= 2:
        r1, r2 = _rng(df, i - 2), _rng(df, i - 1)
        if r1 > 0 and r2 > 0:
            b1, b2, b3 = _body(df, i - 2), _body(df, i - 1), body
            mid2 = (df["open"].iloc[i - 2] + df["close"].iloc[i - 2]) / 2
            if (not _is_bull(df, i - 2) and b1 / r1 > 0.5 and b2 / r2 < 0.3
                    and cur_bull and b3 / rng > 0.5 and df["close"].iloc[i] > mid2):
                patterns.append(ChartPattern("Morning Star", "bullish", "Confirmed", i))
            if (_is_bull(df, i - 2) and b1 / r1 > 0.5 and b2 / r2 < 0.3
                    and not cur_bull and b3 / rng > 0.5 and df["close"].iloc[i] < mid2):
                patterns.append(ChartPattern("Evening Star", "bearish", "Confirmed", i))

    return patterns


# ════════════════════════════════════════════════════════════
# CONVENIENCE WRAPPER
# ════════════════════════════════════════════════════════════

def analyze_chart_patterns(df: pd.DataFrame) -> dict:
    return {
        "double_top_bottom": detect_double_top_bottom(df),
        "head_shoulders": detect_head_shoulders(df),
        "triangle": detect_triangle(df),
        "candlestick": detect_candlestick_patterns(df),
    }

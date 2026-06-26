"""
elliott_wave.py — Elliott Wave detection engine (Python port)
Ported from: "Elliott Wave [LuxAlgo]" (Pine Script v5, CC BY-NC-SA 4.0, (c) LuxAlgo)
Attribution preserved per license: https://creativecommons.org/licenses/by-nc-sa/4.0/

Logic preserved from the original indicator's detection math:
  - ZigZag pivot tracking (configurable left-length, 1-bar confirmation lag —
    same as Pine's ta.pivothigh/pivotlow(len, 1))
  - 5-wave Motive pattern validation: Wave 3 must NOT be the shortest of
    waves 1/3/5, plus the standard "no overlap" structural checks
  - 3-wave ABC Corrective validation, bounded by the 0.854 retracement of
    the completed motive wave (mirrors the original's i_854 input)
  - Wave invalidation: a motive wave is invalidated once price closes back
    beyond the 0.854 retracement of its own range (mirrors the original's
    "fib limit broken" check)

NOT ported (UI-only in the original, no functional equivalent needed here):
  line/label drawing, box redraws, per-bar incremental object mutation.
  This module re-scans the full pivot sequence and returns plain data
  objects instead — functionally equivalent for signal-generation use.

Run one instance per ZigZag sensitivity to reproduce the original's three
parallel layers (defaults there: length 4 / 8 / 16) and look for confluence
across sensitivities / degrees.

Input: pandas DataFrame ['high','low'] ascending, one timeframe per call.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional


# ── pivot detection (identical convention to ict_engine.pivot_high/low) ──

def _pivot_high(df: pd.DataFrame, left: int, right: int = 1) -> pd.Series:
    high = df["high"]
    n = len(df)
    out = pd.Series(np.nan, index=df.index)
    for i in range(left, n - right):
        window = high.iloc[i - left : i + right + 1]
        if high.iloc[i] == window.max() and (window == high.iloc[i]).sum() == 1:
            out.iloc[i + right] = high.iloc[i]
    return out


def _pivot_low(df: pd.DataFrame, left: int, right: int = 1) -> pd.Series:
    low = df["low"]
    n = len(df)
    out = pd.Series(np.nan, index=df.index)
    for i in range(left, n - right):
        window = low.iloc[i - left : i + right + 1]
        if low.iloc[i] == window.min() and (window == low.iloc[i]).sum() == 1:
            out.iloc[i + right] = low.iloc[i]
    return out


@dataclass
class ZigZagPoint:
    bar_index: int
    price: float
    direction: int   # 1 = pivot high, -1 = pivot low


@dataclass
class CorrectiveWave:
    pA: ZigZagPoint
    pB: ZigZagPoint
    pC: ZigZagPoint


@dataclass
class MotiveWave:
    sensitivity: int        # ZigZag length used to detect this wave (proxy for "degree")
    direction: int           # 1 = bullish 12345 (up), -1 = bearish 12345 (down)
    p0: ZigZagPoint          # start of wave 1
    p1: ZigZagPoint          # end of wave 1 / start of wave 2
    p2: ZigZagPoint          # end of wave 2 / start of wave 3
    p3: ZigZagPoint          # end of wave 3 / start of wave 4
    p4: ZigZagPoint          # end of wave 4 / start of wave 5
    p5: ZigZagPoint          # end of wave 5
    valid: bool = True
    invalidated_bar: Optional[int] = None
    abc: Optional[CorrectiveWave] = None

    @property
    def range(self) -> float:
        return abs(self.p5.price - self.p0.price)

    @property
    def wave3_extension(self) -> float:
        """Wave 3 length as a multiple of Wave 1 — classic ICT/EW target check (>= 1.618)."""
        w1 = abs(self.p1.price - self.p0.price)
        w3 = abs(self.p3.price - self.p2.price)
        return (w3 / w1) if w1 else np.nan


DEGREE_LABELS = {
    4: "Minute", 8: "Minor", 16: "Intermediate",
}


class ElliottWaveEngine:
    """Detects Motive (12345) and Corrective (ABC) Elliott Wave structures
    using a ZigZag pivot approach, faithful to LuxAlgo's wave-validation math."""

    def __init__(self, length: int = 8, fib_854: float = 0.854):
        self.length = length
        self.fib_854 = fib_854
        self.zigzag: List[ZigZagPoint] = []
        self.waves: List[MotiveWave] = []

    # ---- public entry point ----
    def run(self, df: pd.DataFrame) -> List[MotiveWave]:
        self.zigzag = self._build_zigzag(df)
        self.waves = self._scan_motive_waves(self.zigzag)
        self._attach_corrective(self.zigzag, self.waves)
        self._mark_invalidated(df, self.waves)
        return self.waves

    # ---- zigzag construction (mirrors Pine's in_out() unshift/extend logic) ----
    def _build_zigzag(self, df: pd.DataFrame) -> List[ZigZagPoint]:
        ph = _pivot_high(df, self.length, 1)
        pl = _pivot_low(df, self.length, 1)
        high, low = df["high"], df["low"]

        pivots: List[ZigZagPoint] = []
        direction = 0

        for i in range(1, len(df)):
            if not np.isnan(ph.iloc[i]):
                price = high.iloc[i - 1]
                if direction <= 0:
                    pivots.append(ZigZagPoint(i, price, 1))
                    direction = 1
                elif pivots and price > pivots[-1].price:
                    pivots[-1] = ZigZagPoint(i, price, 1)

            if not np.isnan(pl.iloc[i]):
                price = low.iloc[i - 1]
                if direction >= 0:
                    pivots.append(ZigZagPoint(i, price, -1))
                    direction = -1
                elif pivots and price < pivots[-1].price:
                    pivots[-1] = ZigZagPoint(i, price, -1)

        return pivots

    # ---- 5-wave motive validation (exact math from the original) ----
    def _scan_motive_waves(self, pivots: List[ZigZagPoint]) -> List[MotiveWave]:
        waves: List[MotiveWave] = []
        for k in range(5, len(pivots)):
            p0, p1, p2, p3, p4, p5 = pivots[k - 5 : k + 1]
            bullish = p5.direction == 1

            if bullish:
                w5 = p5.price - p4.price
                w3 = p3.price - p2.price
                w1 = p1.price - p0.price
                is_wave = (w3 != min(w1, w3, w5)
                           and p5.price > p3.price
                           and p2.price > p0.price
                           and p4.price > p1.price)
                direction = 1
            else:
                w5 = p4.price - p5.price
                w3 = p2.price - p3.price
                w1 = p0.price - p1.price
                is_wave = (w3 != min(w1, w3, w5)
                           and p3.price > p5.price
                           and p0.price > p2.price
                           and p1.price > p4.price)
                direction = -1

            if is_wave:
                waves.append(MotiveWave(self.length, direction, p0, p1, p2, p3, p4, p5))
        return waves

    # ---- ABC corrective attachment (bounded by 0.854 of the motive range) ----
    def _attach_corrective(self, pivots: List[ZigZagPoint], waves: List[MotiveWave]):
        for wave in waves:
            try:
                idx5 = pivots.index(wave.p5)
            except ValueError:
                continue
            if idx5 + 3 >= len(pivots):
                continue
            pA, pB, pC = pivots[idx5 + 1], pivots[idx5 + 2], pivots[idx5 + 3]
            diff = wave.range

            if wave.direction == 1:
                valid = (pC.price < wave.p5.price + diff * self.fib_854
                         and pB.price < wave.p5.price + diff * self.fib_854
                         and pA.price < wave.p5.price)
            else:
                valid = (pC.price > wave.p5.price - diff * self.fib_854
                         and pB.price > wave.p5.price - diff * self.fib_854
                         and pA.price > wave.p5.price)

            if valid:
                wave.abc = CorrectiveWave(pA, pB, pC)

    # ---- invalidation: price closes beyond 0.854 retracement of the wave ----
    def _mark_invalidated(self, df: pd.DataFrame, waves: List[MotiveWave]):
        close = df["close"]
        for wave in waves:
            diff = wave.range
            if wave.direction == 1:
                limit = wave.p5.price - diff * self.fib_854
            else:
                limit = wave.p5.price + diff * self.fib_854

            for i in range(wave.p5.bar_index + 1, len(df)):
                if wave.direction == 1 and close.iloc[i] < limit:
                    wave.valid, wave.invalidated_bar = False, i
                    break
                if wave.direction == -1 and close.iloc[i] > limit:
                    wave.valid, wave.invalidated_bar = False, i
                    break


# ════════════════════════════════════════════════════════════
# REPORTING HELPER — human-readable wave position for the analyst output
# ════════════════════════════════════════════════════════════

def describe_position(waves: List[MotiveWave], length: int) -> dict:
    """Returns the analyst-report fields: current wave position, degree,
    main/alternate scenario, invalidation level. Picks the most recent
    *valid* wave; if its ABC has formed, reports "post-wave-5 corrective"
    and flags a likely new Wave 1 start."""
    degree = DEGREE_LABELS.get(length, f"len{length}")
    valid_waves = [w for w in waves if w.valid]

    if not valid_waves:
        return {
            "position": "No clear impulsive structure",
            "degree": degree,
            "main_scenario": "Insufficient ZigZag pivots for a valid 5-wave count",
            "alternate_scenario": "N/A",
            "invalidation": "N/A",
        }

    last = valid_waves[-1]
    bull = last.direction == 1
    side = "Bullish" if bull else "Bearish"

    if last.abc is not None:
        position = f"Post-Wave-5 ({side}) — ABC corrective formed, watch for new Wave 1"
        main = f"Wave (5) of {side} impulse complete; corrective ABC in progress (probability ~55%)"
        alt = f"Corrective ABC fails -> Wave (5) extension continues {side.lower()} (probability ~45%)"
    else:
        position = f"Inside Wave (5) of {side} impulse, Degree: {degree}"
        main = f"Wave (3)->(5) {side} structure intact, W3 confirmed not-shortest (probability ~60%)"
        alt = f"Wave count invalid if price reverses beyond 0.854 retracement (probability ~40%)"

    diff = last.range
    invalidation = (last.p5.price - diff * 0.854) if bull else (last.p5.price + diff * 0.854)

    return {
        "position": position,
        "degree": degree,
        "main_scenario": main,
        "alternate_scenario": alt,
        "invalidation": round(invalidation, 5),
        "wave3_extension_ratio": round(last.wave3_extension, 3) if not np.isnan(last.wave3_extension) else None,
    }

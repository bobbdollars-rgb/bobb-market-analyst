"""
divergence.py — RSI/MACD Regular & Hidden Divergence detector
Source: BOBB MARKET ANALYST v3.0 prompt, Step 5D

Regular Divergence (reversal signal):
  Bearish: Price Higher High, indicator Lower High -> SELL
  Bullish: Price Lower Low, indicator Higher Low -> BUY
Hidden Divergence (continuation signal):
  Bearish: Price Lower High, indicator Higher High -> continue down
  Bullish: Price Higher Low, indicator Lower Low -> continue up

Multi-timeframe agreement (2+ TF showing the same divergence kind) is
flagged as HIGH PRIORITY, per the prompt's "⚠️ HIGH PRIORITY" rule.

Input: pandas DataFrame ['close'] ascending, one timeframe per call.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Dict


# ── indicators ──

def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def _pivot_idx(series: pd.Series, left: int, right: int):
    """Indicator pivot detector. Unlike price pivots, oscillators (RSI/MACD)
    commonly plateau at a saturated value (e.g. RSI=100) across several bars
    during strong trends — a strict 'unique max' check would miss these
    entirely. Ties are resolved by taking the LAST bar of the plateau
    (closest to the subsequent reversal), which matches standard divergence-
    indicator convention."""
    n = len(series)
    highs, lows = [], []
    for i in range(left, n - right):
        w = series.iloc[i - left : i + right + 1]
        wmax, wmin = w.max(), w.min()
        if series.iloc[i] == wmax:
            tied_max_idx = w[w == wmax].index
            last_tied = series.index.get_loc(tied_max_idx[-1])
            if last_tied == i:
                highs.append(i)
        if series.iloc[i] == wmin:
            tied_min_idx = w[w == wmin].index
            last_tied = series.index.get_loc(tied_min_idx[-1])
            if last_tied == i:
                lows.append(i)
    return highs, lows


@dataclass
class Divergence:
    kind: str         # "Regular Bearish" / "Regular Bullish" / "Hidden Bearish" / "Hidden Bullish"
    indicator: str      # "RSI" or "MACD"
    bar_a: int
    bar_b: int
    note: str


def detect_divergence(df: pd.DataFrame, indicator: str = "RSI", left: int = 5, right: int = 5) -> List[Divergence]:
    close = df["close"]
    if indicator.upper() == "RSI":
        ind = rsi(close)
    else:
        ind, _, _ = macd(close)
    ind = ind.bfill()

    p_highs, p_lows = _pivot_idx(close, left, right)
    i_highs, i_lows = _pivot_idx(ind, left, right)
    window = left + right

    def nearest(idx, candidates):
        cands = [c for c in candidates if abs(c - idx) <= window]
        return min(cands, key=lambda c: abs(c - idx)) if cands else None

    divs: List[Divergence] = []

    for a, b in zip(p_highs, p_highs[1:]):
        ia, ib = nearest(a, i_highs), nearest(b, i_highs)
        if ia is None or ib is None:
            continue
        price_hh = close.iloc[b] > close.iloc[a]
        price_lh = close.iloc[b] < close.iloc[a]
        ind_lh = ind.iloc[ib] < ind.iloc[ia]
        ind_hh = ind.iloc[ib] > ind.iloc[ia]
        if price_hh and ind_lh:
            divs.append(Divergence("Regular Bearish", indicator, a, b, "Price HH, indicator LH"))
        if price_lh and ind_hh:
            divs.append(Divergence("Hidden Bearish", indicator, a, b, "Price LH, indicator HH"))

    for a, b in zip(p_lows, p_lows[1:]):
        ia, ib = nearest(a, i_lows), nearest(b, i_lows)
        if ia is None or ib is None:
            continue
        price_ll = close.iloc[b] < close.iloc[a]
        price_hl = close.iloc[b] > close.iloc[a]
        ind_hl = ind.iloc[ib] > ind.iloc[ia]
        ind_ll = ind.iloc[ib] < ind.iloc[ia]
        if price_ll and ind_hl:
            divs.append(Divergence("Regular Bullish", indicator, a, b, "Price LL, indicator HL"))
        if price_hl and ind_ll:
            divs.append(Divergence("Hidden Bullish", indicator, a, b, "Price HL, indicator LL"))

    return divs


def analyze_divergence_multi_tf(dfs_by_tf: Dict[str, pd.DataFrame], indicator: str = "RSI") -> dict:
    """dfs_by_tf: {'M15': df, 'H1': df, ...}. Flags HIGH PRIORITY when 2+
    timeframes show the same divergence kind (recent N pivots only)."""
    results = {tf: detect_divergence(df, indicator) for tf, df in dfs_by_tf.items()}
    kinds_per_tf = {tf: {d.kind for d in divs[-3:]} for tf, divs in results.items()}
    all_kinds = set().union(*kinds_per_tf.values()) if kinds_per_tf else set()
    high_priority = [k for k in all_kinds
                      if sum(1 for ks in kinds_per_tf.values() if k in ks) >= 2]
    return {"by_timeframe": results, "high_priority_multi_tf": high_priority}

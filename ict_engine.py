"""
ict_engine.py — Bobb Infinity Core ICT/SMC Engine (Python port)
Ported from: Bobb_Infinity_Core-7_fixed_v2.txt (Pine Script v6, © Bobb)

Modules ported (1:1 fidelity):
  A. Market Structure — External BOS/CHoCH (solid) + Internal M15 CHoCH (dashed)
  B. HTF Bias        — D1 & H4: close[1] vs EMA200 (exact Pine logic)
  C. H1 Bias Engine  — 4-layer voting: LaRSI + CCI50 + EMA(13/80/200) + DI+/DI- (2-of-4 majority, ADX gate)
  D. Smart Order Block — z-score momentum trigger + mitigation + Breaker Block promotion
  E. Fair Value Gap  — classic 3-candle ICT imbalance + mitigation
  F. Strong FVG      — ATR-filtered impulsive FVG
  G. BPR             — Balanced Price Range (overlap two opposing FVGs)
  H. Rejection Blocks — RSI-filtered OB at extreme RSI zone
  I. Equal Highs/Lows — EQH/EQL liquidity pool detection
  J. NWOG/NDOG       — New Week/Day Opening Gap
  K. Fib OTE Engine  — ATR-filtered BOS/CHoCH anchor → OTE zone 0.618–0.886
  L. ROV Signal      — D1 + H4 + H1 Bias gate combo (final entry signal)

No-look-ahead design: every bar reads only confirmed closed bars (mirrors
Pine's barstate.isconfirmed + [1] indexing convention throughout Infinity Core).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple


# ════════════════════════════════════════════════════════════
# UTILITIES
# ════════════════════════════════════════════════════════════

def _atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(length).mean()

def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def _cci(df: pd.DataFrame, length: int = 50) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(length).mean()
    md = tp.rolling(length).apply(lambda x: np.abs(x - x.mean()).mean())
    return (tp - ma) / (0.015 * md)

def _dmi(df: pd.DataFrame, di_len: int = 14, adx_len: int = 14):
    """Returns (adx, di_plus, di_minus) as pd.Series."""
    h, l, c = df["high"], df["low"], df["close"]
    prev_h, prev_l = h.shift(1), l.shift(1)
    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    dm_plus  = ((h - prev_h).clip(lower=0)).where((h - prev_h) > (prev_l - l), 0)
    dm_minus = ((prev_l - l).clip(lower=0)).where((prev_l - l) > (h - prev_h), 0)
    atr_s = tr.ewm(span=di_len, adjust=False).mean()
    dp = 100 * dm_plus.ewm(span=di_len,  adjust=False).mean() / atr_s
    dm = 100 * dm_minus.ewm(span=di_len, adjust=False).mean() / atr_s
    dx = (100 * (dp - dm).abs() / (dp + dm).replace(0, np.nan)).fillna(0)
    adx = dx.ewm(span=adx_len, adjust=False).mean()
    return adx, dp, dm

def _laguerre_rsi(close: pd.Series, gamma: float = 0.2) -> pd.Series:
    """Laguerre RSI — exact port of Pine _h1_bias_calc() LaRSI."""
    lg = 1.0 - gamma
    L0 = pd.Series(0.0, index=close.index)
    L1 = pd.Series(0.0, index=close.index)
    L2 = pd.Series(0.0, index=close.index)
    L3 = pd.Series(0.0, index=close.index)
    for i in range(1, len(close)):
        L0.iloc[i] = (1 - lg) * close.iloc[i] + lg * L0.iloc[i-1]
        L1.iloc[i] = -lg * L0.iloc[i] + L0.iloc[i-1] + lg * L1.iloc[i-1]
        L2.iloc[i] = -lg * L1.iloc[i] + L1.iloc[i-1] + lg * L2.iloc[i-1]
        L3.iloc[i] = -lg * L2.iloc[i] + L2.iloc[i-1] + lg * L3.iloc[i-1]
    cu = ((L0 - L1).clip(lower=0) + (L1 - L2).clip(lower=0) + (L2 - L3).clip(lower=0))
    cd = ((L1 - L0).clip(lower=0) + (L2 - L1).clip(lower=0) + (L3 - L2).clip(lower=0))
    denom = cu + cd
    return (cu / denom.replace(0, np.nan) * 100).fillna(50.0)

def pivot_high(df: pd.DataFrame, left: int, right: int = 1) -> pd.Series:
    high = df["high"]; n = len(df)
    out = pd.Series(np.nan, index=df.index)
    for i in range(left, n - right):
        w = high.iloc[i - left : i + right + 1]
        if high.iloc[i] == w.max() and (w == high.iloc[i]).sum() == 1:
            out.iloc[i + right] = high.iloc[i]
    return out

def pivot_low(df: pd.DataFrame, left: int, right: int = 1) -> pd.Series:
    low = df["low"]; n = len(df)
    out = pd.Series(np.nan, index=df.index)
    for i in range(left, n - right):
        w = low.iloc[i - left : i + right + 1]
        if low.iloc[i] == w.min() and (w == low.iloc[i]).sum() == 1:
            out.iloc[i + right] = low.iloc[i]
    return out


# ════════════════════════════════════════════════════════════
# A. MARKET STRUCTURE — External BOS/CHoCH
# ════════════════════════════════════════════════════════════

@dataclass
class StructureEvent:
    bar_index: int
    kind: str       # "BOS" | "CHoCH"
    direction: str  # "bullish" | "bearish"
    level: float

@dataclass
class _SwingPivot:
    level: float = np.nan
    crossed: bool = False
    bar_index: int = -1

class MarketStructure:
    """External-swing BOS/CHoCH. Mirrors _msSwingTrend in Infinity Core."""
    def __init__(self, swing_len: int = 5):
        self.swing_len = swing_len
        self.swing_high = _SwingPivot()
        self.swing_low  = _SwingPivot()
        self.bias = 0
        self.events: List[StructureEvent] = []

    def update(self, df: pd.DataFrame) -> List[StructureEvent]:
        ph = pivot_high(df, self.swing_len, self.swing_len)
        pl = pivot_low(df,  self.swing_len, self.swing_len)
        close = df["close"]
        self.events = []
        for i in range(1, len(df)):
            if not np.isnan(pl.iloc[i]):
                self.swing_low  = _SwingPivot(pl.iloc[i], False, i)
            if not np.isnan(ph.iloc[i]):
                self.swing_high = _SwingPivot(ph.iloc[i], False, i)
            c, pc = close.iloc[i], close.iloc[i-1]
            if (not np.isnan(self.swing_high.level) and not self.swing_high.crossed
                    and pc <= self.swing_high.level < c):
                kind = "CHoCH" if self.bias == -1 else "BOS"
                self.events.append(StructureEvent(i, kind, "bullish", self.swing_high.level))
                self.swing_high.crossed = True
                self.bias = 1
            if (not np.isnan(self.swing_low.level) and not self.swing_low.crossed
                    and pc >= self.swing_low.level > c):
                kind = "CHoCH" if self.bias == 1 else "BOS"
                self.events.append(StructureEvent(i, kind, "bearish", self.swing_low.level))
                self.swing_low.crossed = True
                self.bias = -1
        return self.events


# ════════════════════════════════════════════════════════════
# B. HTF BIAS — D1 + H4: close[1] vs EMA200
#    Exact port of _d1TrendDir / _h4TrendDir in Infinity Core
# ════════════════════════════════════════════════════════════

def htf_bias(df_d1: pd.DataFrame, df_h4: pd.DataFrame) -> Dict:
    """close[1] vs EMA200 — mirrors lookahead_off + close[1] in Pine."""
    d1_ema200 = _ema(df_d1["close"], 200)
    h4_ema200 = _ema(df_h4["close"], 200)
    # [1] = use confirmed prior bar close (lookahead_off equivalent)
    d1_c = float(df_d1["close"].iloc[-2]) if len(df_d1) >= 2 else float(df_d1["close"].iloc[-1])
    h4_c = float(df_h4["close"].iloc[-2]) if len(df_h4) >= 2 else float(df_h4["close"].iloc[-1])
    d1_e = float(d1_ema200.iloc[-1])
    h4_e = float(h4_ema200.iloc[-1])
    d1_dir = 1 if d1_c > d1_e else (-1 if d1_c < d1_e else 0)
    h4_dir = 1 if h4_c > h4_e else (-1 if h4_c < h4_e else 0)
    return {
        "d1_dir": d1_dir, "d1_close": d1_c, "d1_ema200": d1_e,
        "h4_dir": h4_dir, "h4_close": h4_c, "h4_ema200": h4_e,
        "aligned": d1_dir != 0 and d1_dir == h4_dir,
        "bias": "BULLISH" if d1_dir == 1 else ("BEARISH" if d1_dir == -1 else "NEUTRAL"),
    }


# ════════════════════════════════════════════════════════════
# C. H1 BIAS ENGINE — 4-layer voting (port of _h1_bias_calc)
#    LaRSI(gamma=0.2) + CCI50 + EMA(13/80/200) + DI+/DI-
#    ADX gate (threshold=15, override if DI spread > 10)
#    2-of-4 majority → BULL | BEAR | NEUT
# ════════════════════════════════════════════════════════════

def h1_bias(df_h1: pd.DataFrame,
             gamma: float = 0.2,
             rsi_buffer: float = 5.0,
             cci_buffer: float = 25.0,
             adx_len: int = 14,
             adx_thresh: float = 15.0) -> Dict:
    """Port of Infinity Core H1 Bias Engine — 4-layer 2-of-4 vote."""
    close = df_h1["close"]
    # [1] = confirmed prior bar, anti-repaint
    la_rsi = _laguerre_rsi(close, gamma).shift(1)
    cci50  = _cci(df_h1, 50).shift(1)
    e13    = _ema(close, 13).shift(1)
    e80    = _ema(close, 80).shift(1)
    e200   = _ema(close, 200).shift(1)
    adx_s, di_plus, di_minus = _dmi(df_h1, adx_len, adx_len)
    adx_v  = adx_s.shift(1)
    dip    = di_plus.shift(1)
    dim    = di_minus.shift(1)
    c1     = close.shift(1)

    # ADX trending gate (di_strong override if DI spread > 10)
    di_strong  = (dip - dim).abs() > 10
    is_trending = (adx_v >= adx_thresh) | di_strong

    # Layer 1: EMA structure (fast/mid/slow)
    ema_bull = (c1 > e13) & ((c1 > e80) | (c1 > e200))
    ema_bear = (c1 < e13) & ((c1 < e80) | (c1 < e200))

    # Layer 2: LaRSI with buffer zone
    rsi_bull = la_rsi > (50.0 + rsi_buffer)
    rsi_bear = la_rsi < (50.0 - rsi_buffer)

    # Layer 3: CCI 50 with buffer zone
    cci_bull = cci50 >  cci_buffer
    cci_bear = cci50 < -cci_buffer

    # Layer 4: DI direction tiebreaker
    di_bull = dip > dim
    di_bear = dim > dip

    # 2-of-4 majority
    bull_votes = ema_bull.astype(int) + rsi_bull.astype(int) + cci_bull.astype(int) + di_bull.astype(int)
    bear_votes = ema_bear.astype(int) + rsi_bear.astype(int) + cci_bear.astype(int) + di_bear.astype(int)

    def _vote(trending, bv, sv):
        if not trending: return "NEUT"
        if bv >= 2: return "BULL"
        if sv >= 2: return "BEAR"
        return "NEUT"

    raw_bias = [_vote(is_trending.iloc[i], bull_votes.iloc[i], bear_votes.iloc[i])
                for i in range(len(df_h1))]

    last = raw_bias[-1]
    return {
        "bias": last,
        "bull_votes": int(bull_votes.iloc[-1]),
        "bear_votes": int(bear_votes.iloc[-1]),
        "adx": round(float(adx_v.iloc[-1]), 1) if not np.isnan(adx_v.iloc[-1]) else 0.0,
        "is_trending": bool(is_trending.iloc[-1]),
        "la_rsi": round(float(la_rsi.iloc[-1]), 1) if not np.isnan(la_rsi.iloc[-1]) else 50.0,
        "cci50":  round(float(cci50.iloc[-1]),  1) if not np.isnan(cci50.iloc[-1])  else 0.0,
    }


# ════════════════════════════════════════════════════════════
# D. SMART ORDER BLOCK
#    z-score momentum trigger + mitigation + Breaker Block
# ════════════════════════════════════════════════════════════

@dataclass
class OrderBlock:
    top: float
    bottom: float
    bar_start: int
    direction: str    # "bull" | "bear"
    broken: bool = False
    is_breaker: bool = False
    break_bar: int = -1

class SmartOrderBlock:
    def __init__(self, z_len: int = 14, z_thresh: float = 2.0,
                 min_size_atr: float = 0.0, max_age: int = 300):
        self.z_len = z_len; self.z_thresh = z_thresh
        self.min_size_atr = min_size_atr; self.max_age = max_age

    def detect(self, df: pd.DataFrame) -> List[OrderBlock]:
        o, c, h, l = df["open"], df["close"], df["high"], df["low"]
        n = len(df)
        up_run = np.zeros(n); dn_run = np.zeros(n)
        for i in range(n):
            up_run[i] = (up_run[i-1] + (c.iloc[i]-o.iloc[i])) if i>0 and c.iloc[i]>o.iloc[i] else max(0.0, c.iloc[i]-o.iloc[i])
            dn_run[i] = (dn_run[i-1] + (o.iloc[i]-c.iloc[i])) if i>0 and c.iloc[i]<o.iloc[i] else max(0.0, o.iloc[i]-c.iloc[i])
        up_s = pd.Series(up_run, index=df.index)
        dn_s = pd.Series(dn_run, index=df.index)
        z_up = (up_s - up_s.rolling(self.z_len).mean()) / up_s.rolling(self.z_len).std().replace(0, np.nan)
        z_dn = (dn_s - dn_s.rolling(self.z_len).mean()) / dn_s.rolling(self.z_len).std().replace(0, np.nan)
        a = _atr(df, 14); mh = a * self.min_size_atr
        bull_obs: List[OrderBlock] = []; bear_obs: List[OrderBlock] = []
        last_down = None; last_up = None
        for i in range(n):
            if c.iloc[i] < o.iloc[i]: last_down = (h.iloc[i], l.iloc[i], i)
            if c.iloc[i] > o.iloc[i]: last_up   = (h.iloc[i], l.iloc[i], i)
            if i < 1: continue
            zu0, zu1 = z_up.iloc[i-1], z_up.iloc[i]
            zd0, zd1 = z_dn.iloc[i-1], z_dn.iloc[i]
            m = float(mh.iloc[i]) if not np.isnan(mh.iloc[i]) else 0.0
            if not np.isnan(zu0) and not np.isnan(zu1) and zu0 <= self.z_thresh < zu1 and last_down:
                dh, dl, di = last_down
                if (dh - dl) >= m: bull_obs.append(OrderBlock(dh, dl, di, "bull"))
            if not np.isnan(zd0) and not np.isnan(zd1) and zd0 <= self.z_thresh < zd1 and last_up:
                uh, ul, ui = last_up
                if (uh - ul) >= m: bear_obs.append(OrderBlock(uh, ul, ui, "bear"))
        self._mitigate(bull_obs, bear_obs, df)
        return bull_obs + bear_obs

    def _mitigate(self, bull_obs, bear_obs, df):
        c = df["close"]; n = len(df)
        for ob in bull_obs:
            for i in range(ob.bar_start+1, min(n, ob.bar_start+1+self.max_age)):
                if c.iloc[i] < ob.bottom:
                    ob.broken, ob.is_breaker, ob.break_bar = True, True, i; break
        for ob in bear_obs:
            for i in range(ob.bar_start+1, min(n, ob.bar_start+1+self.max_age)):
                if c.iloc[i] > ob.top:
                    ob.broken, ob.is_breaker, ob.break_bar = True, True, i; break


# ════════════════════════════════════════════════════════════
# E. FAIR VALUE GAP — 3-candle classic ICT
# ════════════════════════════════════════════════════════════

@dataclass
class FVG:
    top: float
    bottom: float
    bar_index: int
    direction: str      # "bullish" | "bearish"
    is_strong: bool = False
    mitigated: bool = False
    mitigated_bar: int = -1

def detect_fvg(df: pd.DataFrame, atr_multiplier: float = 0.0) -> List[FVG]:
    h, l, c, o = df["high"], df["low"], df["close"], df["open"]
    a = _atr(df, 20)
    fvgs: List[FVG] = []
    for i in range(2, len(df)):
        h1, l1 = h.iloc[i-2], l.iloc[i-2]
        h3, l3 = h.iloc[i],   l.iloc[i]
        gap = 0.0
        # Strong FVG: impulsive middle candle (body > 60% of range)
        body1 = abs(c.iloc[i-1] - o.iloc[i-1])
        rng1  = h.iloc[i-1] - l.iloc[i-1]
        is_strong = (body1 / rng1 > 0.6) if rng1 > 0 else False
        atr_min = float(a.iloc[i] * atr_multiplier) if not np.isnan(a.iloc[i]) else 0.0
        if l3 > h1:
            gap = l3 - h1
            if gap >= atr_min:
                fvgs.append(FVG(l3, h1, i, "bullish", is_strong))
        elif h3 < l1:
            gap = l1 - h3
            if gap >= atr_min:
                fvgs.append(FVG(l1, h3, i, "bearish", is_strong))
    hi, lo = df["high"], df["low"]
    for fvg in fvgs:
        for j in range(fvg.bar_index+1, len(df)):
            if fvg.direction == "bullish" and lo.iloc[j] <= fvg.bottom:
                fvg.mitigated, fvg.mitigated_bar = True, j; break
            if fvg.direction == "bearish" and hi.iloc[j] >= fvg.top:
                fvg.mitigated, fvg.mitigated_bar = True, j; break
    return fvgs


# ════════════════════════════════════════════════════════════
# G. BPR — Balanced Price Range (overlap two opposing FVGs)
#    Port of BPR ENGINE in Infinity Core
# ════════════════════════════════════════════════════════════

@dataclass
class BPR:
    top: float
    bottom: float
    bar_index: int
    direction: str    # "bullish" | "bearish" (direction of the zone that absorbed the other)
    invalidated: bool = False

def detect_bpr(fvgs: List[FVG]) -> List[BPR]:
    """Detect BPR: overlap between a bullish FVG and a bearish FVG that appeared
    in different directions. The overlap zone is the BPR. Later zone sets bounds."""
    bprs: List[BPR] = []
    used = set()
    for i, f1 in enumerate(fvgs):
        for j, f2 in enumerate(fvgs):
            if i >= j or i in used or j in used: continue
            if f1.direction == f2.direction: continue
            # Check overlap
            ov_top = min(f1.top, f2.top)
            ov_bot = max(f1.bottom, f2.bottom)
            if ov_top > ov_bot:
                later = f1 if f1.bar_index > f2.bar_index else f2
                bprs.append(BPR(ov_top, ov_bot, later.bar_index, later.direction))
                used.add(i); used.add(j)
    return bprs


# ════════════════════════════════════════════════════════════
# H. REJECTION BLOCKS — RSI-filtered OB at extreme RSI
#    Port of RSIOB engine (rsiob_rsiFilter: skip if RSI 30-70)
# ════════════════════════════════════════════════════════════

@dataclass
class RejectionBlock:
    top: float
    bottom: float
    bar_index: int
    direction: str    # "bull" | "bear"
    rsi_at_block: float
    mitigated: bool = False

def detect_rejection_blocks(df: pd.DataFrame,
                              rsi_len: int = 14,
                              sensitivity: int = 10,
                              rsi_filter: bool = True,
                              mit_method: str = "Close") -> List[RejectionBlock]:
    """RSI-filtered Order Blocks: only create block if RSI is OUTSIDE 30-70
    (extreme zone). Mirrors rsiob_rsiFilter in Infinity Core."""
    from divergence import rsi as _rsi_fn
    rsi_s = _rsi_fn(df["close"], rsi_len)
    ph = pivot_high(df, sensitivity, sensitivity)
    pl = pivot_low(df,  sensitivity, sensitivity)
    blocks: List[RejectionBlock] = []
    for i in range(len(df)):
        rsi_val = float(rsi_s.iloc[i]) if not np.isnan(rsi_s.iloc[i]) else 50.0
        if rsi_filter and 30 <= rsi_val <= 70: continue
        if not np.isnan(ph.iloc[i]) and rsi_val >= 70:
            blocks.append(RejectionBlock(
                df["high"].iloc[i], df["low"].iloc[i], i, "bear", rsi_val))
        if not np.isnan(pl.iloc[i]) and rsi_val <= 30:
            blocks.append(RejectionBlock(
                df["high"].iloc[i], df["low"].iloc[i], i, "bull", rsi_val))
    # Mitigation
    c, h, l = df["close"], df["high"], df["low"]
    for b in blocks:
        for j in range(b.bar_index+1, len(df)):
            ref_top = c.iloc[j] if mit_method == "Close" else h.iloc[j]
            ref_bot = c.iloc[j] if mit_method == "Close" else l.iloc[j]
            if b.direction == "bull" and ref_bot < b.bottom:
                b.mitigated = True; break
            if b.direction == "bear" and ref_top > b.top:
                b.mitigated = True; break
    return blocks


# ════════════════════════════════════════════════════════════
# I. EQUAL HIGHS / EQUAL LOWS — EQH/EQL liquidity pools
#    Port of equalHighsShow/equalLowsShow engine
# ════════════════════════════════════════════════════════════

@dataclass
class EqualLevel:
    level: float
    bar_indices: List[int]
    kind: str    # "EQH" | "EQL"
    swept: bool = False

def detect_equal_levels(df: pd.DataFrame, left: int = 5, right: int = 5,
                          tol_pct: float = 0.03) -> List[EqualLevel]:
    """Detect EQH/EQL: two or more pivot highs/lows within tol_pct% of each other."""
    ph = pivot_high(df, left, right)
    pl = pivot_low(df,  left, right)
    h, l = df["high"], df["low"]
    levels: List[EqualLevel] = []

    # Cluster pivot highs
    high_pivots = [(i, float(ph.iloc[i])) for i in range(len(df)) if not np.isnan(ph.iloc[i])]
    used = set()
    for a, (ia, va) in enumerate(high_pivots):
        if a in used: continue
        cluster = [(ia, va)]
        for b, (ib, vb) in enumerate(high_pivots):
            if b <= a or b in used: continue
            if abs(vb - va) / va * 100 <= tol_pct * 100:
                cluster.append((ib, vb)); used.add(b)
        if len(cluster) >= 2:
            avg = np.mean([v for _, v in cluster])
            bars = [i for i, _ in cluster]
            eql = EqualLevel(avg, bars, "EQH")
            # Check if swept
            for j in range(max(bars)+1, len(df)):
                if h.iloc[j] > avg * (1 + tol_pct/100):
                    eql.swept = True; break
            levels.append(eql); used.add(a)

    # Cluster pivot lows
    low_pivots = [(i, float(pl.iloc[i])) for i in range(len(df)) if not np.isnan(pl.iloc[i])]
    used = set()
    for a, (ia, va) in enumerate(low_pivots):
        if a in used: continue
        cluster = [(ia, va)]
        for b, (ib, vb) in enumerate(low_pivots):
            if b <= a or b in used: continue
            if abs(vb - va) / va * 100 <= tol_pct * 100:
                cluster.append((ib, vb)); used.add(b)
        if len(cluster) >= 2:
            avg = np.mean([v for _, v in cluster])
            bars = [i for i, _ in cluster]
            eql = EqualLevel(avg, bars, "EQL")
            for j in range(max(bars)+1, len(df)):
                if l.iloc[j] < avg * (1 - tol_pct/100):
                    eql.swept = True; break
            levels.append(eql); used.add(a)

    return levels


# ════════════════════════════════════════════════════════════
# J. NWOG / NDOG — New Week/Day Opening Gap
#    Port of NWOG/NDOG engine in Infinity Core
# ════════════════════════════════════════════════════════════

@dataclass
class OpeningGap:
    kind: str      # "NWOG" | "NDOG"
    top: float
    bottom: float
    mid: float
    bar_index: int
    closed: bool = False

def detect_opening_gaps(df: pd.DataFrame,
                         df_timestamps: Optional[pd.Series] = None,
                         max_nwog: int = 3, max_ndog: int = 1) -> List[OpeningGap]:
    """Detect New Week/Day Opening Gaps: gap between prev session close and
    current session open. Requires datetime column in df."""
    gaps: List[OpeningGap] = []
    if "datetime" not in df.columns: return gaps
    dt = pd.to_datetime(df["datetime"])
    o, c = df["open"], df["close"]
    nwog_count = 0; ndog_count = 0
    for i in range(1, len(df)):
        prev_c = float(c.iloc[i-1])
        cur_o  = float(o.iloc[i])
        gap    = abs(cur_o - prev_c)
        if gap == 0: continue
        top    = max(cur_o, prev_c)
        bot    = min(cur_o, prev_c)
        mid    = (top + bot) / 2
        # New Week: Monday (weekday=0)
        if dt.iloc[i].weekday() == 0 and nwog_count < max_nwog:
            gaps.append(OpeningGap("NWOG", top, bot, mid, i))
            nwog_count += 1
        # New Day: any day gap > 0 (open != prev close)
        elif dt.iloc[i].date() != dt.iloc[i-1].date() and ndog_count < max_ndog:
            gaps.append(OpeningGap("NDOG", top, bot, mid, i))
            ndog_count += 1
    # Check if price closed the gap
    h, l = df["high"], df["low"]
    for g in gaps:
        for j in range(g.bar_index+1, len(df)):
            if l.iloc[j] <= g.bottom and h.iloc[j] >= g.top:
                g.closed = True; break
    return gaps


# ════════════════════════════════════════════════════════════
# K. FIB OTE ENGINE — ATR-filtered BOS/CHoCH → OTE 0.618–0.886
#    Port of FIBONACCI STRUCTURE INJECT in Infinity Core
# ════════════════════════════════════════════════════════════

@dataclass
class FibOTE:
    direction: str        # "bullish" | "bearish"
    anchor_high: float
    anchor_low: float
    p236: float; p382: float; p500: float
    p618: float; p786: float; p886: float
    ote_top: float        # max(p618, p886)
    ote_bottom: float     # min(p618, p886)
    bar_index: int

class FibOTEEngine:
    """ATR-filtered swing pivots → BOS/CHoCH state → OTE zone 0.618–0.886.
    Mirrors _fib_bias + _fib_natDir logic in Infinity Core exactly."""
    def __init__(self, swing_len: int = 10, atr_mult: float = 0.5):
        self.swing_len = swing_len
        self.atr_mult  = atr_mult

    def compute(self, df: pd.DataFrame) -> Optional[FibOTE]:
        ph = pivot_high(df, self.swing_len, self.swing_len)
        pl = pivot_low(df,  self.swing_len, self.swing_len)
        a  = _atr(df, 14)
        c, h, l = df["close"], df["high"], df["low"]
        sw_h1 = sw_l1 = np.nan
        bias  = 0
        nat_h = nat_l = np.nan
        nat_h_live = nat_l_live = False
        nat_dir = 0
        last_bar = -1
        for i in range(len(df)):
            atr_min = float(a.iloc[i] * self.atr_mult) if not np.isnan(a.iloc[i]) else 0.0
            if not np.isnan(ph.iloc[i]):
                if np.isnan(sw_l1) or (ph.iloc[i] - sw_l1) >= atr_min:
                    sw_h1 = ph.iloc[i]
            if not np.isnan(pl.iloc[i]):
                if np.isnan(sw_h1) or (sw_h1 - pl.iloc[i]) >= atr_min:
                    sw_l1 = pl.iloc[i]
            bull = not np.isnan(sw_h1) and c.iloc[i] > sw_h1
            bear = not np.isnan(sw_l1) and c.iloc[i] < sw_l1
            if bull and bear:
                bear = False if bias <= 0 else True; bull = not bear
            if bull:
                is_choch = bias <= 0
                bias = 1; nat_dir = 1; last_bar = i
                nat_h = h.iloc[i]; nat_h_live = True
                nat_l = sw_l1 if not np.isnan(sw_l1) else l.iloc[i]; nat_l_live = False
            if bear:
                is_choch = bias >= 0
                bias = -1; nat_dir = -1; last_bar = i
                nat_l = l.iloc[i]; nat_l_live = True
                nat_h = sw_h1 if not np.isnan(sw_h1) else h.iloc[i]; nat_h_live = False
            # Track natural high/low extension
            if not bull and not bear:
                if nat_h_live and not np.isnan(nat_h) and h.iloc[i] > nat_h:
                    nat_h = h.iloc[i]
                if nat_l_live and not np.isnan(nat_l) and l.iloc[i] < nat_l:
                    nat_l = l.iloc[i]
            if not np.isnan(ph.iloc[i]):
                if nat_h_live: nat_h = ph.iloc[i]; nat_h_live = False
                elif not np.isnan(nat_h) and ph.iloc[i] != nat_h: nat_h = ph.iloc[i]
            if not np.isnan(pl.iloc[i]):
                if nat_l_live: nat_l = pl.iloc[i]; nat_l_live = False
                elif not np.isnan(nat_l) and pl.iloc[i] != nat_l: nat_l = pl.iloc[i]

        if nat_dir == 0 or np.isnan(nat_h) or np.isnan(nat_l): return None
        rng = nat_h - nat_l
        if rng <= 0: return None

        def _ret(level):
            if nat_dir == 1:
                return nat_h - rng * level
            return nat_l + rng * level

        p236 = _ret(0.236); p382 = _ret(0.382); p500 = _ret(0.500)
        p618 = _ret(0.618); p786 = _ret(0.786); p886 = _ret(0.886)
        ote_top = max(p618, p886)
        ote_bot = min(p618, p886)
        return FibOTE(
            direction="bullish" if nat_dir == 1 else "bearish",
            anchor_high=nat_h, anchor_low=nat_l,
            p236=p236, p382=p382, p500=p500,
            p618=p618, p786=p786, p886=p886,
            ote_top=ote_top, ote_bottom=ote_bot,
            bar_index=last_bar,
        )


# ════════════════════════════════════════════════════════════
# L. ROV SIGNAL — D1 + H4 + H1 Bias gate combo
#    Port of _rov_computeCandidate in Infinity Core
#    Entry signal: D1 & H4 aligned + H1 not conflicting
# ════════════════════════════════════════════════════════════

@dataclass
class ROVSignal:
    signal: str         # "BULL" | "BEAR" | "NONE"
    d1_dir: int
    h4_dir: int
    h1_bias: str
    h1_conflict: bool
    entry_valid: bool    # D1+H4+H1 all aligned (strongest)
    setup_valid: bool    # D1+H4 aligned but H1 not confirmed yet

def rov_signal(d1_dir: int, h4_dir: int, h1_bias: str) -> ROVSignal:
    """Port of _rov_computeCandidate. Full entry = D1+H4+H1 all agree."""
    h1_conflict_bull = d1_dir == 1  and h4_dir == 1  and h1_bias == "BEAR"
    h1_conflict_bear = d1_dir == -1 and h4_dir == -1 and h1_bias == "BULL"
    h1_conflict = h1_conflict_bull or h1_conflict_bear

    entry_bull = d1_dir == 1  and h4_dir == 1  and h1_bias == "BULL"
    entry_bear = d1_dir == -1 and h4_dir == -1 and h1_bias == "BEAR"
    setup_bull = d1_dir == 1  and h4_dir == 1  and not h1_conflict
    setup_bear = d1_dir == -1 and h4_dir == -1 and not h1_conflict

    if entry_bull:   sig = "BULL"
    elif entry_bear: sig = "BEAR"
    elif setup_bull: sig = "BULL"
    elif setup_bear: sig = "BEAR"
    else:            sig = "NONE"

    return ROVSignal(
        signal=sig, d1_dir=d1_dir, h4_dir=h4_dir, h1_bias=h1_bias,
        h1_conflict=h1_conflict,
        entry_valid=(entry_bull or entry_bear),
        setup_valid=(setup_bull or setup_bear),
    )


# ════════════════════════════════════════════════════════════
# M. INTERNAL M15 CHoCH — Pure Structure Break (dashed)
#    Port of _m15_structBias() in Infinity Core
# ════════════════════════════════════════════════════════════

def m15_struct_bias(df: pd.DataFrame, sens: int = 3) -> int:
    """Returns 1 (bull) | -1 (bear) | 0 (neutral). Mirrors _m15_structBias()."""
    ph = pivot_high(df, sens, sens)
    pl = pivot_low(df,  sens, sens)
    c  = df["close"]
    bias = 0; swg_h = np.nan; swg_l = np.nan
    h_crossed = False; l_crossed = False
    for i in range(len(df)):
        if not np.isnan(ph.iloc[i]):
            swg_h = df["high"].iloc[max(0, i-sens)]
            h_crossed = False
        if not np.isnan(pl.iloc[i]):
            swg_l = df["low"].iloc[max(0, i-sens)]
            l_crossed = False
        if not np.isnan(swg_h) and not h_crossed and i > 0 and c.iloc[i-1] <= swg_h < c.iloc[i]:
            bias = 1; h_crossed = True
        if not np.isnan(swg_l) and not l_crossed and i > 0 and c.iloc[i-1] >= swg_l > c.iloc[i]:
            bias = -1; l_crossed = True
    return bias


# ════════════════════════════════════════════════════════════
# MASTER WRAPPER — analyze_ict()
# ════════════════════════════════════════════════════════════

def analyze_ict(df_ltf: pd.DataFrame, df_d1: pd.DataFrame, df_h4: pd.DataFrame,
                 df_h1: Optional[pd.DataFrame] = None,
                 swing_len: int = 5) -> Dict:
    """Runs the full Infinity Core ICT/SMC stack.
    df_h1 is optional; if None, df_h4 is used as proxy for H1 Bias Engine."""
    df_h1_actual = df_h1 if df_h1 is not None else df_h4

    # A. Market Structure
    ms = MarketStructure(swing_len=swing_len)
    structure_events = ms.update(df_ltf)

    # B. HTF Bias (D1 + H4)
    bias_htf = htf_bias(df_d1, df_h4)

    # C. H1 Bias Engine
    bias_h1 = h1_bias(df_h1_actual)

    # D. Smart OB
    sob = SmartOrderBlock()
    obs = sob.detect(df_ltf)

    # E. FVG
    fvgs = detect_fvg(df_ltf)

    # F. Strong FVG
    sfvgs = [f for f in detect_fvg(df_ltf) if f.is_strong]

    # G. BPR
    bprs = detect_bpr(fvgs)

    # H. Rejection Blocks
    try:
        rjbs = detect_rejection_blocks(df_ltf)
    except Exception:
        rjbs = []

    # I. Equal Levels
    eq_levels = detect_equal_levels(df_ltf)

    # J. NWOG/NDOG
    gaps = detect_opening_gaps(df_ltf)

    # K. Fib OTE
    fib_engine = FibOTEEngine(swing_len=swing_len)
    ote = fib_engine.compute(df_h4)

    # L. ROV Signal
    rov = rov_signal(bias_htf["d1_dir"], bias_htf["h4_dir"], bias_h1["bias"])

    # M. M15 internal CHoCH
    m15_bias = m15_struct_bias(df_ltf, sens=3)

    return {
        "htf_bias": bias_htf,
        "h1_bias": bias_h1,
        "rov_signal": rov,
        "m15_bias": m15_bias,
        "market_structure_events": structure_events,
        "last_structure_event": structure_events[-1] if structure_events else None,
        "order_blocks_active":  [o for o in obs if not o.broken],
        "order_blocks_breaker": [o for o in obs if o.is_breaker],
        "fvg_active":   [f for f in fvgs  if not f.mitigated],
        "sfvg_active":  [f for f in sfvgs if not f.mitigated],
        "bpr_active":   [b for b in bprs  if not b.invalidated],
        "rejection_blocks": [r for r in rjbs if not r.mitigated],
        "equal_levels": eq_levels,
        "opening_gaps": gaps,
        "fib_ote": ote,
    }

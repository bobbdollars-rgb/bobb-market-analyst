"""
telegram_formatter.py — Format output 100% sesuai Prompt_Claude.txt
5 bagian wajib:
  1. Market Context & Regime
  2. Key Levels
  3. Price Action & Confluence
  4. Trade Setup (TP1/TP2/TP3, R:R >= 1:2)
  5. Risk Management (max risk % + macro risk terbesar)
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass
class SignalReport:
    pair: str
    timestamp_wib: dt.datetime
    macro_lines: List[str]

    # 1. Market Context
    regime: str
    adx_h4: float
    h4_close: float
    h4_ema50: float
    h4_ema200: float
    d1_close: float
    d1_ema50: float
    d1_ema200: float
    # 2. Key Levels
    key_support: str
    key_resistance: str

    # 3. Price Action & Confluence
    rsi_m15: float
    rsi_m15_zone: str
    rsi_h1: float
    rsi_h1_zone: str
    chart_pattern_name: str
    chart_pattern_status: str
    candlestick_note: str
    divergence_note: str
    divergence_high_priority: bool

    # ICT/SMC
    htf_bias: str
    market_structure: str
    pd_array: str
    kill_zone_next: str

    # Elliott Wave
    ew_position: str
    ew_degree: str
    ew_main_scenario: str
    ew_alt_scenario: str
    ew_invalidation: str

    # Fibonacci & Harmonic
    fib_swing_ref: str
    fib_ote_zone: str
    harmonic_prz: str

    # Astronacci
    lunar_phase_name: str
    lunar_bias: str
    fib_time_active: bool
    planetary_active: bool
    planetary_note: str
    astro_score: int

    # Confluence Score
    score_breakdown: Dict[str, int]
    score_total: int
    score_max: int
    conviction: str
    conviction_emoji: str
    winrate: str

    # 4. Trade Setup
    bias_emoji: str
    bias_text: str
    signal: str
    entry_zone: str
    stop_loss: str
    tp1: str
    tp2: str
    tp3: str
    rr_ratio: str
    invalidation_setup: str
    size: str

    # 5. Risk Management
    macro_risk: str
    max_risk_pct: str
    macro_warning: str = ""
    sl_pips: str = ""   # SL distance in pips/points

    # ICT Extra (H1 Bias Engine, ROV Signal, M15 Bias)
    h1_bias: str = ""
    rov_signal: str = ""
    m15_bias: int = 0


def format_signal_message(r: SignalReport) -> str:
    ts  = r.timestamp_wib.strftime("%d %b %Y %H:%M WIB")
    sep = "─" * 30

    parts = [
        f"📊 <b>{r.signal} {r.pair}</b>  {r.conviction_emoji} {r.conviction}",
        f"🕐 {ts}",
        sep,
        f"{r.bias_emoji} Bias    : {r.htf_bias} | Score {r.score_total}/14 (~{r.winrate} WR)",
        f"📍 Entry   : {r.entry_zone}",
        f"🛑 SL      : {r.stop_loss}",
        f"🎯 TP1     : {r.tp1}",
        f"🎯 TP2     : {r.tp2}",
        f"🎯 TP3     : {r.tp3}",
        f"📐 R:R     : {r.rr_ratio}",
        f"💰 Size    : {r.size}",
        sep,
    ]

    # Macro warning kalau ada Tier 1 imminent
    if r.macro_warning:
        parts.append(r.macro_warning)

    # Calendar events relevan (max 2)
    if r.macro_lines:
        relevant = [l for l in r.macro_lines if "WIB" in l][:2]
        for line in relevant:
            parts.append(line.strip())

    if r.macro_warning or any("WIB" in l for l in r.macro_lines):
        parts.append(sep)

    return "\n".join(parts)

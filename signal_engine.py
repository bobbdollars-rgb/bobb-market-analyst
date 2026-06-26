"""
signal_engine.py — Bobb Market Analyst Signal Engine
Logika 100% sesuai Prompt_Claude.txt:

  1. Market Context & Regime  — H4/D1 trend, Bullish/Bearish/Sideways (ADX)
  2. Key Levels               — S/R pivot terkuat dari H4/D1
  3. Price Action & Confluence — EMA50/200, RSI value+zone, chart pattern, divergence
  4. Trade Setup              — Bias, Entry Zone, SL (swing H/L), TP1/TP2/TP3 + pips, R:R >= 1:2
  5. Risk Management          — macro risk terbesar + max risk % per trade

Multi-pair: semua 11 pairs dengan logika identik (sesuai permintaan).
SL = swing High/Low ICT yang jelas (titik invalidasi), bukan % arbitrary.
TP output = harga level + jarak pips/points.

Signal fire rules:
  - score >= MIN_SCORE_TO_SEND (default 6)
  - signal BUY atau SELL
  - htf_bias_aligned + h4_choch_bos + fib_ote_or_prz semua True (mandatory gates)
  - 24 jam nonstop, tidak tergantung Kill Zone
"""

from __future__ import annotations

import datetime as dt
import traceback
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

import data_fetch as DF
import ict_engine as ICT
import elliott_wave as EW
import harmonic_patterns as HP
import chart_patterns as CP
import divergence as DIV
import astronacci as ASTRO
import killzone as KZ
import confluence_score as CS
import news_detector as ND
import telegram_formatter as FMT
import trade_tracker as TT
try:
    import xgboost_filter as XGB
    _XGB_AVAILABLE = True
except ImportError:
    _XGB_AVAILABLE = False

WIB = dt.timezone(dt.timedelta(hours=7))

PAIRS_TO_ANALYZE: List[str] = DF.ALL_PAIRS
EW_SENSITIVITIES = [8]
HARMONIC_SENSITIVITIES = [10]
MS_SWING_LEN = 5

MIN_SCORE_TO_SEND = 6
SEND_SUMMARY_IF_NO_SIGNAL = True
MANDATORY_GATES = {"htf_bias_aligned", "h4_choch_bos", "fib_ote_or_prz"}

# ── Pip/Point size per pair ──
# Gold (XAUUSD/XAGUSD): 1 pip = $0.01 (price quoted to 2dp, 1 point = $0.01)
# Crypto (BTC/ETH): 1 point = $1
# Forex 4dp pairs (EUR, GBP, AUD, NZD, USD/CHF, USD/CAD): 1 pip = 0.0001
# Forex 2dp pairs (USD/JPY): 1 pip = 0.01
# JPY crosses (GBP/JPY, EUR/JPY): 1 pip = 0.01
PIP_SIZE: dict = {
    "XAUUSD":  0.01,    # Gold  — 1 pip = $0.01
    "BTCUSDT": 1.0,     # BTC   — 1 point = $1
    "EURUSD":  0.0001,
    "GBPUSD":  0.0001,
    "USDJPY":  0.01,
    "GBPJPY":  0.01,
}
PIP_LABEL: dict = {
    "XAUUSD":  "pts",
    "BTCUSDT": "pts",
}  # forex defaults to "pips"

def _pip_size(pair: str) -> float:
    return PIP_SIZE.get(pair.upper(), 0.0001)

def _pip_label(pair: str) -> str:
    return PIP_LABEL.get(pair.upper(), "pips")

def _to_pips(price_diff: float, pair: str) -> float:
    ps = _pip_size(pair)
    return round(abs(price_diff) / ps, 1) if ps else 0.0

def _fmt_tp(level: float, entry_ref: float, pair: str, rr: float) -> str:
    pips = _to_pips(abs(level - entry_ref), pair)
    lbl  = _pip_label(pair)
    return f"{level:.4f} ({pips:.0f} {lbl} | R:R 1:{rr:.1f})"

def _fmt_sl(level: float, entry_ref: float, pair: str) -> str:
    pips = _to_pips(abs(level - entry_ref), pair)
    lbl  = _pip_label(pair)
    return f"{level:.4f} ({pips:.0f} {lbl})"

def _swing_sl(df_h4: pd.DataFrame, bullish: bool,
               swing_len: int = 5, buffer_atr_mult: float = 0.1) -> float:
    """SL = swing Low (bullish) or swing High (bearish) H4 + ATR buffer."""
    from ict_engine import pivot_high, pivot_low, _atr
    ph      = pivot_high(df_h4, swing_len, swing_len)
    pl      = pivot_low(df_h4,  swing_len, swing_len)
    atr_val = float(_atr(df_h4, 14).iloc[-1])
    buf     = atr_val * buffer_atr_mult
    last_c  = float(df_h4["close"].iloc[-1])
    if bullish:
        lows = sorted(
            [float(pl.iloc[i]) for i in range(len(df_h4))
             if not np.isnan(pl.iloc[i]) and float(pl.iloc[i]) < last_c],
            reverse=True
        )
        return round((lows[0] - buf) if lows else (last_c - atr_val * 2), 4)
    else:
        highs = sorted(
            [float(ph.iloc[i]) for i in range(len(df_h4))
             if not np.isnan(ph.iloc[i]) and float(ph.iloc[i]) > last_c]
        )
        return round((highs[0] + buf) if highs else (last_c + atr_val * 2), 4)



# ════════════════════════════════════════════════════════════
# HELPERS — semua indicator yang di-prompt
# ════════════════════════════════════════════════════════════

def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _rsi(close: pd.Series, length: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/length, adjust=False).mean()
    rs = gain / loss
    rsi_series = 100 - (100 / (1 + rs))
    return round(float(rsi_series.iloc[-1]), 1)


def _adx(df: pd.DataFrame, length: int = 14) -> float:
    """ADX untuk deteksi Sideways/Trending. ADX < 20 = Sideways."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    dm_plus = ((high - prev_high).clip(lower=0)).where(
        (high - prev_high) > (prev_low - low), 0)
    dm_minus = ((prev_low - low).clip(lower=0)).where(
        (prev_low - low) > (high - prev_high), 0)
    atr = tr.ewm(span=length, adjust=False).mean()
    di_plus = 100 * dm_plus.ewm(span=length, adjust=False).mean() / atr
    di_minus = 100 * dm_minus.ewm(span=length, adjust=False).mean() / atr
    dx = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus)).fillna(0)
    adx = dx.ewm(span=length, adjust=False).mean()
    return round(float(adx.iloc[-1]), 1)


def _key_levels(df: pd.DataFrame, n_levels: int = 3) -> Tuple[List[float], List[float]]:
    """Pivot-based S/R levels dari H4/D1. Returns (supports, resistances)."""
    from ict_engine import pivot_high, pivot_low
    ph = pivot_high(df, left=5, right=5)
    pl = pivot_low(df, left=5, right=5)
    last_close = df["close"].iloc[-1]

    res_levels = sorted(
        [v for v in ph.dropna().values if v > last_close],
        key=lambda x: abs(x - last_close)
    )[:n_levels]
    sup_levels = sorted(
        [v for v in pl.dropna().values if v < last_close],
        key=lambda x: abs(x - last_close)
    )[:n_levels]
    return sup_levels, res_levels


def _market_regime(htf_bias: str, adx_h4: float) -> str:
    """Returns 'Bullish', 'Bearish', atau 'Sideways/Consolidation'."""
    if adx_h4 < 20:
        return "Sideways/Consolidation"
    return htf_bias.capitalize() if htf_bias in ("BULLISH", "BEARISH") else "Sideways/Consolidation"


def _rsi_zone(rsi_val: float) -> str:
    if rsi_val >= 70:
        return "Overbought"
    if rsi_val <= 30:
        return "Oversold"
    if rsi_val >= 55:
        return "Bullish zone"
    if rsi_val <= 45:
        return "Bearish zone"
    return "Neutral"


def _macro_risk(pair: str, macro_lines: List[str]) -> str:
    """Identifikasi risiko makro terbesar untuk pair ini."""
    pair_upper = pair.upper()
    risk_map = {
        "XAUUSD": "NFP, CPI, Fed rate decision — Gold sensitif terhadap real yield USD",
        "XAGUSD": "Industrial demand + Fed policy — Silver lebih volatile dari Gold",
        "BTCUSDT": "Regulatory news, ETF flows, liquidation cascade — cek funding rate",
        "ETHUSDT": "Regulatory + ETH staking yield — korelasi tinggi dengan BTC",
        "EURUSD": "ECB vs Fed divergence, CPI Eurozone, NFP",
        "GBPUSD": "BoE policy, UK CPI, NFP — Cable volatile saat London open",
        "USDJPY": "BoJ intervention risk, US-Japan yield spread, NFP",
        "USDCHF": "SNB policy, risk-off flows — CHF safe haven",
        "AUDUSD": "China PMI, Iron ore, RBA — commodity currency",
        "NZDUSD": "RBNZ, dairy prices, risk sentiment",
        "USDCAD": "Oil price, BoC policy, CAD correlated ke crude",
    }
    base_risk = risk_map.get(pair_upper, "Cek economic calendar untuk currency terkait")
    # Tambah warning kalau ada breaking news
    if any("Breaking" in l for l in macro_lines):
        base_risk = "⚠️ Ada breaking news — " + base_risk
    return base_risk


def _winrate_estimate(score_total: int, score_max: int) -> str:
    """Estimasi winrate berdasarkan confluence score (per prompt: >65% threshold)."""
    pct = 50 + (score_total / score_max) * 40  # 50%-90% range
    return f"~{pct:.0f}%"


# ════════════════════════════════════════════════════════════
# MACRO SNAPSHOT
# ════════════════════════════════════════════════════════════

def _format_macro_lines(macro: dict) -> List[str]:
    """Convert macro dict to lines for Telegram Risk Management section."""
    lines = []
    # Warning dulu kalau ada Tier 1 imminent
    if macro.get("warning"):
        lines.append(macro["warning"])
    # Relevant events untuk pair ini
    rel = macro.get("relevant_events", [])
    if rel:
        lines.append(f"Event relevan hari ini ({macro.get('source','')}):")
        for e in rel:
            tier_tag = f" {e.get('tier_label','')}" if e.get("tier", 0) >= 1 else ""
            warn_tag = f" — {e.get('tier_warning','')}" if e.get("tier", 0) == 1 else ""
            lines.append(f"  {e['impact_emoji']} {e['time_wib']} WIB {e['currency']} — {e['title']}{tier_tag}{warn_tag}")
    else:
        lines.append("Tidak ada event relevan terjadwal hari ini.")
    # Breaking news
    for b in macro.get("breaking_news", [])[:1]:
        lines.append(f"📰 Breaking: {b.get('title','')[:120]}")
    return lines


def build_macro_snapshot(now_wib: dt.datetime, pair: str = "") -> dict:
    """
    Fetch macro context untuk Risk Management section di sinyal.
    Returns dict dengan:
      - events_today: list event high/medium impact hari ini
      - upcoming_tier1: event Tier 1 dalam 2 jam ke depan (warning!)
      - breaking: breaking news terbaru
      - risk_text: ringkasan risiko untuk pair ini
      - warning: string warning kalau ada Tier 1 imminent
    """
    today = now_wib.date()
    events_today = []
    upcoming_tier1 = []
    breaking_news = []
    warning = ""

    # ── Fetch calendar ──
    try:
        events, source = ND.fetch_events(today)
        # Filter high/medium impact saja
        events_today = [e for e in events if e.get("impact_priority", 0) >= 2]

        # Check Tier 1 event dalam 2 jam ke depan
        for e in events_today:
            tier = e.get("tier", 0)
            if tier == 1:
                try:
                    event_dt = e["dt_wib"]
                    now_naive = now_wib.replace(tzinfo=None)
                    if hasattr(event_dt, "tzinfo") and event_dt.tzinfo:
                        event_dt = event_dt.replace(tzinfo=None)
                    mins_away = (event_dt - now_naive).total_seconds() / 60
                    if 0 < mins_away <= 120:
                        upcoming_tier1.append((e, int(mins_away)))
                except Exception:
                    pass
    except Exception as ex:
        events_today = []
        source = "unavailable"

    # Warning kalau Tier 1 imminent
    if upcoming_tier1:
        names = ", ".join(f"{e['title']} ({m}m)" for e, m in upcoming_tier1[:2])
        warning = f"⚠️ TIER 1 EVENT DALAM {upcoming_tier1[0][1]} MENIT: {names} — HINDARI ENTRY BARU"

    # ── Breaking news ──
    try:
        raw_breaking = ND.fetch_rss_breaking(
            now_wib.astimezone(dt.timezone.utc).replace(tzinfo=None))
        if raw_breaking:
            breaking_news = raw_breaking[:2]
    except Exception:
        pass

    # ── Risk text per pair ──
    pair_risk_map = {
        "XAUUSD":  "NFP, CPI, Fed rate decision — Gold sensitif real yield USD",
        "BTCUSDT": "Regulatory news, ETF flows, funding rate, liquidation cascade",
        "EURUSD":  "ECB vs Fed divergence, CPI Eurozone, NFP",
        "GBPUSD":  "BoE policy, UK CPI, NFP — volatile saat London open",
        "USDJPY":  "BoJ intervention risk, US-Japan yield spread, NFP",
        "GBPJPY":  "BoE + BoJ divergence — volatile, range besar tiap sesi",
    }
    base_risk = pair_risk_map.get(pair.upper(), "Cek economic calendar pair terkait")

    # Upgrade: tambah event spesifik yang relevan ke pair ini
    pair_currency = {
        "XAUUSD":  ["USD", "XAU"],
        "BTCUSDT": ["USD", "BTC"],
        "EURUSD":  ["USD", "EUR"],
        "GBPUSD":  ["USD", "GBP"],
        "USDJPY":  ["USD", "JPY"],
        "GBPJPY":  ["GBP", "JPY"],
    }.get(pair.upper(), ["USD"])

    relevant_events = [
        e for e in events_today
        if e.get("currency") in pair_currency
    ][:3]

    return {
        "events_today": events_today[:5],
        "relevant_events": relevant_events,
        "upcoming_tier1": upcoming_tier1,
        "breaking_news": breaking_news,
        "base_risk": base_risk,
        "warning": warning,
        "source": source if events_today else "unavailable",
    }


# ════════════════════════════════════════════════════════════
# PER-PAIR ANALYSIS
# ════════════════════════════════════════════════════════════

def analyze_pair(pair: str, now_wib: dt.datetime,
                  data: Optional[dict] = None) -> FMT.SignalReport:

    tf = data if data is not None else DF.fetch_multi_timeframe(pair)
    df_m15 = tf["M15"]
    df_h1 = tf.get("H1", tf["H4"])   # real H1 if available, H4 as fallback
    df_h4 = tf["H4"]
    df_d1 = tf["D1"]

    # ── 1. Market Context & Regime ──
    ict = ICT.analyze_ict(df_m15, df_d1, df_h4, df_h1=df_h1, swing_len=MS_SWING_LEN)
    htf_bias = ict["htf_bias"]["bias"]
    h1_b     = ict["h1_bias"]
    rov      = ict["rov_signal"]
    m15_b    = ict["m15_bias"]
    adx_h4   = _adx(df_h4)
    regime   = _market_regime(htf_bias, adx_h4)

    # EMA 50 + EMA 200 (H4 & D1)
    h4_ema50  = round(float(_ema(df_h4["close"], 50).iloc[-1]), 4)
    h4_ema200 = round(float(_ema(df_h4["close"], 200).iloc[-1]), 4)
    d1_ema50  = round(float(_ema(df_d1["close"], 50).iloc[-1]), 4)
    d1_ema200 = round(float(_ema(df_d1["close"], 200).iloc[-1]), 4)
    h4_close  = round(float(df_h4["close"].iloc[-1]), 4)
    d1_close  = round(float(df_d1["close"].iloc[-1]), 4)

    # ── 2. Key Levels ──
    sup_h4, res_h4 = _key_levels(df_h4)
    sup_d1, res_d1 = _key_levels(df_d1)
    def fmt_levels(levels): return " | ".join(f"{l:.4f}" for l in levels[:2]) if levels else "N/A"
    last_close = float(df_m15["close"].iloc[-1])
    key_support    = fmt_levels(sorted(set(sup_h4 + sup_d1), key=lambda x: abs(x - last_close))[:2])
    key_resistance = fmt_levels(sorted(set(res_h4 + res_d1), key=lambda x: abs(x - last_close))[:2])

    # EQH/EQL as additional key levels
    eq_levels = ict["equal_levels"]
    eqh = [e for e in eq_levels if e.kind == "EQH" and not e.swept]
    eql = [e for e in eq_levels if e.kind == "EQL" and not e.swept]
    eqh_text = f" | EQH {eqh[0].level:.4f}" if eqh else ""
    eql_text = f" | EQL {eql[0].level:.4f}" if eql else ""

    # ── 3. Price Action & Confluence ──
    # RSI
    rsi_m15      = _rsi(df_m15["close"])
    rsi_h1       = _rsi(df_h4["close"])
    rsi_m15_zone = _rsi_zone(rsi_m15)
    rsi_h1_zone  = _rsi_zone(rsi_h1)

    # Market Structure
    last_struct      = ict["last_structure_event"]
    market_structure = (f"{last_struct.kind} {last_struct.direction} @ {last_struct.level:.4f}"
                         if last_struct else "Belum ada structure break")
    h4_choch_bos     = last_struct is not None and last_struct.bar_index >= len(df_m15) - 50

    # PD Array — priority: BPR > OTE > Strong FVG > regular FVG > Rejection Block
    ote   = ict["fib_ote"]
    bprs  = ict["bpr_active"]
    sfvgs = ict["sfvg_active"]
    fvgs  = ict["fvg_active"]
    rjbs  = ict["rejection_blocks"]

    fib_ote_hit = False
    if bprs:
        b = bprs[0]
        pd_array = f"BPR {b.direction} {b.bottom:.4f}-{b.top:.4f}"
        fib_ote_hit = b.bottom <= last_close <= b.top
    elif ote:
        pd_array = f"OTE {ote.direction} {ote.ote_bottom:.4f}-{ote.ote_top:.4f}"
        fib_ote_hit = ote.ote_bottom <= last_close <= ote.ote_top
    elif sfvgs:
        f0 = sfvgs[0]
        pd_array = f"Strong FVG {f0.direction} {f0.bottom:.4f}-{f0.top:.4f}"
        fib_ote_hit = f0.bottom <= last_close <= f0.top
    elif fvgs:
        f0 = fvgs[0]
        pd_array = f"FVG {f0.direction} {f0.bottom:.4f}-{f0.top:.4f}"
        fib_ote_hit = f0.bottom <= last_close <= f0.top
    elif rjbs:
        r0 = rjbs[0]
        pd_array = f"Rejection Block {r0.direction} {r0.bottom:.4f}-{r0.top:.4f}"
        fib_ote_hit = r0.bottom <= last_close <= r0.top
    else:
        pd_array    = "Tidak ada PD Array aktif"
        fib_ote_hit = False

    # H1 Bias detail
    h1_bias_text = (f"{h1_b['bias']} (votes B:{h1_b['bull_votes']} S:{h1_b['bear_votes']},"
                    f" ADX:{h1_b['adx']}, LaRSI:{h1_b['la_rsi']}, CCI:{h1_b['cci50']})")

    # ROV Signal text
    rov_text = (f"{rov.signal} [D1:{rov.d1_dir:+d} H4:{rov.h4_dir:+d} H1:{rov.h1_bias}]"
                f" Entry:{rov.entry_valid} Setup:{rov.setup_valid}")

    # Elliott Wave
    ew_eng    = EW.ElliottWaveEngine(length=EW_SENSITIVITIES[0])
    waves     = ew_eng.run(df_h4)
    ew_report = EW.describe_position(waves, length=EW_SENSITIVITIES[0])
    ew_clear  = bool(waves) and waves[-1].valid

    # Harmonic
    harmonics        = HP.scan_harmonics(df_h4, length=HARMONIC_SENSITIVITIES[0])
    harmonic_hit     = len(harmonics) > 0
    harmonic_prz_text = (f"Ada ({harmonics[0].name}, {harmonics[0].direction})"
                          if harmonic_hit else "Tidak ada")

    # Chart Pattern
    cp_result = CP.analyze_chart_patterns(df_h4)
    confirmed_patterns = [p for plist in cp_result.values()
                           for p in plist if isinstance(p, CP.ChartPattern) and p.status == "Confirmed"]
    chart_pattern_hit = len(confirmed_patterns) > 0
    if chart_pattern_hit:
        cpat = confirmed_patterns[0]
        chart_pattern_name, chart_pattern_status = cpat.name, cpat.status
    else:
        forming = [p for plist in cp_result.values() for p in plist if isinstance(p, CP.ChartPattern)]
        chart_pattern_name  = forming[0].name   if forming else "Tidak ada"
        chart_pattern_status = forming[0].status if forming else "-"
    candles          = cp_result["candlestick"]
    candlestick_note = ", ".join(f"{c.name} ({c.direction})" for c in candles) if candles else "Tidak ada"

    # Divergence
    div_multi             = DIV.analyze_divergence_multi_tf({"LTF": df_m15, "MTF": df_h4}, indicator="RSI")
    ltf_divs              = div_multi["by_timeframe"]["LTF"]
    divergence_hit        = len(ltf_divs) > 0
    divergence_note       = ltf_divs[-1].kind if ltf_divs else "Tidak ada"
    divergence_high_priority = len(div_multi["high_priority_multi_tf"]) > 0

    # Astronacci
    target_date    = now_wib.date()
    lunar          = ASTRO.get_lunar_phase(target_date)
    bias_word      = "Bullish" if htf_bias == "BULLISH" else ("Bearish" if htf_bias == "BEARISH" else "Neutral")
    swing_high_idx = df_d1["high"].idxmax()
    swing_low_idx  = df_d1["low"].idxmin()
    try:
        s_start  = df_d1["datetime"].iloc[min(swing_high_idx, swing_low_idx)].date()
        s_end    = df_d1["datetime"].iloc[max(swing_high_idx, swing_low_idx)].date()
        fib_time = ASTRO.project_fib_time(s_start, s_end) if s_end > s_start else None
    except Exception:
        fib_time = None
    astro           = ASTRO.score_astronacci(target_date, bias_word, fib_time)
    planetary_events = ASTRO.active_planetary_events(target_date)
    planetary_note  = ", ".join(e.name for e in planetary_events) if planetary_events else "Tidak ada"

    # Kill Zone
    kz_now       = KZ.current_kill_zone(now_wib)
    kz_high_active = KZ.is_high_priority_active(now_wib)
    next_kz, mins_until = KZ.next_kill_zone(now_wib)
    kz_text      = (f"AKTIF: {kz_now.name}" if kz_now else
                    f"Next: {next_kz.name} dalam {mins_until:.0f} menit")

    # ── Confluence Score ──
    last_close = float(df_m15["close"].iloc[-1])   # defined here for gate calculations
    # ROV entry_valid = D1+H4+H1 semua aligned → stronger gate
    htf_aligned_full = rov.entry_valid or rov.setup_valid
    # fib_ote_or_prz = OTE hit OR BPR hit OR active FVG near price (within 2× ATR)
    # This prevents the gate being permanently blocked just because price hasn't
    # pulled back to exact OTE zone yet — active FVG/BPR in the path counts too
    from ict_engine import _atr as _ict_atr
    atr_m5 = float(_ict_atr(df_m15, 14).iloc[-1]) if len(df_m15) > 14 else 0
    fvg_near = any(
        abs(last_close - (f.top + f.bottom) / 2) < atr_m5 * 2
        for f in fvgs + sfvgs
    ) if atr_m5 > 0 else False
    bpr_near = any(
        abs(last_close - (b.top + b.bottom) / 2) < atr_m5 * 2
        for b in bprs
    ) if atr_m5 > 0 else False

    inputs = CS.ConfluenceInputs(
        htf_bias_aligned=htf_aligned_full and adx_h4 >= 20,
        elliott_wave_clear=ew_clear,
        fib_ote_or_prz=fib_ote_hit or harmonic_hit or fvg_near or bpr_near,
        chart_pattern_confirmed=chart_pattern_hit,
        harmonic_prz_hit=harmonic_hit,
        divergence_detected=divergence_hit,
        h4_choch_bos=h4_choch_bos,
        kill_zone_active=kz_high_active,
        fib_time_window_active=astro.fib_time_active,
        lunar_cycle_aligned=astro.lunar_aligned,
        planetary_aspect_major=astro.planetary_major,
    )
    result = CS.score(inputs)

    # ── 4. Trade Setup ──
    # Use ROV signal as primary direction (D1+H4+H1 aligned)
    if rov.signal == "BULL":
        bullish = True
    elif rov.signal == "BEAR":
        bullish = False
    else:
        bullish = htf_bias == "BULLISH"

    if result.total < 3 or regime == "Sideways/Consolidation" or rov.signal == "NONE":
        signal = "WAIT"
    elif bullish:
        signal = "BUY"
    else:
        signal = "SELL"

    # ── Entry Zone (PD Array priority: BPR > OTE > FVG) ──
    if ote:
        entry_zone = f"{ote.ote_bottom:.4f} - {ote.ote_top:.4f}"
        tp_base    = ote.ote_top if bullish else ote.ote_bottom
    elif bprs:
        b = bprs[0]
        entry_zone = f"{b.bottom:.4f} - {b.top:.4f}"
        tp_base    = b.top if bullish else b.bottom
    else:
        entry_zone = f"{last_close:.4f} (market price)"
        tp_base    = last_close

    # ── SL = swing High/Low H4 (titik invalidasi ICT) + ATR buffer ──
    sl   = _swing_sl(df_h4, bullish, swing_len=MS_SWING_LEN)
    risk = abs(tp_base - sl)
    if risk == 0:
        risk = abs(last_close * 0.01)

    # ── TP dengan R:R 1:2, 1:3, 1:4 ──
    tp1 = tp_base + risk * 2 if bullish else tp_base - risk * 2
    tp2 = tp_base + risk * 3 if bullish else tp_base - risk * 3
    tp3 = tp_base + risk * 4 if bullish else tp_base - risk * 4

    # ── Format dengan pip/point distance ──
    sl_pips  = _to_pips(abs(tp_base - sl), pair)
    lbl      = _pip_label(pair)
    sl_fmt   = _fmt_sl(sl, tp_base, pair)
    tp1_fmt  = _fmt_tp(tp1, tp_base, pair, 2.0)
    tp2_fmt  = _fmt_tp(tp2, tp_base, pair, 3.0)
    tp3_fmt  = _fmt_tp(tp3, tp_base, pair, 4.0)
    rr1      = 2.0

    # ── 5. Risk Management ──
    macro      = build_macro_snapshot(now_wib, pair=pair)
    macro_lines = _format_macro_lines(macro)
    macro_risk_text = macro["base_risk"]
    macro_warning   = macro["warning"]
    winrate         = _winrate_estimate(result.total, result.max_total)
    max_risk_pct_str = ("2% per trade (score >= 10 HIGH conviction)"
                        if result.total >= 10 else "1% per trade (max)")

    # ── Build Report ──
    report = FMT.SignalReport(
        pair=pair,
        timestamp_wib=now_wib,
        macro_lines=macro_lines,

        # Market Context
        regime=regime,
        adx_h4=adx_h4,
        h4_close=h4_close,
        h4_ema50=h4_ema50,
        h4_ema200=h4_ema200,
        d1_close=d1_close,
        d1_ema50=d1_ema50,
        d1_ema200=d1_ema200,
        h1_bias=h1_bias_text,
        rov_signal=rov_text,
        m15_bias=m15_b,

        # Key Levels
        key_support=key_support + eql_text,
        key_resistance=key_resistance + eqh_text,

        # Price Action
        rsi_m15=rsi_m15,
        rsi_m15_zone=rsi_m15_zone,
        rsi_h1=rsi_h1,
        rsi_h1_zone=rsi_h1_zone,
        chart_pattern_name=chart_pattern_name,
        chart_pattern_status=chart_pattern_status,
        candlestick_note=candlestick_note,
        divergence_note=divergence_note,
        divergence_high_priority=divergence_high_priority,

        # ICT/SMC
        htf_bias=htf_bias,
        market_structure=market_structure,
        pd_array=pd_array,
        kill_zone_next=kz_text,

        # Elliott Wave
        ew_position=ew_report["position"],
        ew_degree=ew_report["degree"],
        ew_main_scenario=ew_report["main_scenario"],
        ew_alt_scenario=ew_report["alternate_scenario"],
        ew_invalidation=str(ew_report["invalidation"]),

        # Fibonacci
        fib_swing_ref=(f"High {ote.anchor_high:.4f} → Low {ote.anchor_low:.4f}" if ote else "N/A"),
        fib_ote_zone=(f"{ote.ote_bottom:.4f} - {ote.ote_top:.4f}" if ote else "N/A"),
        harmonic_prz=harmonic_prz_text,

        # Astronacci
        lunar_phase_name=lunar.phase_name,
        lunar_bias=lunar.bias,
        fib_time_active=astro.fib_time_active,
        planetary_active=astro.planetary_major,
        planetary_note=planetary_note,
        astro_score=astro.total,

        # Confluence
        score_breakdown=result.breakdown,
        score_total=result.total,
        score_max=result.max_total,
        conviction=result.conviction,
        conviction_emoji=result.emoji,
        winrate=winrate,

        # Trade Setup
        bias_emoji="⬆️" if bullish else ("⬇️" if htf_bias == "BEARISH" else "➡️"),
        bias_text=htf_bias,
        signal=signal,
        entry_zone=entry_zone,
        stop_loss=sl_fmt,
        tp1=tp1_fmt,
        tp2=tp2_fmt,
        tp3=tp3_fmt,
        rr_ratio=f"1:{rr1:.1f} (min)",
        sl_pips=f"{sl_pips:.0f} {lbl}",
        invalidation_setup=f"{sl:.4f}",
        size=result.size,

        # Risk Management
        macro_risk=macro_risk_text,
        macro_warning=macro_warning,
        max_risk_pct=max_risk_pct_str,
    )
    return report


# ════════════════════════════════════════════════════════════
# SIGNAL FILTER
# ════════════════════════════════════════════════════════════

def _check_mandatory_gates(report: FMT.SignalReport) -> Tuple[bool, List[str]]:
    failed = [g for g in MANDATORY_GATES if report.score_breakdown.get(g, 0) == 0]
    return (len(failed) == 0), failed


def _is_valid_setup(report: FMT.SignalReport) -> Tuple[bool, str]:
    # Gate 1: Signal direction
    if report.signal not in ("BUY", "SELL"):
        return False, f"signal={report.signal}"

    # Gate 2: Minimum confluence score
    if report.score_total < MIN_SCORE_TO_SEND:
        return False, f"score {report.score_total}/{report.score_max} < {MIN_SCORE_TO_SEND}"

    # Gate 3: Mandatory ICT gates
    gates_ok, failed = _check_mandatory_gates(report)
    if not gates_ok:
        return False, f"mandatory gates failed: {', '.join(failed)}"

    # Gate 4: XGBoost filter (only if model is ready)
    if _XGB_AVAILABLE and XGB.is_model_ready():
        try:
            import trade_tracker as TT
            features = TT._extract_features(report)
            prob, threshold, decision = XGB.predict(features)
            if decision == "SKIP":
                return False, f"XGBoost skip (prob={prob:.2f} < threshold={threshold:.2f})"
            print(f"[XGB] {report.pair}: prob={prob:.2f} threshold={threshold:.2f} → {decision}")
        except Exception as e:
            print(f"[XGB] predict error (bypassing): {e}")
            # Don't block signal if XGB errors — rule-based is enough

    return True, "OK"


def _format_digest(skipped: List[str], now_wib: dt.datetime) -> str:
    return (
        f"📊 <b>Bobb Signal Engine</b> — {now_wib.strftime('%H:%M')} WIB\n"
        f"Tidak ada setup valid saat ini.\n"
        f"Pairs scanned: {', '.join(skipped)}\n"
        f"Threshold: score ≥ {MIN_SCORE_TO_SEND}, gates: {', '.join(MANDATORY_GATES)}"
    )


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════

def run_signal_engine(pairs: Optional[List[str]] = None, send: bool = True,
                       data_override: Optional[dict] = None) -> List[str]:
    pairs = pairs or PAIRS_TO_ANALYZE
    now_wib = dt.datetime.now(WIB)
    messages, valid_pairs, skipped_pairs = [], [], []

    # Check open trades (hit TP1 or SL) every run
    if send:
        try:
            TT.check_open_trades()
        except Exception as e:
            print(f"[tracker] check_open_trades error: {e}")

    for pair in pairs:
        try:
            pair_data = data_override.get(pair) if data_override else None
            report = analyze_pair(pair, now_wib, data=pair_data)
            valid, reason = _is_valid_setup(report)
            if valid:
                msg = FMT.format_signal_message(report)
                messages.append(msg)
                valid_pairs.append(pair)
                print(f"[✅] {pair}: {report.signal} score={report.score_total}/{report.score_max} {report.conviction} winrate={report.winrate} → SENDING")
                if send:
                    ND.send_text(msg)
                    try:
                        TT.log_signal(report)
                    except Exception as e:
                        print(f"[tracker] log_signal error: {e}")
            else:
                skipped_pairs.append(pair)
                print(f"[⏭] {pair}: {report.signal} score={report.score_total}/{report.score_max} → SKIP ({reason})")
        except Exception as e:
            print(f"[❌] {pair}: {e}\n{traceback.format_exc()}")
            skipped_pairs.append(pair)

    if not valid_pairs and SEND_SUMMARY_IF_NO_SIGNAL:
        digest = _format_digest(skipped_pairs, now_wib)
        messages.append(digest)
        if send:
            ND.send_text(digest)

    return messages


if __name__ == "__main__":
    run_signal_engine()

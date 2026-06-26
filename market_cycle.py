"""
market_cycle.py — Astronacci Market Cycle Forecast
Source: BOBB MARKET ANALYST v3.0, Step 2 (Astronacci Time Trading)
100% match dengan prompt — format output persis seperti section ASTRONACCI v3.0

Output format:
  🌙 ASTRONACCI
  Fase Bulan    : [fase + tanggal]
  Lunar Bias    : [Bullish/Bearish/Neutral]
  Fib Time      : [Ya/Tidak + proyeksi tanggal konkret]
  Planetary     : [aspek aktif + nama]
  Astro Score   : [X/5]
  Dark Moon     : [H-3 sebelum New Moon = bottom watch]
  Weekly EQ     : [Premium/Discount zone status]
  Bearish Thesis: [AKTIF/INVALIDATED/TARGET AREA]
  Reversal Windows: [tanggal konkret + prediksi arah]

Kirim ke Telegram sekali sehari jam 07:00 WIB.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Dict, Optional, Tuple
import pandas as pd
import numpy as np

import astronacci as ASTRO
import elliott_wave as EW
import ict_engine as ICT
import data_fetch as DF
import news_detector as ND

WIB = dt.timezone(dt.timedelta(hours=7))

SYNODIC_MONTH = 29.530588853

# ════════════════════════════════════════════════════════════
# BEARISH THESIS — auto-check sesuai prompt v3.0
# ════════════════════════════════════════════════════════════

BEARISH_THESIS = {
    "XAUUSD":  {"ath": 5597,   "ath_date": "Jan 2026", "bear_target": 3816, "key_level": 4100,
                 "invalidation_weekly_close": 4500,
                 "thesis": "Corrective phase dari ATH $5,597. Bear target $3,816 | Key level $4,100"},
    "BTCUSDT": {"ath": 126272, "ath_date": "Okt 2025", "bear_target": 49500, "key_level": 70000,
                 "invalidation_weekly_close": 100000,
                 "thesis": "Projected bottom $44K–$55K (Okt 2026). Bear selama di bawah $100K weekly close"},
    "EURUSD":  {"invalidation_weekly_close": 1.12,
                 "thesis": "DXY bullish = tekanan EUR. Bear selama DXY di atas 104"},
    "GBPUSD":  {"invalidation_weekly_close": 1.32,
                 "thesis": "DXY bullish + BoE dovish = tekanan GBP"},
    "USDJPY":  {"invalidation_weekly_close": 148.0,
                 "thesis": "BoJ intervention risk di atas 155. Bull selama Fed hawkish"},
    "GBPJPY":  {"invalidation_weekly_close": 185.0,
                 "thesis": "BoE + BoJ divergence. Volatile pair — range besar tiap sesi London/Tokyo"},
}

def check_thesis_status(pair: str, current_price: float, weekly_close: float) -> Dict:
    thesis = BEARISH_THESIS.get(pair, {"thesis": "Cek DXY + Fed policy"})
    invalidation = thesis.get("invalidation_weekly_close")
    bear_target  = thesis.get("bear_target")
    key_level    = thesis.get("key_level")
    # Check 3 kondisi update per prompt:
    # 1. Weekly close di atas invalidation
    # 2. Mendekati bear target (<5%)
    # 3. Di key level
    if invalidation and weekly_close > invalidation:
        status = "⚠️ INVALIDATED — cek BOS HTF + Fed pivot"
        detail = f"Weekly close {weekly_close:.4f} > invalidation {invalidation}"
    elif bear_target and current_price <= bear_target * 1.05:
        status = "🎯 TARGET AREA — watch reversal bullish"
        detail = f"Harga {current_price:.4f} mendekati bear target {bear_target}"
    elif key_level and current_price <= key_level * 1.01:
        status = "🔑 KEY LEVEL HIT — support kritis, waspadai bounce"
        detail = f"Harga {current_price:.4f} di key level {key_level}"
    else:
        status = "✅ AKTIF"
        detail = thesis.get("thesis", "Thesis masih valid")
    return {"status": status, "detail": detail, "pair": pair,
            "current_price": current_price, "weekly_close": weekly_close}


# ════════════════════════════════════════════════════════════
# LUNAR PHASE — match persis prompt v3.0 phases
# ════════════════════════════════════════════════════════════

def get_lunar_detail(today: dt.date) -> Dict:
    lunar = ASTRO.get_lunar_phase(today)
    # Dark Moon: H-3 sebelum New Moon (per prompt v3.0)
    dark_moon_window = False
    nearest_nm = None
    for nm in ASTRO.NEW_MOON_2026:
        days_to_nm = (nm - today).days
        if 0 < days_to_nm <= 3:
            dark_moon_window = True
            nearest_nm = nm
            break
    return {
        "phase_name": lunar.phase_name,
        "bias": lunar.bias,
        "in_reversal_window": lunar.in_reversal_window,
        "nearest_new_moon": lunar.nearest_new_moon,
        "nearest_full_moon": lunar.nearest_full_moon,
        "days_to_nearest": lunar.days_to_nearest_event,
        "dark_moon_window": dark_moon_window,
        "dark_moon_date": nearest_nm,
        "source": lunar.source,
    }


def get_upcoming_lunar(today: dt.date, days_ahead: int = 60) -> List[Dict]:
    end = today + dt.timedelta(days=days_ahead)
    events = []
    for nm in ASTRO.NEW_MOON_2026:
        if today <= nm <= end:
            days_away = (nm - today).days
            # Dark Moon window = 3 hari sebelum New Moon
            dark_start = nm - dt.timedelta(days=3)
            events.append({
                "date": nm, "type": "New Moon", "emoji": "🌑",
                "bias": "Reversal/Breakout — perhatikan arah breakout pertama",
                "window_start": nm - dt.timedelta(days=2),
                "window_end":   nm + dt.timedelta(days=2),
                "dark_moon_start": dark_start,
                "days_away": days_away,
            })
    for fm in ASTRO.FULL_MOON_2026:
        if today <= fm <= end:
            days_away = (fm - today).days
            events.append({
                "date": fm, "type": "Full Moon", "emoji": "🌕",
                "bias": "Potential reversal BEARISH — harga sering peak",
                "window_start": fm - dt.timedelta(days=2),
                "window_end":   fm + dt.timedelta(days=2),
                "dark_moon_start": None,
                "days_away": days_away,
            })
    events.sort(key=lambda x: x["date"])
    return events


# ════════════════════════════════════════════════════════════
# FIB TIME PROJECTION — format konkret sesuai prompt contoh
# "0.618 × 150 hari = Sep 2026 = potential reversal"
# ════════════════════════════════════════════════════════════

def compute_fib_time(pair: str, df_d1: pd.DataFrame, today: dt.date) -> Dict:
    h, l = df_d1["high"], df_d1["low"]
    hi_idx = int(h.idxmax()); lo_idx = int(l.idxmin())
    try:
        hi_date  = df_d1["datetime"].iloc[hi_idx].date()
        lo_date  = df_d1["datetime"].iloc[lo_idx].date()
        hi_price = float(h.iloc[hi_idx])
        lo_price = float(l.iloc[lo_idx])
    except Exception:
        return {"available": False, "projections": [], "swing_ref": "N/A"}

    # Tentukan swing High → Low atau Low → High
    if hi_idx < lo_idx:
        swing_start, swing_end = hi_date, lo_date
        swing_label = f"High {hi_price:.2f} ({hi_date.strftime('%b %Y')}) → Low {lo_price:.2f} ({lo_date.strftime('%b %Y')})"
        reversal_bias = "BULLISH reversal expected"
    else:
        swing_start, swing_end = lo_date, hi_date
        swing_label = f"Low {lo_price:.2f} ({lo_date.strftime('%b %Y')}) → High {hi_price:.2f} ({hi_date.strftime('%b %Y')})"
        reversal_bias = "BEARISH reversal expected"

    range_days = (swing_end - swing_start).days
    if range_days <= 0:
        return {"available": False, "projections": [], "swing_ref": swing_label}

    ratios = [
        (0.382, "reversal minor"),
        (0.618, "reversal medium"),
        (1.000, "reversal major (equal time)"),
        (1.618, "reversal extended"),
    ]
    projections = []
    for ratio, label in ratios:
        proj_days = round(range_days * ratio)
        proj_date = swing_end + dt.timedelta(days=proj_days)
        days_from_today = (proj_date - today).days
        in_window = abs(days_from_today) <= 3
        # Format seperti contoh prompt: "0.618 × 150 hari = Sep 2026"
        proj_str = (f"{ratio:.3f} × {range_days} hari = "
                    f"{proj_date.strftime('%d %b %Y')} "
                    f"({'+' if days_from_today >= 0 else ''}{days_from_today} hari) "
                    f"→ {label}")
        projections.append({
            "ratio": ratio,
            "label": label,
            "proj_date": proj_date,
            "days_from_today": days_from_today,
            "in_window": in_window,
            "bias": reversal_bias,
            "desc": proj_str,
            "future": days_from_today >= -3,
        })

    active = [p for p in projections if p["in_window"]]
    return {
        "available": True,
        "swing_ref": swing_label,
        "range_days": range_days,
        "swing_end": swing_end,
        "reversal_bias": reversal_bias,
        "projections": [p for p in projections if p["future"]],
        "active_now": active,
    }


# ════════════════════════════════════════════════════════════
# WEEKLY EQ — Premium vs Discount zone (50% HTF Fib)
# Per prompt Step 7A: price di Premium zone = BEARISH bias
# ════════════════════════════════════════════════════════════

def weekly_eq_status(df_d1: pd.DataFrame) -> Dict:
    """Weekly EQ = 50% dari range D1/W1. Premium = atas EQ, Discount = bawah EQ."""
    h_max = float(df_d1["high"].max())
    l_min = float(df_d1["low"].min())
    weekly_eq = (h_max + l_min) / 2
    current   = float(df_d1["close"].iloc[-1])
    zone      = "PREMIUM" if current > weekly_eq else "DISCOUNT"
    # Per prompt: Premium = bearish bias, Discount = bullish bias
    zone_bias = "BEARISH" if zone == "PREMIUM" else "BULLISH"
    pct_from_eq = (current - weekly_eq) / weekly_eq * 100
    return {
        "weekly_eq": weekly_eq,
        "current": current,
        "zone": zone,
        "zone_bias": zone_bias,
        "pct_from_eq": round(pct_from_eq, 2),
        "range_high": h_max,
        "range_low": l_min,
    }


# ════════════════════════════════════════════════════════════
# STRONG / WEAK HIGH-LOW (per prompt Step 7B)
# ════════════════════════════════════════════════════════════

def strong_weak_levels(df: pd.DataFrame, swing_len: int = 10) -> Dict:
    """Strong High/Low = terbentuk SETELAH BOS (dijaga institusi).
    Weak High/Low = terbentuk SEBELUM BOS (kandidat sweep)."""
    ms = ICT.MarketStructure(swing_len=swing_len)
    events = ms.update(df)
    h, l = df["high"], df["low"]
    if not events:
        return {"strong_high": None, "strong_low": None,
                "weak_high": float(h.max()), "weak_low": float(l.min())}
    last_bos = [e for e in events if e.kind == "BOS"]
    last_ev  = events[-1]
    # Strong levels = highest high / lowest low AFTER last BOS
    if last_bos:
        lb = last_bos[-1]
        post_h = h.iloc[lb.bar_index:]
        post_l = l.iloc[lb.bar_index:]
        strong_high = float(post_h.max()) if len(post_h) else None
        strong_low  = float(post_l.min()) if len(post_l) else None
    else:
        strong_high = strong_low = None
    # Weak = before last BOS
    pre_h = h.iloc[:last_ev.bar_index]
    pre_l = l.iloc[:last_ev.bar_index]
    return {
        "strong_high": round(strong_high, 4) if strong_high else None,
        "strong_low":  round(strong_low,  4) if strong_low  else None,
        "weak_high":   round(float(pre_h.max()), 4) if len(pre_h) else None,
        "weak_low":    round(float(pre_l.min()), 4) if len(pre_l) else None,
        "last_bos": last_ev,
    }


# ════════════════════════════════════════════════════════════
# EW HTF CONTEXT — degree Primary/Intermediate
# ════════════════════════════════════════════════════════════

def ew_htf_context(df_d1: pd.DataFrame) -> Dict:
    eng   = EW.ElliottWaveEngine(length=16)
    waves = eng.run(df_d1)
    report = EW.describe_position(waves, length=16)
    valid  = [w for w in waves if w.valid]
    if not valid:
        return {"position": "Belum teridentifikasi", "degree": report.get("degree", "Unknown"),
                "scenario": report["main_scenario"], "alternate": report.get("alternate_scenario", "N/A"),
                "direction": "NEUTRAL", "phase": "Insufficient data", "timing": "Insufficient data", "invalidation": "N/A"}
    last = valid[-1]
    bull = last.direction == 1
    if last.abc:
        phase    = "Post-Wave-5 ABC corrective (distribusi/akumulasi)"
        next_dir = "BULLISH" if not bull else "BEARISH"
        timing   = "Reversal 2-8 minggu setelah ABC selesai"
    else:
        phase    = f"Inside Wave 5 {'bullish' if bull else 'bearish'} impulse"
        next_dir = "BEARISH" if bull else "BULLISH"
        timing   = "Wave 5 terminal — major reversal approaching"
    return {
        "position":     report["position"],
        "degree":       report.get("degree", "Minor"),
        "scenario":     report["main_scenario"],
        "alternate":    report.get("alternate_scenario", "N/A"),
        "direction":    next_dir,
        "phase":        phase,
        "timing":       timing,
        "invalidation": str(report["invalidation"]),
    }


# ════════════════════════════════════════════════════════════
# ASTRONACCI SCORE — persis sesuai prompt v3.0 Step 2D
# ════════════════════════════════════════════════════════════

def astro_score(lunar: Dict, fib_time: Dict, today: dt.date, market_bias: str) -> Dict:
    """
    Lunar Phase aligned dengan bias    → +1
    Full/New Moon window (±2 hari)     → +1
    Fibonacci Time window aktif        → +1
    Planetary aspect major aktif       → +1
    Mercury/Venus retrograde periode   → +1 (caution flag)
    Score: 4-5=STRONG, 2-3=MODERATE, 0-1=WEAK
    """
    planets = ASTRO.active_planetary_events(today)
    retro   = any("Retrograde" in p.name for p in planets)
    major   = any(p.precise_dates for p in planets)

    lunar_bias = lunar["bias"].lower()
    bias_lower = market_bias.lower()
    lunar_aligned = (("bullish" in lunar_bias and "bull" in bias_lower) or
                     ("bearish" in lunar_bias and "bear" in bias_lower))
    moon_window  = lunar["in_reversal_window"]
    fib_active   = bool(fib_time.get("active_now"))

    scores = {
        "Lunar Phase aligned": (1 if lunar_aligned else 0, lunar["bias"]),
        "Moon window ±2hr":    (1 if moon_window   else 0, "Active" if moon_window else "Not active"),
        "Fib Time window":     (1 if fib_active     else 0, "Active" if fib_active else "Not active"),
        "Planetary major":     (1 if major          else 0, ", ".join(p.name for p in planets) if planets else "None"),
        "Retro caution":       (1 if retro          else 0, "CAUTION" if retro else "Clear"),
    }
    total = sum(v[0] for v in scores.values())
    label = "STRONG" if total >= 4 else ("MODERATE" if total >= 2 else "WEAK")
    return {"scores": scores, "total": total, "label": label,
            "planets": planets, "retro": retro}


# ════════════════════════════════════════════════════════════
# REVERSAL WINDOWS — tanggal konkret terdekat 30 hari
# ════════════════════════════════════════════════════════════

def nearest_reversal_windows(lunar_events: List[Dict], fib_time: Dict,
                               today: dt.date, days: int = 30) -> List[Dict]:
    windows = []
    for e in lunar_events:
        if e["days_away"] <= days:
            windows.append({
                "date": e["date"], "source": e["type"],
                "bias": e["bias"], "emoji": e["emoji"],
                "days_away": e["days_away"],
                "window": f"{e['window_start'].strftime('%d %b')} – {e['window_end'].strftime('%d %b')}",
            })
    if fib_time.get("available"):
        for p in fib_time.get("projections", []):
            if 0 <= p["days_from_today"] <= days:
                emoji = {"0.382": "⬜", "0.618": "🟨", "1.000": "🟧", "1.618": "🟥"}.get(
                    f"{p['ratio']:.3f}", "📍")
                windows.append({
                    "date": p["proj_date"], "source": f"Fib Time {p['ratio']:.3f} ({p['label']})",
                    "bias": p["bias"], "emoji": emoji,
                    "days_away": p["days_from_today"],
                    "window": p["proj_date"].strftime("%d %b %Y"),
                })
    windows.sort(key=lambda x: x["days_away"])
    return windows[:6]


# ════════════════════════════════════════════════════════════
# TELEGRAM FORMAT — persis sesuai v3.0 ASTRONACCI section
# ════════════════════════════════════════════════════════════

def format_cycle_report(pair: str, data: Dict, now_wib: dt.datetime) -> str:
    """Plain-language market cycle report for all users."""
    lunar   = data["lunar"]
    fib     = data["fib_time"]
    score   = data["astro_score"]
    thesis  = data["thesis"]
    eq      = data["weekly_eq"]
    ew      = data["ew_context"]
    windows = data["reversal_windows"]

    pair_names = {
        "XAUUSD": "GOLD (XAUUSD)", "XAGUSD": "SILVER (XAGUSD)",
        "BTCUSDT": "BITCOIN (BTCUSDT)", "ETHUSDT": "ETHEREUM (ETHUSDT)",
        "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
        "USDCHF": "USD/CHF", "AUDUSD": "AUD/USD", "NZDUSD": "NZD/USD",
        "USDCAD": "USD/CAD",
    }
    pair_display = pair_names.get(pair, pair)
    price = thesis.get("current_price", 0)
    price_fmt = ("${:,.0f}".format(price) if price > 100
                 else "${:.4f}".format(price) if price < 10
                 else "${:.2f}".format(price))
    day_wib = now_wib.strftime("%A, %d %b %Y | %H:%M WIB")

    # ── Tren besar ──
    thesis_status = thesis.get("status", "AKTIF")
    thesis_detail = thesis.get("detail", "")
    if "INVALIDATED" in thesis_status:
        tren_emoji = "⬆️"
        tren_text  = "BERUBAH — sinyal NAIK muncul"
        tren_desc  = "Tren bearish mungkin sudah selesai. Perlu konfirmasi lebih lanjut."
    elif "TARGET" in thesis_status:
        tren_emoji = "⚠️"
        tren_text  = "MENDEKATI TARGET BAWAH"
        tren_desc  = thesis_detail
    else:
        tren_emoji = "⬇️"
        tren_text  = "TURUN 📉"
        thesis_obj = BEARISH_THESIS.get(pair, {})
        tren_desc  = thesis_obj.get("thesis", thesis_detail)

    zone      = eq.get("zone", "N/A")
    zone_text = ("bawah level tengah (potensi pantul)"
                 if zone == "DISCOUNT" else "atas level tengah (tekanan jual)")

    # ── Minggu ini ──
    lunar_bias = lunar.get("bias", "")
    in_window  = lunar.get("in_reversal_window", False)
    dark_moon  = lunar.get("dark_moon_window", False)
    if dark_moon:
        week_pred = "⚠️ Potensi BOTTOM minggu ini — Dark Moon aktif"
        week_pred += "\n   Watch peluang BELI jangka pendek"
    elif "Bullish" in lunar_bias:
        week_pred = "⬆️ Cenderung NAIK / sideways minggu ini"
    elif "Bearish" in lunar_bias:
        week_pred = "⬇️ Cenderung TURUN minggu ini"
    else:
        week_pred = "↔️ Sideways / tidak jelas arahnya minggu ini"
    if in_window and not dark_moon:
        week_pred += "\n   " + lunar["phase_name"] + " aktif — watch pembalikan harga"

    # ── Bulan ini ──
    near_month = [w for w in windows if 0 <= w["days_away"] <= 30]
    month_lines = []
    for w in near_month[:3]:
        d        = w["date"].strftime("%d %b")
        days_aw  = w["days_away"]
        bias_raw = w["bias"].lower()
        if "bearish" in bias_raw or "turun" in bias_raw:
            action = "harga sering NAIK dulu lalu BALIK TURUN di sini\n  → Kalau naik, cari peluang JUAL"
        elif "bullish" in bias_raw or "naik" in bias_raw:
            action = "potensi titik BOTTOM → bersiap untuk BELI"
        else:
            action = "watch arah breakout pertama"
        month_lines.append("• {} (+{} hari)\n  {}".format(d, days_aw, action))
    month_block = "\n".join(month_lines) if month_lines else "• Tidak ada event signifikan bulan ini"

    # ── 3 bulan ke depan ──
    proj_3m = []
    if fib.get("available"):
        for p in fib.get("projections", []):
            if 30 < p["days_from_today"] <= 120:
                d    = p["proj_date"].strftime("%b %Y")
                days_aw = p["days_from_today"]
                bias = p["bias"].lower()
                lbl  = p["label"]
                if "bullish" in bias:
                    act = "potensi BOTTOM / titik balik NAIK ({})".format(lbl)
                else:
                    act = "potensi PEAK / titik balik TURUN ({})".format(lbl)
                proj_3m.append("• {} (+{} hari) — {}".format(d, days_aw, act))
    if not proj_3m:
        direction = ew.get("direction", "")
        if direction == "BULLISH":
            proj_3m.append("• 1-3 bulan ke depan — potensi mulai NAIK (Elliott Wave)")
        elif direction == "BEARISH":
            proj_3m.append("• 1-3 bulan ke depan — masih cenderung TURUN (Elliott Wave)")
        else:
            proj_3m.append("• 1-3 bulan ke depan — belum ada sinyal jelas")
    proj_block = "\n".join(proj_3m)

    # ── Action items ──
    actions = []
    if "INVALIDATED" in thesis_status:
        actions.append("⚠️ SEKARANG   → Review ulang posisi — tren berubah")
    elif dark_moon:
        actions.append("⚠️ SEKARANG   → Watch peluang BELI jangka pendek (Dark Moon)")
    else:
        actions.append("✅ SEKARANG   → Tahan dulu, jangan terburu-buru")

    if near_month:
        w0   = near_month[0]
        d0   = w0["date"].strftime("%d %b")
        bias0 = w0["bias"].lower()
        if "bearish" in bias0:
            actions.append("✅ {}       → Kalau harga naik dulu, cari peluang JUAL".format(d0))
        else:
            actions.append("✅ {}       → Watch sinyal BELI kalau harga turun ke support".format(d0))

    if fib.get("available"):
        projs = [p for p in fib.get("projections", []) if p["days_from_today"] > 30]
        if projs:
            p0  = projs[0]
            mon = p0["proj_date"].strftime("%b %Y")
            if "bullish" in p0["bias"].lower():
                actions.append("🎯 {}   → Mulai perhatikan sinyal BELI jangka panjang".format(mon))
            else:
                actions.append("🎯 {}   → Potensi peak — bersiap JUAL".format(mon))
    action_block = "\n".join(actions)

    # ── Risiko ──
    thesis_obj = BEARISH_THESIS.get(pair, {})
    inv_level  = thesis_obj.get("invalidation_weekly_close")
    risk_lines = []
    if inv_level:
        if inv_level > 100:
            inv_str = "${:,.0f}".format(inv_level)
        else:
            inv_str = str(inv_level)
        risk_lines.append(
            "• Kalau harga penutupan mingguan di atas {} → prediksi ini BERUBAH".format(inv_str))
    if score.get("retro"):
        risk_lines.append("• Mercury/Venus Retrograde aktif → harga bisa bergerak tidak terduga")
    planets = score.get("planets", [])
    if planets:
        risk_lines.append("• Aspek planet: " + ", ".join(p.name for p in planets[:2]))
    if not risk_lines:
        risk_lines.append("• Pantau berita ekonomi besar (Fed, CPI, NFP)")
    risk_block = "\n".join(risk_lines)

    sep = "━" * 26
    parts = [
        "🔭 <b>BOBB MARKET ANALYST</b>",
        "📅 " + day_wib,
        sep,
        "<b>" + pair_display + " — " + price_fmt + "</b>",
        sep,
        "",
        "📍 <b>KONDISI MARKET SAAT INI</b>",
        "Tren Besar : " + tren_emoji + " " + tren_text,
        tren_desc,
        "Posisi     : Harga di " + zone_text,
        "",
        sep,
        "📅 <b>MINGGU INI</b>",
        sep,
        week_pred,
        "",
        sep,
        "📅 <b>BULAN INI</b>",
        sep,
        month_block,
        "",
        sep,
        "📅 <b>3 BULAN KE DEPAN</b>",
        sep,
        proj_block,
        "",
        sep,
        "⚡ <b>YANG HARUS DILAKUKAN</b>",
        sep,
        action_block,
        "",
        sep,
        "⚠️ <b>PERHATIKAN</b>",
        sep,
        risk_block,
        sep,
    ]
    return "\n".join(parts)




# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════

CYCLE_PAIRS = ["XAUUSD", "BTCUSDT", "EURUSD", "GBPUSD", "USDJPY", "GBPJPY"]

def run_market_cycle(pairs: Optional[List[str]] = None, send: bool = True,
                      data_override: Optional[dict] = None) -> List[str]:
    pairs    = pairs or CYCLE_PAIRS
    now_wib  = dt.datetime.now(WIB)
    today    = now_wib.date()
    messages = []

    for pair in pairs:
        try:
            if data_override and pair in data_override:
                df_d1 = data_override[pair]
            else:
                df_d1 = DF.fetch_ohlc(pair, "D1", limit=300)

            current_price = float(df_d1["close"].iloc[-1])
            weekly_close  = float(df_d1["close"].iloc[-5]) if len(df_d1) >= 5 else current_price

            # Determine market bias from D1 EMA200
            ema200_d1  = float(df_d1["close"].ewm(span=200, adjust=False).mean().iloc[-1])
            market_bias = "Bullish" if current_price > ema200_d1 else "Bearish"

            lunar       = get_lunar_detail(today)
            fib_time    = compute_fib_time(pair, df_d1, today)
            eq          = weekly_eq_status(df_d1)
            sw          = strong_weak_levels(df_d1)
            ew          = ew_htf_context(df_d1)
            thesis      = check_thesis_status(pair, current_price, weekly_close)
            a_score     = astro_score(lunar, fib_time, today, market_bias)
            lunar_ev    = get_upcoming_lunar(today, days_ahead=60)
            rev_windows = nearest_reversal_windows(lunar_ev, fib_time, today, days=30)

            data = {
                "lunar": lunar, "fib_time": fib_time, "weekly_eq": eq,
                "strong_weak": sw, "ew_context": ew, "thesis": thesis,
                "astro_score": a_score, "lunar_upcoming": lunar_ev,
                "reversal_windows": rev_windows,
            }
            msg = format_cycle_report(pair, data, now_wib)
            messages.append(msg)
            print(f"[cycle] {pair}: {len(msg)} chars | Astro {a_score['total']}/5 {a_score['label']} | {thesis['status']}")
            if send:
                ND.send_text(msg)
        except Exception as e:
            import traceback
            err = f"[cycle] {pair} ERROR: {e}\n{traceback.format_exc()}"
            print(err); messages.append(err)

    return messages


if __name__ == "__main__":
    run_market_cycle()

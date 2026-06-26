"""
trade_tracker.py — Trade Tracking & Weekly Report System
Bobb Market Analyst v3.0

Flow:
  1. signal_engine.py fire signal → log_signal() → simpan ke trades.json
  2. Tiap 5 menit (sama dengan signal engine) → check_open_trades() →
     fetch harga live → kalau hit TP1 = WIN, hit SL = LOSS → update trades.json
  3. Sabtu 08:00 WIB → send_weekly_report() → kirim summary ke Telegram

Storage: trades.json di-commit ke repo via git (persistent, no artifact needed)

Definisi WIN/LOSS:
  WIN  = TP1 hit (price mencapai TP1 level)
  LOSS = SL hit (price mencapai SL level)
  OPEN = belum hit keduanya
  EXPIRED = open > 7 hari tanpa hit (auto-close, tidak dihitung di winrate)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import traceback
from typing import Optional, List, Dict

import requests
try:
    import xgboost_filter as XGB
    _XGB_AVAILABLE = True
except ImportError:
    _XGB_AVAILABLE = False

WIB = dt.timezone(dt.timedelta(hours=7))

# ── Paths ──
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")
TRADES_FILE = os.path.join(DATA_DIR, "trades.json")

# ── Config ──
TRADE_EXPIRY_DAYS = 7       # auto-close open trade setelah 7 hari
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

# ── Pip sizes (same as signal_engine.py) ──
PIP_SIZE = {
    "XAUUSD": 0.01, "XAGUSD": 0.001,
    "BTCUSDT": 1.0, "ETHUSDT": 0.1,
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01,
    "USDCHF": 0.0001, "AUDUSD": 0.0001, "NZDUSD": 0.0001, "USDCAD": 0.0001,
    "GBPJPY": 0.01, "EURJPY": 0.01, "GBPAUD": 0.0001,
}
PIP_LABEL = {"XAUUSD": "pts", "XAGUSD": "pts", "BTCUSDT": "pts", "ETHUSDT": "pts"}

def _pip(pair: str) -> float:
    return PIP_SIZE.get(pair.upper(), 0.0001)

def _lbl(pair: str) -> str:
    return PIP_LABEL.get(pair.upper(), "pips")

def _to_pips(diff: float, pair: str) -> float:
    ps = _pip(pair)
    return round(abs(diff) / ps, 1) if ps else 0.0


# ════════════════════════════════════════════════════════════
# STORAGE — JSON + git commit
# ════════════════════════════════════════════════════════════

def _load_trades() -> List[Dict]:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(TRADES_FILE):
        return []
    try:
        with open(TRADES_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save_trades(trades: List[Dict], commit_msg: str = "update trades"):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2, default=str)
    _git_commit(commit_msg)


def _git_commit(msg: str):
    """Commit + push trades.json ke repo (GitHub Actions has GITHUB_TOKEN)."""
    try:
        subprocess.run(["git", "config", "user.email", "bobb-bot@github.com"], cwd=BASE_DIR, check=False, capture_output=True)
        subprocess.run(["git", "config", "user.name",  "Bobb Signal Bot"],      cwd=BASE_DIR, check=False, capture_output=True)
        subprocess.run(["git", "add", TRADES_FILE],    cwd=BASE_DIR, check=False, capture_output=True)
        result = subprocess.run(
            ["git", "commit", "-m", f"[bot] {msg}"],
            cwd=BASE_DIR, capture_output=True, text=True
        )
        if "nothing to commit" in result.stdout:
            return   # no change, skip push
        subprocess.run(["git", "push"], cwd=BASE_DIR, check=False, capture_output=True)
        print(f"[tracker] git commit: {msg}")
    except Exception as e:
        print(f"[tracker] git error: {e}")


# ════════════════════════════════════════════════════════════
# LOG SIGNAL — dipanggil dari signal_engine saat signal fire
# ════════════════════════════════════════════════════════════

def _extract_features(report) -> dict:
    """
    Extract ML features dari SignalReport untuk XGBoost training.
    Semua fitur di-encode jadi numerik.
    """
    # Encode lunar phase
    lunar_enc = {
        "New Moon": 0, "Waxing Crescent/First Quarter": 1,
        "Waxing Gibbous": 2, "Full Moon": 3,
        "Waning Gibbous": 4, "Waning/Dark Moon": 5,
    }
    lunar_phase_enc = lunar_enc.get(getattr(report, "lunar_phase_name", ""), 2)

    # Encode regime
    regime_enc = {"Bullish": 1, "Bearish": -1, "Sideways/Consolidation": 0}
    regime_val = regime_enc.get(getattr(report, "regime", ""), 0)

    # Encode signal direction
    signal_enc = {"BUY": 1, "SELL": -1, "WAIT": 0}
    signal_val = signal_enc.get(getattr(report, "signal", ""), 0)

    sb = getattr(report, "score_breakdown", {})
    return {
        # Confluence gate features (binary)
        "htf_bias_aligned":       int(sb.get("htf_bias_aligned", 0) > 0),
        "elliott_wave_clear":     int(sb.get("elliott_wave_clear", 0) > 0),
        "fib_ote_or_prz":         int(sb.get("fib_ote_or_prz", 0) > 0),
        "chart_pattern_confirmed":int(sb.get("chart_pattern_confirmed", 0) > 0),
        "harmonic_prz_hit":       int(sb.get("harmonic_prz_hit", 0) > 0),
        "divergence_detected":    int(sb.get("divergence_detected", 0) > 0),
        "h4_choch_bos":           int(sb.get("h4_choch_bos", 0) > 0),
        "kill_zone_active":       int(sb.get("kill_zone_active", 0) > 0),
        "fib_time_window_active": int(sb.get("fib_time_window_active", 0) > 0),
        "lunar_cycle_aligned":    int(sb.get("lunar_cycle_aligned", 0) > 0),
        "planetary_aspect_major": int(sb.get("planetary_aspect_major", 0) > 0),
        # Continuous features
        "score_total":            getattr(report, "score_total", 0),
        "adx_h4":                 round(getattr(report, "adx_h4", 0) or 0, 1),
        "rsi_m15":                round(getattr(report, "rsi_m15", 50) or 50, 1),
        "rsi_h1":                 round(getattr(report, "rsi_h1", 50) or 50, 1),
        "astro_score":            getattr(report, "astro_score", 0),
        # Encoded categoricals
        "lunar_phase_enc":        lunar_phase_enc,
        "regime_enc":             regime_val,
        "signal_enc":             signal_val,
        # Pair group (commodity=1, crypto=2, forex=3)
        "pair_group": (1 if report.pair in ("XAUUSD","XAGUSD")
                       else 2 if report.pair in ("BTCUSDT","ETHUSDT")
                       else 3),
        # Hour WIB (0-23) — time-of-day effect
        "hour_wib": report.timestamp_wib.hour,
    }


def log_signal(report) -> str:
    """
    Catat signal baru ke trades.json.
    report = FMT.SignalReport object dari signal_engine.
    Returns trade_id.
    """
    now_wib = report.timestamp_wib
    trade_id = f"{report.pair}_{now_wib.strftime('%Y%m%d_%H%M')}"

    # Parse level dari formatted string (strip pip info)
    def _parse_level(s: str) -> Optional[float]:
        try:
            return float(s.split("(")[0].strip())
        except Exception:
            return None

    # Entry midpoint
    entry_parts = report.entry_zone.replace(" ", "").split("-")
    try:
        entry_mid = (float(entry_parts[0]) + float(entry_parts[1])) / 2 if len(entry_parts) == 2 else float(entry_parts[0])
    except Exception:
        entry_mid = None

    sl  = _parse_level(report.stop_loss)
    tp1 = _parse_level(report.tp1)
    tp2 = _parse_level(report.tp2)
    tp3 = _parse_level(report.tp3)

    # Extract ML features
    features = _extract_features(report)

    trade = {
        "trade_id":    trade_id,
        "pair":        report.pair,
        "signal":      report.signal,
        "entry_zone":  report.entry_zone,
        "entry_mid":   entry_mid,
        "sl":          sl,
        "tp1":         tp1,
        "tp2":         tp2,
        "tp3":         tp3,
        "score":       report.score_total,
        "conviction":  report.conviction,
        "timestamp":   now_wib.isoformat(),
        "status":      "OPEN",
        "result_pips": None,
        "result_pct":  None,
        "closed_at":   None,
        "closed_price": None,
        "hit_level":   None,
        "features":    features,          # ← ML features untuk XGBoost
    }

    trades = _load_trades()
    trades.append(trade)
    _save_trades(trades, f"signal {report.pair} {report.signal} @ {now_wib.strftime('%H:%M')} WIB")
    print(f"[tracker] Logged: {trade_id} | {report.signal} | SL={sl} TP1={tp1}")
    return trade_id


# ════════════════════════════════════════════════════════════
# LIVE PRICE FETCH — Binance (crypto) + Yahoo (metals/forex)
# ════════════════════════════════════════════════════════════

BINANCE_PAIRS = {"BTCUSDT", "ETHUSDT"}
YAHOO_TICKERS = {
    "XAUUSD": "GC=F", "XAGUSD": "SI=F",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X",
    "USDCHF": "CHF=X", "AUDUSD": "AUDUSD=X", "NZDUSD": "NZDUSD=X",
    "USDCAD": "CAD=X", "GBPJPY": "GBPJPY=X", "EURJPY": "EURJPY=X",
}

def _fetch_price(pair: str) -> Optional[float]:
    try:
        if pair in BINANCE_PAIRS:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": pair}, timeout=8)
            return float(r.json()["price"])
        ticker = YAHOO_TICKERS.get(pair)
        if not ticker:
            return None
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"interval": "1m", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return float(closes[-1]) if closes else None
    except Exception as e:
        print(f"[tracker] price fetch error {pair}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# CHECK OPEN TRADES — dipanggil tiap 5 menit dari signal_engine
# ════════════════════════════════════════════════════════════

def check_open_trades():
    """
    Fetch live price untuk setiap open trade.
    WIN  = price hit TP1
    LOSS = price hit SL
    EXPIRED = open > TRADE_EXPIRY_DAYS
    """
    trades = _load_trades()
    open_trades = [t for t in trades if t["status"] == "OPEN"]
    if not open_trades:
        return

    changed = False
    now_wib = dt.datetime.now(WIB)

    for t in open_trades:
        pair = t["pair"]

        # Check expiry
        try:
            opened = dt.datetime.fromisoformat(t["timestamp"])
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=WIB)
            days_open = (now_wib - opened).days
            if days_open >= TRADE_EXPIRY_DAYS:
                t["status"]    = "EXPIRED"
                t["hit_level"] = "EXPIRED"
                t["closed_at"] = now_wib.isoformat()
                print(f"[tracker] EXPIRED: {t['trade_id']}")
                changed = True
                continue
        except Exception:
            pass

        # Fetch live price
        price = _fetch_price(pair)
        if price is None:
            continue

        sl  = t.get("sl")
        tp1 = t.get("tp1")
        sig = t.get("signal", "BUY")

        # Check hit
        hit = None
        if sig == "BUY":
            if sl and price <= sl:
                hit = "SL"
            elif tp1 and price >= tp1:
                hit = "TP1"
        else:  # SELL
            if sl and price >= sl:
                hit = "SL"
            elif tp1 and price <= tp1:
                hit = "TP1"

        if hit:
            entry = t.get("entry_mid") or price
            if hit == "TP1":
                result_price = tp1
                status = "WIN"
            else:
                result_price = sl
                status = "LOSS"

            pips = _to_pips(abs(result_price - entry), pair)
            pips = pips if status == "WIN" else -pips

            t["status"]       = status
            t["hit_level"]    = hit
            t["closed_at"]    = now_wib.isoformat()
            t["closed_price"] = result_price
            t["result_pips"]  = pips
            changed = True
            print(f"[tracker] {status}: {t['trade_id']} | {hit} @ {result_price:.4f} | {pips:+.1f} {_lbl(pair)}")

            # Send instant notification
            _send_trade_result(t)

    if changed:
        _save_trades(trades, f"trade update {now_wib.strftime('%d%b %H:%M')} WIB")


def _send_trade_result(t: Dict):
    """Kirim notifikasi instant ke Telegram saat trade hit TP1/SL."""
    if not BOT_TOKEN or not CHAT_ID:
        return
    pair   = t["pair"]
    status = t["status"]
    hit    = t["hit_level"]
    pips   = t.get("result_pips", 0) or 0
    lbl    = _lbl(pair)
    emoji  = "✅" if status == "WIN" else "❌"
    closed_price = t.get("closed_price", "")

    msg = (
        f"{emoji} <b>TRADE CLOSED — {pair}</b>\n"
        f"{'─'*30}\n"
        f"Status  : <b>{status}</b> ({hit})\n"
        f"Signal  : {t.get('signal','')}\n"
        f"Entry   : {t.get('entry_zone','')}\n"
        f"Closed  : {closed_price}\n"
        f"Result  : <b>{pips:+.1f} {lbl}</b>\n"
        f"Score   : {t.get('score','')}/14 {t.get('conviction','')}\n"
        f"{'─'*30}\n"
        f"<i>Bobb Signal Tracker v3.0</i>"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=15)
    except Exception as e:
        print(f"[tracker] Telegram error: {e}")


# ════════════════════════════════════════════════════════════
# WEEKLY REPORT — Sabtu 08:00 WIB
# ════════════════════════════════════════════════════════════

def _get_week_range() -> tuple:
    """Returns (monday, sunday) of the previous week."""
    now = dt.datetime.now(WIB)
    # Last Monday
    days_since_monday = (now.weekday()) % 7
    last_monday = (now - dt.timedelta(days=days_since_monday + 7)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    last_sunday = last_monday + dt.timedelta(days=6, hours=23, minutes=59)
    return last_monday, last_sunday


def _stats_by_pair(closed: List[Dict]) -> Dict:
    """Group stats per pair."""
    stats = {}
    for t in closed:
        pair = t["pair"]
        if pair not in stats:
            stats[pair] = {"WIN": 0, "LOSS": 0, "EXPIRED": 0, "pips": 0.0}
        s = t["status"]
        stats[pair][s] = stats[pair].get(s, 0) + 1
        if t.get("result_pips"):
            stats[pair]["pips"] += t["result_pips"]
    return stats


def send_weekly_report():
    """
    Generate & kirim weekly performance report ke Telegram.
    Dipanggil tiap Sabtu jam 08:00 WIB dari GitHub Actions.
    """
    trades = _load_trades()
    now_wib = dt.datetime.now(WIB)
    monday, sunday = _get_week_range()

    # Filter trades dari minggu lalu
    week_trades = []
    for t in trades:
        try:
            ts = dt.datetime.fromisoformat(t["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=WIB)
            if monday <= ts <= sunday:
                week_trades.append(t)
        except Exception:
            continue

    closed   = [t for t in week_trades if t["status"] in ("WIN", "LOSS")]
    wins     = [t for t in closed if t["status"] == "WIN"]
    losses   = [t for t in closed if t["status"] == "LOSS"]
    open_t   = [t for t in week_trades if t["status"] == "OPEN"]
    expired  = [t for t in week_trades if t["status"] == "EXPIRED"]
    total_closed = len(closed)
    winrate  = (len(wins) / total_closed * 100) if total_closed else 0
    total_pips = sum(t.get("result_pips") or 0 for t in closed)
    by_pair  = _stats_by_pair(closed)

    # ── Format message ──
    week_str = f"{monday.strftime('%d %b')} – {sunday.strftime('%d %b %Y')}"

    lines = [
        "═" * 44,
        f"📊 <b>WEEKLY PERFORMANCE REPORT</b>",
        f"📅 {week_str}",
        f"<i>Bobb Market Analyst v3.0</i>",
        "═" * 44,
        "",
        "🏆 <b>OVERALL</b>",
        f"Total Signal  : {len(week_trades)}",
        f"Total Closed  : {total_closed} (WIN {len(wins)} | LOSS {len(losses)})",
        f"Still Open    : {len(open_t)}",
        f"Expired (7d)  : {len(expired)} (tidak dihitung)",
        f"",
        f"{'─'*35}",
        f"Winrate       : <b>{winrate:.1f}%</b>  {'✅' if winrate >= 65 else '⚠️' if winrate >= 50 else '❌'}",
        f"Total Pips    : <b>{total_pips:+.1f} {'' }</b>  {'📈' if total_pips > 0 else '📉'}",
        f"{'─'*35}",
        "",
    ]

    # Per pair breakdown
    if by_pair:
        lines.append("📋 <b>PER PAIR</b>")
        for pair, s in sorted(by_pair.items(), key=lambda x: -(x[1]["WIN"] + x[1]["LOSS"])):
            pair_closed = s["WIN"] + s["LOSS"]
            pair_wr = (s["WIN"] / pair_closed * 100) if pair_closed else 0
            pair_pips = s["pips"]
            lbl = _lbl(pair)
            wr_emoji = "✅" if pair_wr >= 65 else "⚠️" if pair_wr >= 50 else "❌"
            lines.append(
                f"<b>{pair}</b>: {s['WIN']}W {s['LOSS']}L "
                f"| WR {pair_wr:.0f}% {wr_emoji} "
                f"| {pair_pips:+.1f} {lbl}"
            )
        lines.append("")

    # Top wins
    if wins:
        lines.append("🥇 <b>TOP 3 WINS</b>")
        top_wins = sorted(wins, key=lambda x: x.get("result_pips") or 0, reverse=True)[:3]
        for w in top_wins:
            lbl = _lbl(w["pair"])
            lines.append(
                f"  ✅ {w['pair']} {w['signal']} "
                f"+{w.get('result_pips',0):.1f} {lbl} "
                f"({w.get('conviction','')}) "
                f"@ {w['timestamp'][:16]}"
            )
        lines.append("")

    # Worst losses
    if losses:
        lines.append("⚠️ <b>LOSSES THIS WEEK</b>")
        for lo in sorted(losses, key=lambda x: x.get("result_pips") or 0)[:3]:
            lbl = _lbl(lo["pair"])
            lines.append(
                f"  ❌ {lo['pair']} {lo['signal']} "
                f"{lo.get('result_pips',0):.1f} {lbl} "
                f"({lo.get('conviction','')}) "
                f"@ {lo['timestamp'][:16]}"
            )
        lines.append("")

    # ── XGBoost retrain + status ──
    xgb_status = ""
    if _XGB_AVAILABLE:
        try:
            print("[weekly] Triggering XGBoost retrain...")
            train_result = XGB.train_model()
            if train_result.get("success"):
                n   = train_result["n_trades"]
                auc = train_result["cv_auc"]
                thr = train_result["threshold"]
                top = [f[0] for f in train_result["top_features"][:3]]
                xgb_status = (
                    f"\n🤖 <b>XGBoost Update</b>\n"
                    f"Training: {n} trades | AUC {auc:.2f} | Threshold {thr:.2f}\n"
                    f"Top features: {', '.join(top)}"
                )
            else:
                err = train_result.get("error", "unknown")
                xgb_status = f"\n🤖 XGBoost: {err}"
        except Exception as e:
            xgb_status = f"\n🤖 XGBoost error: {e}"
    else:
        # Show how many trades until model ready
        n_closed = len([t for t in trades if t.get("status") in ("WIN","LOSS")])
        remaining = max(0, 50 - n_closed)
        if remaining > 0:
            xgb_status = (f"\n🤖 XGBoost: Belum aktif — butuh {remaining} trade lagi"
                          f" ({n_closed}/50)")
        else:
            xgb_status = "\n🤖 XGBoost: install dengan `pip install xgboost scikit-learn`"

    # Verdict
    lines.append("─" * 35)
    if winrate >= 65 and total_pips > 0:
        verdict = "🟢 Minggu yang SOLID — sistem berjalan sesuai target (WR >65%, profit)"
    elif winrate >= 50:
        verdict = "🟡 Minggu MODERAT — WR di atas 50% tapi belum optimal. Review setup LOW conviction"
    else:
        verdict = "🔴 Minggu CHALLENGING — review mandatory gates & confluence threshold"
    lines.append(verdict)
    if xgb_status:
        lines.append(xgb_status)
    lines.append("")
    lines.append("═" * 44)

    msg = "\n".join(lines)

    if BOT_TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=20)
            print(f"[tracker] Weekly report sent: {len(wins)}W {len(losses)}L WR={winrate:.1f}%")
        except Exception as e:
            print(f"[tracker] Telegram error: {e}")
    else:
        print(msg)

    return msg


# ════════════════════════════════════════════════════════════
# MAIN — weekly report entry point
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    send_weekly_report()

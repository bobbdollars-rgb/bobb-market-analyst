"""
main.py — BOBB MARKET ANALYST v3.0 — Single Entry Point
=========================================================
Satu file, satu workflow GitHub Actions (cron tiap menit).
Internal scheduler yang decide apa yang dijalankan berdasarkan waktu WIB.

Schedule:
  Tiap menit     → news_detector (calendar reminder, breaking news, spike)
  Tiap 5 menit   → signal_engine (ICT+EW+Harmonic+Astronacci signal)
                   + trade_tracker.check_open_trades()
  07:00 WIB      → market_cycle (Astronacci siklus forecast) + daily briefing
  Sabtu 08:00    → trade_tracker.send_weekly_report()

Env vars required:
  BOT_TOKEN, CHAT_ID, TWELVEDATA_API_KEY
"""

from __future__ import annotations

import datetime as dt
import traceback
import sys
import os

WIB = dt.timezone(dt.timedelta(hours=7))


def now_wib() -> dt.datetime:
    return dt.datetime.now(WIB)


def _log(tag: str, msg: str):
    ts = now_wib().strftime("%H:%M:%S")
    print(f"[{ts} WIB] [{tag}] {msg}")


# ════════════════════════════════════════════════════════════
# SCHEDULER — decide what to run based on current WIB time
# ════════════════════════════════════════════════════════════

def should_run_signal(now: dt.datetime) -> bool:
    """Signal engine runs every 5 minutes (M5 candle close)."""
    return now.minute % 30 == 0


def should_run_market_cycle(now: dt.datetime) -> bool:
    """Market cycle forecast runs once daily at 07:00 WIB."""
    return now.minute % 30 == 0


def should_run_weekly_report(now: dt.datetime) -> bool:
    """Weekly report runs every Saturday at 08:00 WIB."""
    return now.weekday() == 5 and now.hour == 8 and now.minute == 0  # Saturday


def should_run_news(now: dt.datetime) -> bool:
    """News detector runs every minute (always)."""
    return now.minute % 30 == 0


# ════════════════════════════════════════════════════════════
# TASK RUNNERS — each wrapped in try/except so one failure
#                never blocks the others
# ════════════════════════════════════════════════════════════

def run_news(now: dt.datetime):
    _log("NEWS", "Running news detector...")
    try:
        import news_detector as ND
        ND.run_news_detector()
        _log("NEWS", "Done")
    except Exception as e:
        _log("NEWS", f"ERROR: {e}\n{traceback.format_exc()}")


def run_signal(now: dt.datetime):
    _log("SIGNAL", "Running signal engine...")
    try:
        import signal_engine as SE
        import trade_tracker as TT
        import news_detector as ND

        # Check open trades first (TP1/SL hit detection)
        try:
            TT.check_open_trades()
            _log("TRACKER", "Open trades checked")
        except Exception as e:
            _log("TRACKER", f"check_open_trades error: {e}")

        # Run signal engine for all pairs
        msgs = SE.run_signal_engine(send=True)
        fired = sum(1 for m in msgs if "BOBB SIGNAL" in m)
        _log("SIGNAL", f"Done — {fired} signal(s) fired")
    except Exception as e:
        _log("SIGNAL", f"ERROR: {e}\n{traceback.format_exc()}")


def run_market_cycle(now: dt.datetime):
    _log("CYCLE", "Running market cycle forecast...")
    try:
        import market_cycle as MC
        msgs = MC.run_market_cycle(send=True)
        _log("CYCLE", f"Done — {len(msgs)} pair(s) reported")
    except Exception as e:
        _log("CYCLE", f"ERROR: {e}\n{traceback.format_exc()}")


def run_weekly_report(now: dt.datetime):
    _log("WEEKLY", "Sending weekly performance report...")
    try:
        import trade_tracker as TT
        TT.send_weekly_report()
        _log("WEEKLY", "Done")
    except Exception as e:
        _log("WEEKLY", f"ERROR: {e}\n{traceback.format_exc()}")


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════

def main():
    now = now_wib()
    _log("MAIN", f"Started at {now.strftime('%d %b %Y %H:%M:%S')} WIB "
                  f"(weekday={now.strftime('%A')})")

    # Validate required env vars
    missing = [k for k in ("BOT_TOKEN", "CHAT_ID", "TWELVEDATA_API_KEY")
               if not os.environ.get(k)]
    if missing:
        _log("MAIN", f"WARNING: Missing env vars: {missing}")

    tasks_run = []

    # 1. Weekly report (Sabtu 08:00) — check first, highest priority
    if should_run_weekly_report(now):
        run_weekly_report(now)
        tasks_run.append("WEEKLY_REPORT")

    # 2. Market cycle (daily 07:00)
    if should_run_market_cycle(now):
        run_market_cycle(now)
        tasks_run.append("MARKET_CYCLE")

    # 3. Signal engine (every 5 min)
    if should_run_signal(now):
        run_signal(now)
        tasks_run.append("SIGNAL_ENGINE")

    # 4. News detector (every minute — always last so it doesn't delay signals)
    if should_run_news(now):
        run_news(now)
        tasks_run.append("NEWS_DETECTOR")

    _log("MAIN", f"Completed. Tasks run: {tasks_run if tasks_run else ['NEWS_DETECTOR']}")


if __name__ == "__main__":
    main()

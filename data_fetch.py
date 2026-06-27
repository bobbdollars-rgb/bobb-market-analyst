"""
data_fetch.py — Multi-pair, multi-timeframe OHLC data fetcher

Routing (per Bobb's existing bot pattern):
  - Crypto (BTCUSDT, ETHUSDT)              -> Binance public REST API (no key needed)
  - Gold/Silver/Forex (XAUUSD, XAGUSD,
    EURUSD, GBPUSD, USDJPY, USDCHF,
    AUDUSD, NZDUSD, USDCAD + minors)       -> TwelveData (requires TWELVEDATA_API_KEY)

Timeframes used by the signal engine:
  M5 — entry / LTF structure (CHoCH, OB, FVG)
  H4 — mid structure (ICT CHoCH/BOS, Fib OTE anchor)
  D1 — HTF bias (EMA200)

This module makes live network calls — it cannot be unit-tested in a
network-disabled sandbox. A `_mock` mode is included for structural testing
(see test_data_fetch.py pattern at the bottom of this file's __main__ block).
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List

import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
TWELVEDATA_BASE = "https://api.twelvedata.com"
BINANCE_BASE = "https://api.binance.com"

CRYPTO_PAIRS = {"BTCUSDT"}

TWELVEDATA_SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY", "GBPJPY": "GBP/JPY",
}
TWELVEDATA_INTERVAL_MAP = {"M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h", "D1": "1day"}
BINANCE_INTERVAL_MAP = {"M15": "15m", "H1": "1h", "H4": "4h", "D1": "1d"}

ALL_PAIRS: List[str] = [
    "XAUUSD", "BTCUSDT",
    "EURUSD", "GBPUSD", "USDJPY", "GBPJPY",
]
MINOR_PAIRS: List[str] = []  # semua sudah di ALL_PAIRS

# TwelveData free tier: 8 req/min -> space calls to stay safe (>=7.5s apart)
_TD_MIN_INTERVAL_SEC = 7.6
_last_td_call = 0.0


def _td_throttle():
    global _last_td_call
    elapsed = time.time() - _last_td_call
    if elapsed < _TD_MIN_INTERVAL_SEC:
        time.sleep(_TD_MIN_INTERVAL_SEC - elapsed)
    _last_td_call = time.time()

# ════════════════════════════════════════
# FILE-BASED CACHE — persists across GitHub Actions runs
# GitHub Actions = fresh container every run
# Cache saved to data/ohlc_cache.json, restored via actions/cache@v4
#
# TTL per timeframe → TwelveData req/day:
#   M15: 96/day | H1: 24/day | H4: 6/day | D1: 1/day
#   5 pairs × (96+24+6+1) = 635 req/day ✅ under 800 free limit
# ════════════════════════════════════════

_CACHE_TTL_MINUTES = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}
_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "ohlc_cache.json"
)


def _cache_load() -> dict:
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _cache_save(cache: dict):
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception as e:
        print(f"[CACHE] Save error: {e}")


def _cache_get(pair: str, interval: str) -> Optional[pd.DataFrame]:
    cache     = _cache_load()
    key       = f"{pair}_{interval}"
    entry     = cache.get(key)
    if not entry:
        return None
    age_min   = (time.time() - entry.get("cached_at", 0)) / 60
    ttl       = _CACHE_TTL_MINUTES.get(interval, 15)
    if age_min < ttl:
        print(f"[CACHE] HIT {pair}/{interval} (age {age_min:.1f}m / {ttl}m TTL)")
        try:
            df = pd.DataFrame(entry["data"])
            df["datetime"] = pd.to_datetime(df["datetime"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df
        except Exception as e:
            print(f"[CACHE] Parse error: {e}")
    print(f"[CACHE] MISS {pair}/{interval} (expired {age_min:.1f}m > {ttl}m)")
    return None


def _cache_set(pair: str, interval: str, df: pd.DataFrame):
    try:
        cache = _cache_load()
        df_copy = df.copy()
        df_copy["datetime"] = df_copy["datetime"].astype(str)
        cache[f"{pair}_{interval}"] = {
            "cached_at": time.time(),
            "data": df_copy.to_dict(orient="records"),
        }
        _cache_save(cache)
    except Exception as e:
        print(f"[CACHE] Set error {pair}/{interval}: {e}")


def _ohlc_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df[["datetime", "open", "high", "low", "close", "volume"]]


def _fetch_twelvedata(symbol: str, interval: str, outputsize: int = 300,
                       retries: int = 3) -> pd.DataFrame:
    if not TWELVEDATA_API_KEY:
        raise RuntimeError("TWELVEDATA_API_KEY belum di-set (env var / GitHub Secrets).")
    if requests is None:
        raise RuntimeError("Package 'requests' tidak tersedia.")

    td_symbol = TWELVEDATA_SYMBOL_MAP.get(symbol, symbol)
    td_interval = TWELVEDATA_INTERVAL_MAP.get(interval, interval)

    last_err = None
    for attempt in range(retries):
        _td_throttle()
        try:
            resp = requests.get(f"{TWELVEDATA_BASE}/time_series", params={
                "symbol": td_symbol, "interval": td_interval,
                "outputsize": outputsize, "apikey": TWELVEDATA_API_KEY,
                "order": "ASC",
            }, timeout=15)
            data = resp.json()
            if isinstance(data, dict) and data.get("status") == "error":
                raise RuntimeError(f"TwelveData error: {data.get('message')}")
            values = data.get("values") if isinstance(data, dict) else None
            if not values:
                raise RuntimeError(f"TwelveData: no data returned for {td_symbol}/{td_interval}")
            df = pd.DataFrame(values)
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.sort_values("datetime").reset_index(drop=True)
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col].astype(float)
            df["volume"] = df["volume"].astype(float) if "volume" in df.columns else 0.0
            return _ohlc_columns(df)
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"TwelveData fetch failed for {symbol}/{interval} after {retries} tries: {last_err}")


def _fetch_binance(symbol: str, interval: str, limit: int = 300,
                    retries: int = 3) -> pd.DataFrame:
    if requests is None:
        raise RuntimeError("Package 'requests' tidak tersedia.")

    bn_interval = BINANCE_INTERVAL_MAP.get(interval, interval)
    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(f"{BINANCE_BASE}/api/v3/klines", params={
                "symbol": symbol, "interval": bn_interval, "limit": limit,
            }, timeout=15)
            raw = resp.json()
            if isinstance(raw, dict) and raw.get("code"):
                raise RuntimeError(f"Binance error: {raw}")
            df = pd.DataFrame(raw, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "trades", "taker_base", "taker_quote", "ignore",
            ])
            df["datetime"] = pd.to_datetime(df["open_time"], unit="ms")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return _ohlc_columns(df)
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Binance fetch failed for {symbol}/{interval} after {retries} tries: {last_err}")


def fetch_ohlc(pair: str, interval: str, limit: int = 300) -> pd.DataFrame:
    """interval: '5min' | '1h' | '4h' | 'D1'. Routes: crypto -> Binance,
    metals/forex -> TwelveData with smart cache to minimize API requests."""
    if pair not in CRYPTO_PAIRS:
        cached = _cache_get(pair, interval)
        if cached is not None:
            return cached
    if pair in CRYPTO_PAIRS:
        df = _fetch_binance(pair, interval, limit=limit)
    else:
        df = _fetch_twelvedata(pair, interval, outputsize=limit)
        _cache_set(pair, interval, df)
    return df


def fetch_multi_timeframe(pair: str, limit_m5: int = 300, limit_h1: int = 300,
                           limit_h4: int = 300, limit_d1: int = 300) -> Dict[str, pd.DataFrame]:
    """Fetches M5 (LTF), H1 (H1 bias), H4 (structure), D1 (HTF bias) for one pair."""
    return {
        
        "H1": fetch_ohlc(pair, "H1", limit_h1),
        "H4": fetch_ohlc(pair, "H4", limit_h4),
        "D1": fetch_ohlc(pair, "D1", limit_d1),
    }


# ════════════════════════════════════════════════════════════
# MOCK MODE — for structural testing without network access
# ════════════════════════════════════════════════════════════

def make_mock_ohlc(n: int = 300, start_price: float = 100.0, seed: int = 0) -> pd.DataFrame:
    """Generates a plausible random-walk OHLC DataFrame for offline testing
    of everything downstream of data_fetch (engines, scorer, formatter)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, start_price * 0.002, n)
    close = start_price + np.cumsum(steps)
    open_ = close - rng.normal(0, start_price * 0.0008, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(start_price * 0.0015, start_price * 0.0008, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(start_price * 0.0015, start_price * 0.0008, n))
    volume = rng.uniform(1000, 5000, n)
    dt_index = pd.date_range(end=pd.Timestamp.utcnow(), periods=n, freq="5min")
    return pd.DataFrame({
        "datetime": dt_index, "open": open_, "high": high,
        "low": low, "close": close, "volume": volume,
    })

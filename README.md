# BOBB MARKET ANALYST v3.0 — Signal Engine

Bot GitHub Actions yang jalan otomatis kirim sinyal ke Telegram setiap M5 candle close,
dengan analisa lengkap sesuai BOBB MARKET ANALYST v3.0 (ICT/SMC + Elliott Wave +
Harmonic + Chart Pattern + Divergence + Astronacci).

---

## STRUKTUR FILE

```
bobb_engines/
├── signal_engine.py          # ← MAIN orchestrator (jalanin ini)
├── data_fetch.py              # Data OHLC: TwelveData (metals/forex) + Binance (crypto)
├── ict_engine.py               # ICT/SMC: HTF bias EMA200, BOS/CHoCH, Smart OB, FVG, Fib OTE
├── elliott_wave.py              # Elliott Wave: 5-wave motive + ABC corrective (port LuxAlgo)
├── harmonic_patterns.py          # Harmonic XABCD: Gartley/Bat/Butterfly/Crab/Cypher dll (port TradingIQ)
├── chart_patterns.py              # Double Top/Bottom, H&S, Triangle, Engulfing, Hammer, Morning Star
├── divergence.py                    # RSI Regular/Hidden Divergence, multi-TF HIGH PRIORITY flag
├── astronacci.py                      # Lunar cycle, planetary aspects, Fibonacci Time Projection
├── killzone.py                          # ICT Kill Zone schedule (WIB), Silver Bullet windows
├── confluence_score.py                    # Step 8 — 14-point scorer → HIGH/MEDIUM/LOW/SKIP
├── telegram_formatter.py                    # Format output persis v3.0
├── news_detector.py                           # v1.8 — Economic calendar + breaking news + price spike
├── requirements.txt
└── .github/workflows/
    ├── bobb_signal_engine.yml    # cron */5 * * * * (tiap M5)
    └── bobb_news_detector.yml    # cron * * * * * (tiap menit)
```

---

## CARA DEPLOY

### 1. Buat repo GitHub baru (private)

```bash
git init bobb-signal-bot
cd bobb-signal-bot
# Copy semua file dari bobb_engines/ ke sini
git add .
git commit -m "init: bobb market analyst v3.0"
git remote add origin https://github.com/USERNAME/bobb-signal-bot.git
git push -u origin main
```

### 2. Set GitHub Secrets

Masuk ke repo → Settings → Secrets and variables → Actions → New repository secret

| Secret name | Keterangan |
|---|---|
| `BOT_TOKEN` | Token bot Telegram baru (revoke yang lama dulu di @BotFather) |
| `CHAT_ID` | Chat ID Telegram lo (channel atau private) |
| `TWELVEDATA_API_KEY` | TwelveData API key (free tier cukup buat testing) |
| `BOBB_NEWSAPI_KEY` | (Opsional) NewsAPI.org key |

### 3. Enable Actions

Repo → Actions → "I understand my workflows, go ahead and enable them"

Setelah itu:
- Signal engine otomatis jalan tiap 5 menit
- News detector otomatis jalan tiap menit
- Telegram lo langsung nerima pesan

---

## PAIRS YANG DI-ANALYZE

| Pair | Source |
|---|---|
| XAUUSD, XAGUSD | TwelveData |
| BTCUSDT, ETHUSDT | Binance (no key needed) |
| EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, NZDUSD, USDCAD | TwelveData |

---

## CONFLUENCE SCORE (14 poin)

| Faktor | Score |
|---|---|
| HTF Bias aligned (D1+H4 EMA200) | +2 |
| Elliott Wave direction clear | +2 |
| Fib OTE / Harmonic PRZ | +2 |
| Chart Pattern confirmed | +1 |
| Harmonic PRZ hit | +1 |
| Divergence RSI detected | +1 |
| H4 CHoCH / BOS | +1 |
| Kill Zone aktif | +1 |
| Fibonacci Time window | +1 |
| Lunar Cycle aligned | +1 |
| Planetary Aspect major | +1 |
| **TOTAL** | **14** |

- Score ≥ 10 → 🟢 HIGH — Full size
- Score 6-9 → 🟡 MEDIUM — Half size
- Score 3-5 → 🟠 LOW — Skip/micro
- Score < 3 → 🔴 SKIP

---

## CATATAN PENTING

- **Revoke token lama** di @BotFather sebelum deploy
- TwelveData free tier: 8 req/min — sudah di-throttle otomatis di `data_fetch.py`
- GitHub Actions free tier: 2,000 menit/bulan. Signal engine (5min) pakai ~8.6 jam/bulan, news detector (1min) ~43 jam/bulan — masih dalam limit
- Astronacci 2026: lunar dates & retrograde windows hard-coded, update setiap tahun

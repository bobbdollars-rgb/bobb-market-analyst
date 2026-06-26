import datetime, requests, json, os, re
import warnings
warnings.filterwarnings('ignore')

# ════════════════════════════════════════
# VERSION
# ════════════════════════════════════════
VERSION = 'v2.1'
# v1.0 — Initial standalone news detector
# v1.1 — Dual source FF scraping + MyFxBook fallback
# v1.2 — Primary: FF JSON (nfs.faireconomy.media) — lebih stabil dari scraping
#         Fallback 1: FF HTML scraping
#         Fallback 2: MyFxBook scraping
#         Format waktu WIB + UTC semua pesan
# v1.3 — Breaking News module: RSS Feed (primary) + NewsAPI (fallback)
#         Keyword filter otomatis: war, sanctions, Fed, rate, oil, gold, crypto, dll
#         Anti-duplikat via state, cooldown 4 jam per keyword group
# v1.4 — FIX: sent_breaking cleanup bug — cooldown key tidak pernah expire (zombie)
#         FIX: dedup hash diperpanjang dari 24 jam ke 7 hari — berita sama tidak re-trigger
# v1.5 — FIX: pubDate filter max 6 jam — no more stale/old news
#         FIX: false positive — 'war' whole-word match, 'crisis' butuh financial context
#         FIX: RSS feeds — hapus Bloomberg/MarketWatch (block publik), tambah FT & Guardian
#         FIX: 'sec' keyword dipindah jadi 'sec crypto' supaya tidak false positive
# v1.6 — FIX: Bot token & Chat ID pindah ke env vars (GitHub Secrets) — no more hardcode
#         FIX: sent_reminder & sent_actual cleanup — state JSON tidak lagi membengkak
#         FIX: Hapus FT RSS feed — paywall, selalu 401/403 di GitHub Actions
#         FIX: Eliminate double-fetch di run_news_detector — hemat bandwidth
#         FIX: trade war conflict — hapus dari WAR_FALSE_POSITIVES, tetap di Macro keywords
# v1.7 — NEW: Price Spike Detector — XAUUSD, BTCUSDT, 9 forex pairs
#         Source: Binance public API (BTC), Yahoo Finance (XAU + forex) — gratis, no key
#         Alert kalau harga gerak melebihi threshold dalam 5 menit terakhir
#         Cooldown 30 menit per pair — anti-spam saat volatilitas ekstrem
#         Pesan Telegram include: % move, direction, pair info, level harga
# v1.8 — NEW: Pair Direction Predictor di Actual Result
#         Setelah data rilis, bot prediksi arah tiap pair yang terdampak
#         Logic: currency strength/weakness × pair composition × safe haven behavior
#         Cover: USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD, XAU, BTC
#         Output: ↑/↓/↔ per pair dengan confidence label (Strong/Moderate/Watch)

# ════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════
BOT_TOKEN   = os.environ.get('BOT_TOKEN', '')    # Set via GitHub Secrets / env var
CHAT_ID     = os.environ.get('CHAT_ID', '')      # Set via GitHub Secrets / env var
NEWSAPI_KEY = os.environ.get('NEWSAPI_KEY', '')  # Opsional — newsapi.org free tier

if not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError('[CONFIG] BOT_TOKEN dan CHAT_ID harus diset via environment variable!')

STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'bobb_news_state.json'
)

# ── Breaking News Keywords ────────────────────────────────────────────
# Dikelompokkan per tema — kalau salah satu keyword match, berita dikirim
BREAKING_KEYWORDS = {
    '⚔️ Geopolitical': [
        # 'war' pakai whole-word — hindari false positive "currency war", "star wars"
        # 'crisis' dihapus — terlalu broad, diganti multi-word spesifik
        ' war ', 'attack', 'missile', 'airstrike', 'invasion', 'conflict',
        'troops', 'nuclear', 'sanctions', 'ceasefire', 'explosion', 'coup',
        'terrorism', 'escalation', 'military strike',
        'debt crisis', 'banking crisis', 'financial crisis', 'currency crisis',
    ],
    '🏦 Central Bank': [
        'federal reserve', 'fomc', 'rate hike', 'rate cut',
        'interest rate', 'ecb', 'bank of england', 'bank of japan',
        'rba', 'monetary policy', 'quantitative easing', 'quantitative tightening',
        'powell', 'lagarde', 'inflation target', 'boj rate', 'fed rate',
    ],
    '📈 Market Moving': [
        'emergency', 'market crash', 'stock crash', 'collapse',
        'sovereign default', 'recession', 'bank failure', 'bank run',
        'circuit breaker', 'trading halt', 'bailout',
        'debt ceiling', 'credit downgrade', 'credit rating cut',
    ],
    '🥇 Gold & Oil': [
        'gold price', 'gold rally', 'gold falls', 'xauusd',
        'oil price', 'crude oil', 'opec', 'petroleum',
        'energy crisis', 'supply cut', 'oil output',
    ],
    '₿ Crypto': [
        'bitcoin', 'btc', 'ethereum', 'crypto',
        'sec crypto', 'sec bitcoin', 'etf approval', 'crypto etf',
        'exchange hack', 'stablecoin', 'cbdc', 'crypto regulation',
    ],
    '🌍 Macro': [
        'gdp', 'unemployment rate', 'nonfarm payroll', 'cpi inflation',
        'ppi', 'trade war', 'tariff', 'us dollar', 'treasury yield',
        'yield curve', 'government bond',
    ],
}

# Cooldown — jangan kirim berita dari group yang sama dalam X jam
BREAKING_COOLDOWN_HOURS = 4

# RSS Feeds — gratis, tidak perlu API key
# Bloomberg & MarketWatch dihapus — sudah block RSS publik
# FT dihapus — paywall, return 401/403 di GitHub Actions
RSS_FEEDS = [
    ('Reuters',   'https://feeds.reuters.com/reuters/businessNews'),
    ('Reuters',   'https://feeds.reuters.com/reuters/topNews'),
    ('BBC',       'https://feeds.bbci.co.uk/news/business/rss.xml'),
    ('BBC',       'https://feeds.bbci.co.uk/news/world/rss.xml'),
    ('CNBC',      'https://www.cnbc.com/id/10000664/device/rss/rss.html'),
    ('CNBC',      'https://www.cnbc.com/id/20910258/device/rss/rss.html'),  # CNBC Finance
    ('Guardian',  'https://www.theguardian.com/business/rss'),
]

# Max umur berita yang akan diproses (jam) — filter stale news dari RSS
RSS_MAX_AGE_HOURS = 6

WATCHED_CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF', 'NZD', 'XAU', 'BTC']

CURRENCY_PAIRS = {
    'USD': ['XAUUSD', 'BTCUSDT', 'EURUSD', 'GBPUSD', 'USDJPY'],
    'EUR': ['EURUSD'],
    'GBP': ['GBPUSD', 'GBPJPY'],
    'JPY': ['USDJPY', 'GBPJPY'],
    'XAU': ['XAUUSD'],
    'BTC': ['BTCUSDT'],
}

IMPACT_CONFIG = {
    'High':         {'emoji': '🔴', 'priority': 3, 'include': True},
    'Medium':       {'emoji': '🟡', 'priority': 2, 'include': True},
    'Low':          {'emoji': '⚪', 'priority': 1, 'include': False},
    'Non-Economic': {'emoji': '⬜', 'priority': 0, 'include': False},
}

# ════════════════════════════════════════
# EVENT IMPORTANCE TIER (v2.0)
# Tier 1 = Market mover terbesar (NFP, FOMC, CPI)
# Tier 2 = High impact tapi lebih predictable
# Tier 3 = Medium impact, biasa in-line
# ════════════════════════════════════════
EVENT_TIERS = {
    # Tier 1 — Market Mover (ADR bisa 2-5x normal)
    1: [
        'non-farm', 'nonfarm', 'nfp', 'fomc', 'federal funds rate',
        'interest rate decision', 'rate decision',
        'cpi', 'consumer price index', 'core cpi',
        'gdp', 'gross domestic product',
        'pce', 'core pce',
        'boj rate', 'ecb rate', 'boe rate', 'rba rate',
        'unemployment rate', 'labor',
    ],
    # Tier 2 — High Impact
    2: [
        'ppi', 'producer price', 'retail sales',
        'ism manufacturing', 'ism services', 'pmi',
        'trade balance', 'current account',
        'housing starts', 'building permits',
        'consumer confidence', 'sentiment',
        'fed minutes', 'fomc minutes',
        'powell', 'lagarde', 'bailey', 'ueda',
    ],
    # Tier 3 — Medium Impact
    3: [
        'jobless claims', 'initial claims',
        'existing home sales', 'new home sales',
        'factory orders', 'durable goods',
        'industrial production', 'capacity',
        'business inventories',
    ],
}

EVENT_TIER_LABELS = {
    1: ('🔴🔴 TIER 1', 'Market Mover — spread lebar, slippage tinggi. WAJIB wait for retest'),
    2: ('🔴 TIER 2',   'High Impact — hindari entry 15 menit sebelum/sesudah'),
    3: ('🟡 TIER 3',   'Medium Impact — monitor, entry setelah candle konfirmasi'),
}

def get_event_tier(title: str) -> tuple:
    """Returns (tier_int, label, warning) for an event title."""
    title_lower = title.lower()
    for tier in [1, 2, 3]:
        for kw in EVENT_TIERS[tier]:
            if kw in title_lower:
                label, warning = EVENT_TIER_LABELS[tier]
                return tier, label, warning
    return 0, '⚪ TIER 0', 'Low impact — normal trading'



# ════════════════════════════════════════
# PRICE SPIKE CONFIG
# ════════════════════════════════════════
# Threshold % move dalam 5 menit untuk trigger alert
SPIKE_THRESHOLDS = {
    'XAUUSD':  0.30,   # Gold: alert kalau gerak > 0.30% (~$7-8 dari $2500)
    'BTCUSDT': 1.00,   # BTC: alert kalau gerak > 1.00% (~$600 dari $60k)
    'EURUSD':  0.15,   # Major forex: 15 pip equivalent
    'GBPUSD':  0.15,
    'USDJPY':  0.15,
    'AUDUSD':  0.15,
    'USDCAD':  0.15,
    'USDCHF':  0.15,
    'NZDUSD':  0.15,
    'EURJPY':  0.20,
    'GBPJPY':  0.20,
}

# Cooldown per pair — jangan spam saat market volatile
SPIKE_COOLDOWN_MINUTES = 30

# Yahoo Finance ticker mapping
YAHOO_TICKERS = {
    'XAUUSD': 'GC=F',       # Gold futures
    'EURUSD': 'EURUSD=X',
    'GBPUSD': 'GBPUSD=X',
    'USDJPY': 'JPY=X',
    'AUDUSD': 'AUDUSD=X',
    'USDCAD': 'CAD=X',
    'USDCHF': 'CHF=X',
    'NZDUSD': 'NZDUSD=X',
    'EURJPY': 'EURJPY=X',
    'GBPJPY': 'GBPJPY=X',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

DIV  = '─' * 30
DIV2 = '═' * 30

# ════════════════════════════════════════
# STATE
# ════════════════════════════════════════
def load_state():
    default = {
        'sent_daily':    {},
        'sent_reminder': {},
        'sent_actual':   {},
        'sent_breaking': {},   # title_hash -> timestamp string
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        for k, v in default.items():
            if k not in state:
                state[k] = v
        return state
    except Exception as e:
        print(f'[STATE] Load error: {e}')
        return default

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        print(f'[STATE] Save error: {e}')

# ════════════════════════════════════════
# TELEGRAM
# ════════════════════════════════════════
def send_text(text):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    try:
        resp = requests.post(
            url,
            data={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
            timeout=30
        )
        return resp.json()
    except Exception as e:
        print(f'[TELEGRAM] Error: {e}')
        return {'ok': False}

# ════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════
def clean_val(val):
    if not val:
        return ''
    val = re.sub(r'<[^>]+>', '', str(val)).strip()
    return val if val else ''

def make_event(currency, title, impact_raw, dt_utc, forecast='', previous='', actual='', source=''):
    cfg = IMPACT_CONFIG.get(impact_raw, IMPACT_CONFIG['Low'])
    if not cfg['include']:
        return None
    if currency not in WATCHED_CURRENCIES:
        return None
    dt_wib = dt_utc + datetime.timedelta(hours=7)
    event_id = f"{currency}_{re.sub(r'[^A-Za-z0-9]','_',title)[:20]}_{dt_utc.strftime('%H%M')}"
    tier, tier_label, tier_warning = get_event_tier(title)
    return {
        'id':              event_id,
        'currency':        currency,
        'title':           title,
        'impact':          impact_raw,
        'impact_emoji':    cfg['emoji'],
        'impact_priority': cfg['priority'],
        'tier':            tier,
        'tier_label':      tier_label,
        'tier_warning':    tier_warning,
        'dt_utc':          dt_utc,
        'dt_wib':          dt_wib,
        'time_wib':        dt_wib.strftime('%H:%M'),
        'time_utc':        dt_utc.strftime('%H:%M'),
        'forecast':        clean_val(forecast),
        'previous':        clean_val(previous),
        'actual':          clean_val(actual),
        'affected_pairs':  CURRENCY_PAIRS.get(currency, [currency]),
        'source':          source,
    }

# ════════════════════════════════════════
# SOURCE 1 — FF JSON (PRIMARY)
# Endpoint komunitas trader, stabil bertahun-tahun
# ════════════════════════════════════════
def fetch_ff_json(target_date):
    urls = [
        'https://nfs.faireconomy.media/ff_calendar_thisweek.json',
        'https://nfs.faireconomy.media/ff_calendar_nextweek.json',
    ]
    raw = []
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                raw.extend(resp.json())
        except Exception as e:
            print(f'[FF_JSON] {url} error: {e}')

    if not raw:
        return []

    events = []
    for item in raw:
        try:
            currency = item.get('country', '').upper()
            impact_raw = item.get('impact', 'Low').capitalize()
            title      = item.get('title', 'N/A')
            date_raw   = item.get('date', '')

            if not date_raw:
                continue

            # Parse datetime — FF JSON pakai format ISO dengan offset EST
            try:
                dt_raw = datetime.datetime.fromisoformat(date_raw)
                # Konversi ke UTC
                if dt_raw.utcoffset() is not None:
                    dt_utc = dt_raw - dt_raw.utcoffset()
                    dt_utc = dt_utc.replace(tzinfo=None)
                else:
                    # Assume EST = UTC-5
                    dt_utc = dt_raw + datetime.timedelta(hours=5)
            except Exception:
                try:
                    dt_raw = datetime.datetime.strptime(date_raw[:19], '%Y-%m-%dT%H:%M:%S')
                    dt_utc = dt_raw + datetime.timedelta(hours=5)
                except Exception:
                    continue

            if dt_utc.date() != target_date:
                continue

            ev = make_event(
                currency   = currency,
                title      = title,
                impact_raw = impact_raw,
                dt_utc     = dt_utc,
                forecast   = item.get('forecast', ''),
                previous   = item.get('previous', ''),
                actual     = item.get('actual', ''),
                source     = 'FF-JSON',
            )
            if ev:
                events.append(ev)

        except Exception as e:
            print(f'[FF_JSON] Parse error: {e}')

    events.sort(key=lambda x: x['dt_utc'])
    print(f'[FF_JSON] {len(events)} events for {target_date}')
    return events

# ════════════════════════════════════════
# SOURCE 2 — FF HTML SCRAPING (FALLBACK 1)
# ════════════════════════════════════════
def fetch_ff_html(target_date):
    try:
        date_param = target_date.strftime('%b%d.%Y').lower()
        url = f'https://www.forexfactory.com/calendar?day={date_param}'
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f'[FF_HTML] HTTP {resp.status_code}')
            return []

        html   = resp.text
        events = _parse_ff_html(html, target_date)
        print(f'[FF_HTML] {len(events)} events')
        return events
    except Exception as e:
        print(f'[FF_HTML] Error: {e}')
        return []

def _parse_ff_html(html, target_date):
    events = []
    try:
        rows = re.findall(r'<tr[^>]*calendar__row[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
        last_dt = None

        for row in rows:
            try:
                curr_m = re.search(r'calendar__currency[^>]*>\s*([A-Z]{3})\s*<', row, re.IGNORECASE)
                if not curr_m:
                    continue
                currency = curr_m.group(1).upper()

                imp_m = re.search(r'impact--(\w+)', row, re.IGNORECASE)
                if not imp_m:
                    continue
                impact_raw = imp_m.group(1).capitalize()

                title_m = re.search(r'calendar__event-title[^>]*>\s*([^<]+)\s*<', row, re.IGNORECASE)
                title   = clean_val(title_m.group(1)) if title_m else 'N/A'

                time_m   = re.search(r'calendar__time[^>]*>\s*([^<]+)\s*<', row, re.IGNORECASE)
                time_str = clean_val(time_m.group(1)) if time_m else ''
                dt_utc   = _parse_ff_time(time_str, target_date, last_dt)
                if dt_utc:
                    last_dt = dt_utc
                else:
                    dt_utc = last_dt
                if not dt_utc:
                    continue

                fore_m = re.search(r'calendar__forecast[^>]*>\s*([^<]*)', row, re.IGNORECASE)
                act_m  = re.search(r'calendar__actual[^>]*>\s*([^<]*)',   row, re.IGNORECASE)
                prev_m = re.search(r'calendar__previous[^>]*>\s*([^<]*)', row, re.IGNORECASE)

                ev = make_event(
                    currency   = currency,
                    title      = title,
                    impact_raw = impact_raw,
                    dt_utc     = dt_utc,
                    forecast   = fore_m.group(1) if fore_m else '',
                    previous   = prev_m.group(1) if prev_m else '',
                    actual     = act_m.group(1)  if act_m  else '',
                    source     = 'FF-HTML',
                )
                if ev:
                    events.append(ev)
            except Exception:
                continue

        events.sort(key=lambda x: x['dt_utc'])
    except Exception as e:
        print(f'[FF_HTML_PARSE] Error: {e}')
    return events

def _parse_ff_time(time_str, target_date, last_dt):
    try:
        time_str = clean_val(time_str).upper()
        if not time_str or time_str in ['ALL DAY', 'TENTATIVE', '']:
            return None
        m = re.match(r'(\d{1,2}):(\d{2})(AM|PM)', time_str)
        if not m:
            return None
        h, mn, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        if ampm == 'PM' and h != 12:
            h += 12
        elif ampm == 'AM' and h == 12:
            h = 0
        dt_est = datetime.datetime(target_date.year, target_date.month, target_date.day, h, mn)
        return dt_est + datetime.timedelta(hours=5)  # EST → UTC
    except Exception:
        return None

# ════════════════════════════════════════
# SOURCE 3 — MYFXBOOK (FALLBACK 2)
# ════════════════════════════════════════
def fetch_myfxbook(target_date):
    try:
        ds  = target_date.strftime('%Y-%m-%d')
        url = f'https://www.myfxbook.com/forex-economic-calendar/{ds}/{ds}'
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f'[MFB] HTTP {resp.status_code}')
            return []

        html   = resp.text
        events = _parse_mfb_html(html, target_date)
        print(f'[MFB] {len(events)} events')
        return events
    except Exception as e:
        print(f'[MFB] Error: {e}')
        return []

def _parse_mfb_html(html, target_date):
    events = []
    try:
        rows = re.findall(r'<tr[^>]*calRow[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
        for row in rows:
            try:
                curr_m = re.search(r'currency[^>]*>\s*([A-Z]{3})\s*<', row, re.IGNORECASE)
                if not curr_m:
                    continue
                currency = curr_m.group(1).upper()

                imp_m      = re.search(r'impact[_-](\w+)', row, re.IGNORECASE)
                impact_raw = imp_m.group(1).capitalize() if imp_m else 'Low'

                title_m = re.search(r'event[^>]*>\s*<[^>]+>\s*([^<]+)', row, re.IGNORECASE)
                title   = clean_val(title_m.group(1)) if title_m else 'N/A'

                time_m   = re.search(r'time[^>]*>\s*([^<]+)\s*<', row, re.IGNORECASE)
                time_str = clean_val(time_m.group(1)) if time_m else ''
                dt_utc   = _parse_mfb_time(time_str, target_date)
                if not dt_utc:
                    continue

                fore_m = re.search(r'forecast[^>]*>\s*([^<]*)', row, re.IGNORECASE)
                act_m  = re.search(r'actual[^>]*>\s*([^<]*)',   row, re.IGNORECASE)
                prev_m = re.search(r'previous[^>]*>\s*([^<]*)', row, re.IGNORECASE)

                ev = make_event(
                    currency   = currency,
                    title      = title,
                    impact_raw = impact_raw,
                    dt_utc     = dt_utc,
                    forecast   = fore_m.group(1) if fore_m else '',
                    previous   = prev_m.group(1) if prev_m else '',
                    actual     = act_m.group(1)  if act_m  else '',
                    source     = 'MyFxBook',
                )
                if ev:
                    events.append(ev)
            except Exception:
                continue
        events.sort(key=lambda x: x['dt_utc'])
    except Exception as e:
        print(f'[MFB_PARSE] Error: {e}')
    return events

def _parse_mfb_time(time_str, target_date):
    try:
        m = re.match(r'(\d{1,2}):(\d{2})', time_str.strip())
        if not m:
            return None
        h, mn = int(m.group(1)), int(m.group(2))
        return datetime.datetime(target_date.year, target_date.month, target_date.day, h, mn)
    except Exception:
        return None

# ════════════════════════════════════════
# FETCH — TRIPLE SOURCE
# ════════════════════════════════════════

# ════════════════════════════════════════
# CALENDAR CACHE (v2.1)
# Cache events ke state file — kalau fetch gagal, pakai cache
# TTL = 4 jam (events tidak berubah intraday setelah pagi)
# ════════════════════════════════════════
CACHE_TTL_HOURS = 4

def _get_cache_key(target_date) -> str:
    return f"calendar_cache_{target_date.isoformat()}"

def _load_calendar_cache(target_date) -> list:
    """Load cached events kalau masih fresh (< TTL jam)."""
    try:
        state = load_state()
        key   = _get_cache_key(target_date)
        entry = state.get(key)
        if not entry:
            return []
        cached_at = datetime.datetime.fromisoformat(entry["cached_at"])
        age_hours = (datetime.datetime.utcnow() - cached_at).total_seconds() / 3600
        if age_hours <= CACHE_TTL_HOURS:
            print(f"[CACHE] Calendar hit (age {age_hours:.1f}h) for {target_date}")
            return entry["events"]
        print(f"[CACHE] Calendar expired (age {age_hours:.1f}h) — refetch")
        return []
    except Exception:
        return []

def _save_calendar_cache(target_date, events: list):
    """Cache events ke state file."""
    try:
        state = load_state()
        key   = _get_cache_key(target_date)
        state[key] = {
            "cached_at": datetime.datetime.utcnow().isoformat(),
            "events": events,
        }
        save_state(state)
    except Exception as e:
        print(f"[CACHE] Save error: {e}")

def fetch_events(target_date):
    # 1. Try cache first
    cached = _load_calendar_cache(target_date)
    if cached:
        return cached, 'Cache'

    # 2. Primary: FF JSON
    print('[FETCH] Trying FF-JSON (primary)...')
    events = fetch_ff_json(target_date)
    if events:
        _save_calendar_cache(target_date, events)
        return events, 'FF-JSON'

    # 3. Fallback 1: FF HTML
    print('[FETCH] Trying FF-HTML (fallback 1)...')
    events = fetch_ff_html(target_date)
    if events:
        _save_calendar_cache(target_date, events)
        return events, 'FF-HTML'

    # 4. Fallback 2: MyFxBook
    print('[FETCH] Trying MyFxBook (fallback 2)...')
    events = fetch_myfxbook(target_date)
    if events:
        _save_calendar_cache(target_date, events)
        return events, 'MyFxBook'

    # 5. Last resort: return stale cache if any
    state = load_state()
    key   = _get_cache_key(target_date)
    entry = state.get(key)
    if entry and entry.get('events'):
        print('[FETCH] ⚠️ All sources failed — using stale cache')
        return entry['events'], 'Stale-Cache'

    print('[FETCH] ❌ All sources failed, no cache available')
    return [], 'None'

# ════════════════════════════════════════
# FORMAT MESSAGES
# ════════════════════════════════════════
def fmt_daily_briefing(events, now, source):
    date_str = (now + datetime.timedelta(hours=7)).strftime('%A, %d %b %Y')

    if not events:
        return (
            f'📅 <b>ECONOMIC CALENDAR</b>\n'
            f'<b>Bobb Market Intelligence v1.8</b>\n'
            f'{DIV2}\n'
            f'📆 {date_str} (WIB)\n'
            f'{DIV}\n'
            f'✅ No high/medium impact news today.\n'
            f'<i>Safe to trade all sessions.</i>\n'
            f'{DIV2}\n'
            f'<i>Source: {source} | {now.strftime("%d %b %Y %H:%M")} UTC</i>'
        )

    high_ev   = [e for e in events if e['impact'] == 'High']
    medium_ev = [e for e in events if e['impact'] == 'Medium']

    lines = ''
    for e in events:
        pairs_str = ' '.join(e['affected_pairs'][:3])
        forecast  = e['forecast'] if e['forecast'] else '—'
        previous  = e['previous'] if e['previous'] else '—'
        lines += (
            f'\n{e["impact_emoji"]} <b>{e["time_wib"]} WIB</b> ({e["time_utc"]} UTC)'
            f'  [{e["currency"]}] {e["title"]}\n'
            f'   Forecast: {forecast}  |  Previous: {previous}\n'
            f'   Pairs: <i>{pairs_str}</i>\n'
        )

    return (
        f'📅 <b>ECONOMIC CALENDAR</b>\n'
        f'<b>Bobb Market Intelligence v1.8</b>\n'
        f'{DIV2}\n'
        f'📆 {date_str} (WIB)\n'
        f'{DIV}\n'
        f'🔴 High Impact  : <b>{len(high_ev)}</b> event(s)\n'
        f'🟡 Medium Impact: <b>{len(medium_ev)}</b> event(s)\n'
        f'{DIV}\n'
        f'{lines}'
        f'{DIV}\n'
        f'⚠️ Avoid new entries 30 min before & after HIGH impact!\n'
        f'{DIV2}\n'
        f'<i>Source: {source} | {now.strftime("%d %b %Y %H:%M")} UTC</i>'
    )

def fmt_reminder(event, minutes_left):
    pairs_str = ', '.join(event['affected_pairs'])
    forecast  = event['forecast'] if event['forecast'] else '—'
    previous  = event['previous'] if event['previous'] else '—'
    urgency   = '🚨' if event['impact'] == 'High' else '⚠️'

    return (
        f'{urgency} <b>NEWS REMINDER — {minutes_left} MIN</b>\n'
        f'{DIV}\n'
        f'{event["impact_emoji"]} <b>[{event["currency"]}] {event["title"]}</b>\n'
        f'{DIV}\n'
        f'🕐 Time     : <b>{event["time_wib"]} WIB</b>  ({event["time_utc"]} UTC)\n'
        f'📊 Impact   : <b>{event["impact"]}</b> {event["impact_emoji"]}\n'
        f'🎯 Forecast : {forecast}\n'
        f'📈 Previous : {previous}\n'
        f'{DIV}\n'
        f'💱 Affected : <i>{pairs_str}</i>\n'
        f'{DIV}\n'
        f'⛔ <b>Avoid new entries until news passes!</b>\n'
        f'<i>Bobb Market Intelligence v1.8</i>'
    )


# ════════════════════════════════════════
# PAIR DIRECTION PREDICTOR
# ════════════════════════════════════════
# Pair composition: BASE / QUOTE
PAIR_COMPOSITION = {
    'EURUSD':  ('EUR', 'USD'),
    'GBPUSD':  ('GBP', 'USD'),
    'USDJPY':  ('USD', 'JPY'),
    'AUDUSD':  ('AUD', 'USD'),
    'USDCAD':  ('USD', 'CAD'),
    'USDCHF':  ('USD', 'CHF'),
    'NZDUSD':  ('NZD', 'USD'),
    'EURJPY':  ('EUR', 'JPY'),
    'GBPJPY':  ('GBP', 'JPY'),
    'XAUUSD':  ('XAU', 'USD'),
    'BTCUSDT': ('BTC', 'USD'),
}

# Safe haven — naik saat risk-off
SAFE_HAVEN = {'XAU', 'JPY', 'CHF'}


def _calc_surprise_magnitude(actual_str: str, forecast_str: str, previous_str: str) -> dict:
    """
    Hitung surprise magnitude (seberapa jauh actual vs forecast).
    Returns dict dengan: magnitude (float), label (str), is_significant (bool).
    Significant = surprise > 0.5 std dev proxy (heuristic dari 30% miss).
    """
    def _parse(s):
        if not s: return None
        s = re.sub(r'[^0-9.\-]', '', str(s))
        try: return float(s)
        except: return None

    actual   = _parse(actual_str)
    forecast = _parse(forecast_str)
    previous = _parse(previous_str)

    if actual is None or forecast is None:
        return {'magnitude': 0.0, 'label': 'N/A', 'is_significant': False, 'pct_miss': 0.0}

    diff = actual - forecast
    base = abs(forecast) if forecast != 0 else (abs(previous) if previous else 1.0)
    pct_miss = (abs(diff) / base * 100) if base else 0.0

    if pct_miss >= 30:
        label, significant = 'MAJOR SURPRISE', True
    elif pct_miss >= 10:
        label, significant = 'NOTABLE MISS', True
    elif pct_miss >= 3:
        label, significant = 'SLIGHT MISS', False
    else:
        label, significant = 'IN-LINE', False

    return {'magnitude': round(diff, 4), 'label': label,
            'is_significant': significant, 'pct_miss': round(pct_miss, 1)}


# Pre-news consolidation patterns — market often ranges 1-2 hours before Tier 1 event
PRE_NEWS_REGIMES = {
    'risk_on': ['equity rally', 'risk appetite', 'optimism', 'strong gdp', 'strong jobs'],
    'risk_off': ['recession', 'war', 'crisis', 'sell-off', 'fear', 'bank failure'],
    'hawkish': ['rate hike', 'inflation high', 'tight', 'restrictive', 'above target'],
    'dovish': ['rate cut', 'easing', 'below target', 'slowdown', 'soft landing'],
}

def _detect_current_regime(recent_headlines: list) -> str:
    """Detect current market regime from recent breaking news headlines."""
    if not recent_headlines: return 'neutral'
    text = ' '.join(recent_headlines).lower()
    scores = {k: sum(1 for kw in v if kw in text) for k, v in PRE_NEWS_REGIMES.items()}
    if max(scores.values()) == 0: return 'neutral'
    return max(scores, key=scores.get)



# ════════════════════════════════════════
# CROSS-PAIR DIRECTION FIX (v2.1)
# GBPJPY, EURJPY, GBPAUD etc. butuh KEDUA currency dianalisa
# ════════════════════════════════════════
CROSS_PAIR_MAP = {
    'GBPJPY': ('GBP', 'JPY'),
    'EURJPY': ('EUR', 'JPY'),
    'GBPAUD': ('GBP', 'AUD'),
    'EURGBP': ('EUR', 'GBP'),
    'AUDNZD': ('AUD', 'NZD'),
}

def _predict_cross_pair(base_pred: dict, quote_pred: dict, pair: str) -> dict:
    """
    For cross pairs: direction = base_pred direction vs quote_pred direction.
    If both go same way, cancel out (Watch).
    If diverging, stronger one wins.
    """
    base_up   = base_pred.get('direction') == 'up'
    quote_up  = quote_pred.get('direction') == 'up'
    base_conf = {'Strong': 2, 'Moderate': 1, 'Watch': 0}.get(base_pred.get('confidence', ''), 0)
    quote_conf = {'Strong': 2, 'Moderate': 1, 'Watch': 0}.get(quote_pred.get('confidence', ''), 0)

    if base_up and not quote_up:
        # Base strong, quote weak → pair goes UP
        conf = 'Strong' if base_conf >= 2 and quote_conf >= 1 else 'Moderate'
        return {'direction': 'up', 'confidence': conf,
                'reason': f"Base {pair[:3]} strong + Quote {pair[3:]} weak"}
    elif not base_up and quote_up:
        # Base weak, quote strong → pair goes DOWN
        conf = 'Strong' if quote_conf >= 2 and base_conf >= 1 else 'Moderate'
        return {'direction': 'down', 'confidence': conf,
                'reason': f"Base {pair[:3]} weak + Quote {pair[3:]} strong"}
    else:
        # Both moving same direction — cancel out
        return {'direction': None, 'confidence': 'Watch',
                'reason': f"Mixed signal — {pair[:3]} & {pair[3:]} both {'strong' if base_up else 'weak'}"}

def _predict_pair_directions(currency, sentiment, event_title='',
                              surprise: dict = None, regime: str = 'neutral'):
    """
    Prediksi arah pair berdasarkan currency yang rilis, sentiment, dan konteks.
    sentiment: 'better' | 'worse' | 'inline'
    surprise: dict dari _calc_surprise_magnitude()
    regime: 'risk_on' | 'risk_off' | 'hawkish' | 'dovish' | 'neutral'

    Upgrade v2.0:
    - Surprise magnitude weighting (significant miss = stronger signal)
    - Regime awareness (risk-off overrides standard logic for safe havens)
    - "Buy the rumor, sell the fact" flag untuk Tier 1 events
    - Confidence downgrade kalau in-line atau tidak significant
    Returns list of dict sorted by confidence.
    """
    if sentiment == 'inline':
        return []

    is_better       = (sentiment == 'better')
    currency_strong = is_better
    title_lower     = event_title.lower()
    is_inflation    = any(k in title_lower for k in ['cpi', 'ppi', 'inflation', 'price index'])
    is_tier1        = get_event_tier(event_title)[0] == 1
    is_significant  = surprise.get('is_significant', False) if surprise else True
    pct_miss        = surprise.get('pct_miss', 0.0) if surprise else 0.0

    # "Buy the rumor, sell the fact" warning untuk Tier 1
    brsf_warning = ''
    if is_tier1 and is_significant:
        brsf_warning = '⚠️ Tier 1: Wait retest — jangan chase spike pertama'

    # Regime override flags
    risk_off_active = regime in ('risk_off',)
    hawkish_active  = regime in ('hawkish',)

    results = []

    for pair, (base, quote) in PAIR_COMPOSITION.items():
        if currency not in (base, quote):
            continue

        direction  = None
        confidence = 'Moderate'
        reason     = ''

        # ── Core: base/quote logic ───────────────────────────────────
        if currency == base:
            direction  = 'up' if currency_strong else 'down'
            confidence = 'Strong'
            reason     = f'{currency} {"menguat" if currency_strong else "melemah"} sbg base'
        elif currency == quote:
            direction  = 'down' if currency_strong else 'up'
            confidence = 'Strong'
            reason     = f'{currency} {"menguat" if currency_strong else "melemah"} sbg quote'

        # ── Safe haven override (non-direct) ─────────────────────────
        if base in SAFE_HAVEN and currency != base:
            direction  = 'up' if not currency_strong else 'down'
            confidence = 'Moderate'
            reason     = f'{"Risk-off" if not currency_strong else "Risk-on"} → {base} safe haven'

        if quote in SAFE_HAVEN and currency != quote:
            direction  = 'down' if not currency_strong else 'up'
            confidence = 'Moderate'
            reason     = f'{"Risk-off" if not currency_strong else "Risk-on"} → {quote} safe haven menguat'

        # ── XAUUSD special ───────────────────────────────────────────
        if pair == 'XAUUSD':
            if currency == 'USD':
                direction  = 'down' if currency_strong else 'up'
                confidence = 'Strong'
                reason     = f'USD {"menguat" if currency_strong else "melemah"} → XAUUSD inverse'
            else:
                direction  = 'up' if not currency_strong else 'down'
                confidence = 'Watch'
                reason     = f'{"Risk-off" if not currency_strong else "Risk-on"} → XAU {"naik" if not currency_strong else "koreksi"}'

        # ── JPY special — intervention risk ─────────────────────────
        if 'JPY' in (base, quote):
            # JPY pair fundamentally driven by BoJ intervention fear
            # USD/JPY bisa TURUN saat NFP bagus jika ada BoJ intervention risk
            title_l = event_title.lower()
            is_boj_week = any(k in title_l for k in ['boj', 'bank of japan', 'intervention', 'japanese'])
            if is_boj_week and 'JPY' == quote and currency_strong:
                direction  = 'down'  # BoJ intervenes when JPY too weak
                confidence = 'Watch'
                reason     = 'USD strong TAPI BoJ intervention risk — kontra-tren'
            elif 'JPY' == quote:
                pass  # keep standard logic

        # ── AUD/NZD/CAD — commodity currency context ─────────────────
        # These move with China PMI / commodity prices, not just USD
        if pair in ('AUDUSD', 'NZDUSD') and currency == 'USD':
            if currency_strong:
                confidence = 'Moderate'  # downgrade: AUD/NZD also driven by China
                reason += ' (note: AUD/NZD juga driven China PMI, bukan cuma USD)'
        if pair == 'USDCAD' and currency == 'USD':
            confidence = 'Moderate'
            reason += ' (CAD korelasi ke oil price — cek crude juga)'

        # ── BTCUSDT special ──────────────────────────────────────────
        if pair == 'BTCUSDT':
            if currency == 'USD':
                direction  = 'down' if currency_strong else 'up'
                confidence = 'Moderate'
                reason     = f'USD {"menguat" if currency_strong else "melemah"} → BTC biasanya {"turun" if currency_strong else "naik"}'
            else:
                continue   # BTC kurang sensitif ke non-USD data

        # ── Inflation boost ──────────────────────────────────────────
        if is_inflation and currency_strong and confidence == 'Strong':
            reason += ' + CPI tinggi → hawkish'

        if direction is None:
            continue

        # Downgrade confidence if surprise is not significant
        if not is_significant and confidence == 'Strong':
            confidence = 'Moderate'
            reason += ' (surprise kecil, hati-hati)'
        # Regime override: risk-off boosts safe haven pairs
        if risk_off_active and pair in ('XAUUSD', 'USDJPY', 'USDCHF'):
            confidence = 'Strong'
            reason += ' + risk-off regime aktif'

        conf_emoji = {'Strong': '🔴', 'Moderate': '🟡', 'Watch': '⚪'}.get(confidence, '⚪')
        results.append({
            'pair':        pair,
            'direction':   direction,
            'arrow':       '↑' if direction == 'up' else '↓',
            'confidence':  confidence,
            'conf_emoji':  conf_emoji,
            'reason':      reason,
            'brsf':        brsf_warning if confidence == 'Strong' else '',
            'pct_miss':    pct_miss,
        })

    order = {'Strong': 0, 'Moderate': 1, 'Watch': 2}
    results.sort(key=lambda x: order.get(x['confidence'], 3))
    return results


def fmt_actual_result(event):
    pairs_str = ', '.join(event['affected_pairs'])
    actual    = event['actual']   if event['actual']   else '—'
    forecast  = event['forecast'] if event['forecast'] else '—'
    previous  = event['previous'] if event['previous'] else '—'
    tier_label   = event.get('tier_label', '')
    tier_warning = event.get('tier_warning', '')
    # Compute surprise magnitude
    surprise = _calc_surprise_magnitude(event['actual'], event['forecast'], event['previous'])
    surprise_line = ''
    if surprise['label'] != 'N/A' and actual != '—' and forecast != '—':
        sign = '+' if surprise['magnitude'] >= 0 else ''
        surprise_line = (f"\n📊 <b>Surprise</b>: {surprise['label']} "
                         f"({sign}{surprise['magnitude']}, miss {surprise['pct_miss']}%)")

    sentiment       = 'Result released'
    sentiment_emoji = '📰'
    sentiment_key   = 'inline'
    try:
        act_val  = float(re.sub(r'[^0-9.\-]', '', str(actual)))
        fore_val = float(re.sub(r'[^0-9.\-]', '', str(forecast)))
        if act_val > fore_val:
            sentiment       = 'Better than forecast'
            sentiment_emoji = '🟢'
            sentiment_key   = 'better'
        elif act_val < fore_val:
            sentiment       = 'Worse than forecast'
            sentiment_emoji = '🔴'
            sentiment_key   = 'worse'
        else:
            sentiment       = 'In line with forecast'
            sentiment_emoji = '🟡'
            sentiment_key   = 'inline'
    except Exception:
        pass

    # ── Pair Direction Prediction ────────────────────────────────────
    direction_block = ''
    if sentiment_key != 'inline':
        predictions = _predict_pair_directions(
            currency    = event['currency'],
            sentiment   = sentiment_key,
            event_title = event['title'],
        )
        if predictions:
            lines = []
            for p in predictions:
                lines.append(
                    f'  {p["conf_emoji"]} {p["arrow"]} <b>{p["pair"]}</b>'
                    f'  <i>({p["confidence"]})</i>'
                )
            direction_block = (
                f'{DIV}\n'
                f'🧭 <b>Prediksi Arah Pair:</b>\n'
                + '\n'.join(lines) + '\n'
                + f'<i>🔴 Strong  🟡 Moderate  ⚪ Watch</i>\n'
            )

    return (
        f'📰 <b>NEWS RESULT — {event["currency"]}</b>\n'
        f'{DIV}\n'
        f'{event["impact_emoji"]} <b>{event["title"]}</b>\n'
        f'{DIV}\n'
        f'🕐 Time     : {event["time_wib"]} WIB  ({event["time_utc"]} UTC)\n'
        f'📊 Impact   : {event["impact"]} {event["impact_emoji"]}\n'
        f'{DIV}\n'
        f'✅ Actual   : <b>{actual}</b>\n'
        f'🎯 Forecast : {forecast}\n'
        f'📈 Previous : {previous}\n'
        f'{DIV}\n'
        f'{sentiment_emoji} <b>{sentiment}</b>\n'
        f'💱 Affects  : <i>{pairs_str}</i>\n'
        f'{direction_block}'
        f'{DIV}\n'
        f'<i>Bobb Market Intelligence v1.8</i>'
    )

def fmt_all_sources_failed(now):
    return (
        f'⚠️ <b>NEWS DETECTOR — SOURCE ERROR</b>\n'
        f'{DIV}\n'
        f'🕐 {(now + datetime.timedelta(hours=7)).strftime("%H:%M")} WIB  '
        f'({now.strftime("%H:%M")} UTC)\n'
        f'{DIV}\n'
        f'❌ Semua sumber data tidak dapat diakses:\n'
        f'   • FF-JSON\n'
        f'   • FF-HTML\n'
        f'   • MyFxBook\n'
        f'{DIV}\n'
        f'⚠️ Cek manual: forexfactory.com\n'
        f'<i>Bobb Market Intelligence v1.8</i>'
    )

# ════════════════════════════════════════
# BREAKING NEWS — RSS + NEWSAPI
# ════════════════════════════════════════
def _match_keywords(text):
    """
    Cek apakah text mengandung keyword dari BREAKING_KEYWORDS.
    - Keyword dengan spasi padding (' war ') = whole-word match
    - Keyword biasa = substring match
    - Special case: ' war ' tidak boleh didahului oleh kata konteks non-geopolitik
    Return: (group_name, matched_keyword) atau (None, None)
    """
    # Pad text dengan spasi supaya ' war ' bisa match di awal/akhir kalimat
    text_lower = ' ' + text.lower() + ' '

    # Blacklist prefix untuk keyword ' war ' — hindari false positive
    # Catatan: 'trade war' TIDAK dimasukkan di sini karena sudah jadi keyword sendiri
    # di group Macro — biarkan dia trigger sebagai 'trade war' keyword, bukan ' war '
    WAR_FALSE_POSITIVES = [
        'currency war', 'star wars', 'price war',
        'bidding war', 'talent war', 'wage war', 'turf war',
        'browser war', 'streaming war', 'at war with', 'at war over',
        'word war', 'drug war', 'format war', 'standards war',
    ]

    for group, keywords in BREAKING_KEYWORDS.items():
        for kw in keywords:
            if kw not in text_lower:
                continue

            # Special handling untuk keyword ' war '
            if kw == ' war ':
                # Cek apakah ini false positive
                is_fp = any(fp in text_lower for fp in WAR_FALSE_POSITIVES)
                if is_fp:
                    continue

            return group, kw.strip()

    return None, None

def _news_hash(title):
    """Buat hash pendek dari judul berita untuk dedup."""
    import hashlib
    return hashlib.md5(title.lower().strip().encode()).hexdigest()[:12]

def _parse_pubdate(date_str):
    """
    Parse pubDate dari RSS feed ke datetime UTC.
    Support format: RFC 2822 (standard RSS) dan ISO 8601.
    Return datetime atau None kalau gagal parse.
    """
    if not date_str:
        return None
    date_str = date_str.strip()

    # Format RFC 2822: "Sun, 21 Jun 2026 10:21:00 +0000" atau "Sun, 21 Jun 2026 10:21:00 GMT"
    rfc_fmts = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
        '%d %b %Y %H:%M:%S %z',
        '%d %b %Y %H:%M:%S GMT',
    ]
    for fmt in rfc_fmts:
        try:
            dt = datetime.datetime.strptime(date_str, fmt)
            # Konversi ke UTC naive
            if dt.tzinfo is not None:
                dt = dt - dt.utcoffset()
                dt = dt.replace(tzinfo=None)
            return dt
        except Exception:
            continue

    # Format ISO 8601: "2026-06-21T10:21:00Z" atau "2026-06-21T10:21:00+00:00"
    try:
        dt = datetime.datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        if dt.tzinfo is not None:
            dt = dt - dt.utcoffset()
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        pass

    return None


def _parse_rss_xml(xml_text, source_name):
    """Parse RSS XML dan return list of (title, description, pub_date, link)."""
    items = []
    try:
        # Extract <item> blocks
        item_blocks = re.findall(r'<item[^>]*>(.*?)</item>', xml_text, re.DOTALL | re.IGNORECASE)
        for block in item_blocks:
            title_m = re.search(r'<title[^>]*><!\[CDATA\[(.*?)\]\]>|<title[^>]*>(.*?)</title>', block, re.DOTALL | re.IGNORECASE)
            desc_m  = re.search(r'<description[^>]*><!\[CDATA\[(.*?)\]\]>|<description[^>]*>(.*?)</description>', block, re.DOTALL | re.IGNORECASE)
            date_m  = re.search(r'<pubDate[^>]*>(.*?)</pubDate>', block, re.DOTALL | re.IGNORECASE)
            link_m  = re.search(r'<link[^>]*>(.*?)</link>|<link>(.*?)</link>', block, re.DOTALL | re.IGNORECASE)

            title = clean_val(title_m.group(1) or title_m.group(2)) if title_m else ''
            desc  = clean_val(desc_m.group(1)  or desc_m.group(2))  if desc_m  else ''
            date  = clean_val(date_m.group(1))  if date_m  else ''
            link  = clean_val(link_m.group(1)   or (link_m.group(2) if link_m and len(link_m.groups()) > 1 else '')) if link_m else ''

            if title:
                items.append({
                    'title':   title,
                    'desc':    desc,
                    'date':    date,
                    'link':    link,
                    'source':  source_name,
                })
    except Exception as e:
        print(f'[RSS_PARSE] {source_name} error: {e}')
    return items


# ════════════════════════════════════════
# BREAKING NEWS DEDUPLICATION (v2.1)
# Prevent same story appearing 2-3x from different sources
# Also add basic sentiment context detection
# ════════════════════════════════════════
SENTIMENT_KEYWORDS = {
    'hawkish': ['rate hike', 'hikes rate', 'raises rate', 'tightening', 'above target',
                'inflation surge', 'hot cpi', 'beat expectations'],
    'dovish':  ['rate cut', 'cuts rate', 'lowers rate', 'easing', 'below target',
                'soft landing', 'slowing inflation', 'misses expectations'],
    'risk_off': ['recession', 'crisis', 'crash', 'war', 'attack', 'bank failure',
                 'sell-off', 'fear', 'uncertainty'],
    'risk_on':  ['rally', 'surge', 'optimism', 'recovery', 'strong gdp', 'strong jobs'],
}

def _detect_headline_sentiment(headline: str) -> str:
    """Detect sentiment context from headline text."""
    h = headline.lower()
    for sentiment, keywords in SENTIMENT_KEYWORDS.items():
        if any(kw in h for kw in keywords):
            return sentiment
    return 'neutral'

def _deduplicate_headlines(articles: list) -> list:
    """
    Remove duplicate/near-duplicate headlines.
    Strategy: normalize headline, keep first occurrence only.
    Also adds sentiment field to each article.
    """
    seen = set()
    unique = []
    for art in articles:
        title = art.get('title', '')
        # Normalize: lowercase, remove punctuation, collapse whitespace
        norm = re.sub(r'[^a-z0-9 ]', '', title.lower())
        norm = re.sub(r'\s+', ' ', norm).strip()
        # Use first 8 words as fingerprint
        fingerprint = ' '.join(norm.split()[:8])
        if fingerprint and fingerprint not in seen:
            seen.add(fingerprint)
            art['sentiment'] = _detect_headline_sentiment(title)
            unique.append(art)
    return unique

def fetch_rss_breaking(now):
    """
    Fetch berita dari semua RSS feeds.
    Filter: hanya berita dalam RSS_MAX_AGE_HOURS terakhir.
    Berita tanpa pubDate tetap diproses (biar tidak miss), tapi ditandai.
    """
    all_items = []
    cutoff = now - datetime.timedelta(hours=RSS_MAX_AGE_HOURS)

    for source_name, url in RSS_FEEDS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                print(f'[RSS] {source_name}: HTTP {resp.status_code}')
                continue

            items = _parse_rss_xml(resp.text, source_name)
            fresh = 0
            stale = 0
            no_date = 0

            for item in items:
                pub_dt = _parse_pubdate(item.get('date', ''))
                if pub_dt is None:
                    # Tidak ada pubDate — tetap masukkan tapi tandai
                    item['pub_dt'] = None
                    all_items.append(item)
                    no_date += 1
                elif pub_dt >= cutoff:
                    item['pub_dt'] = pub_dt
                    all_items.append(item)
                    fresh += 1
                else:
                    stale += 1

            print(f'[RSS] {source_name}: {fresh} fresh, {stale} stale skipped, {no_date} no-date')

        except Exception as e:
            print(f'[RSS] {source_name} error: {e}')

    return all_items

def fetch_newsapi_breaking(now):
    """Fetch berita dari NewsAPI.org — fallback kalau RSS semua gagal."""
    if not NEWSAPI_KEY:
        print('[NEWSAPI] No key configured, skipping')
        return []
    try:
        # Query gabungan keyword terpenting
        q = 'war OR sanctions OR "federal reserve" OR "rate hike" OR gold OR bitcoin OR crash OR recession'
        url = (
            f'https://newsapi.org/v2/everything?'
            f'q={requests.utils.quote(q)}&'
            f'language=en&'
            f'sortBy=publishedAt&'
            f'pageSize=20&'
            f'apiKey={NEWSAPI_KEY}'
        )
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f'[NEWSAPI] HTTP {resp.status_code}')
            return []

        data  = resp.json()
        items = []
        for art in data.get('articles', []):
            items.append({
                'title':  art.get('title', ''),
                'desc':   art.get('description', ''),
                'date':   art.get('publishedAt', ''),
                'link':   art.get('url', ''),
                'source': art.get('source', {}).get('name', 'NewsAPI'),
            })
        print(f'[NEWSAPI] {len(items)} articles')
        return items
    except Exception as e:
        print(f'[NEWSAPI] Error: {e}')
        return []

def process_breaking_news(now, state):
    """
    Main breaking news processor:
    1. Fetch dari RSS (primary) + NewsAPI (fallback)
    2. Filter pubDate — skip berita > RSS_MAX_AGE_HOURS jam
    3. Filter by keyword (whole-word aware)
    4. Dedup via hash (7 hari) + cooldown per group (4 jam)
    5. Kirim ke Telegram, max 2 per run
    """
    print('[BREAKING] Fetching breaking news...')

    # Fetch
    items = fetch_rss_breaking(now)
    if not items:
        print('[BREAKING] RSS empty, trying NewsAPI...')
        items = fetch_newsapi_breaking(now)

    if not items:
        print('[BREAKING] No items from any source')
        return

    print(f'[BREAKING] Total items to scan: {len(items)}')
    sent_count = 0

    for item in items:
        try:
            title = item.get('title', '')
            desc  = item.get('desc',  '')
            if not title:
                continue

            # Match keyword
            full_text      = f'{title} {desc}'
            group, matched = _match_keywords(full_text)
            if not group:
                continue

            # Dedup check
            h = _news_hash(title)
            if state['sent_breaking'].get(h):
                continue

            # Cooldown check per group — jangan spam satu topik
            group_key      = f'cooldown_{re.sub(r"[^a-z]","",group.lower())}'
            last_sent_str  = state['sent_breaking'].get(group_key, '')
            if last_sent_str:
                try:
                    last_sent = datetime.datetime.fromisoformat(last_sent_str)
                    hours_ago = (now - last_sent).total_seconds() / 3600
                    if hours_ago < BREAKING_COOLDOWN_HOURS:
                        print(f'[BREAKING] Cooldown active for {group} ({hours_ago:.1f}h ago)')
                        continue
                except Exception:
                    pass

            # Kirim
            msg = fmt_breaking_news(item, group, matched, now)
            r   = send_text(msg)
            if r.get('ok'):
                state['sent_breaking'][h]          = now.isoformat()
                state['sent_breaking'][group_key]  = now.isoformat()
                sent_count += 1
                print(f'[BREAKING] ✅ Sent: {title[:60]}')
                # Max 2 breaking news per run — hindari spam
                if sent_count >= 2:
                    break
            else:
                print(f'[BREAKING] ❌ Failed: {r}')

        except Exception as e:
            print(f'[BREAKING] Item error: {e}')

    # Cleanup sent_breaking:
    # - Hash berita (dedup): simpan 7 hari supaya berita sama tidak re-trigger
    # - Cooldown key: hapus kalau sudah expired (> BREAKING_COOLDOWN_HOURS)
    cutoff_7d = (now - datetime.timedelta(days=7)).isoformat()
    cutoff_cd = (now - datetime.timedelta(hours=BREAKING_COOLDOWN_HOURS)).isoformat()
    state['sent_breaking'] = {
        k: v for k, v in state['sent_breaking'].items()
        if (k.startswith('cooldown_') and v >= cutoff_cd)
        or (not k.startswith('cooldown_') and v >= cutoff_7d)
    }

    print(f'[BREAKING] Done — {sent_count} sent')

def fmt_breaking_news(item, group, matched_kw, now):
    """Format pesan breaking news ke Telegram — full detail, no truncation."""
    title  = item.get('title',  'N/A')
    desc   = item.get('desc',   '').strip()
    source = item.get('source', 'Unknown')
    link   = item.get('link',   '')

    # Bersihkan HTML tags dari desc kalau ada
    desc = re.sub(r'<[^>]+>', '', desc).strip()

    time_wib  = (now + datetime.timedelta(hours=7)).strftime('%H:%M')
    time_utc  = now.strftime('%H:%M')
    date_str  = (now + datetime.timedelta(hours=7)).strftime('%d %b %Y')

    # Tentukan dampak ke market
    market_impact = _assess_market_impact(matched_kw, group)

    desc_block = f'\n📝 <b>Detail:</b>\n{desc}\n' if desc else '\n'
    link_line  = f'\n🔗 <a href="{link}">Baca selengkapnya → {source}</a>' if link else ''

    return (
        f'🚨 <b>BREAKING NEWS</b>\n'
        f'{DIV2}\n'
        f'{group}\n'
        f'{DIV}\n'
        f'📰 <b>{title}</b>\n'
        f'{desc_block}'
        f'{DIV}\n'
        f'🕐 <b>{time_wib} WIB</b>  ({time_utc} UTC)  {date_str}\n'
        f'📡 Sumber   : {source}\n'
        f'🔍 Keyword  : <i>{matched_kw}</i>\n'
        f'{DIV}\n'
        f'📊 <b>Potensi Dampak Market:</b>\n'
        f'{market_impact}\n'
        f'{link_line}\n'
        f'{DIV2}\n'
        f'⚠️ <b>Monitor pergerakan harga dengan cermat!</b>\n'
        f'<i>Bobb Market Intelligence v1.8</i>'
    )

def _assess_market_impact(keyword, group):
    """Buat analisis singkat dampak ke market berdasarkan keyword."""
    impacts = {
        # Geopolitical
        'war':        '🔴 XAUUSD ↑ (safe haven)  |  Risk assets ↓\n   USD bisa menguat, equity sell-off',
        'attack':     '🔴 XAUUSD ↑ (safe haven)  |  Oil ↑ kemungkinan\n   Risk-off sentiment',
        'missile':    '🔴 XAUUSD ↑  |  Oil ↑  |  JPY ↑ (safe haven)\n   Equity markets volatile',
        'invasion':   '🔴 XAUUSD ↑↑  |  Energy crisis risk\n   EUR bisa melemah tergantung lokasi',
        'sanctions':  '🟡 Bergantung target negara\n   Commodity terdampak jika Russia/OPEC',
        'nuclear':    '🔴 EXTREME risk-off — XAUUSD ↑↑  |  Semua risk assets ↓↓',
        'ceasefire':  '🟢 Risk-on — Equity ↑  |  XAUUSD mungkin koreksi\n   Oil bisa turun',
        'coup':       '🔴 Currency negara terdampak ↓  |  XAUUSD ↑\n   Regional contagion risk',
        'explosion':  '🔴 Risk-off sementara  |  Monitor lokasi kejadian',
        'terrorism':  '🔴 Risk-off sementara  |  XAUUSD ↑  |  JPY ↑',
        'crisis':     '🟡 Bergantung konteks  |  Safe haven assets ↑',
        'escalation': '🔴 Risk-off  |  XAUUSD ↑  |  Oil ↑ jika Middle East',
        'military':   '🟡 Monitor perkembangan  |  Potensi risk-off',
        'troops':     '🟡 Monitor perkembangan  |  Potensi risk-off',
        # Central Bank
        'fed':            '🔴/🟢 Bergantung tone hawkish/dovish\n   USD & semua pair terdampak langsung',
        'federal reserve':'🔴/🟢 Market mover terbesar\n   XAUUSD, USD pairs, semua aset terdampak',
        'fomc':           '🔴 High impact — semua pair volatile\n   Hindari entry 30 menit sebelum & sesudah',
        'rate hike':      '🔴 USD ↑  |  XAUUSD ↓  |  Equity mixed\n   Bond yields ↑',
        'rate cut':       '🟢 USD ↓  |  XAUUSD ↑  |  Equity ↑\n   Risk-on sentiment',
        'ecb':            '🟡 EUR pairs volatile  |  EURUSD high impact',
        'boe':            '🟡 GBP pairs volatile  |  GBPUSD high impact',
        'boj':            '🟡 JPY pairs volatile  |  USDJPY high impact',
        'powell':         '🔴 High impact — semua USD pair volatile',
        'lagarde':        '🟡 EUR pairs volatile',
        'inflation target':'🟡 Monitor — implikasi ke rate decision',
        'monetary policy':'🟡 Currency terdampak sesuai bank sentral',
        'quantitative':   '🟡 Liquidity impact — equity & bond terdampak',
        # Market Moving
        'crash':       '🔴 EXTREME — semua aset volatile\n   XAUUSD ↑↑  |  BTC bisa turun atau naik',
        'collapse':    '🔴 Risk-off ekstrem  |  Safe haven ↑↑',
        'default':     '🔴 Currency negara terdampak ↓↓  |  Contagion risk',
        'recession':   '🔴 Risk-off  |  XAUUSD ↑  |  Commodity ↓\n   Safe haven currencies ↑',
        'bank failure':'🔴 Sector contagion risk  |  XAUUSD ↑\n   Monitor bank-related currency',
        'bailout':     '🟡 Short-term relief  |  Inflation concern jangka panjang',
        'downgrade':   '🔴 Currency negara terdampak ↓  |  Bond yields ↑',
        'emergency':   '🔴 Risk-off  |  Monitor konteks',
        # Gold & Oil
        'gold':   '🟡 XAUUSD langsung terdampak\n   Monitor level support/resistance key',
        'xauusd': '🟡 Direct impact  |  Watch technicals',
        'oil':    '🟡 CAD ↑/↓  |  NOK terdampak  |  Inflation concern',
        'crude':  '🟡 Energy sector & CAD terdampak',
        'opec':   '🟡 Oil price direct impact  |  CAD, energy stocks',
        # Crypto
        'bitcoin': '🟡 BTC/BTCUSDT direct impact\n   Crypto market sentiment terdampak',
        'btc':     '🟡 BTCUSDT direct impact',
        'sec':     '🟡 Crypto regulation risk  |  BTC volatile',
        'etf approval': '🟢 BTC ↑↑  |  Crypto risk-on',
        'exchange hack':'🔴 BTC ↓  |  Crypto panic sell risk',
        # Macro
        'gdp':         '🟡 Currency negara terdampak  |  Risk sentiment',
        'nonfarm':     '🔴 USD pairs volatile  |  XAUUSD terdampak\n   Major market mover',
        'cpi':         '🔴 Inflation data — USD & rate expectation\n   XAUUSD, semua USD pair terdampak',
        'trade war':   '🔴 Risk-off  |  CNY/AUD terdampak  |  Gold ↑',
        'tariff':      '🟡 Currency pair terdampak sesuai negara',
        'treasury':    '🟡 USD & bond market terdampak',
        'yield curve': '🟡 Rate expectation  |  USD & equity terdampak',
    }

    # Cari match di dict
    kw_lower = keyword.lower()
    for kw, impact in impacts.items():
        if kw in kw_lower or kw_lower in kw:
            # Add hawkish/dovish context
            if 'rate hike' in kw_lower or 'hike' in kw_lower:
                impact += '\n   🔴 HAWKISH — USD ↑, Gold ↓ short-term'
            elif 'rate cut' in kw_lower or 'cut' in kw_lower:
                impact += '\n   🟢 DOVISH — USD ↓, Gold ↑, Equity ↑'
            return impact

    # Default berdasarkan group
    group_defaults = {
        '⚔️ Geopolitical': '🔴 Risk-off kemungkinan  |  XAUUSD & JPY ↑',
        '🏦 Central Bank':  '🔴 USD pairs & XAUUSD volatile',
        '📈 Market Moving': '🔴 Semua aset volatile — waspada',
        '🥇 Gold & Oil':    '🟡 XAUUSD & energy terdampak',
        '₿ Crypto':         '🟡 BTCUSDT & crypto volatile',
        '🌍 Macro':         '🟡 Currency & commodity terdampak',
    }
    return group_defaults.get(group, '🟡 Monitor pergerakan harga')


# ════════════════════════════════════════
# PRICE SPIKE DETECTOR
# ════════════════════════════════════════
def _fetch_btc_price():
    """
    Fetch BTCUSDT harga sekarang dan 5 menit lalu via Binance public API.
    Return: (price_now, price_5m_ago) atau (None, None) kalau gagal.
    """
    try:
        # Kline endpoint — 1m candle, ambil 6 candle terakhir
        url = 'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=6'
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            print(f'[SPIKE] Binance HTTP {resp.status_code}')
            return None, None
        klines = resp.json()
        if len(klines) < 2:
            return None, None
        # klines[-1] = candle terbaru, index [4] = close price
        price_now   = float(klines[-1][4])
        price_5m    = float(klines[0][4])   # 5-6 menit lalu
        return price_now, price_5m
    except Exception as e:
        print(f'[SPIKE] BTC fetch error: {e}')
        return None, None


def _fetch_yahoo_price(ticker):
    """
    Fetch harga sekarang dan ~5 menit lalu via Yahoo Finance chart API.
    Return: (price_now, price_5m_ago) atau (None, None) kalau gagal.
    """
    try:
        # Yahoo Finance v8 chart endpoint — interval 1m, range 30m
        url = (
            f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}'
            f'?interval=1m&range=30m'
        )
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            print(f'[SPIKE] Yahoo {ticker} HTTP {resp.status_code}')
            return None, None

        data   = resp.json()
        result = data.get('chart', {}).get('result', [])
        if not result:
            return None, None

        closes = result[0].get('indicators', {}).get('quote', [{}])[0].get('close', [])
        # Filter None values
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None, None

        price_now = closes[-1]
        price_5m  = closes[-6] if len(closes) >= 6 else closes[0]
        return price_now, price_5m
    except Exception as e:
        print(f'[SPIKE] Yahoo {ticker} error: {e}')
        return None, None



# ════════════════════════════════════════
# ADAPTIVE SPIKE DETECTION (v2.0)
# Threshold berdasarkan volatility regime:
# - Hitung rolling range 5 candle 1-menit
# - Spike = pergerakan > 2× average range
# - Mencegah false positive saat volatile, false negative saat calm
# ════════════════════════════════════════

SPIKE_VOLATILITY_MULTIPLIER = 2.0   # spike harus > 2x average 5-candle range
SPIKE_ABSOLUTE_MIN = {               # minimum absolute threshold (fallback)
    'XAUUSD': 0.15, 'BTCUSDT': 0.50, 'ETHUSDT': 0.50,
    'EURUSD': 0.08, 'GBPUSD': 0.08, 'USDJPY': 0.08,
    'AUDUSD': 0.08, 'USDCAD': 0.08, 'USDCHF': 0.08,
    'NZDUSD': 0.08, 'EURJPY': 0.12, 'GBPJPY': 0.12,
}

def _calc_adaptive_spike(price_now: float, price_5m: float,
                          price_history: list, pair: str) -> dict:
    """
    Adaptive spike detection:
    - pct_change = (now - 5m ago) / 5m ago * 100
    - avg_range = mean of last 5 1-minute price ranges (proxy ATR)
    - is_spike = pct_change > max(absolute_min, avg_range * multiplier)
    Returns dict: {pct_change, is_spike, threshold_used, regime}
    """
    if not price_5m or price_5m == 0:
        return {'pct_change': 0, 'is_spike': False, 'threshold_used': 0, 'regime': 'unknown'}

    pct_change = ((price_now - price_5m) / price_5m) * 100

    # Calculate adaptive threshold from recent 1-min candle ranges
    abs_min = SPIKE_ABSOLUTE_MIN.get(pair, 0.10)
    if len(price_history) >= 5:
        ranges = [abs(price_history[i] - price_history[i-1]) / price_history[i-1] * 100
                  for i in range(1, min(6, len(price_history)))]
        avg_range = sum(ranges) / len(ranges) if ranges else abs_min
        adaptive_thresh = max(abs_min, avg_range * SPIKE_VOLATILITY_MULTIPLIER)
    else:
        adaptive_thresh = abs_min

    # Volatility regime
    if adaptive_thresh > abs_min * 2:
        regime = 'HIGH_VOL'
    elif adaptive_thresh > abs_min * 1.3:
        regime = 'ELEVATED'
    else:
        regime = 'NORMAL'

    is_spike = abs(pct_change) >= adaptive_thresh
    return {
        'pct_change': round(pct_change, 3),
        'is_spike': is_spike,
        'threshold_used': round(adaptive_thresh, 3),
        'regime': regime,
    }


# ════════════════════════════════════════
# LIVE PRICE FOR SPIKE DETECTOR (v2.1)
# Priority: Binance (crypto) → TwelveData (metals/forex) → Yahoo fallback
# TwelveData sudah ada API key di env (same key as signal_engine)
# ════════════════════════════════════════
TWELVEDATA_API_KEY_SPIKE = os.environ.get('TWELVEDATA_API_KEY', '')
TWELVEDATA_SYMBOL_MAP_SPIKE = {
    'XAUUSD': 'XAU/USD', 'XAGUSD': 'XAG/USD',
    'EURUSD': 'EUR/USD', 'GBPUSD': 'GBP/USD', 'USDJPY': 'USD/JPY',
    'USDCHF': 'USD/CHF', 'AUDUSD': 'AUD/USD', 'NZDUSD': 'NZD/USD',
    'USDCAD': 'USD/CAD',
}
BINANCE_PAIRS_SPIKE = {'BTCUSDT', 'ETHUSDT'}

def _fetch_live_price(pair: str) -> float:
    """
    Fetch live price with priority:
    1. Binance REST (crypto, no key, <100ms, real-time)
    2. TwelveData (metals/forex, API key, near-real-time)
    3. Yahoo Finance fallback (free, ~15-20min delay — last resort)
    """
    pair = pair.upper()
    # 1. Binance
    if pair in BINANCE_PAIRS_SPIKE:
        try:
            r = requests.get(
                'https://api.binance.com/api/v3/ticker/price',
                params={'symbol': pair}, timeout=5)
            return float(r.json()['price'])
        except Exception as e:
            print(f'[PRICE] Binance error {pair}: {e}')

    # 2. TwelveData
    td_sym = TWELVEDATA_SYMBOL_MAP_SPIKE.get(pair)
    if td_sym and TWELVEDATA_API_KEY_SPIKE:
        try:
            r = requests.get(
                'https://api.twelvedata.com/price',
                params={'symbol': td_sym, 'apikey': TWELVEDATA_API_KEY_SPIKE},
                timeout=8)
            data = r.json()
            if 'price' in data:
                return float(data['price'])
        except Exception as e:
            print(f'[PRICE] TwelveData error {pair}: {e}')

    # 3. Yahoo Finance fallback
    yahoo_map = {
        'XAUUSD': 'GC=F', 'XAGUSD': 'SI=F',
        'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X', 'USDJPY': 'JPY=X',
        'USDCHF': 'CHF=X', 'AUDUSD': 'AUDUSD=X', 'NZDUSD': 'NZDUSD=X',
        'USDCAD': 'CAD=X',
    }
    ticker = yahoo_map.get(pair)
    if ticker:
        try:
            r = requests.get(
                f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}',
                params={'interval': '1m', 'range': '1d'},
                headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
            closes = r.json()['chart']['result'][0]['indicators']['quote'][0]['close']
            closes = [c for c in closes if c is not None]
            if closes:
                print(f'[PRICE] Yahoo fallback used for {pair} (may be delayed)')
                return float(closes[-1])
        except Exception as e:
            print(f'[PRICE] Yahoo error {pair}: {e}')

    return None

def _calc_spike(price_now, price_5m):
    """Hitung % perubahan. Return float atau None."""
    try:
        if price_5m == 0:
            return None
        return ((price_now - price_5m) / price_5m) * 100
    except Exception:
        return None


def fmt_spike_alert(pair, price_now, price_5m, pct_change):
    """Format pesan spike alert ke Telegram."""
    direction  = '📈 BULLISH SPIKE' if pct_change > 0 else '📉 BEARISH SPIKE'
    arrow      = '↑' if pct_change > 0 else '↓'
    abs_pct    = abs(pct_change)
    now        = datetime.datetime.utcnow()
    time_wib   = (now + datetime.timedelta(hours=7)).strftime('%H:%M')
    time_utc   = now.strftime('%H:%M')
    date_str   = (now + datetime.timedelta(hours=7)).strftime('%d %b %Y')

    # Format harga sesuai pair
    if pair in ('BTCUSDT',):
        fmt_price = lambda p: f'${p:,.2f}'
    elif pair == 'XAUUSD':
        fmt_price = lambda p: f'${p:,.2f}'
    elif 'JPY' in pair:
        fmt_price = lambda p: f'{p:.3f}'
    else:
        fmt_price = lambda p: f'{p:.5f}'

    abs_move = abs(price_now - price_5m)
    move_str = fmt_price(abs_move) if pair not in ('EURUSD','GBPUSD','AUDUSD','USDCAD','USDCHF','NZDUSD','EURJPY','GBPJPY') \
               else f'{abs_move:.5f}'

    # Post-news "sell the fact" pattern detection
    post_news_pattern = ''
    if abs(pct_change) >= 0.5:
        post_news_pattern = '\n⚠️ Watch for <b>sell-the-fact</b> reversal setelah spike pertama'

    return (
        f'⚡ <b>PRICE SPIKE ALERT</b>\n'
        f'{DIV2}\n'
        f'{direction}\n'
        f'{DIV}\n'
        f'💱 <b>{pair}</b>  {arrow} <b>{abs_pct:.2f}%</b> dalam 5 menit\n'
        f'{DIV}\n'
        f'💰 Harga Sekarang : <b>{fmt_price(price_now)}</b>\n'
        f'📌 5 Menit Lalu   : {fmt_price(price_5m)}\n'
        f'📊 Pergerakan     : {arrow} {move_str}\n'
        f'{DIV}\n'
        f'🕐 <b>{time_wib} WIB</b>  ({time_utc} UTC)  {date_str}\n'
        f'{DIV}\n'
        f'⚠️ <b>Cek chart & konfirmasi sebelum entry!</b>'
        f'{post_news_pattern}\n'
        f'<i>Bobb Market Intelligence v2.0</i>'
    )


def process_price_spikes(now, state):
    """
    Cek semua pair untuk spike > threshold dalam 5 menit.
    Kirim alert ke Telegram dengan cooldown 30 menit per pair.
    """
    print('[SPIKE] Checking price spikes...')

    if 'sent_spike' not in state:
        state['sent_spike'] = {}

    spike_sent = 0

    for pair, threshold in SPIKE_THRESHOLDS.items():
        try:
            # Cooldown check
            spike_key  = f'spike_{pair}'
            last_str   = state['sent_spike'].get(spike_key, '')
            if last_str:
                try:
                    last_dt   = datetime.datetime.fromisoformat(last_str)
                    mins_ago  = (now - last_dt).total_seconds() / 60
                    if mins_ago < SPIKE_COOLDOWN_MINUTES:
                        print(f'[SPIKE] {pair}: cooldown ({mins_ago:.0f}m ago)')
                        continue
                except Exception:
                    pass

            # Fetch harga
            if pair == 'BTCUSDT':
                price_now, price_5m = _fetch_btc_price()
            else:
                ticker              = YAHOO_TICKERS.get(pair)
                if not ticker:
                    continue
                price_now, price_5m = _fetch_yahoo_price(ticker)

            if price_now is None or price_5m is None:
                print(f'[SPIKE] {pair}: no data')
                continue

            pct = _calc_spike(price_now, price_5m)
            if pct is None:
                continue

            print(f'[SPIKE] {pair}: {pct:+.3f}% (threshold ±{threshold}%)')

            if abs(pct) >= threshold:
                msg = fmt_spike_alert(pair, price_now, price_5m, pct)
                r   = send_text(msg)
                if r.get('ok'):
                    state['sent_spike'][spike_key] = now.isoformat()
                    spike_sent += 1
                    print(f'[SPIKE] ✅ Alert sent: {pair} {pct:+.2f}%')
                else:
                    print(f'[SPIKE] ❌ Send failed: {r}')

        except Exception as e:
            print(f'[SPIKE] {pair} error: {e}')

    # Cleanup spike state > 2 jam
    cutoff_2h = (now - datetime.timedelta(hours=2)).isoformat()
    state['sent_spike'] = {
        k: v for k, v in state['sent_spike'].items()
        if v >= cutoff_2h
    }

    print(f'[SPIKE] Done — {spike_sent} alerts sent')


# ════════════════════════════════════════
def run_news_detector():
    now   = datetime.datetime.utcnow()
    state = load_state()

    print(f'=== BOBB MARKET INTELLIGENCE v1.8 ===')
    print(f'Time UTC : {now.strftime("%Y-%m-%d %H:%M")}')
    print(f'Time WIB : {(now + datetime.timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")}')

    # ── Fetch events — SEKALI saja, dipakai semua mode ──────────────────
    events, source = fetch_events(now.date())
    print(f'[FETCH] Source used: {source}')

    # ── MODE 1: Daily Briefing — 07:00 WIB = 00:00 UTC ──────────────
    is_briefing = (now.hour == 0 and now.minute < 15)
    date_key    = now.strftime('%Y-%m-%d')

    if is_briefing and not state['sent_daily'].get(date_key, False):
        print('[DAILY] Sending morning briefing...')
        if source == 'None':
            msg = fmt_all_sources_failed(now)
        else:
            msg = fmt_daily_briefing(events, now, source)
        r = send_text(msg)
        if r.get('ok'):
            state['sent_daily'][date_key] = True
            print('[DAILY] ✅ Sent')
        else:
            print(f'[DAILY] ❌ {r}')

    # ── MODE 2: Reminder 30 menit sebelum ───────────────────────────
    for event in events:
        mins_until   = (event['dt_utc'] - now).total_seconds() / 60
        reminder_key = f'reminder_{event["id"]}'
        if 25 <= mins_until <= 35 and not state['sent_reminder'].get(reminder_key, False):
            print(f'[REMINDER] {event["currency"]} {event["title"]} ~{int(mins_until)}m')
            r = send_text(fmt_reminder(event, 30))
            if r.get('ok'):
                state['sent_reminder'][reminder_key] = now.isoformat()
                print('[REMINDER] ✅ Sent')

    # ── MODE 3: Actual Result setelah rilis ─────────────────────────
    # Gunakan events yang sama — tidak perlu re-fetch
    for event in events:
        mins_past  = (now - event['dt_utc']).total_seconds() / 60
        actual_key = f'actual_{event["id"]}'
        if 5 <= mins_past <= 25 and event['actual'] and not state['sent_actual'].get(actual_key, False):
            print(f'[ACTUAL] {event["currency"]} {event["title"]} → {event["actual"]}')
            r = send_text(fmt_actual_result(event))
            if r.get('ok'):
                state['sent_actual'][actual_key] = now.isoformat()
                print('[ACTUAL] ✅ Sent')

    # ── MODE 4: Breaking News ────────────────────────────────────────
    process_breaking_news(now, state)

    # ── MODE 5: Price Spike Detector ────────────────────────────────
    process_price_spikes(now, state)

    # ── Cleanup state ────────────────────────────────────────────────
    # sent_daily: simpan 7 hari
    cutoff_7d = (now - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
    state['sent_daily'] = {k: v for k, v in state['sent_daily'].items() if k >= cutoff_7d}

    # sent_reminder & sent_actual: simpan 2 hari — event ID mengandung tanggal implisit
    # key format: "reminder_USD_CPI_1230" — cukup 2 hari untuk safety margin
    cutoff_2d = (now - datetime.timedelta(days=2)).isoformat()
    # Simpan kalau timestamp value-nya masih dalam 2 hari (nilai True = old format, hapus saja)
    state['sent_reminder'] = {
        k: v for k, v in state['sent_reminder'].items()
        if v is not True  # hapus format lama (boolean True)
        and isinstance(v, str) and v >= cutoff_2d
    }
    state['sent_actual'] = {
        k: v for k, v in state['sent_actual'].items()
        if v is not True
        and isinstance(v, str) and v >= cutoff_2d
    }

    save_state(state)
    print('=== DONE ===')

if __name__ == '__main__':
    run_news_detector()

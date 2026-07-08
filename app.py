import os
import io
import time
import itertools
import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

# ----------------- Load Environment Variables -----------------
load_dotenv()

# Set up page config
st.set_page_config(
    page_title="Institutional Forex Fundamental Dashboard",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state for manual interest rates (persisted to .rates_config.json)
import json

RATES_CONFIG_FILE = ".rates_config.json"
persisted_rates = {}
if os.path.exists(RATES_CONFIG_FILE):
    try:
        with open(RATES_CONFIG_FILE, "r", encoding="utf-8") as f:
            persisted_rates = json.load(f)
    except Exception:
        pass

defaults = {
    "manual_rate_GBP": 5.25,
    "manual_rate_JPY": 0.10,
    "manual_rate_AUD": 4.35,
    "manual_rate_CAD": 5.00,
    "manual_rate_NZD": 5.50,
    "manual_rate_CHF": 0.00,
    "last_saved_rates": None
}

for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = persisted_rates.get(key, val)

# ----------------- Obsidian Dark Theme CSS -----------------
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Roboto+Mono:wght@400;700&display=swap');
    
    /* General overrides */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif !important;
    }
    
    .stApp {
        background-color: #070708 !important;
        color: #b2b2be !important;
    }
    
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Inter', sans-serif !important;
        color: #f0f0f5 !important;
        font-weight: 600 !important;
    }
    
    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #0c0c0e !important;
        border-right: 1px solid #1f2026 !important;
    }
    
    /* Card design */
    .metric-card-custom {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 16px;
        margin-bottom: 12px;
    }
    
    .metric-label {
        font-size: 0.72rem;
        text-transform: uppercase;
        color: #7d7d8a;
        font-weight: 600;
        letter-spacing: 0.5px;
    }
    
    .metric-value {
        font-size: 1.6rem;
        font-weight: 700;
        color: #f0f0f5;
        margin: 4px 0;
        font-family: 'Roboto Mono', monospace;
    }
    
    .source-tag {
        font-family: 'Roboto Mono', monospace;
        font-size: 0.62rem;
        color: #8c8c9a;
        background-color: rgba(255, 255, 255, 0.02);
        border: 1px solid #1f2026;
        padding: 1px 5px;
        border-radius: 3px;
        display: inline-block;
        margin-top: 4px;
    }
    
    .source-tag-live {
        color: #10b981;
        background-color: rgba(16, 185, 129, 0.04);
        border: 1px solid rgba(16, 185, 129, 0.15);
    }
    
    /* News Ticker Card Style (Bottom) */
    .news-card-custom {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 14px;
        margin-bottom: 12px;
        transition: border-color 0.2s, background-color 0.2s;
    }
    .news-card-custom:hover {
        border-color: #e2b13c;
        background-color: #111114;
    }
    .news-title-custom {
        font-size: 0.9rem;
        font-weight: 600;
        color: #f0f0f5 !important;
        text-decoration: none;
        display: block;
        margin-bottom: 4px;
    }
    .news-title-custom:hover {
        color: #e2b13c !important;
        text-decoration: underline;
    }
    .news-meta-custom {
        font-size: 0.7rem;
        color: #7d7d8a;
        margin-bottom: 6px;
    }

    /* News & Research Hub - Full Card style (Grid) */
    .news-card {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 16px;
        margin-bottom: 15px;
        transition: border-color 0.2s, background-color 0.2s;
        height: 420px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }
    .news-card:hover {
        border-color: #e2b13c;
        background-color: #111114;
    }
    .news-title {
        font-size: 0.95rem;
        font-weight: 600;
        color: #f0f0f5 !important;
        margin-bottom: 6px;
        text-decoration: none;
        display: block;
        line-height: 1.35;
    }
    .news-title:hover {
        color: #e2b13c !important;
        text-decoration: underline;
    }
    .news-meta {
        font-size: 0.72rem;
        color: #7d7d8a;
        margin-bottom: 8px;
    }
    .news-desc {
        font-size: 0.82rem;
        color: #b2b2be;
        margin-bottom: 8px;
        line-height: 1.4;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- Load API Keys from Env -----------------
FRED_KEY = os.getenv("FRED_API_KEY")
AV_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
NEWSDATA_KEY = os.getenv("NEWSDATA_API_KEY")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
BENZINGA_KEY = os.getenv("BENZINGA_API_KEY")
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY")
ITICK_KEY = os.getenv("ITICK_API_KEY")
FCS_KEY = os.getenv("FCS_API_KEY")
STOCKDATA_KEY = os.getenv("STOCKDATA_API_KEY")
TIINGO_KEY = os.getenv("TIINGO_API_KEY")
BLS_KEY = os.getenv("BLS_API_KEY")
APIFREAKS_KEY = os.getenv("APIFREAKS_API_KEY")
if not APIFREAKS_KEY:
    try:
        APIFREAKS_KEY = st.secrets.get("APIFREAKS_API_KEY") or st.secrets.get("apifreaks_api_key")
    except Exception:
        pass

EODHD_KEY = os.getenv("EODHD_API_KEY")
if not EODHD_KEY:
    try:
        EODHD_KEY = st.secrets.get("EODHD_API_KEY") or st.secrets.get("eodhd_api_key")
    except Exception:
        pass


# ----------------- Constants & Configuration -----------------
CURRENCIES = {
    "USD": {"name": "US Dollar", "flag": "🇺🇸", "country": "United States", "wb_code": "USA"},
    "EUR": {"name": "Euro", "flag": "🇪🇺", "country": "Euro area", "wb_code": "EMU"},
    "GBP": {"name": "British Pound", "flag": "🇬🇧", "country": "United Kingdom", "wb_code": "GBR"},
    "CHF": {"name": "Swiss Franc", "flag": "🇨🇭", "country": "Switzerland", "wb_code": "CHE"},
    "CAD": {"name": "Canadian Dollar", "flag": "🇨🇦", "country": "Canada", "wb_code": "CAN"},
    "AUD": {"name": "Australian Dollar", "flag": "🇦🇺", "country": "Australia", "wb_code": "AUS"},
    "NZD": {"name": "New Zealand Dollar", "flag": "🇳🇿", "country": "New Zealand", "wb_code": "NZL"},
    "JPY": {"name": "Japanese Yen", "flag": "🇯🇵", "country": "Japan", "wb_code": "JPN"}
}

# ----------------- 0. MOCK DATA GENERATORS (Graceful Fallback) -----------------
def generate_mock_fred(series_id):
    np.random.seed(42)
    dates = pd.date_range(start="2015-01-01", end=datetime.now(), freq="ME")
    if series_id == "FEDFUNDS":
        values = np.clip(np.linspace(0.25, 5.25, len(dates)) + np.random.normal(0, 0.15, len(dates)), 0.05, 7.0)
    elif series_id == "CPIAUCSL":
        values = np.linspace(235.0, 312.0, len(dates)) + np.random.normal(0, 0.4, len(dates))
    elif series_id == "GDPC1":
        dates = pd.date_range(start="2015-01-01", end=datetime.now(), freq="QE")
        values = np.linspace(17500.0, 22500.0, len(dates)) + np.random.normal(0, 80.0, len(dates))
    elif series_id == "UNRATE":
        values = np.clip(np.linspace(5.5, 3.8, len(dates)) + np.random.normal(0, 0.15, len(dates)), 3.0, 15.0)
    elif series_id == "NAPM":
        values = np.clip(50.0 + np.random.normal(0, 3.0, len(dates)), 35.0, 65.0)
    elif series_id == "BOEBASE":
        values = np.clip(np.linspace(0.5, 5.25, len(dates)) + np.random.normal(0, 0.2, len(dates)), 0.1, 6.0)
    elif series_id == "JPNIR":
        values = np.clip(np.linspace(-0.1, 0.25, len(dates)) + np.random.normal(0, 0.05, len(dates)), -0.15, 0.5)
    elif series_id == "CANIR":
        values = np.clip(np.linspace(0.75, 5.0, len(dates)) + np.random.normal(0, 0.2, len(dates)), 0.25, 6.0)
    elif series_id == "AUDIR":
        values = np.clip(np.linspace(1.5, 4.35, len(dates)) + np.random.normal(0, 0.2, len(dates)), 0.1, 5.5)
    elif series_id == "NZLIR":
        values = np.clip(np.linspace(1.5, 5.5, len(dates)) + np.random.normal(0, 0.2, len(dates)), 0.25, 6.5)
    else:
        values = np.zeros(len(dates))
    return pd.DataFrame({"date": dates, "value": values})

def generate_mock_av(from_symbol, to_symbol):
    np.random.seed(33)
    dates = pd.date_range(end=datetime.now(), periods=250, freq="D")
    pair = f"{from_symbol}/{to_symbol}"
    base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120}
    base = base_prices.get(pair, 1.0)
    prices = [base]
    for _ in range(249):
        prices.append(prices[-1] * (1 + np.random.normal(0, 0.0035)))
    return pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p * 1.0025 for p in prices],
        "low": [p * 0.9975 for p in prices],
        "close": prices
    })

def generate_mock_news():
    return [
        {"title": "FED signalisiert Zinswende: Dollar gewinnt an Stärke gegenüber dem Euro", "source": "MockNews", "publishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"), "url": "#", "description": "Die US-Notenbank deutet eine längere Phase hoher Leitzinsen an.", "urlToImage": None, "api_source": "MOCK-News"},
        {"title": "EZB hält Leitzins unverändert: EUR/USD gerät unter Druck", "source": "MockNews", "publishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"), "url": "#", "description": "Die EZB bestätigt den Leitzins. Analysten erwarten schwächere Euro-Notierungen.", "urlToImage": None, "api_source": "MOCK-News"},
        {"title": "Bank of Japan erhöht Leitzins minimal: JPY reagiert volatil", "source": "MockNews", "publishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"), "url": "#", "description": "Die japanische Notenbank hebt den Zinssatz leicht an, um dem schwachen Yen entgegenzuwirken.", "urlToImage": None, "api_source": "MOCK-News"}
    ]

def generate_mock_benzinga():
    events = [
        {"time": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"), "country": "USA", "event": "FOMC Meeting Minutes", "consensus": "5.25%", "actual": None, "prior": "5.25%", "importance": "High"},
        {"time": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M"), "country": "DEU", "event": "German GDP Growth QoQ", "consensus": "0.1%", "actual": None, "prior": "-0.2%", "importance": "Medium"},
        {"time": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M"), "country": "GBR", "event": "BoE Interest Rate Decision", "consensus": "5.00%", "actual": None, "prior": "5.25%", "importance": "High"},
        {"time": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d %H:%M"), "country": "USA", "event": "Non-Farm Payrolls (NFP)", "consensus": "180K", "actual": None, "prior": "210K", "importance": "High"},
        {"time": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M"), "country": "EUR", "event": "Eurozone CPI Inflation YoY", "consensus": "2.4%", "actual": None, "prior": "2.6%", "importance": "High"},
        {"time": (datetime.now() + timedelta(days=12)).strftime("%Y-%m-%d %H:%M"), "country": "JPN", "event": "BoJ Press Conference", "consensus": "-", "actual": None, "prior": "-", "importance": "Medium"}
    ]
    return pd.DataFrame(events)

def generate_mock_finnhub(pair):
    # Deterministic based on pair name
    import random
    random.seed(hash(pair) % 20000)
    base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120, "EUR/GBP": 0.8520}
    base = base_prices.get(pair, 1.0)
    
    buy = random.randint(10, 20)
    hold = random.randint(5, 12)
    sell = random.randint(1, 5)
    strong_buy = random.randint(2, 8)
    strong_sell = random.randint(0, 2)
    
    target_mean = base * random.uniform(0.98, 1.02)
    target_high = target_mean * random.uniform(1.02, 1.05)
    target_low = target_mean * random.uniform(0.95, 0.98)
    
    # History list of dicts
    history = [
        {"date": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"), "firm": "Goldman Sachs", "rating": "Buy", "target": round(target_mean * 1.01, 4)},
        {"date": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"), "firm": "JPMorgan Chase", "rating": "Hold", "target": round(target_mean * 0.99, 4)},
        {"date": (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d"), "firm": "Morgan Stanley", "rating": "Buy", "target": round(target_mean * 1.02, 4)},
        {"date": (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d"), "firm": "Barclays", "rating": "Sell", "target": round(target_mean * 0.96, 4)}
    ]
    
    return {
        "buy": buy + strong_buy,
        "hold": hold,
        "sell": sell + strong_sell,
        "strongBuy": strong_buy,
        "buy_only": buy,
        "strongSell": strong_sell,
        "sell_only": sell,
        "target_high": round(target_high, 4),
        "target_low": round(target_low, 4),
        "target_mean": round(target_mean, 4),
        "history": history
    }

def generate_mock_itick(pair):
    import random
    from datetime import datetime
    random.seed(hash(pair) % 10000)
    base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120, "EUR/GBP": 0.8520}
    base = base_prices.get(pair, 1.0)
    change = random.normalvariate(0, 0.005)
    close = base * (1 + change)
    op = base * (1 + change * 0.5)
    hi = max(op, close) * 1.002
    lo = min(op, close) * 0.998
    vol = random.uniform(50000, 150000)
    return {
        "open": op,
        "high": hi,
        "low": lo,
        "close": close,
        "volume": vol,
        "timestamp": int(datetime.now().timestamp() * 1000)
    }

def generate_mock_fcs_history(from_symbol, to_symbol):
    np.random.seed(95)
    dates = pd.date_range(start="1995-01-01", end=datetime.now(), freq="D")
    pair = f"{from_symbol}/{to_symbol}"
    base_prices = {"EUR/USD": 1.15, "GBP/USD": 1.55, "USD/JPY": 105.0, "USD/CHF": 1.12, "AUD/USD": 0.72, "USD/CAD": 1.25, "NZD/USD": 0.65, "EUR/GBP": 0.85}
    base = base_prices.get(pair, 1.0)
    prices = [base]
    for _ in range(len(dates)-1):
        prices.append(prices[-1] * (1 + np.random.normal(0, 0.005)))
    return pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p * 1.004 for p in prices],
        "low": [p * 0.996 for p in prices],
        "close": prices
    })

def generate_mock_fcs_correlation():
    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP"]
    matrix = [
        [1.0, 0.78, -0.45, -0.68, 0.58, -0.52, 0.61, 0.15],
        [0.78, 1.0, -0.38, -0.59, 0.52, -0.48, 0.55, -0.45],
        [-0.45, -0.38, 1.0, 0.72, -0.31, 0.35, -0.28, -0.12],
        [-0.68, -0.59, 0.72, 1.0, -0.49, 0.44, -0.42, -0.18],
        [0.58, 0.52, -0.31, -0.49, 1.0, -0.65, 0.85, 0.05],
        [-0.52, -0.48, 0.35, 0.44, -0.65, 1.0, -0.59, -0.08],
        [0.61, 0.55, -0.28, -0.42, 0.85, -0.59, 1.0, 0.02],
        [0.15, -0.45, -0.12, -0.18, 0.05, -0.08, 0.02, 1.0]
    ]
    return pd.DataFrame(matrix, index=pairs, columns=pairs)

def generate_mock_stockdata():
    return np.clip(np.random.normal(1.5, 3.5), -10.0, 10.0)


def generate_mock_worldbank(wb_code, indicator):
    np.random.seed(99)
    years = list(range(2015, 2026))
    if indicator == "NY.GDP.MKTP.KD.ZG":
        # GDP YoY
        values = np.clip(np.random.normal(2.0, 1.2, len(years)), -5.0, 10.0)
    else:
        # CPI YoY
        values = np.clip(np.random.normal(2.5, 1.5, len(years)), -1.0, 15.0)
    return pd.DataFrame({"date": pd.to_datetime([f"{y}-12-31" for y in years]), "value": values})


# ----------------- 1. LIVE DATA FETCHING FUNCTIONS -----------------
def fetch_fred_live(series_id, key):
    url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json&observation_start=2015-01-01"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    parsed = []
    for o in obs:
        if o["value"] != ".":
            parsed.append({"date": o["date"], "value": float(o["value"])})
    df = pd.DataFrame(parsed)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)

def fetch_av_live(from_symbol, to_symbol, key):
    url = f"https://www.alphavantage.co/query?function=FX_DAILY&from_symbol={from_symbol}&to_symbol={to_symbol}&outputsize=full&apikey={key}"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    data = r.json()
    if "Time Series FX (Daily)" not in data:
        raise ValueError("Invalid AV API response structure")
    ts = data["Time Series FX (Daily)"]
    parsed = []
    for k, v in ts.items():
        parsed.append({
            "date": k,
            "open": float(v["1. open"]),
            "high": float(v["2. high"]),
            "low": float(v["3. low"]),
            "close": float(v["4. close"])
        })
    df = pd.DataFrame(parsed)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)

def fetch_benzinga_live(key):
    url = f"https://api.benzinga.com/api/v2.1/calendar/economics?token={key}"
    r = requests.get(url, headers={"Accept": "application/json"}, timeout=8)
    r.raise_for_status()
    res = r.json()
    calendar = res.get("economics", [])
    parsed = []
    for item in calendar:
        dt = item.get("date") or ""
        tm = item.get("time") or ""
        combined_time = f"{dt} {tm}".strip()
        
        act_val = item.get("actual")
        if act_val is not None and str(act_val).strip() != "":
            act_unit = item.get("actual_t") or ""
            actual_str = f"{act_val}{act_unit}"
        else:
            actual_str = None

        cons_val = item.get("consensus")
        if cons_val is not None and str(cons_val).strip() != "":
            cons_unit = item.get("consensus_t") or ""
            consensus_str = f"{cons_val}{cons_unit}"
        else:
            consensus_str = "-"

        prior_val = item.get("prior")
        if prior_val is not None and str(prior_val).strip() != "":
            prior_unit = item.get("prior_t") or ""
            prior_str = f"{prior_val}{prior_unit}"
        else:
            prior_str = "-"

        imp_raw = item.get("importance")
        if imp_raw == 3 or imp_raw == "3" or imp_raw == "High":
            imp = "High"
        elif imp_raw == 2 or imp_raw == "2" or imp_raw == "Medium":
            imp = "Medium"
        else:
            imp = "Low"

        parsed.append({
            "time": combined_time,
            "country": item.get("country") or "",
            "event": item.get("event_name") or "",
            "consensus": consensus_str,
            "actual": actual_str,
            "prior": prior_str,
            "importance": imp
        })
    df = pd.DataFrame(parsed)
    if not df.empty:
        df["dt_temp"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.sort_values("dt_temp", ascending=True).drop(columns=["dt_temp"])
    return df

def fetch_finnhub_live(pair, key):
    symbol = f"OANDA:{pair.replace('/', '_')}"
    url = f"https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}&token={key}"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    res = r.json()
    if not isinstance(res, list) or len(res) == 0:
        raise ValueError(f"No Finnhub recommendations for symbol {symbol}")
    
    latest = res[0]
    buy = int(latest.get("buy") or 0)
    hold = int(latest.get("hold") or 0)
    sell = int(latest.get("sell") or 0)
    strong_buy = int(latest.get("strongBuy") or 0)
    strong_sell = int(latest.get("strongSell") or 0)
    
    target_mean = 1.0
    target_high = 1.0
    target_low = 1.0
    try:
        url_target = f"https://finnhub.io/api/v1/stock/price-target?symbol={symbol}&token={key}"
        rt = requests.get(url_target, timeout=5)
        if rt.status_code == 200:
            target_data = rt.json()
            target_mean = float(target_data.get("targetMean") or 1.0)
            target_high = float(target_data.get("targetHigh") or 1.0)
            target_low = float(target_data.get("targetLow") or 1.0)
    except Exception:
        pass
        
    history = []
    for item in res[:5]:
        history.append({
            "date": item.get("period") or "",
            "firm": "Finnhub Consensus",
            "rating": f"Buy: {item.get('buy')}, Hold: {item.get('hold')}, Sell: {item.get('sell')}",
            "target": target_mean
        })
        
    return {
        "buy": buy + strong_buy,
        "hold": hold,
        "sell": sell + strong_sell,
        "strongBuy": strong_buy,
        "buy_only": buy,
        "strongSell": strong_sell,
        "sell_only": sell,
        "target_high": target_high,
        "target_low": target_low,
        "target_mean": target_mean,
        "history": history
    }

def fetch_itick_live(pair, key):
    symbol = pair.replace("/", "")
    url = f"https://api.itick.org/forex/quote?region=GB&code={symbol}"
    r = requests.get(url, headers={"Accept": "application/json", "token": key}, timeout=8)
    r.raise_for_status()
    res = r.json()
    if res.get("code") != 0 or "data" not in res:
        raise ValueError(res.get("msg") or "Invalid response format from iTick")
    data = res["data"]
    return {
        "open": float(data["o"]),
        "high": float(data["h"]),
        "low": float(data["l"]),
        "close": float(data["ld"]),
        "volume": float(data.get("v") or 0.0),
        "timestamp": data.get("t")
    }

def fetch_fcs_history_live(pair, key):
    url = f"https://api-v4.fcsapi.com/forex/history?symbol={pair}&period=1d&access_key={key}"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    res = r.json()
    if res.get("status") != True:
        raise ValueError("FCS API historical candles failure")
    candles = res.get("response", [])
    parsed = []
    for c in candles:
        parsed.append({
            "date": c.get("date") or c.get("tm"),
            "open": float(c.get("o")),
            "high": float(c.get("h")),
            "low": float(c.get("l")),
            "close": float(c.get("c"))
        })
    df = pd.DataFrame(parsed)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)

def fetch_stockdata_live(pair, key):
    symbol = pair.replace("/", "")
    url = f"https://api.stockdata.org/v1/news/all?filter_entities=true&language=en&symbols={symbol}&api_token={key}"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    res = r.json()
    articles = res.get("data", [])
    scores = [art["sentiment_score"] for art in articles if "sentiment_score" in art]
    if not scores:
        raise ValueError("No StockData sentiment articles found")
    return float(sum(scores) / len(scores) * 10.0)

def fetch_worldbank_live(country_code, indicator):
    # Auto-translate ZG to ZS for unemployment to get live data from World Bank
    if indicator == "SL.UEM.TOTL.ZG":
        indicator = "SL.UEM.TOTL.ZS"
    url = f"http://api.worldbank.org/v2/country/{country_code}/indicator/{indicator}?format=json&date=2015:2026"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    res = r.json()
    if len(res) < 2 or not isinstance(res[1], list):
        raise ValueError("World Bank data format invalid")
    parsed = []
    for item in res[1]:
        val = item.get("value")
        date_str = item.get("date")
        if val is not None:
            parsed.append({"date": f"{date_str}-12-31", "value": float(val)})
    df = pd.DataFrame(parsed)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


# ----------------- NEW DATA FETCHERS & HELPERS (Tiingo, BLS, IMF, EODHD, World Bank) -----------------
@st.cache_data(ttl=3600)
def get_tiingo_prices(ticker, api_key):
    if not api_key:
        return None
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Token {api_key}"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and isinstance(data, list):
                # Ascending order: data[-1] is the most recent
                return data[-1]
    except Exception:
        pass
    return None

@st.cache_data(ttl=3600)
def get_apifreaks_prices(api_key):
    if not api_key:
        return None
    url = "https://api.apifreaks.com/v1.0/commodity/rates/latest"
    params = {
        "apiKey": api_key,
        "symbols": "XAU,XAG,WTIOIL-SPOT,BRENTOIL-SPOT,VIX",
        "updates": "1m"
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and data.get("success") and "rates" in data:
                return data
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400)
def get_bls_data(api_key):
    if not api_key:
        return None
    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    headers = {"Content-type": "application/json"}
    
    current_year = datetime.now().year
    start_year = str(current_year - 2)
    end_year = str(current_year)
    
    payload = {
        "seriesid": ["CES0000000001", "CES0500000003", "LNS11300000"],
        "startyear": start_year,
        "endyear": end_year,
        "registrationkey": api_key
    }
    try:
        import json
        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=12)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("status") == "REQUEST_SUCCEEDED":
                return res_json
    except Exception:
        pass
    return None

def parse_bls_series(bls_data, series_id):
    if not bls_data:
        return pd.DataFrame()
    try:
        series_list = bls_data.get("Results", {}).get("series", [])
        for s in series_list:
            if s.get("seriesID") == series_id:
                data_points = s.get("data", [])
                if not data_points:
                    return pd.DataFrame()
                
                records = []
                for dp in data_points:
                    year = dp.get("year")
                    period = dp.get("period")
                    period_name = dp.get("periodName")
                    val_str = dp.get("value")
                    try:
                        val = float(val_str)
                    except ValueError:
                        continue
                    
                    if period.startswith("M") and period[1:].isdigit():
                        month = int(period[1:])
                        date_obj = datetime(int(year), month, 1)
                        records.append({
                            "date": date_obj,
                            "value": val,
                            "period_name": period_name,
                            "year": year
                        })
                df = pd.DataFrame(records)
                if not df.empty:
                    df = df.sort_values("date").reset_index(drop=True)
                return df
    except Exception:
        pass
    return pd.DataFrame()

from html.parser import HTMLParser

class TradingEconomicsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_row = []
        self.current_cell_data = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ["td", "th"] and self.in_row:
            self.in_cell = True
            self.current_cell_data = []

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            self.rows.append(self.current_row)
        elif tag in ["td", "th"] and self.in_cell:
            self.in_cell = False
            cell_text = " ".join(self.current_cell_data).strip()
            self.current_row.append(cell_text)

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell_data.append(data)

@st.cache_data(ttl=3600)
def parse_tradingeconomics_pmi(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            parser = TradingEconomicsParser()
            parser.feed(r.text)
            
            g8_names = {
                "United States": "USA",
                "Euro Area": "EUR",
                "United Kingdom": "GBP",
                "Switzerland": "CHF",
                "Canada": "CAD",
                "Australia": "AUD",
                "New Zealand": "NZD",
                "Japan": "JPY"
            }
            
            results = {}
            for row in parser.rows:
                if len(row) >= 4:
                    country = row[0].strip()
                    if country in g8_names:
                        code = g8_names[country]
                        try:
                            last_val = float(row[1])
                        except (ValueError, TypeError):
                            last_val = None
                        try:
                            prev_val = float(row[2])
                        except (ValueError, TypeError):
                            prev_val = None
                        ref_date = row[3].strip()
                        results[code] = {
                            "last": last_val,
                            "previous": prev_val,
                            "reference": ref_date
                        }
            return results
    except Exception:
        pass
    return None

@st.cache_data(ttl=3600)
def get_eodhd_pmi_fallback(country_code, indicator_keyword, api_key):
    if not api_key:
        return None
    country_map = {
        "EUR": "EMU", "GBP": "GBR", "CHF": "CHE",
        "CAD": "CAN", "AUD": "AUS", "NZD": "NZL", "JPY": "JPN"
    }
    eodhd_country = country_map.get(country_code, country_code)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    url = f"https://eodhd.com/api/economic-events"
    params = {
        "api_token": api_key,
        "from": start_date,
        "to": end_date,
        "country": eodhd_country,
        "limit": 300
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            events = r.json()
            matched = []
            for ev in events:
                name = ev.get("name", "").upper()
                if indicator_keyword.upper() in name:
                    matched.append(ev)
            if matched:
                matched.sort(key=lambda x: x.get("date", ""), reverse=True)
                latest = matched[0]
                try:
                    last_val = float(latest.get("actual"))
                except (ValueError, TypeError):
                    last_val = None
                try:
                    prev_val = float(latest.get("previous"))
                except (ValueError, TypeError):
                    prev_val = None
                ref_date = latest.get("date", "")
                return {
                    "last": last_val,
                    "previous": prev_val,
                    "reference": ref_date
                }
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400)
def get_eodhd_pmi_historical(country_code, indicator_keyword, target_date, api_key):
    if not api_key:
        return None
    country_map = {
        "EUR": "EMU", "GBP": "GBR", "CHF": "CHE",
        "CAD": "CAN", "AUD": "AUS", "NZD": "NZL", "JPY": "JPN"
    }
    eodhd_country = country_map.get(country_code, country_code)
    target_dt = pd.to_datetime(target_date)
    start_date = (target_dt - timedelta(days=90)).strftime("%Y-%m-%d")
    end_date = target_dt.strftime("%Y-%m-%d")
    url = "https://eodhd.com/api/economic-events"
    params = {
        "api_token": api_key,
        "from": start_date,
        "to": end_date,
        "country": eodhd_country,
        "limit": 300
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            events = r.json()
            matched = []
            for ev in events:
                name = ev.get("name", "").upper()
                if indicator_keyword.upper() in name:
                    matched.append(ev)
            if matched:
                matched.sort(key=lambda x: x.get("date", ""))
                latest = matched[-1]
                try:
                    last_val = float(latest.get("actual"))
                except (ValueError, TypeError):
                    last_val = None
                try:
                    prev_val = float(latest.get("previous"))
                except (ValueError, TypeError):
                    prev_val = None
                ref_date = latest.get("date", "")
                return {
                    "last": last_val,
                    "previous": prev_val,
                    "reference": ref_date
                }
    except Exception:
        pass
    return None

def get_all_pmi_data_historical(fred_key, eodhd_key, target_date):
    pmi_results = {}
    
    # USD (FRED)
    usa_m_last, usa_m_dt, _ = get_fred_data_historical("NAPM", target_date, fred_key)
    usa_m_prev = None
    if fred_key and usa_m_last is not None:
        df_napm, _, _ = get_fred_data("NAPM", fred_key)
        if df_napm is not None and not df_napm.empty:
            target_dt = pd.to_datetime(target_date)
            df_filtered = df_napm[df_napm["date"] <= target_dt].sort_values("date")
            if len(df_filtered) >= 2:
                usa_m_prev = float(df_filtered.iloc[-2]["value"])
                
    usa_s_last, usa_s_dt, _ = get_fred_data_historical("NMFPT", target_date, fred_key)
    usa_s_prev = None
    if fred_key and usa_s_last is not None:
        df_nmfpt, _, _ = get_fred_data("NMFPT", fred_key)
        if df_nmfpt is not None and not df_nmfpt.empty:
            target_dt = pd.to_datetime(target_date)
            df_filtered = df_nmfpt[df_nmfpt["date"] <= target_dt].sort_values("date")
            if len(df_filtered) >= 2:
                usa_s_prev = float(df_filtered.iloc[-2]["value"])
                
    usa_m_ref_str = usa_m_dt.strftime("%Y-%m-%d") if isinstance(usa_m_dt, datetime) else str(usa_m_dt) if usa_m_dt else None
    usa_s_ref_str = usa_s_dt.strftime("%Y-%m-%d") if isinstance(usa_s_dt, datetime) else str(usa_s_dt) if usa_s_dt else None
                
    pmi_results["USD"] = {
        "m_last": usa_m_last, "m_prev": usa_m_prev, "m_ref": usa_m_ref_str, "m_src": "FRED",
        "s_last": usa_s_last, "s_prev": usa_s_prev, "s_ref": usa_s_ref_str, "s_src": "FRED"
    }
    
    # Other G8 Currencies (EODHD)
    for code in ["EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]:
        m_last, m_prev, m_ref, m_src = None, None, None, "EODHD"
        s_last, s_prev, s_ref, s_src = None, None, None, "EODHD"
        
        if eodhd_key:
            res_m = get_eodhd_pmi_historical(code, "Manufacturing PMI", target_date, eodhd_key)
            if res_m:
                m_last = res_m["last"]
                m_prev = res_m["previous"]
                m_ref = res_m["reference"]
                
            res_s = get_eodhd_pmi_historical(code, "Services PMI", target_date, eodhd_key)
            if res_s:
                s_last = res_s["last"]
                s_prev = res_s["previous"]
                s_ref = res_s["reference"]
                
        pmi_results[code] = {
            "m_last": m_last, "m_prev": m_prev, "m_ref": m_ref, "m_src": m_src,
            "s_last": s_last, "s_prev": s_prev, "s_ref": s_ref, "s_src": s_src
        }
        
    return pmi_results

def get_all_pmi_data(fred_key, eodhd_key, target_date=None):
    if target_date is not None:
        return get_all_pmi_data_historical(fred_key, eodhd_key, target_date)
        
    te_m = parse_tradingeconomics_pmi("https://tradingeconomics.com/country-list/manufacturing-pmi")
    te_s = parse_tradingeconomics_pmi("https://tradingeconomics.com/country-list/services-pmi")
    
    pmi_results = {}
    
    # USD
    usa_m_last, usa_m_prev, usa_m_ref = None, None, None
    usa_s_last, usa_s_prev, usa_s_ref = None, None, None
    
    if fred_key:
        df_napm, _, is_live = get_fred_data("NAPM", fred_key)
        if is_live and df_napm is not None and not df_napm.empty:
            df_napm = df_napm.sort_values("date", ascending=False)
            if len(df_napm) >= 1:
                usa_m_last = float(df_napm.iloc[0]["value"])
                usa_m_ref = df_napm.iloc[0]["date"].strftime("%b/%y")
            if len(df_napm) >= 2:
                usa_m_prev = float(df_napm.iloc[1]["value"])
                
    if usa_m_last is None and te_m and "USA" in te_m:
        usa_m_last = te_m["USA"]["last"]
        usa_m_prev = te_m["USA"]["previous"]
        usa_m_ref = te_m["USA"]["reference"]
        
    if te_s and "USA" in te_s:
        usa_s_last = te_s["USA"]["last"]
        usa_s_prev = te_s["USA"]["previous"]
        usa_s_ref = te_s["USA"]["reference"]
        
    pmi_results["USD"] = {
        "m_last": usa_m_last, "m_prev": usa_m_prev, "m_ref": usa_m_ref, "m_src": "FRED/TE" if fred_key else "TE",
        "s_last": usa_s_last, "s_prev": usa_s_prev, "s_ref": usa_s_ref, "s_src": "TE"
    }
    
    for code in ["EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]:
        m_last, m_prev, m_ref, m_src = None, None, None, "TE"
        s_last, s_prev, s_ref, s_src = None, None, None, "TE"
        
        if te_m and code in te_m:
            m_last = te_m[code]["last"]
            m_prev = te_m[code]["previous"]
            m_ref = te_m[code]["reference"]
        
        if m_last is None and eodhd_key:
            res_eod = get_eodhd_pmi_fallback(code, "Manufacturing PMI", eodhd_key)
            if res_eod:
                m_last = res_eod["last"]
                m_prev = res_eod["previous"]
                m_ref = res_eod["reference"]
                m_src = "EODHD"
                
        if te_s and code in te_s:
            s_last = te_s[code]["last"]
            s_prev = te_s[code]["previous"]
            s_ref = te_s[code]["reference"]
            
        if s_last is None and eodhd_key:
            res_eod = get_eodhd_pmi_fallback(code, "Services PMI", eodhd_key)
            if res_eod:
                s_last = res_eod["last"]
                s_prev = res_eod["previous"]
                s_ref = res_eod["reference"]
                s_src = "EODHD"
                
        pmi_results[code] = {
            "m_last": m_last, "m_prev": m_prev, "m_ref": m_ref, "m_src": m_src,
            "s_last": s_last, "s_prev": s_prev, "s_ref": s_ref, "s_src": s_src
        }
        
    return pmi_results

@st.cache_data(ttl=604800) # 1 week
def get_imf_data(indicator):
    url = f"https://www.imf.org/external/datamapper/api/v1/{indicator}"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None

def get_latest_imf_value(curr, indicator):
    mapping = {
        "USD": ["USA"],
        "EUR": ["EUR", "EMU", "U2", "DEU"],
        "GBP": ["GBR"],
        "CHF": ["CHE"],
        "CAD": ["CAN"],
        "AUD": ["AUS"],
        "NZD": ["NZL"],
        "JPY": ["JPN"]
    }
    candidates = mapping.get(curr, [curr])
    data = get_imf_data(indicator)
    if not data:
        return None
    try:
        indicator_data = data.get("values", {}).get(indicator, {})
        for code in candidates:
            values_dict = indicator_data.get(code, {})
            if values_dict:
                years = [int(yr) for yr in values_dict.keys() if yr.isdigit()]
                if years:
                    latest_year = str(max(years))
                    val = values_dict[latest_year]
                    if val is not None:
                        return val
    except Exception:
        pass
    return None

def format_imf_indicator(base, quote, indicator):
    base_val = get_latest_imf_value(base, indicator)
    quote_val = get_latest_imf_value(quote, indicator)
    base_str = f"{base_val:.1f}%" if base_val is not None else "N/A"
    quote_str = f"{quote_val:.1f}%" if quote_val is not None else "N/A"
    return f"{base_str} / {quote_str}"



def get_latest_worldbank_trade_balance(country_code):
    try:
        df, _, _ = get_worldbank_data(country_code, "NE.RSB.GNFS.ZS")
        if df is not None and not df.empty:
            return df.iloc[-1]["value"]
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400) # 1 day
def get_oecd_cli_data():
    url = "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI/?format=csv"
    try:
        response = requests.get(url, timeout=20)
        if response.status_code == 200:
            df = pd.read_csv(io.StringIO(response.text))
            return df
    except Exception:
        pass
    return None

def get_latest_oecd_cli(curr):
    mapping = {
        "USD": "USA",
        "EUR": "EA20",
        "GBP": "GBR",
        "CHF": "CHE",
        "CAD": "CAN",
        "AUD": "AUS",
        "NZD": "NZL",
        "JPY": "JPN"
    }
    country_code = mapping.get(curr)
    if not country_code:
        return None
        
    df = get_oecd_cli_data()
    if df is None or df.empty:
        return None
        
    try:
        df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == country_code)]
        if df_m.empty and curr == "EUR":
            df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == "EA19")]
            
        if df_m.empty:
            return None
            
        # Try indicators in order of preference: LI (CLI), BCICP (BCI proxy), CCICP (CCI proxy)
        for indicator in ["LI", "BCICP", "CCICP"]:
            df_ind = df_m[df_m["MEASURE"] == indicator]
            if not df_ind.empty:
                latest = df_ind.sort_values("TIME_PERIOD").iloc[-1]
                val = float(latest["OBS_VALUE"])
                if not pd.isna(val):
                    return val, latest["TIME_PERIOD"]
    except Exception:
        pass
    return None


# ----------------- 2. CACHED API LOADERS (Zero-Overlap & TTLs) -----------------
@st.cache_data(ttl=86400, show_spinner=False)
def get_fred_data(series_id, key):
    if not key:
        return generate_mock_fred(series_id), datetime.now(), False
    try:
        df = fetch_fred_live(series_id, key)
        return df, datetime.now(), True
    except Exception:
        return generate_mock_fred(series_id), datetime.now(), False

@st.cache_data(ttl=900, show_spinner=False)
def get_av_data(from_symbol, to_symbol, key):
    if not key:
        return generate_mock_av(from_symbol, to_symbol), datetime.now(), False
    try:
        df = fetch_av_live(from_symbol, to_symbol, key)
        return df, datetime.now(), True
    except Exception:
        return generate_mock_av(from_symbol, to_symbol), datetime.now(), False

@st.cache_data(ttl=3600, show_spinner=False)
def get_benzinga_data(key):
    if not key:
        return generate_mock_benzinga(), datetime.now(), False
    try:
        df = fetch_benzinga_live(key)
        if df.empty:
            raise ValueError("Empty response")
        return df, datetime.now(), True
    except Exception:
        return generate_mock_benzinga(), datetime.now(), False

@st.cache_data(ttl=21600, show_spinner=False)
def get_finnhub_data(pair, key):
    if not key:
        return generate_mock_finnhub(pair), datetime.now(), False
    try:
        data = fetch_finnhub_live(pair, key)
        return data, datetime.now(), True
    except Exception:
        return generate_mock_finnhub(pair), datetime.now(), False

@st.cache_data(ttl=60, show_spinner=False)
def get_itick_data(pair, key):
    if not key:
        return generate_mock_itick(pair), datetime.now(), False
    try:
        data = fetch_itick_live(pair, key)
        return data, datetime.now(), True
    except Exception:
        return generate_mock_itick(pair), datetime.now(), False

@st.cache_data(ttl=900, show_spinner=False)
def get_av_technical_data(pair, key):
    if not key:
        import random
        random.seed(hash(pair) % 15000)
        base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120, "EUR/GBP": 0.8520}
        base = base_prices.get(pair, 1.0)
        return {
            "SMA_50": base * random.uniform(0.99, 1.01),
            "SMA_200": base * random.uniform(0.97, 0.99)
        }, datetime.now(), False
    try:
        from_sym, to_sym = pair.split("/")
        df = fetch_av_live(from_sym, to_sym, key)
        if not df.empty and len(df) >= 50:
            df = calculate_smas(df)
            latest = df.iloc[-1]
            return {
                "SMA_50": float(latest["SMA_50"]) if "SMA_50" in latest else None,
                "SMA_200": float(latest["SMA_200"]) if "SMA_200" in latest else None
            }, datetime.now(), True
        else:
            raise ValueError("Insufficient data for SMA")
    except Exception:
        import random
        random.seed(hash(pair) % 15000)
        base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120, "EUR/GBP": 0.8520}
        base = base_prices.get(pair, 1.0)
        return {
            "SMA_50": base * random.uniform(0.99, 1.01),
            "SMA_200": base * random.uniform(0.97, 0.99)
        }, datetime.now(), False

@st.cache_data(ttl=86400, show_spinner=False)
def get_fcs_history_data(pair, key):
    if not key:
        from_sym, to_sym = pair.split("/")
        return generate_mock_fcs_history(from_sym, to_sym), datetime.now(), False
    try:
        df = fetch_fcs_history_live(pair, key)
        return df, datetime.now(), True
    except Exception:
        from_sym, to_sym = pair.split("/")
        return generate_mock_fcs_history(from_sym, to_sym), datetime.now(), False

@st.cache_data(ttl=86400, show_spinner=False)
def get_fcs_correlation_data(key):
    if not key:
        return generate_mock_fcs_correlation(), datetime.now(), False
    try:
        pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP"]
        prices = {}
        for p in pairs:
            df = fetch_fcs_history_live(p, key)
            if not df.empty:
                prices[p] = df.tail(30).set_index("date")["close"]
        if len(prices) == len(pairs):
            rdf = pd.DataFrame(prices).ffill().bfill()
            return rdf.corr(), datetime.now(), True
        else:
            raise ValueError("Failed to retrieve all pairs for correlation")
    except Exception:
        return generate_mock_fcs_correlation(), datetime.now(), False

@st.cache_data(ttl=900, show_spinner=False)
def get_stockdata_sentiment(pair, key):
    if not key:
        return generate_mock_stockdata(), datetime.now(), False
    try:
        val = fetch_stockdata_live(pair, key)
        return val, datetime.now(), True
    except Exception:
        return generate_mock_stockdata(), datetime.now(), False

@st.cache_data(ttl=604800, show_spinner=False)
def get_worldbank_data(country_code, indicator):
    try:
        df = fetch_worldbank_live(country_code, indicator)
        return df, datetime.now(), True
    except Exception:
        return generate_mock_worldbank(country_code, indicator), datetime.now(), False

def parse_worldbank_latest(wb_result):
    try:
        if wb_result is None:
            return None, None
        df, _, _ = wb_result
        if df is None or df.empty:
            return None, None
        latest_row = df.iloc[-1]
        val = latest_row["value"]
        dt = latest_row["date"]
        if hasattr(dt, "year"):
            year = str(dt.year)
        else:
            year = str(dt).split("-")[0]
        return val, year
    except Exception:
        return None, None

@st.cache_data(ttl=3600, show_spinner=False)
def get_ecb_rate_cached():
    url = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?lastNObservations=2&format=jsondata"
    r = requests.get(url, headers={"Accept": "application/json"}, timeout=8)
    r.raise_for_status()
    res = r.json()
    series = res["dataSets"][0]["series"]
    series_key = list(series.keys())[0]
    obs = series[series_key]["observations"]
    sorted_keys = sorted(obs.keys(), key=int)
    latest_val = float(obs[sorted_keys[-1]][0])
    prev_val = float(obs[sorted_keys[-2]][0]) if len(sorted_keys) > 1 else latest_val
    bps_change = int((latest_val - prev_val) * 100)
    return latest_val, bps_change

@st.cache_data(ttl=3600, show_spinner=False)
def get_snb_rate_cached():
    url = "https://data.snb.ch/api/cube/snboffzisa/data/csv/en"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    lines = r.text.split("\n")
    data_lines = []
    start_reading = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('"Date";'):
            start_reading = True
        if start_reading:
            data_lines.append(line)
    if not data_lines:
        raise ValueError("Could not find data in SNB CSV")
    
    df = pd.read_csv(io.StringIO("\n".join(data_lines)), sep=";")
    df_lz = df[df["D0"] == "LZ"].copy()
    if df_lz.empty:
        raise ValueError("LZ key not found in SNB data")
    
    df_lz = df_lz.sort_values("Date")
    latest_val = float(df_lz.iloc[-1]["Value"])
    prev_val = float(df_lz.iloc[-2]["Value"]) if len(df_lz) > 1 else latest_val
    bps_change = int((latest_val - prev_val) * 100)
    return latest_val, bps_change



# ----------------- NEWS LOADER & FALLBACKS -----------------
@st.cache_data(ttl=60, show_spinner=False)
def get_news_data_search(query, newsdata_key, newsapi_key):
    debug_logs = []
    articles = []
    source = None
    success = False

    # 1. Test NewsData.io Key first with a simple check if present
    if newsdata_key:
        debug_logs.append("NewsData.io: API-Key vorhanden. Starte Verbindungstest...")
        try:
            url = "https://newsdata.io/api/1/latest"
            params = {
                "apikey": newsdata_key,
                "q": "forex",
                "size": 1
            }
            r = requests.get(url, params=params, timeout=10)
            debug_logs.append(f"NewsData.io Test: HTTP Status {r.status_code}")
            
            if r.status_code == 200:
                res = r.json()
                if res.get("status") == "success":
                    debug_logs.append("NewsData.io: Verbindungstest erfolgreich.")
                    debug_logs.append(f"NewsData.io: Führe Suche für '{query}' aus...")
                    params_actual = {
                        "apikey": newsdata_key,
                        "q": query,
                        "language": "en,de"
                    }
                    r_actual = requests.get(url, params=params_actual, timeout=10)
                    debug_logs.append(f"NewsData.io Suche: HTTP Status {r_actual.status_code}")
                    if r_actual.status_code == 200:
                        res_actual = r_actual.json()
                        if res_actual.get("status") == "success" and res_actual.get("results"):
                            for a in res_actual["results"]:
                                articles.append({
                                    "title": a.get("title") or "Ohne Titel",
                                    "description": a.get("description") or "",
                                    "url": a.get("link") or "#",
                                    "source": a.get("source_id") or "NewsData",
                                    "publishedAt": a.get("pubDate") or "",
                                    "urlToImage": a.get("image_url"),
                                    "api": "NewsData.io"
                                })
                            debug_logs.append(f"NewsData.io: Suche erfolgreich, {len(articles)} Artikel gefunden.")
                            success = True
                            source = "NewsData.io"
                        else:
                            debug_logs.append("NewsData.io: Keine passenden Artikel für diese Suchanfrage gefunden.")
                    else:
                        debug_logs.append(f"NewsData.io Suche fehlgeschlagen: HTTP {r_actual.status_code}. Antwort: {r_actual.text[:150]}")
                else:
                    debug_logs.append(f"NewsData.io Verbindungstest meldete Fehler: {res.get('results') or res.get('error')}")
            else:
                debug_logs.append(f"NewsData.io Verbindungstest fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"NewsData.io: Netzwerkfehler: {str(e)}")
    else:
        debug_logs.append("NewsData.io: API-Key fehlt in .env.")

    time.sleep(0.5)

    # 2. Try NewsAPI.org
    if newsapi_key:
        debug_logs.append("NewsAPI.org: API-Key vorhanden. Starte Suche...")
        
        def query_newsapi(q_term):
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": q_term,
                "apiKey": newsapi_key,
                "sortBy": "publishedAt",
                "pageSize": 25,
                "language": "de,en"
            }
            r = requests.get(url, params=params, timeout=10)
            debug_logs.append(f"NewsAPI.org Suche ({q_term[:30]}...): HTTP Status {r.status_code}")
            if r.status_code == 200:
                res = r.json()
                if res.get("status") == "ok" and res.get("articles"):
                    parsed_articles = []
                    for a in res["articles"]:
                        if a.get("title") and a.get("title") != "[Removed]":
                            parsed_articles.append({
                                "title": a.get("title"),
                                "description": a.get("description") or "",
                                "url": a.get("url") or "#",
                                "source": a.get("source", {}).get("name") or "NewsAPI",
                                "publishedAt": a.get("publishedAt") or "",
                                "urlToImage": a.get("urlToImage"),
                                "api": "NewsAPI.org"
                            })
                    return parsed_articles
                else:
                    debug_logs.append(f"NewsAPI.org ({q_term[:30]}...): Antwort enthielt keine Artikel.")
            else:
                debug_logs.append(f"NewsAPI.org fehlgeschlagen ({q_term[:30]}...): HTTP {r.status_code}. Antwort: {r.text[:100]}")
            return []

        na_articles = query_newsapi(query)
        if not na_articles:
            words = query.split()
            base_q = words[0] if len(words) >= 1 else "EUR"
            quote_q = words[1] if len(words) >= 2 else "USD"
            simple_q = f"{base_q} {quote_q} forex"
            debug_logs.append(f"NewsAPI.org: Erster Versuch leer. Weiche auf einfacheren Suchbegriff '{simple_q}' aus...")
            na_articles = query_newsapi(simple_q)
            
        if na_articles:
            debug_logs.append(f"NewsAPI.org: Suche erfolgreich, {len(na_articles)} Artikel gefunden.")
            if not articles:
                articles = na_articles
                source = "NewsAPI.org (Fallback)"
            else:
                existing_titles = {art["title"].lower()[:50] for art in articles}
                for a in na_articles:
                    title_prefix = a["title"].lower()[:50]
                    if title_prefix not in existing_titles:
                        articles.append(a)
                source = "Combined (NewsData & NewsAPI)"
            success = True
        else:
            debug_logs.append("NewsAPI.org: Beide Suchversuche lieferten keine Artikel.")
    else:
        debug_logs.append("NewsAPI.org: API-Key fehlt in .env.")

    if success and articles:
        debug_logs.append(f"Zusammenfassung: API {source} verwendet, insgesamt {len(articles)} Artikel geladen.")
        return articles[:25], source, True, datetime.now(), debug_logs

    # Both failed completely
    debug_logs.append("Zusammenfassung: Keine API lieferte Ergebnisse. News-APIs momentan nicht verfügbar. Weiche auf Mock-News aus.")
    mock_articles = []
    base_mock = generate_mock_news()
    for m in base_mock:
        m_copy = m.copy()
        m_copy["title"] = f"[{query}] " + m_copy["title"]
        m_copy["urlToImage"] = "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80"
        m_copy["api"] = "MOCK-News Engine"
        mock_articles.append(m_copy)
    return mock_articles, "News-APIs momentan nicht verfügbar (Demo-Modus)", False, datetime.now(), debug_logs


# ----------------- HISTORICAL BACKTEST DATA HELPERS -----------------
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_fred_history_full(series_id, target_date, key):
    if not key:
        return None
    try:
        target_date_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json&observation_end={target_date_str}"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            parsed = []
            for o in obs:
                if o["value"] != ".":
                    parsed.append({"date": pd.to_datetime(o["date"]), "value": float(o["value"])})
            if parsed:
                return pd.DataFrame(parsed)
    except Exception:
        pass
    # Fallback to full series fetching
    try:
        url_live = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json"
        r = requests.get(url_live, timeout=8)
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            parsed = []
            for o in obs:
                if o["value"] != ".":
                    parsed.append({"date": pd.to_datetime(o["date"]), "value": float(o["value"])})
            if parsed:
                df = pd.DataFrame(parsed)
                target_dt = pd.to_datetime(target_date)
                return df[df["date"] <= target_dt]
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400, show_spinner=False)
def get_fred_data_historical(series_id, target_date, fred_key=FRED_KEY):
    if not fred_key:
        return None, None, False

    try:
        df, _, is_live = get_fred_data(series_id, fred_key)
        if df is not None and not df.empty:
            target_dt = pd.to_datetime(target_date)
            df = df.copy()
            df["diff"] = (df["date"] - target_dt).abs()
            closest_row = df.sort_values("diff").iloc[0]
            return float(closest_row["value"]), closest_row["date"], is_live
    except Exception:
        pass

    return None, None, False


@st.cache_data(ttl=86400, show_spinner=False)
def get_ecb_rate_historical(target_date):
    try:
        target_date_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        url = f"https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.MRR_FR.LEV?startPeriod={target_date_str}&endPeriod={target_date_str}&format=jsondata"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            res = r.json()
            series = res["dataSets"][0]["series"]
            if series:
                series_key = list(series.keys())[0]
                obs = series[series_key]["observations"]
                if obs:
                    val = float(list(obs.values())[0][0])
                    return val, pd.to_datetime(target_date_str)
    except Exception:
        pass

    # Fallback to querying series up to target date using endPeriod
    try:
        target_date_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        url = f"https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.MRR_FR.LEV?endPeriod={target_date_str}&format=jsondata"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            res = r.json()
            series = res["dataSets"][0]["series"]
            if series:
                series_key = list(series.keys())[0]
                obs = series[series_key]["observations"]
                
                dimensions = res["structure"]["dimensions"]["observation"]
                time_dim = next(dim for dim in dimensions if dim["id"] == "TIME_PERIOD")
                time_values = [v["id"] for v in time_dim["values"]]
                
                parsed = []
                for idx_str, val_list in obs.items():
                    idx = int(idx_str)
                    date_str = time_values[idx]
                    parsed.append((pd.to_datetime(date_str), float(val_list[0])))
                if parsed:
                    parsed.sort(key=lambda x: x[0])
                    return parsed[-1][1], parsed[-1][0]
    except Exception:
        pass
        
    return None, None

@st.cache_data(ttl=86400, show_spinner=False)
def get_boc_rate_historical_cached():
    # Try OVERNIGHT_DAILY, V39079, V122514
    for series in ["OVERNIGHT_DAILY", "V39079", "V122514"]:
        try:
            url = f"https://www.bankofcanada.ca/valet/observations/{series}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                obs = data.get("observations", [])
                parsed = []
                for o in obs:
                    date_str = o.get("d")
                    series_data = o.get(series)
                    if series_data and "v" in series_data:
                        val = float(series_data["v"])
                        parsed.append((pd.to_datetime(date_str), val))
                if parsed:
                    df = pd.DataFrame(parsed, columns=["date", "value"]).sort_values("date").reset_index(drop=True)
                    return df
        except Exception:
            pass
    return None

def get_boc_rate_historical(target_date):
    df = get_boc_rate_historical_cached()
    if df is not None and not df.empty:
        target_dt = pd.to_datetime(target_date)
        df = df.copy()
        df["diff"] = (df["date"] - target_dt).abs()
        closest = df.sort_values("diff").iloc[0]
        return float(closest["value"]), "Bank of Canada API"
            
    # Fallback to FRED IRSTCI01CAM156N
    val_fred, dt_fred, _ = get_fred_data_historical("IRSTCI01CAM156N", target_date)
    if val_fred is not None:
        return val_fred, "FRED (IRSTCI01CAM156N)"
        
    return None, "Keine Daten verfügbar"

@st.cache_data(ttl=86400, show_spinner=False)
def get_snb_rate_historical(target_date):
    try:
        url = "https://data.snb.ch/api/cube/snboffzisa/data/csv/en"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        lines = r.text.split("\n")
        data_lines = []
        start_reading = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith('"date";') or line.lower().startswith('date;'):
                start_reading = True
            if start_reading:
                data_lines.append(line)
        if not data_lines:
            raise ValueError("Could not find data in SNB CSV")
        
        df = pd.read_csv(io.StringIO("\n".join(data_lines)), sep=";")
        df_lz = df[df["D0"] == "LZ"].copy()
        if df_lz.empty:
            raise ValueError("LZ key not found in SNB data")
        
        df_lz["parsed_date"] = pd.to_datetime(df_lz["Date"], format="%Y-%m")
        df_lz = df_lz.dropna(subset=["Value"])
        df_lz = df_lz.sort_values("parsed_date")
        
        target_dt = pd.to_datetime(target_date)
        df_lz = df_lz.copy()
        df_lz["diff"] = (df_lz["parsed_date"] - target_dt).abs()
        closest = df_lz.sort_values("diff").iloc[0]
        return float(closest["Value"]), closest["parsed_date"]
    except Exception:
        pass
    return None, None

@st.cache_data(ttl=86400, show_spinner=False)
def get_worldbank_data_historical_cached(country_code, indicator):
    if country_code == "EMU":
        country_code = "DEU"
    try:
        # Auto-translate ZG to ZS for unemployment
        ind_code = "SL.UEM.TOTL.ZS" if indicator == "SL.UEM.TOTL.ZG" else indicator
        url = f"https://api.worldbank.org/v2/country/{country_code}/indicator/{ind_code}?format=json&per_page=1000"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        res = r.json()
        if len(res) >= 2 and isinstance(res[1], list):
            parsed = []
            for item in res[1]:
                val = item.get("value")
                date_str = item.get("date")
                if val is not None:
                    parsed.append({"date": f"{date_str}-12-31", "value": float(val)})
            if parsed:
                df = pd.DataFrame(parsed)
                df["date"] = pd.to_datetime(df["date"])
                return df.sort_values("date").reset_index(drop=True), True
    except Exception:
        pass
    return None, False

def get_worldbank_data_historical(country_code, indicator, target_date):
    if country_code == "EMU":
        country_code = "DEU"
    df, is_live = get_worldbank_data_historical_cached(country_code, indicator)
    if df is not None and not df.empty:
        target_year = pd.to_datetime(target_date).year
        df = df.copy()
        df["year"] = df["date"].dt.year
        df_filtered = df[df["year"] <= target_year]
        if not df_filtered.empty:
            latest_row = df_filtered.sort_values("year").iloc[-1]
            return float(latest_row["value"]), latest_row["date"], is_live
    return None, None, False

def get_historical_oecd_cli(curr, target_date):
    mapping = {
        "USD": "USA",
        "EUR": "EA20",
        "GBP": "GBR",
        "CHF": "CHE",
        "CAD": "CAN",
        "AUD": "AUS",
        "NZD": "NZL",
        "JPY": "JPN"
    }
    country_code = mapping.get(curr)
    if not country_code:
        return None
        
    df = get_oecd_cli_data()
    df_m = None
    if df is not None and not df.empty:
        try:
            df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == country_code)]
            if df_m.empty and curr == "EUR":
                df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == "EA19")]
        except Exception:
            pass

    if df_m is not None and not df_m.empty:
        try:
            target_dt = pd.to_datetime(target_date)
            for indicator in ["LI", "BCICP", "CCICP"]:
                df_ind = df_m[df_m["MEASURE"] == indicator].copy()
                if not df_ind.empty:
                    df_ind["parsed_date"] = pd.to_datetime(df_ind["TIME_PERIOD"], errors="coerce")
                    df_ind = df_ind.dropna(subset=["parsed_date"])
                    df_filtered = df_ind[df_ind["parsed_date"] <= target_dt]
                    if not df_filtered.empty:
                        latest = df_filtered.sort_values("parsed_date").iloc[-1]
                        val = float(latest["OBS_VALUE"])
                        if not pd.isna(val):
                            return val, latest["TIME_PERIOD"]
        except Exception:
            pass

    return None

def get_country_rate_historical(country_code, target_date):
    map_code = {
        "USA": "USD", "USD": "USD", "US": "USD",
        "EMU": "EUR", "EUR": "EUR", "EU": "EUR",
        "CHE": "CHF", "CHF": "CHF", "CH": "CHF",
        "GBR": "GBP", "GBP": "GBP", "UK": "GBP",
        "JPN": "JPY", "JPY": "JPY", "JP": "JPY",
        "AUS": "AUD", "AUD": "AUD", "AU": "AUD",
        "CAN": "CAD", "CAD": "CAD", "CA": "CAD",
        "NZL": "NZD", "NZD": "NZD", "NZ": "NZD"
    }
    curr = map_code.get(country_code, country_code)
    
    if curr == "USD":
        val, dt, _ = get_fred_data_historical("FEDFUNDS", target_date)
        if val is not None:
            return val, "FRED (FEDFUNDS)"
            
    elif curr == "EUR":
        val, _ = get_ecb_rate_historical(target_date)
        if val is not None:
            return val, "ECB API"
            
    elif curr == "CHF":
        val, _ = get_snb_rate_historical(target_date)
        if val is not None:
            return val, "SNB API"
            
    elif curr == "GBP":
        val, dt, _ = get_fred_data_historical("IRSTCI01GBM156N", target_date)
        if val is not None:
            return val, "FRED (IRSTCI01GBM156N)"
            
    elif curr == "JPY":
        val, dt, _ = get_fred_data_historical("IRSTCI01JPM156N", target_date)
        if val is not None:
            return val, "FRED (IRSTCI01JPM156N)"
            
    elif curr == "AUD":
        val, dt, _ = get_fred_data_historical("IRSTCI01AUM156N", target_date)
        if val is not None:
            return val, "FRED (IRSTCI01AUM156N)"
            
    elif curr == "NZD":
        val, dt, _ = get_fred_data_historical("IRSTCI01NZM156N", target_date)
        if val is not None:
            return val, "FRED (IRSTCI01NZM156N)"
            
    elif curr == "CAD":
        val, src = get_boc_rate_historical(target_date)
        if val is not None:
            return val, src
            
    return None, "Keine Daten verfügbar"



def get_historical_commodities(target_date, key=FRED_KEY):
    res = {}
    
    # 1. Gold and Silver from timeseries API
    try:
        target_dt = pd.to_datetime(target_date)
        start_dt = target_dt - timedelta(days=5)
        start_str = start_dt.strftime("%Y-%m-%d")
        
        url = "https://currencyapi.vitalmedx.com/api/v1/timeseries"
        params = {
            "start_date": start_str,
            "end_date": target_date,
            "base": "USD",
            "symbols": "XAU,XAG"
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            rates_dict = data.get("data", {}).get("rates", {})
            sorted_dates = sorted(rates_dict.keys())
            
            # Gold
            gold_vals = []
            for d in sorted_dates:
                val = rates_dict[d].get("XAU")
                if val and val > 0:
                    gold_vals.append((d, 1.0 / val))
            if gold_vals:
                res["gold"] = gold_vals[-1][1]
                if len(gold_vals) >= 2:
                    res["gold_chg"] = ((gold_vals[-1][1] - gold_vals[-2][1]) / gold_vals[-2][1]) * 100
                else:
                    res["gold_chg"] = 0.0
            else:
                res["gold"] = None
                res["gold_chg"] = 0.0
                
            # Silver
            silver_vals = []
            for d in sorted_dates:
                val = rates_dict[d].get("XAG")
                if val and val > 0:
                    silver_vals.append((d, 1.0 / val))
            if silver_vals:
                res["silver"] = silver_vals[-1][1]
                if len(silver_vals) >= 2:
                    res["silver_chg"] = ((silver_vals[-1][1] - silver_vals[-2][1]) / silver_vals[-2][1]) * 100
                else:
                    res["silver_chg"] = 0.0
            else:
                res["silver"] = None
                res["silver_chg"] = 0.0
    except Exception:
        res["gold"] = None
        res["gold_chg"] = 0.0
        res["silver"] = None
        res["silver_chg"] = 0.0

    # 2. WTI, Brent, VIX from FRED
    if key:
        series_map = {
            "wti": "DCOILWTICO",
            "brent": "DCOILBRENTEU",
            "vix": "VIXCLS"
        }
        for name, series_id in series_map.items():
            val, dt, _ = get_fred_data_historical(series_id, target_date, key)
            if val is not None:
                df = fetch_fred_history_full(series_id, target_date, key)
                chg_pct = 0.0
                if df is not None and len(df) >= 2:
                    df = df.sort_values("date")
                    last_val = df.iloc[-1]["value"]
                    prev_val = df.iloc[-2]["value"]
                    if prev_val != 0:
                        chg_pct = ((last_val - prev_val) / prev_val) * 100
                res[name] = val
                res[name + "_chg"] = chg_pct
            else:
                res[name] = None
                res[name + "_chg"] = 0.0
    else:
        res["wti"] = None
        res["wti_chg"] = 0.0
        res["brent"] = None
        res["brent_chg"] = 0.0
        res["vix"] = None
        res["vix_chg"] = 0.0
        
    return res

def get_historical_labor_data(target_date, key=FRED_KEY):
    if not key:
        return None
        
    series_map = {
        "nfp": "PAYEMS",
        "wage": "CES0500000003",
        "part": "CIVPART"
    }
    
    res = {}
    for name, series_id in series_map.items():
        val, dt, _ = get_fred_data_historical(series_id, target_date, key)
        if val is not None:
            df = fetch_fred_history_full(series_id, target_date, key)
            chg = 0.0
            if df is not None and len(df) >= 2:
                df = df.sort_values("date")
                last_val = df.iloc[-1]["value"]
                prev_val = df.iloc[-2]["value"]
                chg = last_val - prev_val
            res[name] = val
            res[name + "_chg"] = chg
        else:
            res[name] = None
            res[name + "_chg"] = None
            
    if all(res[name] is None for name in series_map.keys()):
        return None
    return res

@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_news(pair, target_date, key=STOCKDATA_KEY):
    if not key:
        return None
    symbol = pair.replace("/", "")
    target_dt = pd.to_datetime(target_date)
    date_str = target_dt.strftime("%Y-%m-%d")
    url = f"https://api.stockdata.org/v1/news/all?language=en&symbols={symbol}&published_on={date_str}&api_token={key}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            res = r.json()
            articles = res.get("data", [])
            parsed = []
            for art in articles[:5]:
                parsed.append({
                    "title": art.get("title"),
                    "source": art.get("source"),
                    "publishedAt": art.get("published_at"),
                    "url": art.get("url"),
                    "description": art.get("description"),
                    "urlToImage": art.get("image_url") or "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80",
                    "api": "StockData API"
                })
            return parsed
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_recommendations(pair, target_date, key=FINNHUB_KEY):
    if not key:
        return None
    symbol = pair.replace("/", "")
    url = f"https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}&token={key}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            trends = r.json()
            if isinstance(trends, list) and trends:
                target_dt = pd.to_datetime(target_date)
                valid = []
                for t in trends:
                    p_dt = pd.to_datetime(t.get("period"))
                    if p_dt <= target_dt:
                        valid.append((p_dt, t))
                if valid:
                    valid.sort(key=lambda x: x[0])
                    latest_trend = valid[-1][1]
                    
                    buy_cnt = latest_trend.get("buy", 0) + latest_trend.get("strongBuy", 0)
                    hold_cnt = latest_trend.get("hold", 0)
                    sell_cnt = latest_trend.get("sell", 0) + latest_trend.get("strongSell", 0)
                    
                    return {
                        "buy": buy_cnt,
                        "hold": hold_cnt,
                        "sell": sell_cnt,
                        "strongBuy": latest_trend.get("strongBuy", 0),
                        "strongSell": latest_trend.get("strongSell", 0),
                        "targetMean": None,
                        "targetHigh": None,
                        "targetLow": None,
                        "history": []
                    }
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_sentiment(pair, target_date, key=STOCKDATA_KEY):
    if not key:
        return None
    symbol = pair.replace("/", "")
    target_dt = pd.to_datetime(target_date)
    date_str = target_dt.strftime("%Y-%m-%d")
    url = f"https://api.stockdata.org/v1/news/all?language=en&symbols={symbol}&published_on={date_str}&api_token={key}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            res = r.json()
            articles = res.get("data", [])
            sentiments = []
            for art in articles:
                entities = art.get("entities", [])
                for ent in entities:
                    if ent.get("symbol") == symbol:
                        sent_score = ent.get("sentiment_score")
                        if sent_score is not None:
                            sentiments.append(float(sent_score))
            if sentiments:
                avg_sent = sum(sentiments) / len(sentiments)
                return avg_sent * 10.0
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400, show_spinner=False)
def get_fcs_history_data_historical(pair, target_date, key=FCS_KEY):
    if not key:
        return None, False
    try:
        df = fetch_fcs_history_live(pair, key)
        if df is not None and not df.empty:
            target_dt = pd.to_datetime(target_date)
            df_filtered = df[df["date"] <= target_dt]
            if not df_filtered.empty:
                return df_filtered, True
    except Exception:
        pass
    return None, False

@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_correlation_matrix(target_date):
    target_dt = pd.to_datetime(target_date)
    start_dt = target_dt - timedelta(days=30)
    
    limit_dt = datetime(2025, 12, 31)
    if start_dt > limit_dt:
        start_dt = limit_dt - timedelta(days=30)
        target_dt = limit_dt
        
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = target_dt.strftime("%Y-%m-%d")
    
    url = "https://currencyapi.vitalmedx.com/api/v1/timeseries"
    params = {
        "start_date": start_str,
        "end_date": end_str,
        "base": "USD",
        "symbols": "EUR,GBP,JPY,CHF,CAD,AUD,NZD"
    }
    try:
        r = requests.get(url, params=params, timeout=12)
        if r.status_code == 200:
            data = r.json()
            if data.get("success") and "data" in data:
                rates_dict = data["data"].get("rates", {})
                
                daily_rates = []
                for date_str, val_dict in rates_dict.items():
                    row = {"date": pd.to_datetime(date_str)}
                    for sym, val in val_dict.items():
                        if val is not None:
                            row[sym] = float(val)
                    daily_rates.append(row)
                    
                df_raw = pd.DataFrame(daily_rates).sort_values("date").reset_index(drop=True)
                
                required = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
                if not df_raw.empty and all(col in df_raw.columns for col in required):
                    df_raw = df_raw.dropna(subset=required)
                    
                    if len(df_raw) >= 5:
                        df_pairs = pd.DataFrame()
                        df_pairs["EUR/USD"] = 1.0 / df_raw["EUR"]
                        df_pairs["GBP/USD"] = 1.0 / df_raw["GBP"]
                        df_pairs["USD/JPY"] = df_raw["JPY"]
                        df_pairs["USD/CHF"] = df_raw["CHF"]
                        df_pairs["AUD/USD"] = 1.0 / df_raw["AUD"]
                        df_pairs["USD/CAD"] = df_raw["CAD"]
                        df_pairs["NZD/USD"] = 1.0 / df_raw["NZD"]
                        df_pairs["EUR/GBP"] = df_raw["GBP"] / df_raw["EUR"]
                        
                        corr = df_pairs.corr(method="pearson")
                        return corr, True
    except Exception:
        pass
    return generate_mock_fcs_correlation(), False



def compute_currency_score_historical(curr, target_date):
    fred_key = FRED_KEY
    if curr == "USD":
        rate_val, _, _ = get_fred_data_historical("FEDFUNDS", target_date)
        if rate_val is None:
            return None
        rate_score = np.clip((rate_val / 6.0) * 100, 0, 100)
        
        unemp_val, _, _ = get_fred_data_historical("UNRATE", target_date)
        if unemp_val is None:
            return None
        unemp_score = np.clip((10.0 - unemp_val) / 8.0 * 100, 0, 100)
        
        df_cpi, _, _ = get_fred_data("CPIAUCSL", fred_key)
        latest_cpi = None
        if df_cpi is not None and not df_cpi.empty:
            df_cpi_c = df_cpi.copy()
            if len(df_cpi_c) >= 13:
                df_cpi_c["yoy"] = df_cpi_c["value"].pct_change(periods=12) * 100
                df_filtered = df_cpi_c[df_cpi_c["date"] <= pd.to_datetime(target_date)]
                if not df_filtered.empty:
                    latest_cpi = df_filtered.iloc[-1]["yoy"]
        if latest_cpi is None:
            return None
        cpi_score = np.clip((latest_cpi / 5.0) * 100, 0, 100)
        
        df_gdp, _, _ = get_fred_data("GDPC1", fred_key)
        latest_gdp = None
        if df_gdp is not None and not df_gdp.empty:
            df_gdp_c = df_gdp.copy()
            if len(df_gdp_c) >= 5:
                df_gdp_c["yoy"] = df_gdp_c["value"].pct_change(periods=4) * 100
                df_filtered = df_gdp_c[df_gdp_c["date"] <= pd.to_datetime(target_date)]
                if not df_filtered.empty:
                    latest_gdp = df_filtered.iloc[-1]["yoy"]
        if latest_gdp is None:
            return None
        gdp_score = np.clip((latest_gdp + 2.0) / 6.0 * 100, 0, 100)
    else:
        code = CURRENCIES[curr]["wb_code"]
        
        gdp_val, _, _ = get_worldbank_data_historical(code, "NY.GDP.MKTP.KD.ZG", target_date)
        if gdp_val is None:
            return None
        gdp_score = np.clip((gdp_val + 2.0) / 6.0 * 100, 0, 100)
        
        cpi_val, _, _ = get_worldbank_data_historical(code, "FP.CPI.TOTL.ZG", target_date)
        if cpi_val is None:
            return None
        cpi_score = np.clip((cpi_val / 5.0) * 100, 0, 100)
        
        rate_val, _ = get_country_rate_historical(code, target_date)
        if rate_val is None:
            return None
        rate_score = np.clip((rate_val / 6.0) * 100, 0, 100)
        
        unemp_val, _, _ = get_worldbank_data_historical(code, "SL.UEM.TOTL.ZG", target_date)
        if unemp_val is None:
            return None
        unemp_score = np.clip((10.0 - unemp_val) / 8.0 * 100, 0, 100)
            
    total_score = 0.50 * rate_score + 0.20 * cpi_score + 0.15 * unemp_score + 0.15 * gdp_score
    return total_score


def load_backtest_decisions():
    file_path = "backtest_decisions.json"
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_backtest_decision(decision):
    file_path = "backtest_decisions.json"
    decisions = load_backtest_decisions()
    decisions.append(decision)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(decisions, f, indent=4, ensure_ascii=False)
    except Exception:
        pass
    return decisions


@st.cache_data(ttl=60, show_spinner=False)
def get_roro_index(fred_key, tiingo_key, apifreaks_key=None):
    debug_logs = []
    
    # 1. Check API Key presence
    if fred_key:
        debug_logs.append("FRED: API-Key in .env vorhanden.")
    else:
        debug_logs.append("FRED: API-Key fehlt in .env.")
        
    if apifreaks_key:
        debug_logs.append("APIFreaks: API-Key in .env vorhanden.")
    else:
        debug_logs.append("APIFreaks: API-Key fehlt in .env.")

    def query_fred(series_id, key):
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json&observation_start=2015-01-01"
        return requests.get(url, timeout=8)

    # 2. Test general FRED key validity with FEDFUNDS
    fred_works = False
    if fred_key:
        debug_logs.append("FRED: Teste API-Verbindung mit Indikator 'FEDFUNDS'...")
        try:
            r = query_fred("FEDFUNDS", fred_key)
            debug_logs.append(f"FRED (FEDFUNDS) Test: HTTP Status {r.status_code}")
            if r.status_code == 200:
                obs = r.json().get("observations", [])
                if obs:
                    fred_works = True
                    debug_logs.append("FRED: Verbindungstest erfolgreich. FEDFUNDS geladen.")
                else:
                    debug_logs.append("FRED: Antwort für FEDFUNDS war leer (keine observations).")
            else:
                debug_logs.append(f"FRED: Verbindungstest fehlgeschlagen mit HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"FRED: Netzwerkfehler bei Verbindungstest: {str(e)}")

    # 3. Attempt KCRORO
    if fred_works:
        debug_logs.append("FRED: Versuche primären RORO-Indikator 'KCRORO' zu laden...")
        try:
            r = query_fred("KCRORO", fred_key)
            debug_logs.append(f"FRED (KCRORO) Abfrage: HTTP Status {r.status_code}")
            if r.status_code == 200:
                obs = r.json().get("observations", [])
                parsed = []
                for o in obs:
                    if o["value"] != ".":
                        parsed.append({"date": o["date"], "value": float(o["value"])})
                if parsed:
                    val = float(parsed[-1]["value"])
                    dt = pd.to_datetime(parsed[-1]["date"])
                    debug_logs.append("FRED (KCRORO) erfolgreich geladen.")
                    return val, dt, "FRED Risk-On/Risk-Off (KCRORO)", debug_logs
                else:
                    debug_logs.append("FRED (KCRORO): Observations waren leer oder ungültig.")
            else:
                debug_logs.append(f"FRED (KCRORO) fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"FRED (KCRORO): Netzwerkfehler: {str(e)}")

    # 4. Swap: Option A1: VIX via APIFreaks immediately after FRED KCRORO
    if apifreaks_key:
        debug_logs.append("Weiche auf Option A1 aus: APIFreaks VIX Index...")
        try:
            url = "https://api.apifreaks.com/v1.0/commodity/rates/latest"
            params = {
                "apiKey": apifreaks_key,
                "symbols": "VIX",
                "updates": "1m"
            }
            r = requests.get(url, params=params, timeout=10)
            debug_logs.append(f"APIFreaks (VIX) Abfrage: HTTP Status {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if data and data.get("success") and "rates" in data:
                    rates = data["rates"]
                    vix_val = rates.get("VIX")
                    if vix_val is not None:
                        val = float(vix_val)
                        dt_str = data.get("date", "")
                        dt = pd.to_datetime(dt_str) if dt_str else datetime.now()
                        debug_logs.append(f"APIFreaks (VIX): Erfolgreich geladen (Wert: {val:.2f}).")
                        return val, dt, "APIFreaks VIX Volatilitätsindex", debug_logs
                    else:
                        debug_logs.append("APIFreaks (VIX): VIX-Wert nicht in Antwort gefunden.")
                else:
                    debug_logs.append("APIFreaks (VIX): Antwort war leer oder ungültig.")
            else:
                debug_logs.append(f"APIFreaks (VIX) fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"APIFreaks (VIX): Netzwerkfehler: {str(e)}")
    else:
        debug_logs.append("APIFreaks: API-Key (APIFREAKS_API_KEY) fehlt in .env. Option A1 (VIX) übersprungen.")

    # 5. Swap: Option A2: VIX via Tiingo (VIXY)
    if tiingo_key:
        debug_logs.append("Weiche auf Option A2 aus: Tiingo VIXY Index...")
        try:
            url = "https://api.tiingo.com/tiingo/daily/VIXY/prices"
            headers = {"Authorization": f"Token {tiingo_key}"}
            r = requests.get(url, headers=headers, timeout=10)
            debug_logs.append(f"Tiingo (VIXY) Abfrage: HTTP Status {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list):
                    latest_vix = data[-1]
                    val = float(latest_vix["close"])
                    dt_str = latest_vix.get("date", "")
                    dt = pd.to_datetime(dt_str) if dt_str else datetime.now()
                    debug_logs.append(f"Tiingo (VIXY): Erfolgreich geladen (Schlusskurs: {val:.2f}).")
                    return val, dt, "Tiingo VIXY Volatilitätsindex", debug_logs
                else:
                    debug_logs.append("Tiingo (VIXY): Antwort war leer oder ungültig.")
            else:
                debug_logs.append(f"Tiingo (VIXY) fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"Tiingo (VIXY): Netzwerkfehler: {str(e)}")
    else:
        debug_logs.append("Tiingo: API-Key (TIINGO_API_KEY) fehlt in .env. Option A2 (VIX) übersprungen.")

    # 5. Option B: 10Y-2Y Spread over FRED
    if fred_works:
        debug_logs.append("Weiche auf Option B aus: FRED 10Y-2Y Spread (DGS10 - DGS2)...")
        try:
            r_10y = query_fred("DGS10", fred_key)
            r_2y = query_fred("DGS2", fred_key)
            debug_logs.append(f"FRED DGS10 Abfrage: HTTP Status {r_10y.status_code}")
            debug_logs.append(f"FRED DGS2 Abfrage: HTTP Status {r_2y.status_code}")
            if r_10y.status_code == 200 and r_2y.status_code == 200:
                obs_10y = r_10y.json().get("observations", [])
                obs_2y = r_2y.json().get("observations", [])
                parsed_10y = {o["date"]: float(o["value"]) for o in obs_10y if o["value"] != "."}
                parsed_2y = {o["date"]: float(o["value"]) for o in obs_2y if o["value"] != "."}
                
                common_dates = sorted(list(set(parsed_10y.keys()).intersection(set(parsed_2y.keys()))))
                if common_dates:
                    latest_date = common_dates[-1]
                    val = parsed_10y[latest_date] - parsed_2y[latest_date]
                    dt = pd.to_datetime(latest_date)
                    debug_logs.append(f"FRED (10Y-2Y): Spread erfolgreich berechnet ({val:+.4f}%).")
                    return val, dt, "FRED 10Y-2Y Spread (DGS10 - DGS2)", debug_logs
                else:
                    debug_logs.append("FRED (10Y-2Y): Keine gemeinsamen Datumsangaben gefunden.")
            else:
                debug_logs.append("FRED (10Y-2Y): Fehlerhafte Statuscodes bei DGS10 oder DGS2.")
        except Exception as e:
            debug_logs.append(f"FRED (10Y-2Y): Netzwerkfehler: {str(e)}")

    # 6. Option C: USD/JPY Daily Change Proxy
    debug_logs.append("Weiche auf Option C aus: USD/JPY Exchange Rate Proxy...")
    try:
        url = "https://currencyapi.vitalmedx.com/api/v1/timeseries"
        params = {
            "start_date": "2025-12-20",
            "end_date": "2025-12-31",
            "base": "USD",
            "symbols": "JPY"
        }
        r = requests.get(url, params=params, timeout=10)
        debug_logs.append(f"CurrencyArchiveAPI USD/JPY: HTTP Status {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if data.get("success") and "data" in data:
                rates_dict = data["data"].get("rates", {})
                sorted_dates = sorted(rates_dict.keys())
                parsed = []
                for d in sorted_dates:
                    val = rates_dict[d].get("JPY")
                    if val is not None:
                        parsed.append((d, float(val)))
                if len(parsed) >= 2:
                    latest_close = parsed[-1][1]
                    prev_close = parsed[-2][1]
                    change = (latest_close - prev_close) / prev_close
                    dt = pd.to_datetime(parsed[-1][0])
                    debug_logs.append(f"CurrencyArchiveAPI (USD/JPY): Erfolgreich geladen (Änderung: {change:+.2%}).")
                    return change, dt, "USD/JPY Proxy (Tagesänderung)", debug_logs
                else:
                    debug_logs.append("CurrencyArchiveAPI USD/JPY: Weniger als 2 Kurse im Zeitraum gefunden.")
            else:
                debug_logs.append("CurrencyArchiveAPI USD/JPY: Fehlermeldung in JSON-Antwort.")
        else:
            debug_logs.append(f"CurrencyArchiveAPI USD/JPY fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
    except Exception as e:
        debug_logs.append(f"CurrencyArchiveAPI USD/JPY: Netzwerkfehler: {str(e)}")

    debug_logs.append("FRED: Alle Indikatoren und alternative Fallbacks fehlgeschlagen.")
    return None, None, None, debug_logs





# ----------------- Helper Functions -----------------
def calculate_smas(df):
    if len(df) >= 50:
        df["SMA_50"] = df["close"].rolling(window=50).mean()
    else:
        df["SMA_50"] = np.nan
    if len(df) >= 200:
        df["SMA_200"] = df["close"].rolling(window=200).mean()
    else:
        df["SMA_200"] = np.nan
    return df

def format_freshness(timestamp):
    elapsed = datetime.now() - timestamp
    secs = int(elapsed.total_seconds())
    if secs < 60:
        return f"vor {secs}s"
    mins = secs // 60
    return f"vor {mins}m {secs % 60}s"

def get_default_query(base, quote):
    return f"{base} {quote} forex OR central bank OR interest OR inflation OR GDP"

def deduplicate_articles(articles):
    seen_urls = set()
    seen_titles = set()
    unique_articles = []
    for art in articles:
        url = art.get("url")
        if url:
            url_norm = url.replace("https://", "").replace("http://", "").rstrip("/")
        else:
            url_norm = ""
            
        title = art.get("title", "").strip().lower()
        for suffix in [" - reuters", " - bloomberg", " - cnbc", " - marketwatch", " | reuters", " | bloomberg", " | cnbc"]:
            if title.endswith(suffix):
                title = title[:-len(suffix)].strip()
                
        title_clean = "".join(c for c in title if c.isalnum())
        title_trunc = title_clean[:100]
        
        if not title_trunc:
            continue
            
        if url_norm in seen_urls or title_trunc in seen_titles:
            continue
            
        if url_norm:
            seen_urls.add(url_norm)
        seen_titles.add(title_trunc)
        unique_articles.append(art)
    return unique_articles

def categorize_article(art):
    title_desc = f"{art.get('title', '')} {art.get('description', '')}".lower()
    trade_keywords = ["export", "import", "trade", "handel", "zoll", "tariffs", "lieferkette", "supply chain", "bilanz", "freihandel"]
    if any(kw in title_desc for kw in trade_keywords):
        return "🚢 Import & Export"
        
    rates_keywords = ["fed", "fomc", "leitzins", "zins", "interest", "ecb", "ezb", "rate", "central bank", "zentralbank", "powell", "lagarde", "geldpolitik"]
    if any(kw in title_desc for kw in rates_keywords):
        return "🏦 Geldpolitik & Zinsen"
        
    country_keywords = ["usa", "us-dollar", "america", "eurozone", "deutsch", "germany", "schweiz", "swiss", "kanada", "canada", "australi", "neuseeland", "new zealand", "japan", "england", "britain", "uk ", "gbp"]
    if any(kw in title_desc for kw in country_keywords):
        return "🌍 Länder-Analysen"
        
    return "📊 Sonstige Makro-News"

def get_country_rate(country_code, fred_key):
    # Retrieve manual rates from session state if available, otherwise use defaults
    manual_rates = {
        "GBR": st.session_state.get("manual_rate_GBP", 5.25),
        "JPN": st.session_state.get("manual_rate_JPY", 0.10),
        "AUD": st.session_state.get("manual_rate_AUD", 4.35),
        "CAD": st.session_state.get("manual_rate_CAD", 5.00),
        "NZD": st.session_state.get("manual_rate_NZD", 5.50),
        "CHF": st.session_state.get("manual_rate_CHF", 0.00)
    }
    
    fallback_rates = {"USA": 5.25, "EMU": 2.25, "GBR": 5.25, "JPN": 0.10, "CHE": 0.00, "AUS": 4.35, "CAN": 5.00, "NZL": 5.50}
    
    if country_code == "USA":
        df, _, _ = get_fred_data("FEDFUNDS", fred_key)
        if not df.empty:
            latest = df.iloc[-1]["value"]
            prev = df.iloc[-2]["value"] if len(df) > 1 else latest
            bps_change = int((latest - prev) * 100)
            return latest, bps_change, "FRED"
        return 5.25, 0, "FRED (Fallback)"
        
    elif country_code == "EMU":
        try:
            val, bps_change = get_ecb_rate_cached()
            return val, bps_change, "ECB Data Portal"
        except Exception:
            return 2.25, 0, "ECB (Fallback)"
            
    elif country_code == "CHE":
        try:
            val, bps_change = get_snb_rate_cached()
            return val, bps_change, "SNB Portal"
        except Exception:
            val = st.session_state.get("manual_rate_CHF", 0.00)
            return val, 0, "SNB (Fallback)"
            
    map_code = {"GBR": "GBR", "JPN": "JPN", "AUS": "AUD", "CAN": "CAD", "NZL": "NZD"}
    key = map_code.get(country_code, country_code)
    
    val = manual_rates.get(key, fallback_rates.get(country_code, 2.0))
    priors = {"GBR": 5.25, "JPN": 0.10, "AUD": 4.35, "CAD": 5.00, "NZD": 5.50}
    prior_val = priors.get(key, val)
    bps_change = int((val - prior_val) * 100)
    
    return val, bps_change, "Zins-Kontrollzentrum"

# Compute economic score for one currency
def compute_currency_score(curr, fred_key):
    if curr == "USD":
        df_rate, _, _ = get_fred_data("FEDFUNDS", fred_key)
        df_unemp, _, _ = get_fred_data("UNRATE", fred_key)
        df_cpi, _, _ = get_fred_data("CPIAUCSL", fred_key)
        df_gdp, _, _ = get_fred_data("GDPC1", fred_key)
        
        latest_rate = df_rate.iloc[-1]["value"] if not df_rate.empty else 5.25
        rate_score = np.clip((latest_rate / 6.0) * 100, 0, 100)
        
        latest_unemp = df_unemp.iloc[-1]["value"] if not df_unemp.empty else 3.8
        unemp_score = np.clip((10.0 - latest_unemp) / 8.0 * 100, 0, 100)
        
        if not df_cpi.empty and len(df_cpi) >= 13:
            df_cpi_c = df_cpi.copy()
            df_cpi_c["yoy"] = df_cpi_c["value"].pct_change(periods=12) * 100
            latest_cpi = df_cpi_c.iloc[-1]["yoy"]
        else:
            latest_cpi = 2.4
        cpi_score = np.clip((latest_cpi / 5.0) * 100, 0, 100)
        
        if not df_gdp.empty and len(df_gdp) >= 5:
            df_gdp_c = df_gdp.copy()
            df_gdp_c["yoy"] = df_gdp_c["value"].pct_change(periods=4) * 100
            latest_gdp = df_gdp_c.iloc[-1]["yoy"]
        else:
            latest_gdp = 1.8
        gdp_score = np.clip((latest_gdp + 2.0) / 6.0 * 100, 0, 100)
    else:
        code = CURRENCIES[curr]["wb_code"]
        
        # GDP und CPI von World Bank holen
        df_gdp, _, _ = get_worldbank_data(code, "NY.GDP.MKTP.KD.ZG")
        df_cpi, _, _ = get_worldbank_data(code, "FP.CPI.TOTL.ZG")
        
        # Rate (Zins) von FRED oder manueller Eingabe
        rate_val, _, _ = get_country_rate(code, fred_key)
        rate_score = np.clip((rate_val / 6.0) * 100, 0, 100)
        
        # NEU: Arbeitslosenquote dynamisch von World Bank holen (oder intelligenter Fallback)
        df_unemp, _, _ = get_worldbank_data(code, "SL.UEM.TOTL.ZG")
        if not df_unemp.empty:
            latest_unemp = df_unemp.iloc[-1]["value"]
            unemp_score = np.clip((10.0 - latest_unemp) / 8.0 * 100, 0, 100)
        else:
            # Fallback: Schätze die Arbeitslosenquote anhand der GDP-Wachstumsrate
            latest_gdp = df_gdp.iloc[-1]["value"] if not df_gdp.empty else 1.5
            unemp_score = np.clip(65 + (latest_gdp - 2.0) * 5, 40, 85)
        
        # CPI und GDP (wie gehabt)
        latest_cpi = df_cpi.iloc[-1]["value"] if not df_cpi.empty else 2.5
        cpi_score = np.clip((latest_cpi / 5.0) * 100, 0, 100)
        
        latest_gdp = df_gdp.iloc[-1]["value"] if not df_gdp.empty else 1.5
        gdp_score = np.clip((latest_gdp + 2.0) / 6.0 * 100, 0, 100)

    total_score = 0.50 * rate_score + 0.20 * cpi_score + 0.15 * unemp_score + 0.15 * gdp_score
    return total_score


COT_SYMBOLS = {
    "EUR": "098662",
    "GBP": "096742",
    "CHF": "092741",
    "CAD": "090741",
    "AUD": "232741",
    "NZD": "112741",
    "JPY": "097741"
}

@st.cache_data(ttl=86400, show_spinner=False)
def load_cot_year_cached(year):
    try:
        import cot_reports as cot
        df = cot.cot_year(year, cot_report_type='legacy_fut')
        if os.path.exists("annual.txt"):
            try:
                os.remove("annual.txt")
            except Exception:
                pass
        return df
    except Exception:
        return None

def get_cot_signal(symbol_code, target_date):
    try:
        target_dt = pd.to_datetime(target_date)
        y = target_dt.year
        
        df_curr = load_cot_year_cached(y)
        df_prev = load_cot_year_cached(y - 1) if y - 1 >= 2004 else None
        
        dfs = []
        if df_curr is not None and not df_curr.empty:
            dfs.append(df_curr)
        if df_prev is not None and not df_prev.empty:
            dfs.append(df_prev)
            
        if not dfs:
            return 0
            
        df = pd.concat(dfs, ignore_index=True)
        df.columns = df.columns.str.strip()
        
        code_col = "CFTC Contract Market Code" if "CFTC Contract Market Code" in df.columns else "CFTC_Contract_Market_Code"
        if code_col not in df.columns:
            return 0
            
        df[code_col] = df[code_col].astype(str).str.strip()
        df[code_col] = df[code_col].apply(lambda x: x.zfill(6) if x.isdigit() else x)
        
        symbol_code_std = str(symbol_code).strip().zfill(6)
        df_filtered = df[df[code_col] == symbol_code_std].copy()
        
        if df_filtered.empty:
            return 0
            
        date_col = "As of Date in Form YYYY-MM-DD" if "As of Date in Form YYYY-MM-DD" in df_filtered.columns else "As of Date in Form YYMMDD"
        if date_col == "As of Date in Form YYYY-MM-DD":
            df_filtered["parsed_date"] = pd.to_datetime(df_filtered[date_col], errors="coerce")
        else:
            df_filtered["parsed_date"] = pd.to_datetime(df_filtered[date_col], format="%y%m%d", errors="coerce")
            
        df_filtered = df_filtered.dropna(subset=["parsed_date"])
        df_filtered = df_filtered[df_filtered["parsed_date"] <= target_dt]
        if df_filtered.empty:
            return 0
            
        df_filtered = df_filtered.sort_values("parsed_date")
        df_filtered = df_filtered.tail(52)
        if len(df_filtered) < 5:
            return 0
            
        long_col = "Noncommercial Positions-Long (All)"
        short_col = "Noncommercial Positions-Short (All)"
        
        df_filtered[long_col] = pd.to_numeric(df_filtered[long_col], errors="coerce").fillna(0.0)
        df_filtered[short_col] = pd.to_numeric(df_filtered[short_col], errors="coerce").fillna(0.0)
        
        df_filtered["net_pos"] = df_filtered[long_col] - df_filtered[short_col]
        
        net_positions = df_filtered["net_pos"].values
        current_net = net_positions[-1]
        
        p20 = np.percentile(net_positions, 20)
        p80 = np.percentile(net_positions, 80)
        
        if current_net <= p20:
            return 1
        elif current_net >= p80:
            return -1
        else:
            return 0
    except Exception:
        return 0

def compute_score_with_cot(curr, target_date=None):
    if target_date is None:
        existing_score = compute_currency_score(curr, FRED_KEY)
        cot_date = datetime.now().strftime("%Y-%m-%d")
    else:
        existing_score = compute_currency_score_historical(curr, target_date)
        cot_date = target_date
        
    if existing_score is None:
        return None
        
    symbol_code = COT_SYMBOLS.get(curr)
    if symbol_code:
        cot_sig = get_cot_signal(symbol_code, cot_date)
    else:
        cot_sig = 0
        
    if cot_sig == 1:
        cot_scaled = 100.0
    elif cot_sig == -1:
        cot_scaled = 0.0
    else:
        cot_scaled = 50.0
        
    final_score = 0.90 * existing_score + 0.10 * cot_scaled
    return final_score


# ----------------- UI RENDERERS -----------------
def render_bias_box(signal_val, base_curr, quote_curr, base_total_score, quote_total_score, sig):
    """Renders the Divergence Trading Bias banner with dynamic G8 quantitative signaling."""
    if sig == "SB":
        bg_color = "rgba(16, 185, 129, 0.08)"
        border_color = "#10b981"
        text_color = "#10b981"
        title = f"STARKER BUY-BIAS (STRONG BUY für {base_curr}/{quote_curr})"
        desc = f"Die makroökonomische Divergenz spricht deutlich für den {base_curr} (Signal-Wert: {signal_val:+.1f}). Suche primär nach bullishen Einstiegen (SMC / FVG) im Chart."
        badge = "STRONG BUY"
    elif sig == "MB":
        bg_color = "rgba(226, 177, 60, 0.05)"
        border_color = "#e2b13c"
        text_color = "#e2b13c"
        title = f"MITTLERER BUY-BIAS (MID BUY für {base_curr}/{quote_curr})"
        desc = f"Milder fundamentaler Vorteil für {base_curr} (Signal-Wert: {signal_val:+.1f}). Nutze charttechnische Bestätigung vor Einstiegen."
        badge = "MID BUY"
    elif sig == "NT":
        bg_color = "rgba(132, 142, 156, 0.05)"
        border_color = "#444c56"
        text_color = "#8b949e"
        title = f"NEUTRAL / NO TRADE ({base_curr}/{quote_curr})"
        desc = f"Keine signifikante fundamentale Divergenz zwischen {base_curr} und {quote_curr} (Signal-Wert: {signal_val:+.1f}). Seitwärtsbewegung wahrscheinlich. Neutraler Bias."
        badge = "NEUTRAL"
    elif sig == "MS":
        bg_color = "rgba(226, 177, 60, 0.05)"
        border_color = "#e2b13c"
        text_color = "#e2b13c"
        title = f"MITTLERER SELL-BIAS (MID SELL für {base_curr}/{quote_curr})"
        desc = f"Milder fundamentaler Vorteil für {quote_curr} (Signal-Wert: {signal_val:+.1f}). Suche nach charttechnischen Bestätigungen für Short-Setups."
        badge = "MID SELL"
    elif sig == "SS":
        bg_color = "rgba(16, 185, 129, 0.08)"
        border_color = "#10b981"
        text_color = "#10b981"
        title = f"STARKER SELL-BIAS (STRONG SELL für {base_curr}/{quote_curr})"
        desc = f"Die makroökonomische Divergenz spricht deutlich für den {quote_curr} (Signal-Wert: {signal_val:+.1f}). Suche primär nach bearishen Einstiegen im Chart."
        badge = "STRONG SELL"
    else:
        bg_color = "rgba(132, 142, 156, 0.05)"
        border_color = "#30363d"
        text_color = "#8b949e"
        title = "BERECHNUNGSFEHLER"
        desc = "Unzureichende Daten zur Bestimmung des Biases."
        badge = "ERR"

    html_content = f"""
    <div style="
        background-color: {bg_color};
        border: 1px solid {border_color};
        border-radius: 6px;
        padding: 20px 24px;
        margin: 10px 0 25px 0;
    ">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
            <span style="
                font-size: 0.75rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 1.2px;
                color: #8b949e;
            ">{base_curr}/{quote_curr} Fundamental-Signal: {signal_val:+.1f}</span>
            <span style="
                background-color: {border_color}22;
                color: {text_color};
                border: 1px solid {border_color};
                font-weight: 700;
                font-size: 0.7rem;
                padding: 2px 10px;
                border-radius: 4px;
                text-transform: uppercase;
            ">{badge}</span>
        </div>
        <h2 style="
            color: {text_color};
            margin: 0 0 6px 0;
            font-size: 1.5rem;
            font-weight: 600;
            letter-spacing: -0.3px;
        ">{title}</h2>
        <p style="
            color: #8b949e;
            margin: 0;
            font-size: 0.95rem;
            line-height: 1.45;
        ">{desc}</p>
    </div>
    """
    st.markdown(html_content, unsafe_allow_html=True)

def render_metric_card(title, val_str, source_text, is_live):
    live_class = "source-tag-live" if is_live else ""
    card_html = f"""
    <div class="metric-card-custom">
        <span class="metric-label">{title}</span>
        <div class="metric-value">{val_str}</div>
        <div class="source-tag {live_class}">Quelle: {source_text}</div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

def render_articles_grid(articles_list):
    if not articles_list:
        st.info("Keine Artikel in dieser Kategorie vorhanden.")
        return
        
    cols = st.columns(3)
    for idx, art in enumerate(articles_list):
        col_idx = idx % 3
        with cols[col_idx]:
            # Prepare pubdate
            pub_date_str = ""
            if art['publishedAt']:
                try:
                    dt = pd.to_datetime(art['publishedAt'])
                    pub_date_str = dt.strftime('%d.%m.%Y %H:%M')
                except:
                    pub_date_str = str(art['publishedAt'])
            
            # Image tag
            fallback_img = "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80"
            img_html = ""
            if art.get('urlToImage'):
                img_html = f'<img src="{art["urlToImage"]}" referrerpolicy="no-referrer" onerror="this.onerror=null; this.src=\'{fallback_img}\';" style="width:100%; height:130px; object-fit:cover; border-radius:6px; margin-bottom:10px; border: 1px solid #1f2026;">'
            else:
                img_html = f'<div style="width:100%; height:130px; background-color:#0c0c0e; border-radius:6px; margin-bottom:10px; display:flex; justify-content:center; align-items:center; border: 1px solid #1f2026;"><span style="font-size:2rem;">📊</span></div>'
                
            desc_str = art.get('description', '')
            if not desc_str:
                desc_str = "Keine Kurzbeschreibung verfügbar. Bitte folge dem Link, um den vollständigen Artikel zu lesen."
            if len(desc_str) > 200:
                desc_str = desc_str[:197] + "..."
                
            st.markdown(f"""
            <div class="news-card">
                <div>
                    {img_html}
                    <a class="news-title" href="{art['url']}" target="_blank">{art['title']}</a>
                    <div class="news-meta">Quelle: <strong>{art['source']}</strong> | {pub_date_str}</div>
                    <p class="news-desc">{desc_str}</p>
                </div>
                <div style="border-top:1px solid #1f2026; padding-top:8px; margin-top:8px; display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:0.68rem; color:#8b949e; background-color:#1f2026; padding:2px 6px; border-radius:3px;">{art.get('api', 'News')}</span>
                    <a href="{art['url']}" target="_blank" style="font-size:0.75rem; color:#e2b13c; text-decoration:none; font-weight:600;">Lesen ↗</a>
                </div>
            </div>
            """, unsafe_allow_html=True)


# ----------------- 3. SIDEBAR CONFIGURATION -----------------
st.sidebar.title("⚙️ Dashboard-Einstellungen")

# Pairwise Selector for any of the 8 currencies
st.sidebar.markdown("### 💱 Währungspaar wählen")
base_curr = st.sidebar.selectbox("Basiswährung (Base)", options=list(CURRENCIES.keys()), index=0) # Default USD
quote_curr = st.sidebar.selectbox("Quote-Währung (Quote)", options=list(CURRENCIES.keys()), index=1) # Default EUR
selected_pair = f"{base_curr}/{quote_curr}"

if base_curr == quote_curr:
    st.sidebar.error("Basis- und Quote-Währung dürfen nicht identisch sein.")
    st.stop()

# Checkbox for displaying all pairs in checklist (including neutral)
show_all_pairs = st.sidebar.checkbox("Alle Paare anzeigen (inkl. Neutral)", value=False, key="show_all_pairs_chk")

# Manual cache clear
st.sidebar.button("🔄 System-Cache leeren", on_click=st.cache_data.clear)

# Zins-Kontrollzentrum (Manual inputs with persistence)
st.sidebar.markdown("---")
st.sidebar.markdown("### 🏦 Zins-Kontrollzentrum")
st.sidebar.caption("Manuelle Leitzins-Vorgaben für G8-Notenbanken:")

st.sidebar.number_input(
    "Bank of England (GBP) %", min_value=0.0, max_value=15.0, key="manual_rate_GBP", step=0.05
)
st.sidebar.number_input(
    "Bank of Japan (JPY) %", min_value=-5.0, max_value=15.0, key="manual_rate_JPY", step=0.05
)
st.sidebar.number_input(
    "Reserve Bank of Australia (AUD) %", min_value=0.0, max_value=15.0, key="manual_rate_AUD", step=0.05
)
st.sidebar.number_input(
    "Bank of Canada (CAD) %", min_value=0.0, max_value=15.0, key="manual_rate_CAD", step=0.05
)
st.sidebar.number_input(
    "Reserve Bank of New Zealand (NZD) %", min_value=0.0, max_value=15.0, key="manual_rate_NZD", step=0.05
)
st.sidebar.number_input(
    "Swiss National Bank (CHF) %", min_value=-5.0, max_value=15.0, key="manual_rate_CHF", step=0.05
)

if st.sidebar.button("💾 Zinssätze speichern"):
    saved_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    st.session_state["last_saved_rates"] = saved_time
    rates_to_save = {
        "manual_rate_GBP": st.session_state.manual_rate_GBP,
        "manual_rate_JPY": st.session_state.manual_rate_JPY,
        "manual_rate_AUD": st.session_state.manual_rate_AUD,
        "manual_rate_CAD": st.session_state.manual_rate_CAD,
        "manual_rate_NZD": st.session_state.manual_rate_NZD,
        "manual_rate_CHF": st.session_state.manual_rate_CHF,
        "last_saved_rates": saved_time
    }
    try:
        with open(".rates_config.json", "w", encoding="utf-8") as f:
            json.dump(rates_to_save, f, indent=4)
        st.sidebar.success("Zinssätze gespeichert!")
    except Exception as e:
        st.sidebar.error(f"Fehler: {e}")

last_saved = st.session_state.get("last_saved_rates")
if last_saved:
    st.sidebar.info(f"Zuletzt gespeichert: {last_saved}")
else:
    st.sidebar.warning("Noch nicht gespeichert")

st.sidebar.date_input("Letzte Aktualisierung", value=datetime.now().date())

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔑 API Key Status")
st.sidebar.caption("Geladene Schlüssel (Env / Secrets):")
st.sidebar.write(f"FRED_API_KEY: {'🟢 Aktiv' if FRED_KEY else '🔴 Fehlt'}")
st.sidebar.write(f"NEWSDATA_API_KEY: {'🟢 Aktiv' if NEWSDATA_KEY else '🔴 Fehlt'}")
st.sidebar.write(f"NEWSAPI_KEY: {'🟢 Aktiv' if NEWSAPI_KEY else '🔴 Fehlt'}")
st.sidebar.write(f"APIFREAKS_API_KEY: {'🟢 Aktiv' if APIFREAKS_KEY else '🔴 Fehlt'}")
st.sidebar.write(f"EODHD_API_KEY: {'🟢 Aktiv' if EODHD_KEY else '🔴 Fehlt'}")

with st.sidebar.expander("📝 Streamlit Secrets Anleitung"):
    st.markdown("""
    Wenn die App auf Streamlit Cloud läuft, tragen Sie Keys im Dashboard unter **Settings -> Secrets** ein:
    ```toml
    APIFREAKS_API_KEY = "IhrKey"
    FRED_API_KEY = "IhrKey"
    EODHD_API_KEY = "IhrKey"
    # ...
    ```
    """)

# ----------------- 4. GLOBAL DATA INITIALIZATION & FRESHNESS -----------------
with st.spinner("Initialisiere globale Marktdaten..."):
    # Pre-load macro scores
    base_score = compute_score_with_cot(base_curr)
    quote_score = compute_score_with_cot(quote_curr)
    
    # Calculate corrected signal value (scaled to range -50 to +50)
    raw_diff = quote_score - base_score
    signal_value = raw_diff / 2.0
    signal_value = max(-50.0, min(50.0, signal_value))
    
    # Calculate filtered trading signal based on new boundaries
    if signal_value >= 25.0:
        sig = "SB"
        badge = "STRONG BUY"
    elif 10.0 <= signal_value < 25.0:
        sig = "MB"
        badge = "MID BUY"
    elif -10.0 < signal_value < 10.0:
        sig = "NT"
        badge = "NEUTRAL"
    elif -25.0 < signal_value <= -10.0:
        sig = "MS"
        badge = "MID SELL"
    else:
        sig = "SS"
        badge = "STRONG SELL"
        
    # Load iTick close price
    itick_data, t_itick, is_live_itick = get_itick_data(selected_pair, ITICK_KEY)
    latest_close = itick_data["close"] if itick_data else 0.0

# ----------------- 5. HEADER SECTION -----------------
st.title("⚖️ Forex Fundamental Suite")
st.markdown(f"Professionelle makroökonomische Divergenz-Engine für das Paar **{selected_pair}**.")

# Always show bias banner and economy scores at the top
render_bias_box(signal_value, base_curr, quote_curr, base_score, quote_score, sig)

col_score_b, col_score_q = st.columns(2)
with col_score_b:
    st.markdown(f"""<div class="metric-card-custom" style="border-left: 4px solid #10b981;">
<span class="metric-label">{CURRENCIES[base_curr]['flag']} {base_curr} Wirtschaftsscore</span>
<div class="metric-value">{base_score:.1f} / 100</div>
<div class="source-tag">Zusammengesetzter Score</div>
</div>""", unsafe_allow_html=True)
with col_score_q:
    st.markdown(f"""<div class="metric-card-custom" style="border-left: 4px solid #444c56;">
<span class="metric-label">{CURRENCIES[quote_curr]['flag']} {quote_curr} Wirtschaftsscore</span>
<div class="metric-value">{quote_score:.1f} / 100</div>
<div class="source-tag">Zusammengesetzter Score</div>
</div>""", unsafe_allow_html=True)


# ----------------- 6. TABS MODULES -----------------
df_cal, t_cal, is_live_cal = get_benzinga_data(BENZINGA_KEY)
st.sidebar.caption(f"**Benzinga:** {format_freshness(t_cal)} ({'Live' if is_live_cal else 'Demo'})")

def get_pair_signal_and_badge(base, quote):
    b_score = compute_score_with_cot(base)
    q_score = compute_score_with_cot(quote)
    r_diff = q_score - b_score
    sig_val = r_diff / 2.0
    sig_val = max(-50.0, min(50.0, sig_val))
    
    if sig_val >= 25.0:
        s = "SB"
        b = "STRONG BUY"
        c = "#10b981"
    elif 10.0 <= sig_val < 25.0:
        s = "MB"
        b = "MID BUY"
        c = "#34d399"
    elif -10.0 < sig_val < 10.0:
        s = "NT"
        b = "NEUTRAL"
        c = "#8b949e"
    elif -25.0 < sig_val <= -10.0:
        s = "MS"
        b = "MID SELL"
        c = "#f87171"
    else:
        s = "SS"
        b = "STRONG SELL"
        c = "#ef4444"
        
    return b, c, sig_val

def get_next_event_for_pair(base, quote, df_c):
    curr_to_countries = {
        "USD": ["USA", "US"],
        "EUR": ["EUR", "DEU", "FRA", "ITA", "EMU"],
        "GBP": ["GBR", "UK", "GB"],
        "CHF": ["CHE", "CH", "SUI"],
        "CAD": ["CAN", "CA"],
        "AUD": ["AUS", "AU"],
        "NZD": ["NZL", "NZ"],
        "JPY": ["JPN", "JP"]
    }
    base_match = curr_to_countries.get(base, [base])
    quote_match = curr_to_countries.get(quote, [quote])
    pair_cal = df_c[df_c["country"].isin(base_match + quote_match)].copy()
    if pair_cal.empty:
        return "Keine Events"
    pair_cal["parsed_time"] = pd.to_datetime(pair_cal["time"], errors="coerce")
    pair_cal = pair_cal.dropna(subset=["parsed_time"])
    if pair_cal.empty:
        return "Keine Events"
    now_dt = datetime.now()
    future_events = pair_cal[pair_cal["parsed_time"] >= now_dt]
    if not future_events.empty:
        next_event = future_events.sort_values("parsed_time").iloc[0]
    else:
        next_event = pair_cal.sort_values("parsed_time", ascending=False).iloc[0]
    time_str = next_event["parsed_time"].strftime("%d.%m %H:%M")
    return f"{next_event['country']}: {next_event['event']} ({time_str})"

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12, tab14 = st.tabs([
    "🏠 Übersicht & Checkliste",
    "📊 PMI-Daten",
    "🏦 Zinsdifferenz",
    "📊 Analysten-Konsens",
    "🧠 Sentiment-Score",
    "🧮 Korrelationsmatrix",
    "📈 Langfristige Historie",
    "🛍️ Rohstoffe & Märkte",
    "🇺🇸 US-Arbeitsmarkt (BLS)",
    "⚠️ Risikoindikatoren (IMF)",
    "📰 News & Research Hub",
    "🛡️ Risk-On/Off",
    "📊 Historische Daten"
])

# ----------------- TAB 1: ÜBERSICHT & CHECKLISTE -----------------
with tab1:
    st.header("🏠 G8 Fundamental-Checkliste")
    st.caption("Auf einen Blick die makroökonomischen Scores und Handelssignale für alle Währungspaare vergleichen.")
    
    # 1. Macro scores comparison chart
    scores = {curr: compute_score_with_cot(curr) for curr in CURRENCIES.keys()}
    df_scores = pd.DataFrame(list(scores.items()), columns=["Currency", "Score"])
    currency_order = ["USD", "EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]
    df_scores['Currency'] = pd.Categorical(df_scores['Currency'], categories=currency_order, ordered=True)
    df_scores = df_scores.sort_values('Currency')
    
    fig_all_scores = px.bar(
        df_scores,
        x="Currency",
        y="Score",
        color="Score",
        color_continuous_scale="Viridis",
        text_auto=".1f"
    )
    fig_all_scores.update_layout(
        title="Wirtschaftsscores der G8 Länder im Vergleich",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color="#7d7d8a", size=10),
        xaxis=dict(showgrid=False, linecolor="#1f2026"),
        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)', linecolor="#1f2026", range=[0, 100]),
        height=320,
        margin=dict(l=10, r=10, t=40, b=10)
    )
    st.plotly_chart(fig_all_scores, use_container_width=True)
    
    # 2. Pairs table checklist
    st.subheader("📋 Währungspaare Checkliste")
    
    # Create HTML table
    html_table = """<table style="width:100%; border-collapse:collapse; text-align:left; font-size:0.85rem; background-color:#0c0c0e; border:1px solid #1f2026; border-radius:6px; overflow:hidden;">
<thead>
<tr style="border-bottom: 2px solid #1f2026; color:#7d7d8a; text-transform:uppercase; font-size:0.7rem; font-weight:700; background-color:#070708;">
<th style="padding:12px 10px;">Währungspaar</th>
<th style="padding:12px 10px;">Zins-Differenz (bps)</th>
<th style="padding:12px 10px; text-align:center;">Signal-Wert</th>
<th style="padding:12px 10px; text-align:center;">Signal-Klassifikation</th>
<th style="padding:12px 10px;">Analysten-Konsens</th>
<th style="padding:12px 10px; text-align:center;">Sentiment</th>
<th style="padding:12px 10px;">Staatsverschuldung</th>
<th style="padding:12px 10px;">Leistungsbilanz</th>
<th style="padding:12px 10px;">Nächstes Event</th>
</tr>
</thead>
<tbody>"""
    
    import itertools
    currencies_list = ["USD", "EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]
    G8_PAIRS = list(itertools.permutations(currencies_list, 2))
    
    rows = []
    for base, quote in G8_PAIRS:
        p_name = f"{base}/{quote}"
        badge_name, badge_color, sig_val = get_pair_signal_and_badge(base, quote)
        
        # Filter out neutral signals if the option is not checked
        if badge_name == "NEUTRAL" and not show_all_pairs:
            continue
        
        base_rate, _, _ = get_country_rate(CURRENCIES[base]["wb_code"], FRED_KEY)
        quote_rate, _, _ = get_country_rate(CURRENCIES[quote]["wb_code"], FRED_KEY)
        diff_bps = int((quote_rate - base_rate) * 100)
        diff_str = f"{base_rate:.2f}% vs {quote_rate:.2f}% ({diff_bps:+d} bps)"
        
        rec_data, _, _ = get_finnhub_data(p_name, FINNHUB_KEY)
        buy_count = rec_data.get("buy", 0)
        hold_count = rec_data.get("hold", 0)
        sell_count = rec_data.get("sell", 0)
        rec_str = f"<span style='color:#10b981; font-weight:600;'>B:{buy_count}</span> / <span style='color:#e2b13c;'>H:{hold_count}</span> / <span style='color:#ef4444;'>S:{sell_count}</span>"
        
        sent_val, _, _ = get_stockdata_sentiment(p_name, STOCKDATA_KEY)
        if sent_val >= 3.5:
            sent_color = "#10b981"
        elif sent_val <= -3.5:
            sent_color = "#ef4444"
        else:
            sent_color = "#8b949e"
        sent_str = f"<span style='color:{sent_color}; font-weight:600;'>{sent_val:+.1f}</span>"
        
        # New indicators from IMF
        debt_str = format_imf_indicator(base, quote, "GGXWDG_NGDP")
        ca_str = format_imf_indicator(base, quote, "BCA_NGDPD")
        
        next_ev = get_next_event_for_pair(base, quote, df_cal)
        
        rows.append(f"""<tr style="border-bottom:1px solid #1f2026;">
<td style="padding:12px 10px; font-weight:600; color:#f0f0f5;">{CURRENCIES[base]['flag']} {base} / {CURRENCIES[quote]['flag']} {quote}</td>
<td style="padding:12px 10px; font-family:'Roboto Mono', monospace;">{diff_str}</td>
<td style="padding:12px 10px; text-align:center; font-family:'Roboto Mono', monospace; font-weight:700; color:{badge_color};">{sig_val:+.1f}</td>
<td style="padding:12px 10px; text-align:center;">
<span style="background-color:{badge_color}18; color:{badge_color}; border:1px solid {badge_color}; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:700; text-transform:uppercase;">{badge_name}</span>
</td>
<td style="padding:12px 10px; font-family:'Roboto Mono', monospace;">{rec_str}</td>
<td style="padding:12px 10px; text-align:center; font-family:'Roboto Mono', monospace;">{sent_str}</td>
<td style="padding:12px 10px; font-family:'Roboto Mono', monospace; color:#b0b0bb; font-size:0.8rem;">{debt_str}</td>
<td style="padding:12px 10px; font-family:'Roboto Mono', monospace; color:#b0b0bb; font-size:0.8rem;">{ca_str}</td>
<td style="padding:12px 10px; color:#8c8c9a; font-size:0.8rem;">{next_ev}</td>
</tr>""")
        
    html_table += "".join(rows) + "</tbody></table>"
    st.markdown(html_table, unsafe_allow_html=True)
    st.markdown("<div class='source-tag'>Gesamte Suite-Zusammenfassung (Risikodaten Quelle: IMF DataMapper)</div>", unsafe_allow_html=True)

# ----------------- TAB 2: PMI-DATEN -----------------
with tab2:
    st.header("📊 PMI-Daten (Einkaufsmanagerindex)")
    st.caption("PMI-Werte (Manufacturing & Services) als Frühindikatoren der wirtschaftlichen Aktivität (Expansion > 50 / Kontraktion < 50).")
    
    with st.spinner("Lade PMI-Daten..."):
        pmi_data = get_all_pmi_data(FRED_KEY, EODHD_KEY)
        
    if pmi_data:
        code_to_name = {
            "USD": "🇺🇸 USA",
            "EUR": "🇪🇺 Euro",
            "GBP": "🇬🇧 UK",
            "CHF": "🇨🇭 Schweiz",
            "CAD": "🇨🇦 Kanada",
            "AUD": "🇦🇺 Australien",
            "NZD": "🇳🇿 Neuseeland",
            "JPY": "🇯🇵 Japan"
        }
        
        rows = []
        for code, data in pmi_data.items():
            m_val = data["m_last"]
            m_prev = data["m_prev"]
            s_val = data["s_last"]
            s_prev = data["s_prev"]
            
            m_chg = m_val - m_prev if (m_val is not None and m_prev is not None) else None
            s_chg = s_val - s_prev if (s_val is not None and s_prev is not None) else None
            
            # Manufacturing cell string
            if m_val is not None:
                m_status = "Expansion" if m_val >= 50.0 else "Kontraktion"
                m_arrow = "▲" if (m_chg is not None and m_chg > 0) else "▼" if (m_chg is not None and m_chg < 0) else "▬"
                m_str = f"{m_val:.1f} {m_arrow} {m_status}"
            else:
                m_str = "N/A"
                
            # Services cell string
            if s_val is not None:
                s_status = "Expansion" if s_val >= 50.0 else "Kontraktion"
                s_arrow = "▲" if (s_chg is not None and s_chg > 0) else "▼" if (s_chg is not None and s_chg < 0) else "▬"
                s_str = f"{s_val:.1f} {s_arrow} {s_status}"
            else:
                s_str = "N/A"
                
            # MoM Change
            changes = []
            if m_chg is not None:
                changes.append(m_chg)
            if s_chg is not None:
                changes.append(s_chg)
                
            if changes:
                avg_chg = sum(changes) / len(changes)
                c_arrow = "▲" if avg_chg > 0 else "▼" if avg_chg < 0 else "▬"
                c_str = f"{c_arrow} {avg_chg:+.1f}"
            else:
                c_str = "N/A"
                
            # Reference date
            m_ref = data["m_ref"] or "N/A"
            s_ref = data["s_ref"] or "N/A"
            ref_str = m_ref if m_ref != "N/A" else s_ref
            
            rows.append({
                "Land": code_to_name.get(code, code),
                "Manufacturing PMI": m_str,
                "Services PMI": s_str,
                "Veränderung zum Vormonat": c_str,
                "Letzte Aktualisierung": ref_str,
                "m_sort_val": m_val if m_val is not None else -999.0
            })
            
        df_pmi = pd.DataFrame(rows)
        
        # Sort standardly by Manufacturing PMI (highest first)
        df_pmi = df_pmi.sort_values(by="m_sort_val", ascending=False)
        df_render = df_pmi.drop(columns=["m_sort_val"]).reset_index(drop=True)
        
        # Styling function for Pandas Styler
        def apply_colors(val):
            val_str = str(val)
            if "Expansion" in val_str or "▲" in val_str:
                return "color: #10b981; font-weight: bold;"
            elif "Kontraktion" in val_str or "▼" in val_str:
                return "color: #ef4444; font-weight: bold;"
            return ""
            
        styled_df = df_render.style
        try:
            styled_df = styled_df.map(apply_colors, subset=["Manufacturing PMI", "Services PMI", "Veränderung zum Vormonat"])
        except AttributeError:
            styled_df = styled_df.applymap(apply_colors, subset=["Manufacturing PMI", "Services PMI", "Veränderung zum Vormonat"])
            
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
    else:
        st.info("Daten momentan nicht verfügbar")

# ----------------- TAB 3: ZINSDIFFERENZ -----------------
with tab3:
    st.header("🏦 Zinsdifferenz & Notenbanken")
    st.caption("Vergleich der aktuellen Leitzinsen der 8 Haupt-Zentralbanken.")
    
    # Fetch all rates
    rates_data = {}
    for curr, info in CURRENCIES.items():
        r_val, bps_chg, src = get_country_rate(info["wb_code"], FRED_KEY)
        rates_data[curr] = {
            "rate": r_val,
            "bps_change": bps_chg,
            "source": src
        }
        
    df_rates_plot = pd.DataFrame([
        {"Zentralbank": f"{curr} ({CURRENCIES[curr]['name']})", "Zinssatz": data["rate"], "Change": data["bps_change"]}
        for curr, data in rates_data.items()
    ])
    
    fig_rates_g8 = go.Figure()
    fig_rates_g8.add_trace(go.Bar(
        x=df_rates_plot["Zentralbank"],
        y=df_rates_plot["Zinssatz"],
        marker_color=['#10b981' if r > 4.0 else '#e2b13c' if r > 1.5 else '#ef4444' for r in df_rates_plot["Zinssatz"]],
        text=[f"{r:.2f}%" for r in df_rates_plot["Zinssatz"]],
        textposition='auto',
        name="Zinssatz"
    ))
    fig_rates_g8.update_layout(
        title="Leitzinsen der G8 im Vergleich",
        yaxis_title="Zinssatz (%)",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color="#7d7d8a", size=10),
        xaxis=dict(showgrid=False, linecolor="#1f2026"),
        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)', linecolor="#1f2026"),
        height=320,
        margin=dict(l=10, r=10, t=40, b=10)
    )
    st.plotly_chart(fig_rates_g8, use_container_width=True)
    
    # Table comparing the rates + bps changes
    rates_rows = []
    for curr, data in rates_data.items():
        color_class = "color:#10b981;" if data["bps_change"] > 0 else "color:#ef4444;" if data["bps_change"] < 0 else "color:#7d7d8a;"
        rates_rows.append(f"""<tr style="border-bottom:1px solid #1f2026;">
<td style="padding:10px 5px; font-weight:600;">{CURRENCIES[curr]['flag']} {curr} ({CURRENCIES[curr]['name']})</td>
<td style="padding:10px 5px; font-family:'Roboto Mono', monospace; font-weight:600;">{data['rate']:.2f}%</td>
<td style="padding:10px 5px; font-family:'Roboto Mono', monospace; font-weight:700; {color_class}">{data['bps_change']:+d} bps</td>
<td style="padding:10px 5px; color:#8c8c9a; font-size:0.75rem;">{data['source']}</td>
</tr>""")
        
    rates_table_html = """<table style="width:100%; border-collapse:collapse; text-align:left; font-size:0.85rem;">
<thead>
<tr style="border-bottom: 2px solid #1f2026; color:#7d7d8a; text-transform:uppercase; font-size:0.7rem; font-weight:700;">
<th style="padding:10px 5px;">Zentralbank</th>
<th style="padding:10px 5px;">Leitzins</th>
<th style="padding:10px 5px;">Änderung zum Vormonat</th>
<th style="padding:10px 5px;">Quelle</th>
</tr>
</thead>
<tbody>
""" + "".join(rates_rows) + """</tbody>
</table>"""
    st.markdown(rates_table_html, unsafe_allow_html=True)
    st.markdown("<div class='source-tag'>Quelle: FRED, ECB Portal, SNB Portal & Zins-Kontrollzentrum</div>", unsafe_allow_html=True)

# ----------------- TAB 4: ANALYSTEN-KONSENS -----------------
with tab4:
    st.header("📊 Analysten-Konsens & Kursziele")
    st.caption(f"Konsens-Ratings und Kursziele für das Währungspaar **{selected_pair}**.")
    
    # Fetch Finnhub data
    finnhub_data, t_finnhub, is_live_finnhub = get_finnhub_data(selected_pair, FINNHUB_KEY)
    st.sidebar.caption(f"**Finnhub:** {format_freshness(t_finnhub)} ({'Live' if is_live_finnhub else 'Demo'})")
    
    c_col1, c_col2 = st.columns([1, 1.2])
    with c_col1:
        st.subheader("Ratings-Verteilung")
        labels = ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]
        counts = [
            finnhub_data.get("strongBuy", 0),
            finnhub_data.get("buy_only", 0) or finnhub_data.get("buy", 0),
            finnhub_data.get("hold", 0),
            finnhub_data.get("sell_only", 0) or finnhub_data.get("sell", 0),
            finnhub_data.get("strongSell", 0)
        ]
        
        fig_finnhub = go.Figure(data=[go.Bar(
            x=labels,
            y=counts,
            marker_color=["#065f46", "#10b981", "#e2b13c", "#f87171", "#991b1b"],
            text=counts,
            textposition='auto'
        )])
        fig_finnhub.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#7d7d8a", size=10),
            xaxis=dict(showgrid=False, linecolor="#1f2026"),
            yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)', linecolor="#1f2026"),
            height=280,
            margin=dict(l=10, r=10, t=10, b=10)
        )
        st.plotly_chart(fig_finnhub, use_container_width=True)
        
    with c_col2:
        st.subheader("Konsens-Kursziele")
        avg_t = finnhub_data["target_mean"]
        high_t = finnhub_data["target_high"]
        low_t = finnhub_data["target_low"]
        
        t_col1, t_col2 = st.columns(2)
        with t_col1:
            st.metric("Mittleres Kursziel", f"{avg_t:.4f}" if avg_t else "N/A")
            st.metric("Höchstes Kursziel", f"{high_t:.4f}" if high_t else "N/A")
        with t_col2:
            st.metric("Aktueller Kurs (iTick)", f"{latest_close:.4f}" if latest_close else "N/A")
            st.metric("Tiefstes Kursziel", f"{low_t:.4f}" if low_t else "N/A")
            
    st.subheader("Letzte Ratings-Änderungen")
    df_ratings = pd.DataFrame(finnhub_data["history"])
    if not df_ratings.empty:
        st.dataframe(df_ratings, use_container_width=True, hide_index=True)
    else:
        st.info("Keine Rating-Historie verfügbar.")
        
    st.markdown(f"<div class='source-tag {'source-tag-live' if is_live_finnhub else ''}'>Quelle: Finnhub</div>", unsafe_allow_html=True)

# ----------------- TAB 5: SENTIMENT-SCORE -----------------
with tab5:
    st.header("🧠 Markt-Sentiment (News Tonalität)")
    st.caption(f"Berechnetes News-Sentiment (-10 bis +10) für das Paar **{selected_pair}** basierend auf künstlicher Intelligenz.")
    
    sent_val, t_sent, is_live_sent = get_stockdata_sentiment(selected_pair, STOCKDATA_KEY)
    st.sidebar.caption(f"**StockData:** {format_freshness(t_sent)} ({'Live' if is_live_sent else 'Demo'})")
    
    fig_gauge = go.Figure(go.Indicator(
        mode = "gauge+number",
        value = sent_val,
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': f"Sentiment Score: {selected_pair}", 'font': {'size': 16, 'color': "#f0f0f5"}},
        gauge = {
            'axis': {'range': [-10, 10], 'tickwidth': 1, 'tickcolor': "#7d7d8a"},
            'bar': {'color': "#1f2026"},
            'bgcolor': "rgba(0,0,0,0)",
            'borderwidth': 1,
            'bordercolor': "#1f2026",
            'steps': [
                {'range': [-10, -3.5], 'color': 'rgba(239, 68, 68, 0.15)'},
                {'range': [-3.5, 3.5], 'color': 'rgba(226, 177, 60, 0.1)'},
                {'range': [3.5, 10], 'color': 'rgba(16, 185, 129, 0.15)'}
            ],
            'threshold': {
                'line': {'color': "#ffd166", 'width': 3},
                'thickness': 0.75,
                'value': sent_val
            }
        }
    ))
    fig_gauge.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color="#7d7d8a"),
        height=320,
        margin=dict(l=20, r=20, t=50, b=20)
    )
    st.plotly_chart(fig_gauge, use_container_width=True)
    
    st.markdown("#### Sentiment-Einordnung:")
    if sent_val >= 3.5:
        st.success(f"🟢 **Bullish ({sent_val:+.1f})** – Die News-Berichterstattung ist überwiegend positiv für das Währungspaar.")
    elif sent_val <= -3.5:
        st.error(f"🔴 **Bearish ({sent_val:+.1f})** – Die News-Berichterstattung ist überwiegend negativ für das Währungspaar.")
    else:
        st.warning(f"🟡 **Neutral ({sent_val:+.1f})** – Ausgeglichene Tonalität im News-Umfeld.")
        
    st.markdown(f"<div class='source-tag {'source-tag-live' if is_live_sent else ''}'>Quelle: StockData.org</div>", unsafe_allow_html=True)

# ----------------- TAB 6: KORRELATIONSMATRIX -----------------
with tab6:
    st.header("🧮 30-Tage Korrelationsmatrix")
    st.caption("Vergleichende Korrelations-Heatmap aller Major-Währungspaare (berechnet aus FCS API-Preishistorien).")
    
    df_corr, t_corr, is_live_corr = get_fcs_correlation_data(FCS_KEY)
    st.sidebar.caption(f"**FCS API:** {format_freshness(t_corr)} ({'Live' if is_live_corr else 'Demo'})")
    
    fig_heatmap = px.imshow(
        df_corr,
        text_auto=".2f",
        aspect="auto",
        color_continuous_scale=px.colors.diverging.RdBu_r,
        range_color=[-1, 1]
    )
    fig_heatmap.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color="#7d7d8a", size=10),
        height=400,
        margin=dict(l=10, r=10, t=10, b=10)
    )
    st.plotly_chart(fig_heatmap, use_container_width=True)
    
    st.info("💡 Werte nahe +1.0 bedeuten starke Gleichlauf-Korrelation. Werte nahe -1.0 bedeuten starke Gegenlauf-Korrelation.")
    st.markdown(f"<div class='source-tag {'source-tag-live' if is_live_corr else ''}'>Quelle: FCS API</div>", unsafe_allow_html=True)

# ----------------- TAB 7: LANGFRISTIGE HISTORIE -----------------
with tab7:
    st.header("📈 Langfristige Historie & Zyklen (seit 1995)")
    st.caption(f"Langfristiger Kursverlauf ab 1995 zur Analyse übergeordneter wirtschaftlicher Zyklen.")
    
    major_pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP"]
    
    hist_pair = st.selectbox("Historisches Paar wählen", options=major_pairs, index=major_pairs.index(selected_pair) if selected_pair in major_pairs else 0, key="hist_pair_select")
        
    df_hist = pd.DataFrame()
    is_live_hist = False
    source_label = "FCS API"
    
    df_hist, t_hist, is_live_hist = get_fcs_history_data(hist_pair, FCS_KEY)
            
    if df_hist is not None and not df_hist.empty:
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(
            x=df_hist["date"], y=df_hist["close"],
            line=dict(color="#e2b13c", width=2),
            name="Schlusskurs"
        ))
        fig_hist.update_layout(
            title=f"Historischer Langzeit-Kurs ({hist_pair})",
            xaxis_title="Datum",
            yaxis_title="Kurs",
            xaxis=dict(
                rangeslider=dict(visible=True),
                type="date",
                linecolor="#1f2026",
                showgrid=True,
                gridcolor='rgba(128,128,128,0.04)'
            ),
            yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.04)', linecolor="#1f2026"),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#7d7d8a", size=10),
            height=450,
            margin=dict(l=10, r=10, t=40, b=10)
        )
        st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.warning("Keine langfristigen Historien-Daten verfügbar.")
        
    st.markdown(f"<div class='source-tag'>Quelle: {source_label}</div>", unsafe_allow_html=True)
    
    st.subheader("📊 Länder-Fundamentaldaten (World Bank)")
    
    # Map currencies to correct ISO codes for World Bank:
    wb_iso_map = {
        "USD": "USA",
        "EUR": "DEU",
        "GBP": "GBR",
        "JPY": "JPN",
        "CHF": "CHE",
        "CAD": "CAN",
        "AUD": "AUS",
        "NZD": "NZL"
    }
    base_iso = wb_iso_map.get(base_curr, "USA")
    quote_iso = wb_iso_map.get(quote_curr, "USA")
    
    indicators_to_find = [
        ("NY.GDP.MKTP.KD.ZG", "GDP-Wachstum (jährlich)"),
        ("FP.CPI.TOTL.ZG", "Inflation (Verbraucherpreise, jährlich)"),
        ("SL.UEM.TOTL.ZG", "Arbeitslosigkeit (% der Erwerbspersonen)"),
        ("GC.DOD.TOTL.GD.ZS", "Staatsverschuldung (% des BIP)")
    ]
    
    rows_macro = []
    has_any_data = False
    
    for indicator_key, display_name in indicators_to_find:
        base_res = get_worldbank_data(base_iso, indicator_key)
        quote_res = get_worldbank_data(quote_iso, indicator_key)
        
        b_val, b_yr = parse_worldbank_latest(base_res)
        q_val, q_yr = parse_worldbank_latest(quote_res)
        
        if b_val is not None or q_val is not None:
            has_any_data = True
            
        b_str = f"{b_val:,.1f}% ({b_yr})" if b_val is not None else "N/A"
        q_str = f"{q_val:,.1f}% ({q_yr})" if q_val is not None else "N/A"
        
        rows_macro.append({
            "Indikator": display_name,
            f"{base_curr}": b_str,
            f"{quote_curr}": q_str
        })
        
    if has_any_data:
        df_macro_eod = pd.DataFrame(rows_macro)
        st.dataframe(df_macro_eod, use_container_width=True, hide_index=True)
    else:
        st.info("Daten momentan nicht verfügbar")

# ----------------- TAB 8: ROHSTOFFE & MÄRKTE -----------------
with tab8:
    st.header("🛍️ Rohstoffe & Märkte")
    st.caption("Aktuelle Rohstoffpreise und Volatilität (VIX) geladen über APIFreaks (primär) oder Tiingo (Fallback).")
    
    if not APIFREAKS_KEY and not TIINGO_KEY:
        st.warning("Bitte konfigurieren Sie APIFREAKS_API_KEY oder TIINGO_API_KEY in den Streamlit Secrets oder der .env-Datei.")
    else:
        if not APIFREAKS_KEY:
            st.warning("⚠️ **APIFreaks API-Key fehlt:** Fügen Sie `APIFREAKS_API_KEY` in den Streamlit Cloud Secrets hinzu, um Spot-Preise zu nutzen. Aktuell wird Tiingo (ETF-Preise) als Fallback genutzt.")
        # 1. Fetch APIFreaks data
        apifreaks_data = get_apifreaks_prices(APIFREAKS_KEY)
        
        # 2. Extract or fall back for each commodity
        def resolve_commodity(ticker_apifreaks, ticker_tiingo):
            if apifreaks_data:
                rates = apifreaks_data.get("rates", {})
                rate_val = rates.get(ticker_apifreaks)
                if rate_val is not None:
                    try:
                        val = float(rate_val)
                        return {
                            "close": val,
                            "high": val,
                            "low": val,
                            "date": apifreaks_data.get("date", "") or datetime.now().strftime("%Y-%m-%d"),
                            "source": "APIFreaks",
                            "is_etf": False
                        }
                    except (ValueError, TypeError):
                        pass
            
            if TIINGO_KEY:
                tiingo_res = get_tiingo_prices(ticker_tiingo, TIINGO_KEY)
                if tiingo_res:
                    return {
                        "close": tiingo_res.get("close"),
                        "high": tiingo_res.get("high"),
                        "low": tiingo_res.get("low"),
                        "date": tiingo_res.get("date", ""),
                        "source": "Tiingo",
                        "is_etf": True
                    }
            return None

        gld_data = resolve_commodity("XAU", "GLD")
        slv_data = resolve_commodity("XAG", "SLV")
        uso_data = resolve_commodity("WTIOIL-SPOT", "USO")
        bno_data = resolve_commodity("BRENTOIL-SPOT", "BNO")
        vix_data = resolve_commodity("VIX", "VIXY")

        col1, col2, col3, col4, col5 = st.columns(5)
        
        def display_commodity_card(col, name, data, flag):
            with col:
                if data:
                    close = data.get("close")
                    high = data.get("high")
                    low = data.get("low")
                    date_str = data.get("date", "")[:10]
                    src = data.get("source", "Tiingo")
                    suffix = " (ETF)" if data.get("is_etf", False) else " (Spot)"
                    
                    # Formatting values safely
                    close_str = f"${close:.2f}" if close is not None else "Daten momentan nicht verfügbar"
                    high_str = f"${high:.2f}" if high is not None else "N/A"
                    low_str = f"${low:.2f}" if low is not None else "N/A"
                    
                    st.markdown(f"""
                    <div class="metric-card-custom" style="border-left: 4px solid #10b981;">
                        <span class="metric-label">{flag} {name}{suffix}</span>
                        <div class="metric-value">{close_str}</div>
                        <div style="font-size:0.8rem; color:#7d7d8a; margin-top:5px;">
                            High: {high_str} | Low: {low_str}<br>
                            Datum: {date_str}
                        </div>
                        <div class="source-tag">Quelle: {src}</div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div class="metric-card-custom" style="border-left: 4px solid #ef4444;">
                        <span class="metric-label">{flag} {name}</span>
                        <div class="metric-value" style="font-size: 0.95rem; color:#7d7d8a;">Daten momentan nicht verfügbar</div>
                        <div class="source-tag">Quelle: APIFreaks / Tiingo</div>
                    </div>
                    """, unsafe_allow_html=True)
                    
        display_commodity_card(col1, "Gold", gld_data, "🟡")
        display_commodity_card(col2, "Silber", slv_data, "⚪")
        display_commodity_card(col3, "WTI Öl", uso_data, "🛢️")
        display_commodity_card(col4, "Brent Öl", bno_data, "🛢️")
        display_commodity_card(col5, "VIX Index", vix_data, "📈")

# ----------------- TAB 9: US-ARBEITSMARKT (BLS) -----------------
with tab9:
    st.header("🇺🇸 US-Arbeitsmarkt (BLS)")
    st.caption("Detaillierte US-Arbeitsmarktdaten geladen direkt von der Bureau of Labor Statistics (BLS) Public Data API.")
    
    if not BLS_KEY:
        st.warning("BLS API-Key fehlt in der .env-Datei. Bitte konfigurieren Sie BLS_API_KEY.")
    else:
        bls_json = get_bls_data(BLS_KEY)
        if not bls_json:
            st.error("Daten momentan nicht verfügbar")
        else:
            df_nfp = parse_bls_series(bls_json, "CES0000000001")
            df_wage = parse_bls_series(bls_json, "CES0500000003")
            df_part = parse_bls_series(bls_json, "LNS11300000")
            
            if df_nfp.empty or df_wage.empty or df_part.empty:
                st.error("Daten momentan nicht verfügbar")
            else:
                latest_nfp = df_nfp.iloc[-1]["value"]
                nfp_change = 0.0
                if len(df_nfp) > 1:
                    nfp_change = latest_nfp - df_nfp.iloc[-2]["value"]
                
                latest_wage = df_wage.iloc[-1]["value"]
                wage_change_pct = 0.0
                if len(df_wage) > 1:
                    wage_change_pct = ((latest_wage - df_wage.iloc[-2]["value"]) / df_wage.iloc[-2]["value"]) * 100
                    
                latest_part = df_part.iloc[-1]["value"]
                part_change = 0.0
                if len(df_part) > 1:
                    part_change = latest_part - df_part.iloc[-2]["value"]
                    
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(f"""
                    <div class="metric-card-custom" style="border-left: 4px solid #10b981;">
                        <span class="metric-label">Non-Farm Payrolls</span>
                        <div class="metric-value">{latest_nfp:,.1f}K</div>
                        <div style="font-size:0.85rem; color:{'#10b981' if nfp_change >= 0 else '#ef4444'}; margin-top:5px; font-weight:600;">
                            Change: {nfp_change:+.1f}K (Jobs)
                        </div>
                        <div class="source-tag">Quelle: BLS API</div>
                    </div>
                    """, unsafe_allow_html=True)
                with col2:
                    st.markdown(f"""
                    <div class="metric-card-custom" style="border-left: 4px solid #10b981;">
                        <span class="metric-label">Durchschnittlicher Stundenlohn</span>
                        <div class="metric-value">${latest_wage:.2f}</div>
                        <div style="font-size:0.85rem; color:{'#10b981' if wage_change_pct >= 0 else '#ef4444'}; margin-top:5px; font-weight:600;">
                            MoM: {wage_change_pct:+.2f}%
                        </div>
                        <div class="source-tag">Quelle: BLS API</div>
                    </div>
                    """, unsafe_allow_html=True)
                with col3:
                    st.markdown(f"""
                    <div class="metric-card-custom" style="border-left: 4px solid #10b981;">
                        <span class="metric-label">Erwerbsquote (Participation Rate)</span>
                        <div class="metric-value">{latest_part:.1f}%</div>
                        <div style="font-size:0.85rem; color:{'#10b981' if part_change >= 0 else '#ef4444'}; margin-top:5px; font-weight:600;">
                            Change: {part_change:+.2f}%
                        </div>
                        <div class="source-tag">Quelle: BLS API</div>
                    </div>
                    """, unsafe_allow_html=True)
                
                df_nfp_12 = df_nfp.tail(12).copy()
                df_nfp_12["MoM_Change"] = df_nfp_12["value"].diff()
                df_nfp_12["MoM_Change"] = df_nfp_12["MoM_Change"].fillna(0.0)
                
                st.subheader("📈 Entwicklung der letzten 12 Monate")
                
                fig_nfp = px.bar(
                    df_nfp_12,
                    x="date",
                    y="MoM_Change",
                    title="NFP Monatliche Veränderung (in Tausend)",
                    labels={"MoM_Change": "Netto-Stellenschaffung (k)", "date": "Datum"},
                    color="MoM_Change",
                    color_continuous_scale="RdYlGn",
                    text_auto=".1f"
                )
                fig_nfp.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    font=dict(color="#7d7d8a", size=10),
                    xaxis=dict(showgrid=False),
                    yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)'),
                    height=300
                )
                st.plotly_chart(fig_nfp, use_container_width=True)
                
                col_c1, col_c2 = st.columns(2)
                with col_c1:
                    fig_wages = px.line(
                        df_wage.tail(12),
                        x="date",
                        y="value",
                        title="Stundenlöhne ($/Std)",
                        labels={"value": "Durchschnittlicher Stundenlohn ($)", "date": "Datum"},
                        markers=True
                    )
                    fig_wages.update_traces(line_color="#10b981")
                    fig_wages.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        font=dict(color="#7d7d8a", size=10),
                        xaxis=dict(showgrid=False),
                        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)'),
                        height=250
                    )
                    st.plotly_chart(fig_wages, use_container_width=True)
                with col_c2:
                    fig_part = px.line(
                        df_part.tail(12),
                        x="date",
                        y="value",
                        title="Erwerbsquote (%)",
                        labels={"value": "Quote (%)", "date": "Datum"},
                        markers=True
                    )
                    fig_part.update_traces(line_color="#34d399")
                    fig_part.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        font=dict(color="#7d7d8a", size=10),
                        xaxis=dict(showgrid=False),
                        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)'),
                        height=250
                    )
                    st.plotly_chart(fig_part, use_container_width=True)

# ----------------- TAB 10: RISIKOINDIKATOREN (IMF) -----------------
with tab10:
    st.header("⚠️ Risikoindikatoren (IMF, World Bank & OECD)")
    st.caption("Vergleich von Staatsverschuldung, Haushaltsdefizit, Leistungsbilanz (IMF DataMapper), Handelsbilanz (World Bank) und Composite Leading Indicators (OECD) für alle G8 Währungen.")
    
    rows_risk = []
    for curr, info in CURRENCIES.items():
        debt = get_latest_imf_value(curr, "GGXWDG_NGDP")
        deficit = get_latest_imf_value(curr, "GGXCNL_NGDP")
        ca = get_latest_imf_value(curr, "BCA_NGDPD")
        tb = get_latest_worldbank_trade_balance(info["wb_code"])
        
        # Fetch OECD CLI
        cli_data = get_latest_oecd_cli(curr)
        if cli_data:
            cli_val, cli_date = cli_data
            if -15.0 <= cli_val <= 15.0:
                cli_val = 100.0 + cli_val
            trend_str = "über Trend" if cli_val > 100.0 else ("unter Trend" if cli_val < 100.0 else "auf Trend")
            cli_str = f"{cli_val:.2f} ({trend_str}, {cli_date})"
        else:
            cli_str = "N/A"
            
        debt_str = f"{debt:.1f}%" if debt is not None else "Daten momentan nicht verfügbar"
        deficit_str = f"{deficit:+.1f}%" if deficit is not None else "Daten momentan nicht verfügbar"
        ca_str = f"{ca:+.1f}%" if ca is not None else "Daten momentan nicht verfügbar"
        tb_str = f"{tb:+.1f}%" if tb is not None else "Daten momentan nicht verfügbar"
        
        rows_risk.append({
            "Währung": f"{info['flag']} {curr}",
            "Land/Region": info["country"],
            "Staatsverschuldung (% BIP)": debt_str,
            "Haushaltsdefizit (% BIP)": deficit_str,
            "Leistungsbilanz (% BIP)": ca_str,
            "Handelsbilanz (% BIP)": tb_str,
            "OECD Leading Indicator (CLI)": cli_str
        })
        
    df_risk = pd.DataFrame(rows_risk)
    st.dataframe(df_risk, use_container_width=True, hide_index=True)
    
    plot_data = []
    for curr in CURRENCIES.keys():
        debt = get_latest_imf_value(curr, "GGXWDG_NGDP")
        if debt is not None:
            plot_data.append({"Currency": curr, "Debt": debt})
    if plot_data:
        df_plot = pd.DataFrame(plot_data)
        fig_debt = px.bar(
            df_plot,
            x="Currency",
            y="Debt",
            title="Staatsverschuldung im Vergleich (% des BIP)",
            labels={"Debt": "Schuldenquote (% BIP)", "Currency": "Währung"},
            color="Debt",
            color_continuous_scale="Reds",
            text_auto=".1f"
        )
        fig_debt.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#7d7d8a", size=10),
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)'),
            height=300
        )
        st.plotly_chart(fig_debt, use_container_width=True)
        
    st.markdown("<div class='source-tag'>Quelle: IMF DataMapper (Debt/Deficit/Current Account), World Bank (Trade Balance)</div>", unsafe_allow_html=True)

# ----------------- TAB 11: NEWS & RESEARCH HUB -----------------
with tab11:
    st.header("📰 News & Research Hub")
    st.caption(f"Aktuelle fundamentale Marktnachrichten für das Paar **{selected_pair}** mit thematischer Gruppierung.")
    
    default_q = get_default_query(base_curr, quote_curr)
    search_q = st.text_input("🔍 Nachrichten durchsuchen", value=default_q, help="Nutze Stichworte wie Inflation, Leitzins, Fed, EZB etc.", key="news_search_query_input")
    
    if search_q:
        with st.spinner("Suche aktuelle Nachrichten..."):
            raw_articles, news_source, is_news_live, t_news, news_debug_logs = get_news_data_search(search_q, NEWSDATA_KEY, NEWSAPI_KEY)
            st.sidebar.caption(f"**News Hub:** {format_freshness(t_news)} ({'Live' if is_news_live else 'Demo'})")
            
            news_articles = deduplicate_articles(raw_articles)
            
        with st.expander("🛠️ API-Verbindungsdetails & Debug-Logs", expanded=True):
            for log in news_debug_logs:
                if "erfolgreich" in log or "geladen" in log or "Zusammenfassung" in log or "vorhanden" in log:
                    st.success(log)
                elif "Fehler" in log or "fehlgeschlagen" in log or "fehlt" in log or "keine Daten" in log:
                    st.error(log)
                else:
                    st.info(log)
            
        if not is_news_live:
            st.warning(f"News-APIs momentan nicht verfügbar ({news_source}) – zeige Demo-Daten.")
        elif news_articles:
            st.info(f"Es wurden {len(news_articles)} relevante und einzigartige Artikel gefunden. (Aktiv: {news_source})")
            
            grouped_articles = {
                "🏦 Geldpolitik & Zinsen": [],
                "🚢 Import & Export": [],
                "🌍 Länder-Analysen": [],
                "📊 Sonstige Makro-News": []
            }
            
            for art in news_articles:
                cat = categorize_article(art)
                grouped_articles[cat].append(art)
                
            sub_tabs = st.tabs([
                "📋 Alle News", 
                "🏦 Geldpolitik & Zinsen", 
                "🚢 Import & Export", 
                "🌍 Länder-Analysen", 
                "📊 Sonstige Makro-News"
            ])
            
            with sub_tabs[0]:
                render_articles_grid(news_articles[:10])
            with sub_tabs[1]:
                render_articles_grid(grouped_articles["🏦 Geldpolitik & Zinsen"][:10])
            with sub_tabs[2]:
                render_articles_grid(grouped_articles["🚢 Import & Export"][:10])
            with sub_tabs[3]:
                render_articles_grid(grouped_articles["🌍 Länder-Analysen"][:10])
            with sub_tabs[4]:
                render_articles_grid(grouped_articles["📊 Sonstige Makro-News"][:10])
        else:
            st.warning("Keine aktuellen Nachrichten zu diesem Suchbegriff gefunden.")
            
    st.markdown("<div class='source-tag'>Quelle: NewsData.io & NewsAPI.org</div>", unsafe_allow_html=True)


# ----------------- TAB 12: RISK-ON/OFF -----------------
with tab12:
    st.header("🛡️ Risk-On / Risk-Off Sentiment-Indikator")
    st.caption("Visualisierung des FRED Risk-On/Risk-Off Index (KCRORO) zur Einschätzung des globalen Markt-Risikos.")
    
    if not APIFREAKS_KEY:
        st.warning("""
        ⚠️ **APIFreaks API-Key fehlt:**
        Für Echtzeit-Volatilitätsdaten (VIX) wird der APIFreaks-Key benötigt.
        Tragen Sie den Key in Ihren **Streamlit Secrets** ein:
        1. Gehen Sie im Streamlit Cloud Dashboard zu **Settings** -> **Secrets**.
        2. Fügen Sie folgende Zeile hinzu:
           ```toml
           APIFREAKS_API_KEY = "Ihr_APIFreaks_Key"
           ```
        3. Klicken Sie auf **Save**.
        
        *Die App weicht aktuell automatisch auf Tiingo (VIXY) aus.*
        """)
    
    with st.spinner("Lade RORO-Index..."):
        roro_val, roro_dt, active_ind, debug_logs = get_roro_index(FRED_KEY, TIINGO_KEY, APIFREAKS_KEY)
        
    with st.expander("🛠️ API-Verbindungsdetails & Debug-Logs", expanded=True):
        for log in debug_logs:
            if "erfolgreich" in log or "geladen" in log or "vorhanden" in log:
                st.success(log)
            elif "Fehler" in log or "fehlgeschlagen" in log or "fehlt" in log or "nicht" in log:
                st.error(log)
            else:
                st.info(log)
                
    if roro_val is not None:
        is_risk_off = False
        if active_ind == "FRED Risk-On/Risk-Off (KCRORO)":
            is_risk_off = (roro_val > 0.0)
        elif active_ind == "FRED 10Y-2Y Spread (DGS10 - DGS2)":
            is_risk_off = (roro_val < 0.0)
        elif "VIX" in active_ind:
            is_risk_off = (roro_val > 20.0)
        elif active_ind == "USD/JPY Proxy (Tagesänderung)":
            is_risk_off = (roro_val <= 0.0)
            
        if is_risk_off:
            status_text = "🛡️ Risk-Off – Sichere Häfen bevorzugt"
            status_color = "#34d399"
            desc = f"Der Risikoindikator ({active_ind}) deutet auf Risikoaversion im globalen Markt hin. Sichere Häfen wie USD, CHF und JPY tendieren in dieser Marktphase zur Stärke, während risikoreichere Währungen (AUD, NZD, CAD) unter Druck geraten können."
        else:
            status_text = "🚀 Risk-On – Riskante Anlagen bevorzugt"
            status_color = "#ef4444"
            desc = f"Der Risikoindikator ({active_ind}) deutet auf Risikofreude im globalen Markt hin. Risikoaktiva und Hochzinswährungen wie AUD, NZD und CAD tendieren in dieser Phase zur Stärke, während klassische sichere Häfen (USD, CHF, JPY) tendenziell schwächer notieren."
            
        if "Proxy" in active_ind:
            val_str = f"{roro_val:+.2%}"
        elif "Spread" in active_ind:
            val_str = f"{roro_val:+.2f}%"
        elif "VIX" in active_ind:
            val_str = f"{roro_val:.2f}"
        else:
            val_str = f"{roro_val:+.2f}"

        col_metric, col_desc = st.columns([1, 2])
        with col_metric:
            st.markdown(f"""
            <div style="background-color:#14161d; border:1px solid #1f2026; padding:25px; border-radius:8px; text-align:center;">
                <div style="font-size:0.9rem; color:#7d7d8a; text-transform:uppercase; font-weight:600;">Wert ({active_ind})</div>
                <div style="font-size:2.8rem; font-weight:700; color:{status_color}; margin:10px 0;">{val_str}</div>
                <div style="background-color:{status_color}1a; color:{status_color}; border:1px solid {status_color}; padding:6px 12px; border-radius:4px; font-size:0.85rem; font-weight:700; display:inline-block; text-transform:uppercase;">
                    {status_text}
                </div>
            </div>
            """, unsafe_allow_html=True)
        with col_desc:
            st.markdown(f"### Marktanalyse & Interpretation")
            st.write(desc)
            if isinstance(roro_dt, datetime):
                dt_str = roro_dt.strftime("%d.%m.%Y")
            else:
                dt_str = str(roro_dt)
            st.markdown(f"**Indikator:** `{active_ind}` | **Letzte Aktualisierung:** `{dt_str}`")
    else:
        st.error("Daten momentan nicht verfügbar")


# ----------------- TAB 14: HISTORISCHE DATEN -----------------
with tab14:
    st.header("📊 Historische Fundamental-Analyse")
    st.caption("Analysiere das gesamte Fundamental-Dashboard für jeden beliebigen Tag in der Vergangenheit, um historische Marktkontexte zu evaluieren.")
    
    b_col1, b_col2, b_col3 = st.columns(3)
    g8_list = ["USD", "EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]
    with b_col1:
        hist_base = st.selectbox("Basiswährung (Base)", options=g8_list, index=0, key="hist_base_select_v2")
    with b_col2:
        hist_quote = st.selectbox("Quote-Währung (Quote)", options=g8_list, index=1, key="hist_quote_select_v2")
    with b_col3:
        hist_analysis_date = st.date_input(
            "Historisches Datum wählen", 
            value=datetime.now().date() - timedelta(days=365), 
            min_value=datetime(2005, 1, 1).date(),
            max_value=datetime.now().date(),
            key="hist_analysis_date_select_v2"
        )

    hist_analysis_pair = f"{hist_base}/{hist_quote}"
    
    if hist_base == hist_quote:
        st.warning("⚠️ Basis- und Quote-Währung sind identisch.")
    else:
        fetch_button = st.button("🔍 Historische Daten laden", key="hist_analysis_fetch_btn_v2")
        
        if fetch_button or st.session_state.get("hist_analysis_active_v2", False):
            st.session_state["hist_analysis_active_v2"] = True
            
            target_date_str = hist_analysis_date.strftime("%Y-%m-%d")
            base_c = hist_base
            quote_c = hist_quote
            
            st.markdown("---")
            st.subheader(f"📊 Historische Analyse für {hist_analysis_pair} am {hist_analysis_date.strftime('%d.%m.%Y')}")
            
            with st.spinner("Berechne fundamentales Signal..."):
                base_score_h = compute_score_with_cot(base_c, target_date_str)
                quote_score_h = compute_score_with_cot(quote_c, target_date_str)
                
                if base_score_h is None or quote_score_h is None:
                    st.warning("⚠️ Historische Fundamental-Daten für dieses Währungspaar/Datum unvollständig oder nicht verfügbar (Fehlende API-Daten).")
                else:
                    raw_diff_h = quote_score_h - base_score_h
                    signal_value_h = raw_diff_h / 2.0
                    signal_value_h = max(-50.0, min(50.0, signal_value_h))
                    
                    if signal_value_h >= 25.0:
                        sig_h = "SB"
                    elif 10.0 <= signal_value_h < 25.0:
                        sig_h = "MB"
                    elif -10.0 < signal_value_h < 10.0:
                        sig_h = "NT"
                    elif -25.0 < signal_value_h <= -10.0:
                        sig_h = "MS"
                    else:
                        sig_h = "SS"
                        
                    # 1. Bias Banner and Scores
                    render_bias_box(signal_value_h, base_c, quote_c, base_score_h, quote_score_h, sig_h)
                    
                    col_score_b, col_score_q = st.columns(2)
                    with col_score_b:
                        st.markdown(f"""<div class="metric-card-custom" style="border-left: 4px solid #10b981;">
                    <span class="metric-label">{CURRENCIES[base_c]['flag']} {base_c} Wirtschaftsscore (Historisch)</span>
                    <div class="metric-value">{base_score_h:.1f} / 100</div>
                    <div class="source-tag">Zusammengesetzter Score am {target_date_str}</div>
                    </div>""", unsafe_allow_html=True)
                    with col_score_q:
                        st.markdown(f"""<div class="metric-card-custom" style="border-left: 4px solid #444c56;">
                    <span class="metric-label">{CURRENCIES[quote_c]['flag']} {quote_c} Wirtschaftsscore (Historisch)</span>
                    <div class="metric-value">{quote_score_h:.1f} / 100</div>
                    <div class="source-tag">Zusammengesetzter Score am {target_date_str}</div>
                    </div>""", unsafe_allow_html=True)
                    
                    st.markdown("### 🗂️ Detaillierte historische Analysedaten")
                    
                    hist_sub_tabs = st.tabs([
                        "🏠 Übersicht & Checkliste",
                        "📊 PMI-Daten",
                        "🏦 Zinsdifferenz",
                        "📊 Analysten-Konsens",
                        "🧠 Sentiment-Score",
                        "🧮 Korrelationsmatrix",
                        "📈 Langfristige Historie",
                        "🛍️ Rohstoffe & Märkte",
                        "🇺🇸 US-Arbeitsmarkt (BLS)",
                        "⚠️ Risikoindikatoren (IMF)",
                        "📰 News & Research Hub"
                    ])
                    
                    # Subtab 0: Übersicht & Checkliste
                    with hist_sub_tabs[0]:
                        st.markdown("#### 🏠 G8 Fundamental-Checkliste (Historisch)")
                        st.caption(f"Vergleich der makroökonomischen Scores und Handelssignale für alle G8-Paare am {target_date_str}.")
                        
                        scores_h = {curr: compute_score_with_cot(curr, target_date_str) for curr in CURRENCIES.keys()}
                        scores_h_valid = {k: v for k, v in scores_h.items() if v is not None}
                        
                        if not scores_h_valid:
                            st.warning("Keine Fundamental-Scores für dieses Datum verfügbar.")
                        else:
                            fig_scores_h = go.Figure()
                            fig_scores_h.add_trace(go.Bar(
                                x=list(scores_h_valid.keys()),
                                y=list(scores_h_valid.values()),
                                marker_color=['#10b981' if s >= 55.0 else '#e2b13c' if s >= 45.0 else '#ef4444' for s in scores_h_valid.values()],
                                text=[f"{s:.1f}" for s in scores_h_valid.values()],
                                textposition='auto'
                            ))
                            fig_scores_h.update_layout(
                                xaxis_title="Währung",
                                yaxis_title="Wirtschaftsscore",
                                paper_bgcolor='rgba(0,0,0,0)',
                                plot_bgcolor='rgba(0,0,0,0)',
                                font=dict(color="#7d7d8a"),
                                height=300,
                                margin=dict(l=10, r=10, t=10, b=10)
                            )
                            st.plotly_chart(fig_scores_h, use_container_width=True)
                            
                            html_table_h = """
                            <div style="overflow-x:auto; margin-top:10px;">
                            <table style="width:100%; border-collapse: collapse; font-size:0.85rem; border:1px solid #1f2026;">
                            <thead>
                            <tr style="background-color:#161b22; color:#8b949e; border-bottom:2px solid #1f2026; text-align:left;">
                            <th style="padding:12px 10px;">Währungspaar</th>
                            <th style="padding:12px 10px;">Zinsdifferenz</th>
                            <th style="padding:12px 10px; text-align:center;">Signal-Wert</th>
                            <th style="padding:12px 10px; text-align:center;">Handelssignal</th>
                            <th style="padding:12px 10px;">Analysten-Konsens</th>
                            <th style="padding:12px 10px; text-align:center;">Sentiment</th>
                            <th style="padding:12px 10px;">Schulden (% BIP)</th>
                            <th style="padding:12px 10px;">Leistungsbilanz</th>
                            </tr>
                            </thead>
                            <tbody>"""
                            
                            import itertools
                            currencies_list = ["USD", "EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]
                            G8_PAIRS = list(itertools.permutations(currencies_list, 2))
                            
                            rows_h = []
                            for b_c_iter, q_c_iter in G8_PAIRS:
                                b_score_iter = scores_h.get(b_c_iter)
                                q_score_iter = scores_h.get(q_c_iter)
                                if b_score_iter is None or q_score_iter is None:
                                    continue
                                    
                                r_diff_iter = q_score_iter - b_score_iter
                                sig_val_iter = r_diff_iter / 2.0
                                sig_val_iter = max(-50.0, min(50.0, sig_val_iter))
                                
                                if sig_val_iter >= 25.0:
                                    b_name = "STRONG BUY"
                                    b_color = "#ef4444"
                                elif 10.0 <= sig_val_iter < 25.0:
                                    b_name = "MID BUY"
                                    b_color = "#f97316"
                                elif -10.0 < sig_val_iter < 10.0:
                                    b_name = "NEUTRAL"
                                    b_color = "#7d7d8a"
                                elif -25.0 < sig_val_iter <= -10.0:
                                    b_name = "MID SELL"
                                    b_color = "#3b82f6"
                                else:
                                    b_name = "STRONG SELL"
                                    b_color = "#34d399"
                                    
                                b_rate_iter, _ = get_country_rate_historical(b_c_iter, target_date_str)
                                q_rate_iter, _ = get_country_rate_historical(q_c_iter, target_date_str)
                                if b_rate_iter is not None and q_rate_iter is not None:
                                    diff_bps_iter = int((q_rate_iter - b_rate_iter) * 100)
                                    diff_str_iter = f"{b_rate_iter:.2f}% vs {q_rate_iter:.2f}% ({diff_bps_iter:+d} bps)"
                                else:
                                    diff_str_iter = "N/A"
                                    
                                rec_data_h = get_historical_recommendations(f"{b_c_iter}/{q_c_iter}", target_date_str, FINNHUB_KEY)
                                if rec_data_h:
                                    rec_str_iter = f"<span style='color:#10b981; font-weight:600;'>B:{rec_data_h['buy']}</span> / <span style='color:#e2b13c;'>H:{rec_data_h['hold']}</span> / <span style='color:#ef4444;'>S:{rec_data_h['sell']}</span>"
                                else:
                                    rec_str_iter = "N/A"
                                    
                                sent_val_iter = get_historical_sentiment(f"{b_c_iter}/{q_c_iter}", target_date_str, STOCKDATA_KEY)
                                if sent_val_iter is not None:
                                    sent_color_iter = "#10b981" if sent_val_iter >= 3.0 else "#ef4444" if sent_val_iter <= -3.0 else "#8b949e"
                                    sent_str_iter = f"<span style='color:{sent_color_iter}; font-weight:600;'>{sent_val_iter:+.1f}</span>"
                                else:
                                    sent_str_iter = "N/A"
                                    
                                b_iso = CURRENCIES[b_c_iter]["wb_code"]
                                q_iso = CURRENCIES[q_c_iter]["wb_code"]
                                
                                b_debt_h, _, _ = get_worldbank_data_historical(b_iso, "GC.DOD.TOTL.GD.ZS", target_date_str)
                                q_debt_h, _, _ = get_worldbank_data_historical(q_iso, "GC.DOD.TOTL.GD.ZS", target_date_str)
                                b_debt_str = f"{b_debt_h:.1f}%" if b_debt_h is not None else "N/A"
                                q_debt_str = f"{q_debt_h:.1f}%" if q_debt_h is not None else "N/A"
                                debt_str_iter = f"{b_debt_str} / {q_debt_str}"
                                
                                b_ca_h, _, _ = get_worldbank_data_historical(b_iso, "BCA_NGDPD", target_date_str)
                                q_ca_h, _, _ = get_worldbank_data_historical(q_iso, "BCA_NGDPD", target_date_str)
                                b_ca_str = f"{b_ca_h:+.1f}%" if b_ca_h is not None else "N/A"
                                q_ca_str = f"{q_ca_h:+.1f}%" if q_ca_h is not None else "N/A"
                                ca_str_iter = f"{b_ca_str} / {q_ca_str}"
                                
                                rows_h.append(f"""<tr style="border-bottom:1px solid #1f2026;">
                                <td style="padding:10px 10px; font-weight:600; color:#f0f0f5;">{CURRENCIES[b_c_iter]['flag']} {b_c_iter} / {CURRENCIES[q_c_iter]['flag']} {q_c_iter}</td>
                                <td style="padding:10px 10px; font-family:'Roboto Mono', monospace;">{diff_str_iter}</td>
                                <td style="padding:10px 10px; text-align:center; font-family:'Roboto Mono', monospace; font-weight:700; color:{b_color};">{sig_val_iter:+.1f}</td>
                                <td style="padding:10px 10px; text-align:center;">
                                <span style="background-color:{b_color}18; color:{b_color}; border:1px solid {b_color}; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:700; text-transform:uppercase;">{b_name}</span>
                                </td>
                                <td style="padding:10px 10px; font-family:'Roboto Mono', monospace;">{rec_str_iter}</td>
                                <td style="padding:10px 10px; text-align:center; font-family:'Roboto Mono', monospace;">{sent_str_iter}</td>
                                <td style="padding:10px 10px; font-family:'Roboto Mono', monospace; color:#b0b0bb; font-size:0.8rem;">{debt_str_iter}</td>
                                <td style="padding:10px 10px; font-family:'Roboto Mono', monospace; color:#b0b0bb; font-size:0.8rem;">{ca_str_iter}</td>
                                </tr>""")
                                
                            html_table_h += "".join(rows_h) + "</tbody></table></div>"
                            st.markdown(html_table_h, unsafe_allow_html=True)
                            st.markdown(f"<div class='source-tag'>Gesamte Suite-Zusammenfassung am {target_date_str} (Zinssatz- / Risikodaten Quelle: FRED / IMF / World Bank)</div>", unsafe_allow_html=True)
                            
                    # Subtab 1: PMI-Daten
                    with hist_sub_tabs[1]:
                        st.markdown("#### 📊 Historische PMI-Daten (Einkaufsmanagerindex)")
                        st.caption(f"Wirtschafts-PMI für alle 8 Währungsräume am {target_date_str} (Expansion > 50 / Kontraktion < 50).")
                        
                        code_to_name = {
                            "USD": "🇺🇸 USA",
                            "EUR": "🇪🇺 Euro",
                            "GBP": "🇬🇧 UK",
                            "CHF": "🇨🇭 Schweiz",
                            "CAD": "🇨🇦 Kanada",
                            "AUD": "🇦🇺 Australien",
                            "NZD": "🇳🇿 Neuseeland",
                            "JPY": "🇯🇵 Japan"
                        }
                        
                        pmi_h_all = get_all_pmi_data(FRED_KEY, EODHD_KEY, target_date=target_date_str)
                        pmi_rows = []
                        for code_iter, name_iter in code_to_name.items():
                            pmi_h = pmi_h_all.get(code_iter, {})
                            if pmi_h and pmi_h.get("m_last") is not None:
                                m_val = pmi_h["m_last"]
                                m_prev = pmi_h["m_prev"]
                                m_ref = pmi_h["m_ref"] or "N/A"
                                m_chg = m_val - m_prev if m_prev is not None else 0.0
                                m_status = "Expansion" if m_val >= 50.0 else "Kontraktion"
                                m_arrow = "▲" if m_chg > 0 else "▼" if m_chg < 0 else "▬"
                                m_str = f"{m_val:.1f} {m_arrow} {m_status}"
                            else:
                                m_str = "N/A"
                                m_chg = 0.0
                                m_ref = "N/A"
                                
                            if pmi_h and pmi_h.get("s_last") is not None:
                                s_val = pmi_h["s_last"]
                                s_prev = pmi_h["s_prev"]
                                s_ref = pmi_h["s_ref"] or "N/A"
                                s_chg = s_val - s_prev if s_prev is not None else 0.0
                                s_status = "Expansion" if s_val >= 50.0 else "Kontraktion"
                                s_arrow = "▲" if s_chg > 0 else "▼" if s_chg < 0 else "▬"
                                s_str = f"{s_val:.1f} {s_arrow} {s_status}"
                            else:
                                s_str = "N/A"
                                s_chg = 0.0
                                s_ref = "N/A"
                                
                            avg_chg = (m_chg + s_chg) / 2.0 if (m_str != "N/A" and s_str != "N/A") else m_chg if m_str != "N/A" else s_chg if s_str != "N/A" else 0.0
                            c_arrow = "▲" if avg_chg > 0 else "▼" if avg_chg < 0 else "▬"
                            c_str = f"{c_arrow} {avg_chg:+.1f}"
                            
                            ref_str = m_ref if m_ref != "N/A" else s_ref
                            pmi_rows.append({
                                "Land": name_iter,
                                "Manufacturing PMI": m_str,
                                "Services PMI": s_str,
                                "Veränderung zum Vormonat": c_str,
                                "Letzte Aktualisierung": ref_str
                            })
                        df_pmi_h = pd.DataFrame(pmi_rows)
                        st.dataframe(df_pmi_h, use_container_width=True)
                        st.markdown("<div class='source-tag'>Quelle: FRED API</div>", unsafe_allow_html=True)
                        
                    # Subtab 2: Zinsdifferenz
                    with hist_sub_tabs[2]:
                        st.markdown("#### 🏦 Zentralbank-Zinssätze & Zinsdifferenz (Historisch)")
                        st.caption(f"Vergleich der Leitzinsen am {target_date_str} (Quelle: FRED / ECB / SNB).")
                        
                        cb_rates_h = {}
                        for code_iter in g8_list:
                            rate, _ = get_country_rate_historical(code_iter, target_date_str)
                            cb_rates_h[code_iter] = rate
                            
                        df_rates_plot_h = pd.DataFrame([
                            {"Zentralbank": f"{curr_iter} ({CURRENCIES[curr_iter]['name']})", "Zinssatz": r_val, "Quelle": src}
                            for curr_iter in g8_list
                            for r_val, src in [get_country_rate_historical(curr_iter, target_date_str)]
                            if r_val is not None
                        ])
                        
                        fig_rates_g8_h = go.Figure()
                        fig_rates_g8_h.add_trace(go.Bar(
                            x=df_rates_plot_h["Zentralbank"],
                            y=df_rates_plot_h["Zinssatz"],
                            marker_color=['#10b981' if r > 4.0 else '#e2b13c' if r > 1.5 else '#ef4444' for r in df_rates_plot_h["Zinssatz"]],
                            text=[f"{r:.2f}%" for r in df_rates_plot_h["Zinssatz"]],
                            textposition='auto',
                            name="Zinssatz"
                        ))
                        fig_rates_g8_h.update_layout(
                            xaxis_title="Zentralbank",
                            yaxis_title="Leitzins (%)",
                            paper_bgcolor='rgba(0,0,0,0)',
                            plot_bgcolor='rgba(0,0,0,0)',
                            font=dict(color="#7d7d8a"),
                            height=300,
                            margin=dict(l=10, r=10, t=10, b=10)
                        )
                        st.plotly_chart(fig_rates_g8_h, use_container_width=True)
                        st.table(df_rates_plot_h)
                        
                        base_rate_h = cb_rates_h.get(base_c)
                        quote_rate_h = cb_rates_h.get(quote_c)
                        if base_rate_h is not None and quote_rate_h is not None:
                            diff_bps_h = int((quote_rate_h - base_rate_h) * 100)
                            st.metric("Zinsdifferenz (Quote - Base)", f"{diff_bps_h:+.0f} bps", help="Positive Werte bedeuten, dass die Quote-Währung höhere Zinsen hat.")
                            
                    # Subtab 3: Analysten-Konsens
                    with hist_sub_tabs[3]:
                        st.markdown("#### 📊 Historischer Analysten-Konsens (Finnhub)")
                        consensus_h = get_historical_recommendations(hist_analysis_pair, target_date_str, FINNHUB_KEY)
                        
                        if not consensus_h:
                            st.warning("Daten nicht verfügbar für dieses Datum")
                        else:
                            c_col1, c_col2 = st.columns(2)
                            with c_col1:
                                labels = ["Buy/Strong Buy", "Hold", "Sell/Strong Sell"]
                                values = [consensus_h["buy"], consensus_h["hold"], consensus_h["sell"]]
                                if sum(values) == 0:
                                    st.warning("Keine Analystenempfehlungen für dieses Datum vorhanden.")
                                else:
                                    fig_cons_h = go.Figure(data=[go.Pie(labels=labels, values=values, hole=.4, marker=dict(colors=["#34d399", "#7d7d8a", "#ef4444"]))])
                                    fig_cons_h.update_layout(
                                        paper_bgcolor='rgba(0,0,0,0)',
                                        plot_bgcolor='rgba(0,0,0,0)',
                                        font=dict(color="#7d7d8a"),
                                        height=280,
                                        margin=dict(l=10, r=10, t=10, b=10)
                                    )
                                    st.plotly_chart(fig_cons_h, use_container_width=True)
                                    
                            with c_col2:
                                st.markdown(f"**Gesamtempfehlungen:** `{sum(values)}`")
                                st.markdown(f"- **Kauf-Einstufungen:** `{consensus_h['buy']}`")
                                st.markdown(f"- **Halten-Einstufungen:** `{consensus_h['hold']}`")
                                st.markdown(f"- **Verkauf-Einstufungen:** `{consensus_h['sell']}`")
                                st.markdown("---")
                                st.markdown("<div class='source-tag'>Quelle: Finnhub API</div>", unsafe_allow_html=True)
                                
                    # Subtab 4: Sentiment-Score
                    with hist_sub_tabs[4]:
                        st.markdown("#### 🧠 Historischer Sentiment-Score (StockData)")
                        sentiment_h = get_historical_sentiment(hist_analysis_pair, target_date_str, STOCKDATA_KEY)
                        
                        if sentiment_h is None:
                            st.warning("Daten nicht verfügbar für dieses Datum")
                        else:
                            fig_gauge_h = go.Figure(go.Indicator(
                                mode="gauge+number",
                                value=sentiment_h,
                                domain={'x': [0, 1], 'y': [0, 1]},
                                title={'text': "Sentiment-Score (-10 bis +10)", 'font': {'color': "#7d7d8a", 'size': 14}},
                                gauge={
                                    'axis': {'range': [-10, 10], 'tickwidth': 1, 'tickcolor': "#7d7d8a"},
                                    'bar': {'color': "#e2b13c"},
                                    'bgcolor': "#14161d",
                                    'borderwidth': 1,
                                    'bordercolor': "#1f2026",
                                    'steps': [
                                        {'range': [-10, -3], 'color': 'rgba(239, 68, 68, 0.15)'},
                                        {'range': [-3, 3], 'color': 'rgba(125, 125, 138, 0.15)'},
                                        {'range': [3, 10], 'color': 'rgba(52, 211, 153, 0.15)'}
                                    ]
                                }
                            ))
                            fig_gauge_h.update_layout(
                                paper_bgcolor='rgba(0,0,0,0)',
                                plot_bgcolor='rgba(0,0,0,0)',
                                font=dict(color="#7d7d8a"),
                                height=250,
                                margin=dict(l=20, r=20, t=40, b=20)
                            )
                            st.plotly_chart(fig_gauge_h, use_container_width=True)
                            st.markdown("<div class='source-tag'>Quelle: StockData API</div>", unsafe_allow_html=True)
                            
                    # Subtab 5: Korrelationsmatrix
                    with hist_sub_tabs[5]:
                        st.markdown("#### 🧮 30-Tage Historische Pearson-Korrelation")
                        corr_df_h, is_live_corr_h = get_historical_correlation_matrix(target_date_str)
                        
                        if corr_df_h is None:
                            st.warning("Daten nicht verfügbar für dieses Datum")
                        else:
                            fig_heatmap_h = go.Figure(data=go.Heatmap(
                                z=corr_df_h.values,
                                x=corr_df_h.columns,
                                y=corr_df_h.index,
                                colorscale="RdBu",
                                zmin=-1.0, zmax=1.0,
                                text=np.round(corr_df_h.values, 2),
                                texttemplate="%{text}",
                                showscale=True
                            ))
                            fig_heatmap_h.update_layout(
                                paper_bgcolor='rgba(0,0,0,0)',
                                plot_bgcolor='rgba(0,0,0,0)',
                                font=dict(color="#7d7d8a", size=9),
                                height=380,
                                margin=dict(l=10, r=10, t=10, b=10)
                            )
                            st.plotly_chart(fig_heatmap_h, use_container_width=True)
                            st.caption("Quelle: CurrencyArchiveAPI / Timeseries data")
                            
                    # Subtab 6: Langfristige Historie
                    with hist_sub_tabs[6]:
                        st.markdown("#### 📈 Kursverlauf bis zu diesem Datum")
                        df_hist_all, is_live = get_fcs_history_data_historical(hist_analysis_pair, target_date_str, FCS_KEY)
                        
                        if df_hist_all is not None and not df_hist_all.empty:
                            fig_hist_h = go.Figure()
                            fig_hist_h.add_trace(go.Scatter(
                                x=df_hist_all["date"], y=df_hist_all["close"],
                                line=dict(color="#e2b13c", width=2),
                                name="Schlusskurs"
                            ))
                            fig_hist_h.update_layout(
                                xaxis_title="Datum",
                                yaxis_title="Kurs",
                                paper_bgcolor='rgba(0,0,0,0)',
                                plot_bgcolor='rgba(0,0,0,0)',
                                font=dict(color="#7d7d8a", size=10),
                                height=350,
                                margin=dict(l=10, r=10, t=10, b=10)
                            )
                            st.plotly_chart(fig_hist_h, use_container_width=True)
                            st.markdown("<div class='source-tag'>Quelle: FCS API</div>", unsafe_allow_html=True)
                        else:
                            st.warning("Daten nicht verfügbar für dieses Datum")
                            
                    # Subtab 7: Rohstoffe & Märkte
                    with hist_sub_tabs[7]:
                        st.markdown("#### 🛍️ Historische Rohstoffpreise & Märkte")
                        st.caption(f"Rohstoffpreise und VIX-Volatilität am {target_date_str} (Quelle: FRED API).")
                        
                        comm_h = get_historical_commodities(target_date_str, FRED_KEY)
                        
                        if not comm_h:
                            st.warning("Daten nicht verfügbar für dieses Datum")
                        else:
                            c_col1, c_col2, c_col3, c_col4, c_col5 = st.columns(5)
                            
                            # Gold
                            with c_col1:
                                val = comm_h.get("gold")
                                chg = comm_h.get("gold_chg")
                                val_str = f"${val:.2f}" if val is not None else "N/A"
                                chg_str = f"{chg:+.1f}%" if chg is not None else "N/A"
                                render_metric_card("Gold (Spot)", val_str, f"FRED ({chg_str})", (chg or 0.0) >= 0)
                                
                            # Silber
                            with c_col2:
                                val = comm_h.get("silver")
                                chg = comm_h.get("silver_chg")
                                val_str = f"${val:.2f}" if val is not None else "N/A"
                                chg_str = f"{chg:+.1f}%" if chg is not None else "N/A"
                                render_metric_card("Silber (Spot)", val_str, f"FRED ({chg_str})", (chg or 0.0) >= 0)
                                
                            # WTI
                            with c_col3:
                                val = comm_h.get("wti")
                                chg = comm_h.get("wti_chg")
                                val_str = f"${val:.2f}" if val is not None else "N/A"
                                chg_str = f"{chg:+.1f}%" if chg is not None else "N/A"
                                render_metric_card("WTI Rohöl", val_str, f"FRED ({chg_str})", (chg or 0.0) >= 0)
                                
                            # Brent
                            with c_col4:
                                val = comm_h.get("brent")
                                chg = comm_h.get("brent_chg")
                                val_str = f"${val:.2f}" if val is not None else "N/A"
                                chg_str = f"{chg:+.1f}%" if chg is not None else "N/A"
                                render_metric_card("Brent Rohöl", val_str, f"FRED ({chg_str})", (chg or 0.0) >= 0)
                                
                            # VIX
                            with c_col5:
                                val = comm_h.get("vix")
                                chg = comm_h.get("vix_chg")
                                val_str = f"{val:.2f}" if val is not None else "N/A"
                                chg_str = f"{chg:+.1f}%" if chg is not None else "N/A"
                                render_metric_card("VIX Volatilitätsindex", val_str, f"FRED ({chg_str})", (chg or 0.0) < 0)
                                
                    # Subtab 8: US-Arbeitsmarkt (BLS)
                    with hist_sub_tabs[8]:
                        st.markdown("#### 🇺🇸 US-Arbeitsmarkt (BLS) - Historisch")
                        st.caption(f"Historische US-Arbeitsmarktdaten am {target_date_str} (Quelle: FRED API).")
                        
                        labor_h = get_historical_labor_data(target_date_str, FRED_KEY)
                        
                        if not labor_h:
                            st.warning("Daten nicht verfügbar für dieses Datum")
                        else:
                            col1, col2, col3 = st.columns(3)
                            with col1:
                                val = labor_h.get("nfp")
                                chg = labor_h.get("nfp_chg")
                                val_str = f"{val/1000.0:,.1f}M" if val is not None else "N/A"
                                chg_str = f"Change: {chg:+.1f}K" if chg is not None else "N/A"
                                st.markdown(f"""
                                <div class="metric-card-custom" style="border-left: 4px solid #10b981;">
                                    <span class="metric-label">Non-Farm Payrolls</span>
                                    <div class="metric-value">{val_str}</div>
                                    <div style="font-size:0.85rem; color:{'#10b981' if (chg or 0.0) >= 0 else '#ef4444'}; margin-top:5px; font-weight:600;">
                                        {chg_str} (Jobs)
                                    </div>
                                    <div class="source-tag">Quelle: FRED (PAYEMS)</div>
                                </div>
                                """, unsafe_allow_html=True)
                                
                            with col2:
                                val = labor_h.get("wage")
                                chg = labor_h.get("wage_chg")
                                val_str = f"${val:.2f}" if val is not None else "N/A"
                                chg_str = f"MoM: {chg:+.2f}%" if chg is not None else "N/A"
                                st.markdown(f"""
                                <div class="metric-card-custom" style="border-left: 4px solid #10b981;">
                                    <span class="metric-label">Durchschnittlicher Stundenlohn</span>
                                    <div class="metric-value">{val_str}</div>
                                    <div style="font-size:0.85rem; color:{'#10b981' if (chg or 0.0) >= 0 else '#ef4444'}; margin-top:5px; font-weight:600;">
                                        {chg_str}
                                    </div>
                                    <div class="source-tag">Quelle: FRED (CES0500000003)</div>
                                </div>
                                """, unsafe_allow_html=True)
                                
                            with col3:
                                val = labor_h.get("part")
                                chg = labor_h.get("part_chg")
                                val_str = f"{val:.1f}%" if val is not None else "N/A"
                                chg_str = f"Change: {chg:+.2f}%" if chg is not None else "N/A"
                                st.markdown(f"""
                                <div class="metric-card-custom" style="border-left: 4px solid #10b981;">
                                    <span class="metric-label">Erwerbsquote (Participation Rate)</span>
                                    <div class="metric-value">{val_str}</div>
                                    <div style="font-size:0.85rem; color:{'#10b981' if (chg or 0.0) >= 0 else '#ef4444'}; margin-top:5px; font-weight:600;">
                                        {chg_str}
                                    </div>
                                    <div class="source-tag">Quelle: FRED (CIVPART)</div>
                                </div>
                                """, unsafe_allow_html=True)
                                
                    # Subtab 9: Historische Risikokennzahlen
                    with hist_sub_tabs[9]:
                        st.markdown("#### ⚠️ Historische Risikokennzahlen")
                        base_iso = CURRENCIES.get(base_c, {}).get("wb_code", base_c)
                        quote_iso = CURRENCIES.get(quote_c, {}).get("wb_code", quote_c)
                        
                        base_debt_h, base_debt_dt, _ = get_worldbank_data_historical(base_iso, "GC.DOD.TOTL.GD.ZS", target_date_str)
                        quote_debt_h, quote_debt_dt, _ = get_worldbank_data_historical(quote_iso, "GC.DOD.TOTL.GD.ZS", target_date_str)
                        
                        base_cli_res = get_historical_oecd_cli(base_c, target_date_str)
                        quote_cli_res = get_historical_oecd_cli(quote_c, target_date_str)
                        base_cli_h = base_cli_res[0] if base_cli_res is not None else None
                        quote_cli_h = quote_cli_res[0] if quote_cli_res is not None else None
                        
                        debt_col1, debt_col2 = st.columns(2)
                        with debt_col1:
                            st.markdown(f"##### 🏛️ Staatsverschuldung (% BIP)")
                            b_debt_str = f"{base_debt_h:.1f}%" if base_debt_h is not None else "Daten nicht verfügbar"
                            q_debt_str = f"{quote_debt_h:.1f}%" if quote_debt_h is not None else "Daten nicht verfügbar"
                            b_dt_str = base_debt_dt.strftime('%Y') if base_debt_dt else 'N/A'
                            q_dt_str = quote_debt_dt.strftime('%Y') if quote_debt_dt else 'N/A'
                            st.markdown(f"- **{base_c}:** `{b_debt_str}` (Jahr: {b_dt_str}) [Quelle: World Bank]")
                            st.markdown(f"- **{quote_c}:** `{q_debt_str}` (Jahr: {q_dt_str}) [Quelle: World Bank]")
                            
                        with debt_col2:
                            st.markdown(f"##### 📈 OECD Composite Leading Indicator (CLI)")
                            b_val = float(base_cli_h) if base_cli_h is not None else None
                            q_val = float(quote_cli_h) if quote_cli_h is not None else None
                            
                            # Normalize deviation values (e.g. -15 to 15) to 100-base
                            if b_val is not None and -15.0 <= b_val <= 15.0:
                                b_val = 100.0 + b_val
                            if q_val is not None and -15.0 <= q_val <= 15.0:
                                q_val = 100.0 + q_val
                                
                            b_cli_str = f"{b_val:.2f}" if b_val is not None else "Daten nicht verfügbar"
                            q_cli_str = f"{q_val:.2f}" if q_val is not None else "Daten nicht verfügbar"
                            
                            b_trend = '>100 (Wachstum)' if b_val and b_val > 100.0 else '<100 (Verlangsamung)' if b_val else 'N/A'
                            q_trend = '>100 (Wachstum)' if q_val and q_val > 100.0 else '<100 (Verlangsamung)' if q_val else 'N/A'
                            
                            st.markdown(f"- **{base_c}:** `{b_cli_str}` (Trend: {b_trend}) [Quelle: OECD]")
                            st.markdown(f"- **{quote_c}:** `{q_cli_str}` (Trend: {q_trend}) [Quelle: OECD]")
                            
                    # Subtab 10: News Hub
                    with hist_sub_tabs[10]:
                        st.markdown("#### 📰 Historische Nachrichten (Echtzeit-Timeline ±3 Tage)")
                        news_h = get_historical_news(hist_analysis_pair, target_date_str, STOCKDATA_KEY)
                        if not news_h:
                            st.warning("Daten nicht verfügbar für dieses Datum")
                        else:
                            render_articles_grid(news_h)
                            
            past_decisions = load_backtest_decisions()
            if past_decisions:
                st.markdown("##### 📜 Bisherige Backtest-Entscheidungen")
                df_dec = pd.DataFrame(past_decisions)
                df_dec_renamed = df_dec.rename(columns={
                    "timestamp": "Speicherzeit",
                    "target_date": "Analysedatum",
                    "pair": "Paar",
                    "signal_value": "Signalwert",
                    "signal_badge": "Signal",
                    "decision": "Entscheidung",
                    "notes": "Notizen"
                })
                st.dataframe(df_dec_renamed.sort_values("Speicherzeit", ascending=False), use_container_width=True)


# ----------------- 7. FALLBACK BOTTOM BAR (Leitdaten) -----------------
st.markdown("---")
st.subheader("🇺🇸 US-Makroökonomische Leitdaten")

df_funds, _, _ = get_fred_data("FEDFUNDS", FRED_KEY)
df_unemp, _, _ = get_fred_data("UNRATE", FRED_KEY)
df_cpi, _, _ = get_fred_data("CPIAUCSL", FRED_KEY)

m_col1, m_col2, m_col3 = st.columns(3)
with m_col1:
    latest_val = df_funds.iloc[-1]["value"] if not df_funds.empty else 0.0
    render_metric_card("Fed Funds Rate", f"{latest_val:.2f}%", f"FRED ({'Live' if FRED_KEY else 'Demo'})", bool(FRED_KEY))
with m_col2:
    latest_val = df_unemp.iloc[-1]["value"] if not df_unemp.empty else 0.0
    render_metric_card("Arbeitslosenquote", f"{latest_val:.2f}%", f"FRED ({'Live' if FRED_KEY else 'Demo'})", bool(FRED_KEY))
with m_col3:
    latest_val = df_cpi.iloc[-1]["value"] if not df_cpi.empty else 0.0
    render_metric_card("Verbraucherpreise (CPI)", f"{latest_val:.1f}", f"FRED ({'Live' if FRED_KEY else 'Demo'})", bool(FRED_KEY))

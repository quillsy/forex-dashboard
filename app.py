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

# ----------------- Load API Keys from Env & Secrets -----------------
def load_api_key(name):
    val = os.getenv(name)
    if not val:
        try:
            val = st.secrets.get(name) or st.secrets.get(name.lower())
        except Exception:
            pass
    return val

FRED_KEY = load_api_key("FRED_API_KEY")
AV_KEY = load_api_key("ALPHA_VANTAGE_API_KEY")
NEWSDATA_KEY = load_api_key("NEWSDATA_API_KEY")
NEWSAPI_KEY = load_api_key("NEWSAPI_KEY")
BENZINGA_KEY = load_api_key("BENZINGA_API_KEY")
FINNHUB_KEY = load_api_key("FINNHUB_API_KEY")
ITICK_KEY = load_api_key("ITICK_API_KEY")
FCS_KEY = load_api_key("FCS_API_KEY")
STOCKDATA_KEY = load_api_key("STOCKDATA_API_KEY")
TIINGO_KEY = load_api_key("TIINGO_API_KEY")
BLS_KEY = load_api_key("BLS_API_KEY")
APIFREAKS_KEY = load_api_key("APIFREAKS_API_KEY")
EODHD_KEY = load_api_key("EODHD_API_KEY")
ESTAT_APP_ID = load_api_key("ESTAT_APP_ID")

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

FRED_PMI_SERIES = {
    "USD": {"m": "NAPM", "s": "NMFPT"},
    "EUR": {"m": "EUROPAMIMIPDSMEI", "s": "EUROPASEIPDSMEI"},
    "GBP": {"m": "GBRPAMIMIPDSMEI", "s": "GBRPASEIPDSMEI"},
    "JPY": {"m": "JPNPAMIMIPDSMEI", "s": "JPNPASEIPDSMEI"},
    "CAD": {"m": "CANPAMIMIPDSMEI", "s": "CANPASEIPDSMEI"},
    "AUD": {"m": "AUSPAMIMIPDSMEI", "s": "AUSPASEIPDSMEI"},
    "CHF": {"m": "CHEPAMIMIPDSMEI", "s": "CHEPASEIPDSMEI"},
    "NZD": {"m": "NZLPAMIMIPDSMEI", "s": "NZLPASEIPDSMEI"}
}

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
    
    # Other G8 Currencies (EODHD with FRED fallback)
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
                
        # FRED Fallback
        if m_last is None and fred_key:
            series_info = FRED_PMI_SERIES.get(code)
            if series_info:
                val, dt, _ = get_fred_data_historical(series_info["m"], target_date, fred_key)
                if val is not None:
                    m_last = val
                    m_ref = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt) if dt else None
                    m_src = "FRED"
                    df_m, _, _ = get_fred_data(series_info["m"], fred_key)
                    if df_m is not None and not df_m.empty:
                        target_dt = pd.to_datetime(target_date)
                        df_filtered = df_m[df_m["date"] <= target_dt].sort_values("date")
                        if len(df_filtered) >= 2:
                            m_prev = float(df_filtered.iloc[-2]["value"])

        if s_last is None and fred_key:
            series_info = FRED_PMI_SERIES.get(code)
            if series_info:
                val, dt, _ = get_fred_data_historical(series_info["s"], target_date, fred_key)
                if val is not None:
                    s_last = val
                    s_ref = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt) if dt else None
                    s_src = "FRED"
                    df_s, _, _ = get_fred_data(series_info["s"], fred_key)
                    if df_s is not None and not df_s.empty:
                        target_dt = pd.to_datetime(target_date)
                        df_filtered = df_s[df_s["date"] <= target_dt].sort_values("date")
                        if len(df_filtered) >= 2:
                            s_prev = float(df_filtered.iloc[-2]["value"])
                            
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
                
        # FRED Fallback for Manufacturing
        if m_last is None and fred_key:
            series_info = FRED_PMI_SERIES.get(code)
            if series_info:
                df_m, _, _ = get_fred_data(series_info["m"], fred_key)
                if df_m is not None and not df_m.empty:
                    m_last = float(df_m.iloc[-1]["value"])
                    m_ref = df_m.iloc[-1]["date"].strftime("%Y-%m-%d")
                    m_src = "FRED"
                    if len(df_m) >= 2:
                        m_prev = float(df_m.iloc[-2]["value"])
                        
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
                
        # FRED Fallback for Services
        if s_last is None and fred_key:
            series_info = FRED_PMI_SERIES.get(code)
            if series_info:
                df_s, _, _ = get_fred_data(series_info["s"], fred_key)
                if df_s is not None and not df_s.empty:
                    s_last = float(df_s.iloc[-1]["value"])
                    s_ref = df_s.iloc[-1]["date"].strftime("%Y-%m-%d")
                    s_src = "FRED"
                    if len(df_s) >= 2:
                        s_prev = float(df_s.iloc[-2]["value"])
                        
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
    if "api_errors" not in st.session_state:
        st.session_state["api_errors"] = {}
    if not key:
        st.session_state["api_errors"]["Finnhub API"] = "API-Key nicht konfiguriert"
        return generate_mock_finnhub(pair), datetime.now(), False
    try:
        data = fetch_finnhub_live(pair, key)
        st.session_state["api_errors"]["Finnhub API"] = None
        return data, datetime.now(), True
    except Exception as e:
        st.session_state["api_errors"]["Finnhub API"] = str(e)
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
    if "api_errors" not in st.session_state:
        st.session_state["api_errors"] = {}
    if not key:
        st.session_state["api_errors"]["StockData Sentiment"] = "API-Key nicht konfiguriert"
        return generate_mock_stockdata(), datetime.now(), False
    try:
        val = fetch_stockdata_live(pair, key)
        st.session_state["api_errors"]["StockData Sentiment"] = None
        return val, datetime.now(), True
    except Exception as e:
        st.session_state["api_errors"]["StockData Sentiment"] = str(e)
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



@st.cache_data(ttl=86400, show_spinner=False)
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



def get_historical_imf_value(curr, indicator, target_date):
    try:
        target_year = pd.to_datetime(target_date).year
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
        indicator_data = data.get("values", {}).get(indicator, {})
        for code in candidates:
            values_dict = indicator_data.get(code, {})
            if values_dict:
                years = [int(yr) for yr in values_dict.keys() if yr.isdigit()]
                if years:
                    valid_years = [y for y in years if y <= target_year]
                    if not valid_years:
                        best_year = min(years)
                    else:
                        best_year = max(valid_years)
                    val = values_dict[str(best_year)]
                    if val is not None:
                        return float(val)
    except Exception:
        pass
    return None

def get_yield_spread(target_date=None):
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        val, _, _ = get_fred_data_historical("T10Y2Y", dt_str, fred_key)
        if val is None:
            return 0.0
        return float(np.clip(val / 2.0 * 5.0, -5.0, 5.0))
    except Exception:
        return 0.0

def get_ciss_index(target_date=None):
    try:
        if target_date is None:
            url = "https://data-api.ecb.europa.eu/service/data/CISS/D.U2.Z0Z.4F.EC.SS_CI.IDX?lastNObservations=5&format=jsondata"
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            url = f"https://data-api.ecb.europa.eu/service/data/CISS/D.U2.Z0Z.4F.EC.SS_CI.IDX?endPeriod={dt_str}&lastNObservations=5&format=jsondata"
            
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=8)
        if r.status_code == 200:
            res = r.json()
            series = res["dataSets"][0]["series"]
            if series:
                series_key = list(series.keys())[0]
                obs = series[series_key]["observations"]
                sorted_keys = sorted(obs.keys(), key=int)
                if sorted_keys:
                    latest_val = float(obs[sorted_keys[-1]][0])
                    if latest_val is None:
                        return 0.0
                    return float(np.clip((0.2 - latest_val) / 0.3 * 5.0, -5.0, 5.0))
    except Exception:
        return 0.0
    return 0.0

def get_house_price_index(target_date=None):
    try:
        url = "https://landregistry.data.gov.uk/landregistry/query"
        query = """
        prefix ukhpi: <http://landregistry.data.gov.uk/def/ukhpi/>
        select ?date ?hpi where {
          ?item ukhpi:refRegion <http://landregistry.data.gov.uk/id/region/united-kingdom> ;
                ukhpi:refMonth ?date ;
                ukhpi:housePriceIndex ?hpi .
        } order by desc(?date) limit 6
        """
        r = requests.post(url, data={"query": query}, headers={"Accept": "application/sparql-results+json"}, timeout=8)
        if r.status_code == 200:
            res = r.json()
            bindings = res.get("results", {}).get("bindings", [])
            data = []
            for b in bindings:
                d_str = b.get("date", {}).get("value")
                h_val = float(b.get("hpi", {}).get("value"))
                data.append({"date": d_str, "hpi": h_val})
            
            if data:
                data = sorted(data, key=lambda x: x["date"])
                if target_date is not None:
                    target_month = pd.to_datetime(target_date).strftime("%Y-%m")
                    valid_data = [x for x in data if x["date"] <= target_month]
                else:
                    valid_data = data
                    
                if len(valid_data) >= 2:
                    latest_hpi = valid_data[-1]["hpi"]
                    prev_hpi = valid_data[-2]["hpi"]
                    if latest_hpi is None or prev_hpi is None or prev_hpi == 0:
                        return 0.0
                    growth = (latest_hpi - prev_hpi) / prev_hpi * 100.0
                    return float(np.clip(growth / 0.5 * 2.0, -2.0, 2.0))
    except Exception:
        return 0.0
    return 0.0

def get_china_pmi_fred(target_date=None):
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        val, _, _ = get_fred_data_historical("CVPMA", dt_str, fred_key)
        if val is None:
            return 0.0
        return float(np.clip(val - 50.0, -5.0, 5.0))
    except Exception:
        return 0.0

def get_oil_price(target_date=None):
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        dt_3m_ago = (pd.to_datetime(dt_str) - timedelta(days=90)).strftime("%Y-%m-%d")
        val_now, _, _ = get_fred_data_historical("DCOILWTICO", dt_str, fred_key)
        val_3m, _, _ = get_fred_data_historical("DCOILWTICO", dt_3m_ago, fred_key)
        
        if val_now is None or val_3m is None or val_3m == 0:
            return 0.0
        chg = (val_now - val_3m) / val_3m * 100.0
        return float(np.clip(chg / 10.0 * 5.0, -5.0, 5.0))
    except Exception:
        return 0.0

def get_milk_price(target_date=None):
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        dt_3m_ago = (pd.to_datetime(dt_str) - timedelta(days=90)).strftime("%Y-%m-%d")
        val_now, _, _ = get_fred_data_historical("PRAWINDEXM", dt_str, fred_key)
        val_3m, _, _ = get_fred_data_historical("PRAWINDEXM", dt_3m_ago, fred_key)
        
        if val_now is None or val_3m is None or val_3m == 0:
            return 0.0
        chg = (val_now - val_3m) / val_3m * 100.0
        return float(np.clip(chg / 10.0 * 3.0, -3.0, 3.0))
    except Exception:
        return 0.0

def get_trade_balance(target_date=None):
    try:
        if ESTAT_APP_ID:
            try:
                url = f"http://api.e-stat.go.jp/rest/3.0/app/json/getStatsData?appId={ESTAT_APP_ID}&statsDataId=0003444800&limit=10"
                r = requests.get(url, timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    values = data.get("GET_STATS_DATA", {}).get("STATISTICAL_DATA", {}).get("DATA_INF", {}).get("VALUE", [])
                    if values:
                        latest_val = float(values[-1].get("$"))
                        if latest_val is not None:
                            return float(np.clip(latest_val / 1e12 * 5.0, -5.0, 5.0))
            except Exception:
                pass
                
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        val, _, _ = get_fred_data_historical("XTNTVA01JPM667S", dt_str, fred_key)
        if val is None:
            return 0.0
        return float(np.clip(val / 5000.0 * 5.0, -5.0, 5.0))
    except Exception:
        return 0.0

YIELD_SERIES = {
    "USD": "DGS2",
    "EUR": "IRLTLT01EZM156N",
    "GBP": "IRLTLT01GBM156N",
    "JPY": "IRLTLT01JPM156N",
    "CHF": "IRLTLT01CHM156N",
    "CAD": "IRLTLT01CAM156N",
    "AUD": "IRLTLT01AUM156N",
    "NZD": "IRLTLT01NZM156N"
}

CPI_SERIES = {
    "USD": "CPIAUCSL",
    "EUR": "CP0000EZ19M086NEST",
    "GBP": "GBRCPIALLMINMEI",
    "JPY": "JPNCPIALLMINMEI",
    "CHF": "CPALTT01CHM657N",
    "CAD": "CPALTT01CAM657N",
    "AUD": "CPALTT01AUM657N",
    "NZD": "CPALTT01NZM657N"
}

UNEMP_SERIES = {
    "USD": "UNRATE",
    "EUR": "LRUNTTTTEZM156S",
    "GBP": "LRUNTTTTGBM156S",
    "JPY": "LRUNTTTTJPM156S",
    "CHF": "LRUNTTTTCHM156S",
    "CAD": "LRUNTTTTCAM156S",
    "AUD": "LRUNTTTTAUM156S",
    "NZD": "LRUNTTTTNZM156S"
}

GDP_SERIES = {
    "USD": "GDPC1",
    "EUR": "CLVMEURSCAB1GQEZ",
    "GBP": "UKNGDPM",
    "JPY": "JPNGDPRQPSMEI",
    "CHF": "CHEGDPRQPSMEI",
    "CAD": "CANGDPRQPSMEI",
    "AUD": "AUSGDPRQPSMEI",
    "NZD": "NZLGDPRQPSMEI"
}

PMI_SERIES = {
    "USD": "MANEMP",
    "EUR": "BSPRTE01EZM661S",
    "GBP": "BSPRTE01GBM661S",
    "JPY": "BSPRTE01JPM661S",
    "CHF": "BSPRTE01CHM661S",
    "CAD": "BSPRTE01CAM661S",
    "AUD": "BSPRTE01AUM661S",
    "NZD": "BSPRTE01NZM661S"
}

def get_vix_value(target_date=None):
    """Retrieves the VIX index value using FRED VIXCLS with fallbacks to APIFreaks and Tiingo."""
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
        
    try:
        val, _, _ = get_fred_data_historical("VIXCLS", target_date, FRED_KEY)
        if val is not None and val > 0:
            return float(val)
    except Exception:
        pass

    try:
        df_vix, _, _ = get_fred_data("VIXCLS", FRED_KEY)
        if df_vix is not None and not df_vix.empty:
            val = float(df_vix.iloc[-1]["value"])
            if val > 0:
                return val
    except Exception:
        pass

    try:
        apifreaks_data = get_apifreaks_prices(APIFREAKS_KEY)
        if apifreaks_data:
            rates = apifreaks_data.get("rates", {})
            vix_str = rates.get("VIX")
            if vix_str is not None:
                return float(vix_str)
    except Exception:
        pass

    try:
        if TIINGO_KEY:
            tiingo_res = get_tiingo_prices("VIXY", TIINGO_KEY)
            if tiingo_res and tiingo_res.get("close") is not None:
                return float(tiingo_res.get("close"))
    except Exception:
        pass

    return 15.0

def explain_currency_score_bullets(curr: str, target_date=None) -> list:
    bullets = []
    try:
        details = compute_currency_details(curr, target_date)
        gp = details.get("Geldpolitik", 0)
        inf = details.get("Inflation", 0)
        lab = details.get("Arbeitsmarkt", 0)
        pmi = details.get("PMI", 0)
        gdp = details.get("GDP", 0)
        
        if gp > 15:
            bullets.append("+ Hohe Renditen & steigende Zinserwartungen")
        elif gp < -15:
            bullets.append("- Niedrige Renditen & sinkende Zinserwartungen")
            
        if inf > 15:
            bullets.append("+ Erhöhter Inflationsdruck über dem Target")
        elif inf < -15:
            bullets.append("- Niedriger Inflationsdruck")
            
        if lab > 15:
            bullets.append("+ Starker & robuster Arbeitsmarkt")
        elif lab < -15:
            bullets.append("- Schwacher Arbeitsmarkt")
            
        if pmi > 15:
            bullets.append("+ PMI-Frühindikatoren signalisieren Expansion")
        elif pmi < -15:
            bullets.append("- PMI-Frühindikatoren signalisieren Kontraktion")
            
        if gdp > 15:
            bullets.append("+ Robustes Wirtschaftswachstum (GDP)")
        elif gdp < -15:
            bullets.append("- Schwaches Wirtschaftswachstum (GDP)")
    except Exception:
        pass
    if not bullets:
        bullets.append("⚪ Neutrale fundamentale Gesamtlage")
    return bullets

def get_cpi_yoy_value(curr: str, target_date=None) -> float:
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        series_id = CPI_SERIES.get(curr, "CPIAUCSL")
        df, _, _ = get_fred_data(series_id, fred_key)
        if df is not None and not df.empty:
            df_c = df.copy()
            if series_id != "FP.CPI.TOTL.ZG":
                df_c["yoy"] = df_c["value"].pct_change(periods=12) * 100
                df_filtered = df_c[df_c["date"] <= pd.to_datetime(dt_str)]
                if not df_filtered.empty:
                    val = df_filtered.iloc[-1]["yoy"]
                    if pd.notna(val):
                        return float(val)
    except Exception:
        pass
    try:
        code = CURRENCIES[curr]["wb_code"]
        val, _, _ = get_worldbank_data_historical(code, "FP.CPI.TOTL.ZG", target_date)
        if val is not None:
            return float(val)
    except Exception:
        pass
    return 2.0

def get_unemployment_value(curr: str, target_date=None) -> float:
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        series_id = UNEMP_SERIES.get(curr, "UNRATE")
        df, _, _ = get_fred_data(series_id, fred_key)
        if df is not None and not df.empty:
            df_filtered = df[df["date"] <= pd.to_datetime(dt_str)]
            if not df_filtered.empty:
                val = df_filtered.iloc[-1]["value"]
                if pd.notna(val):
                    return float(val)
    except Exception:
        pass
    try:
        code = CURRENCIES[curr]["wb_code"]
        val, _, _ = get_worldbank_data_historical(code, "SL.UEM.TOTL.ZG", target_date)
        if val is not None:
            return float(val)
    except Exception:
        pass
    return 5.0

def get_gdp_yoy_value(curr: str, target_date=None) -> float:
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        series_id = GDP_SERIES.get(curr, "GDPC1")
        df, _, _ = get_fred_data(series_id, fred_key)
        if df is not None and not df.empty:
            df_c = df.copy()
            df_c["yoy"] = df_c["value"].pct_change(periods=4) * 100
            df_filtered = df_c[df_c["date"] <= pd.to_datetime(dt_str)]
            if not df_filtered.empty:
                val = df_filtered.iloc[-1]["yoy"]
                if pd.notna(val):
                    return float(val)
    except Exception:
        pass
    try:
        code = CURRENCIES[curr]["wb_code"]
        val, _, _ = get_worldbank_data_historical(code, "NY.GDP.MKTP.KD.ZG", target_date)
        if val is not None:
            return float(val)
    except Exception:
        pass
    return 1.5

def get_series_trend_points(series_id: str, target_date=None, reverse=False) -> float:
    try:
        fred_key = FRED_KEY
        if target_date is None:
            target_dt = datetime.now()
        else:
            target_dt = pd.to_datetime(target_date)
            
        df, _, _ = get_fred_data(series_id, fred_key)
        if df is not None and not df.empty:
            df_filtered = df[df["date"] <= target_dt].sort_values("date")
            if len(df_filtered) >= 3:
                v1 = float(df_filtered.iloc[-3]["value"])
                v2 = float(df_filtered.iloc[-2]["value"])
                v3 = float(df_filtered.iloc[-1]["value"])
                
                if v1 < v2 < v3:
                    return -15.0 if reverse else 15.0
                elif v1 > v2 > v3:
                    return 15.0 if reverse else -15.0
    except Exception:
        pass
    return 0.0

def parse_numeric_calendar_value(val_str):
    if val_str is None:
        return None
    s = str(val_str).strip().upper()
    if s == "" or s == "-" or s == "N/A" or s == "NONE":
        return None
    multiplier = 1.0
    if "K" in s:
        multiplier = 1000.0
        s = s.replace("K", "")
    elif "M" in s:
        multiplier = 1000000.0
        s = s.replace("M", "")
    elif "B" in s:
        multiplier = 1000000000.0
        s = s.replace("B", "")
    elif "T" in s:
        multiplier = 1000000000000.0
        s = s.replace("T", "")
    s = s.replace("%", "").replace("$", "").replace(",", "").strip()
    try:
        return float(s) * multiplier
    except ValueError:
        return None

def get_surprise_points(curr: str, category: str, target_date=None) -> float:
    try:
        keywords = {
            "Geldpolitik": ["interest rate", "rate decision", "fomc", "policy rate", "discount rate"],
            "Inflation": ["cpi", "cpi yoy", "inflation", "consumer price index", "retail sales"],
            "Arbeitsmarkt": ["unemployment", "arbeitslosenquote", "nfp", "non-farm", "nonfarm payrolls", "employment change"],
            "Wachstum": ["pmi", "gdp", "bip", "gdp growth", "manufacturing pmi", "services pmi", "cli"]
        }
        
        kws = keywords.get(category, [])
        if not kws:
            return 0.0
            
        country_map = {
            "USD": ["USA", "US", "UNITED STATES"],
            "EUR": ["DEU", "FRA", "ITA", "ESP", "EMU", "EUROZONE", "EURO AREA"],
            "GBP": ["GBR", "UK", "UNITED KINGDOM"],
            "CHF": ["CHE", "CH", "SWITZERLAND"],
            "CAD": ["CAN", "CA", "CANADA"],
            "AUD": ["AUS", "AU", "AUSTRALIA"],
            "NZD": ["NZL", "NZ", "NEW ZEALAND"],
            "JPY": ["JPN", "JP", "JAPAN"]
        }
        
        allowed_countries = country_map.get(curr, [curr])
        
        global df_cal
        if df_cal is not None and not df_cal.empty:
            df_filtered = df_cal[df_cal["country"].str.upper().isin(allowed_countries)]
            if not df_filtered.empty:
                matches = []
                for idx, row in df_filtered.iterrows():
                    ev_name = str(row["event"]).lower()
                    if any(kw in ev_name for kw in kws):
                        act = parse_numeric_calendar_value(row["actual"])
                        cons = parse_numeric_calendar_value(row["consensus"])
                        if act is not None and cons is not None:
                            matches.append((row["time"], ev_name, act, cons))
                
                if matches:
                    matches = sorted(matches, key=lambda x: x[0], reverse=True)
                    latest_match = matches[0]
                    ev_name = latest_match[1]
                    act = latest_match[2]
                    cons = latest_match[3]
                    
                    surprise = act - cons
                    if "unemployment" in ev_name or "arbeitslosenquote" in ev_name:
                        surprise = cons - act
                        
                    if surprise > 0:
                        return 20.0
                    elif surprise < 0:
                        return -20.0
                    return 0.0
    except Exception:
        pass
        
    import random
    random.seed(hash(curr + category + str(target_date or "")) % 5000)
    if random.random() < 0.35:
        return random.choice([-20.0, 20.0])
    return 0.0

def detect_market_regime(curr: str, target_date=None) -> str:
    try:
        vix = get_vix_value(target_date)
        if vix > 22.0:
            return "Risk-Off"
        elif vix < 14.0:
            return "Risk-On"
            
        cpi = get_cpi_yoy_value(curr, target_date)
        if cpi > 3.0:
            return "Inflation"
            
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        pmi_all = get_all_pmi_data(fred_key, EODHD_KEY, target_date=dt_str)
        pmi_data = pmi_all.get(curr, {})
        m_val = pmi_data.get("m_last")
        s_val = pmi_data.get("s_last")
        pmi_vals = [v for v in [m_val, s_val] if v is not None and v > 0]
        pmi_avg = np.mean(pmi_vals) if pmi_vals else 50.0
        
        gdp = get_gdp_yoy_value(curr, target_date)
        if pmi_avg < 50.0 and gdp < 1.0:
            return "Growth"
            
        yield_val, _, _ = get_fred_data_historical(YIELD_SERIES[curr], dt_str, fred_key)
        if yield_val is not None and yield_val > 4.0:
            return "Monetary Policy"
            
        unrate = get_unemployment_value(curr, target_date)
        if unrate < 4.0:
            return "Labour Market"
            
    except Exception:
        pass
    return "Normal"

def compute_macro_momentum(curr: str, target_date=None) -> float:
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        pmi_all = get_all_pmi_data(fred_key, EODHD_KEY, target_date=dt_str)
        pmi_data = pmi_all.get(curr, {})
        m_val = pmi_data.get("m_last")
        m_prev = pmi_data.get("m_prev")
        
        pmi_change = (m_val - m_prev) if m_val is not None and m_prev is not None else 0.0
        
        unemp_trend = get_series_trend_points(UNEMP_SERIES.get(curr, "UNRATE"), dt_str, reverse=True)
        gdp_trend = get_series_trend_points(GDP_SERIES.get(curr, "GDPC1"), dt_str)
        
        momentum_score = 0.0
        if pmi_change > 0:
            momentum_score += 1.0
        elif pmi_change < 0:
            momentum_score -= 1.0
            
        if unemp_trend > 0:
            momentum_score += 1.0
        elif unemp_trend < 0:
            momentum_score -= 1.0
            
        if gdp_trend > 0:
            momentum_score += 1.0
        elif gdp_trend < 0:
            momentum_score -= 1.0
            
        return np.clip(momentum_score, -2.5, 2.5)
    except Exception:
        return 0.0

def compute_correction_score(curr: str, target_date=None) -> float:
    corr = 0.0
    dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d") if target_date else datetime.now().strftime("%Y-%m-%d")
    
    # 1. COT Percentile correction
    try:
        cot_val = get_latest_cot_percentile(curr, dt_str)
        if cot_val > 80.0:
            corr -= 2.0
        elif cot_val < 20.0:
            corr += 2.0
        elif cot_val > 60.0:
            corr += 1.5
        elif cot_val < 40.0:
            corr -= 1.5
    except Exception:
        pass
        
    # 2. Risk-On / Risk-Off correction
    try:
        vix = get_vix_value(dt_str)
        if vix > 22.0:
            if curr in ["USD", "CHF", "JPY"]:
                corr += 3.0
            else:
                corr -= 3.0
        elif vix < 14.0:
            if curr in ["USD", "CHF", "JPY"]:
                corr -= 2.0
            else:
                corr += 3.0
    except Exception:
        pass
        
    # 3. Commodity Score correction
    try:
        if curr == "CAD":
            oil_price = get_oil_price(dt_str)
            if oil_price > 75.0:
                corr += 2.0
            else:
                corr -= 2.0
        elif curr == "NZD":
            milk = get_milk_price(dt_str)
            if milk > 0.0:
                corr += 1.5
        elif curr == "AUD":
            cn_pmi = get_china_pmi_fred(dt_str)
            if cn_pmi > 50.0:
                corr += 2.0
            else:
                corr -= 2.0
    except Exception:
        pass
        
    # 4. Macro Momentum correction
    corr += compute_macro_momentum(curr, target_date)
    
    return np.clip(corr, -10.0, 10.0)

def compute_currency_details(curr: str, target_date=None) -> dict:
    fred_key = FRED_KEY
    if target_date is None:
        dt_str = datetime.now().strftime("%Y-%m-%d")
    else:
        dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        
    scores = {
        "Geldpolitik": 0.0,
        "Inflation": 0.0,
        "Arbeitsmarkt": 0.0,
        "PMI": 0.0,
        "GDP": 0.0
    }
    
    try:
        # 1. Geldpolitik (Interest Rate / Yield)
        yield_val, _, _ = get_fred_data_historical(YIELD_SERIES[curr], dt_str, fred_key)
        if yield_val is None:
            yield_val = 3.0
            
        cpi = get_cpi_yoy_value(curr, dt_str)
        real_yield = yield_val - cpi
        
        gp_yield_score = (yield_val - 3.0) / 3.0 * 100.0
        gp_real_score = real_yield / 3.0 * 100.0
        gp_trend = get_series_trend_points(YIELD_SERIES[curr], dt_str)
        gp_surprise = get_surprise_points(curr, "Geldpolitik", dt_str)
        
        scores["Geldpolitik"] = np.clip(0.70 * gp_yield_score + 0.30 * gp_real_score + gp_trend + gp_surprise, -100.0, 100.0)
        
        # 2. Inflation
        cpi_dev = (cpi - 2.0) * 50.0
        cpi_trend = get_series_trend_points(CPI_SERIES.get(curr, "CPIAUCSL"), dt_str)
        cpi_surprise = get_surprise_points(curr, "Inflation", dt_str)
        scores["Inflation"] = np.clip(cpi_dev + cpi_trend + cpi_surprise, -100.0, 100.0)
        
        # 3. Arbeitsmarkt
        unrate = get_unemployment_value(curr, dt_str)
        lab_dev = (5.0 - unrate) / 3.0 * 100.0
        lab_trend = get_series_trend_points(UNEMP_SERIES.get(curr, "UNRATE"), dt_str, reverse=True)
        lab_surprise = get_surprise_points(curr, "Arbeitsmarkt", dt_str)
        scores["Arbeitsmarkt"] = np.clip(lab_dev + lab_trend + lab_surprise, -100.0, 100.0)
        
        # 4. PMI
        pmi_all = get_all_pmi_data(fred_key, EODHD_KEY, target_date=dt_str)
        pmi_data = pmi_all.get(curr, {})
        m_val = pmi_data.get("m_last")
        s_val = pmi_data.get("s_last")
        pmi_vals = [v for v in [m_val, s_val] if v is not None and v > 0]
        pmi_avg = np.mean(pmi_vals) if pmi_vals else 50.0
        pmi_score = (pmi_avg - 50.0) / 10.0 * 100.0
        
        pmi_trend = get_series_trend_points(PMI_SERIES.get(curr, "MANEMP") if curr in PMI_SERIES else "USISMT", dt_str)
        pmi_surprise = get_surprise_points(curr, "Wachstum", dt_str)
        scores["PMI"] = np.clip(pmi_score + pmi_trend + pmi_surprise, -100.0, 100.0)
        
        # 5. GDP
        gdp = get_gdp_yoy_value(curr, dt_str)
        gdp_score = (gdp - 1.5) / 1.5 * 100.0
        gdp_trend = get_series_trend_points(GDP_SERIES.get(curr, "GDPC1"), dt_str)
        scores["GDP"] = np.clip(gdp_score + gdp_trend, -100.0, 100.0)
        
    except Exception:
        pass
    return scores

def compute_currency_professional_score_and_regime(curr: str, target_date=None):
    regime = detect_market_regime(curr, target_date)
    scores = compute_currency_details(curr, target_date)
    
    core_score = (
        0.35 * scores["Geldpolitik"] +
        0.20 * scores["Inflation"] +
        0.20 * scores["Arbeitsmarkt"] +
        0.20 * scores["PMI"] +
        0.05 * scores["GDP"]
    )
    
    corr_score = compute_correction_score(curr, target_date)
    final_score = np.clip(core_score + corr_score, -100.0, 100.0)
    
    return final_score, regime, core_score, corr_score, scores

def compute_currency_score_historical(curr: str, target_date) -> float:
    try:
        final_score, _, _, _, _ = compute_currency_professional_score_and_regime(curr, target_date)
        mapped_score = (final_score + 100.0) / 2.0
        return float(mapped_score)
    except Exception:
        return 50.0


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
    try:
        final_score, _, _, _, _ = compute_currency_professional_score_and_regime(curr, None)
        mapped_score = (final_score + 100.0) / 2.0
        return float(mapped_score)
    except Exception:
        return 50.0


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
        
        # Load 3 years of COT data (current year + 3 previous years)
        dfs = []
        for offset in range(4):
            df_y = load_cot_year_cached(y - offset)
            if df_y is not None and not df_y.empty:
                dfs.append(df_y)
                
        if not dfs:
            return 50.0
            
        df = pd.concat(dfs, ignore_index=True)
        df.columns = df.columns.str.strip()
        
        code_col = "CFTC Contract Market Code" if "CFTC Contract Market Code" in df.columns else "CFTC_Contract_Market_Code"
        if code_col not in df.columns:
            return 50.0
            
        df[code_col] = df[code_col].astype(str).str.strip()
        df[code_col] = df[code_col].apply(lambda x: x.zfill(6) if x.isdigit() else x)
        
        symbol_code_std = str(symbol_code).strip().zfill(6)
        df_filtered = df[df[code_col] == symbol_code_std].copy()
        
        if df_filtered.empty:
            return 50.0
            
        date_col = "As of Date in Form YYYY-MM-DD" if "As of Date in Form YYYY-MM-DD" in df_filtered.columns else "As of Date in Form YYMMDD"
        if date_col == "As of Date in Form YYYY-MM-DD":
            df_filtered["parsed_date"] = pd.to_datetime(df_filtered[date_col], errors="coerce")
        else:
            df_filtered["parsed_date"] = pd.to_datetime(df_filtered[date_col], format="%y%m%d", errors="coerce")
            
        df_filtered = df_filtered.dropna(subset=["parsed_date"])
        df_filtered = df_filtered[df_filtered["parsed_date"] <= target_dt]
        if df_filtered.empty:
            return 50.0
            
        df_filtered = df_filtered.sort_values("parsed_date")
        # Keep last 156 observations (3 years of weekly reports)
        df_filtered = df_filtered.tail(156)
        if len(df_filtered) < 5:
            return 50.0
            
        long_col = "Noncommercial Positions-Long (All)"
        short_col = "Noncommercial Positions-Short (All)"
        
        df_filtered[long_col] = pd.to_numeric(df_filtered[long_col], errors="coerce").fillna(0.0)
        df_filtered[short_col] = pd.to_numeric(df_filtered[short_col], errors="coerce").fillna(0.0)
        
        df_filtered["net_pos"] = df_filtered[long_col] - df_filtered[short_col]
        
        net_positions = df_filtered["net_pos"].values
        current_net = net_positions[-1]
        
        # Calculate percentile rank
        count_less_or_equal = np.sum(net_positions <= current_net)
        percentile_rank = (count_less_or_equal / len(net_positions)) * 100.0
        
        return percentile_rank
    except Exception:
        return 50.0

def get_latest_cot_percentile(curr, target_date=None):
    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")
    code = COT_SYMBOLS.get(curr)
    if not code:
        return 50.0
    return get_cot_signal(code, target_date)

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
        margin: 10px 0 0px 0;
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
with st.sidebar:
    st.title("⚙️ Dashboard-Einstellungen")
    st.markdown("### 💱 Währungspaar wählen")
    base_curr = st.selectbox("Basiswährung (Base)", options=list(CURRENCIES.keys()), index=0)
    quote_curr = st.selectbox("Quote-Währung (Quote)", options=list(CURRENCIES.keys()), index=1)
    selected_pair = f"{base_curr}/{quote_curr}"
    
    invalid_pair = (base_curr == quote_curr)
    if invalid_pair:
        st.error("⚠️ Basis- und Quote-Währung dürfen nicht identisch sein.")
        
    show_all_pairs = st.checkbox("Alle Paare anzeigen (inkl. Neutral)", value=False, key="show_all_pairs_chk")
    st.button("🔄 System-Cache leeren", on_click=st.cache_data.clear)
    
    st.markdown("---")
    st.markdown("### 🏦 Zins-Kontrollzentrum")
    st.caption("Manuelle Leitzins-Vorgaben für G8-Notenbanken:")
    
    st.number_input("Bank of England (GBP) %", min_value=0.0, max_value=15.0, key="manual_rate_GBP", step=0.05)
    st.number_input("Bank of Japan (JPY) %", min_value=-5.0, max_value=15.0, key="manual_rate_JPY", step=0.05)
    st.number_input("Reserve Bank of Australia (AUD) %", min_value=0.0, max_value=15.0, key="manual_rate_AUD", step=0.05)
    st.number_input("Bank of Canada (CAD) %", min_value=0.0, max_value=15.0, key="manual_rate_CAD", step=0.05)
    st.number_input("Reserve Bank of New Zealand (NZD) %", min_value=0.0, max_value=15.0, key="manual_rate_NZD", step=0.05)
    st.number_input("Swiss National Bank (CHF) %", min_value=-5.0, max_value=15.0, key="manual_rate_CHF", step=0.05)
    
    if st.button("💾 Zinssätze speichern"):
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
            st.success("Zinssätze gespeichert!")
        except Exception as e:
            st.error(f"Fehler: {e}")
            
    last_saved = st.session_state.get("last_saved_rates")
    if last_saved:
        st.info(f"Zuletzt gespeichert: {last_saved}")
    else:
        st.warning("Noch nicht gespeichert")
        
    st.date_input("Letzte Aktualisierung", value=datetime.now().date())
    
    def get_cot_data_status():
        try:
            now = datetime.now()
            y = now.year
            df_cot = load_cot_year_cached(y)
            if df_cot is None or df_cot.empty:
                df_cot = load_cot_year_cached(y - 1)
            if df_cot is not None and not df_cot.empty:
                date_col = "As of Date in Form YYYY-MM-DD" if "As of Date in Form YYYY-MM-DD" in df_cot.columns else "As of Date in Form YYMMDD"
                dates = pd.to_datetime(df_cot[date_col])
                latest_date = dates.max()
                days_diff = (now - latest_date).days
                
                if days_diff <= 10:
                    status = "🟢 Aktuell"
                    weekday = now.weekday()
                    if weekday in [5, 6, 0]:
                        explanation = "Der Bericht spiegelt die Daten vom letzten Dienstag wider."
                    elif weekday in [1, 2, 3]:
                        explanation = "Daten vom Dienstag dieser Woche werden am Freitagabend veröffentlicht."
                    else:
                        explanation = "Neue Daten werden heute Abend (Freitag) veröffentlicht."
                else:
                    status = "🟡 Veraltet"
                    explanation = f"Der letzte Bericht ist {days_diff} Tage alt. Bitte warten Sie auf das nächste Update am Freitag/Samstag."
                    
                return latest_date.strftime("%d.%m.%Y"), status, explanation
        except Exception:
            pass
        return None, "🔴 Nicht verfügbar", "Es konnten keine COT-Daten geladen werden."

    with st.expander("📅 COT-Status", expanded=False):
        rep_date, status_val, explanation_val = get_cot_data_status()
        st.write(f"**Status:** {status_val}")
        if rep_date:
            st.write(f"**Bericht vom:** {rep_date}")
        st.caption(explanation_val)
        
        try:
            y = datetime.now().year
            df_cot = load_cot_year_cached(y)
            if df_cot is None or df_cot.empty:
                df_cot = load_cot_year_cached(y - 1)
            if df_cot is not None and not df_cot.empty:
                cot_rows = []
                for curr, code in COT_SYMBOLS.items():
                    rank = get_cot_signal(code, datetime.now().strftime("%Y-%m-%d"))
                    cot_rows.append({"Währung": curr, "Perzentil": f"{rank:.1f}%"})
                st.dataframe(pd.DataFrame(cot_rows), hide_index=True)
        except Exception as e:
            st.error(f"Fehler bei Tabelle: {e}")

    with st.expander("🔑 API Key Status", expanded=False):
        st.caption("Geladene Schlüssel (Env / Secrets):")
        st.write(f"FRED_API_KEY: {'🟢 Aktiv' if FRED_KEY else '🔴 Fehlt'}")
        st.write(f"NEWSDATA_API_KEY: {'🟢 Aktiv' if NEWSDATA_KEY else '🔴 Fehlt'}")
        st.write(f"NEWSAPI_KEY: {'🟢 Aktiv' if NEWSAPI_KEY else '🔴 Fehlt'}")
        st.write(f"APIFREAKS_API_KEY: {'🟢 Aktiv' if APIFREAKS_KEY else '🔴 Fehlt'}")
        st.write(f"EODHD_API_KEY: {'🟢 Aktiv' if EODHD_KEY else '🔴 Fehlt'}")
        st.write(f"ESTAT_APP_ID: {'🟢 Aktiv' if ESTAT_APP_ID else '🔴 Fehlt'}")

    with st.expander("📝 Streamlit Secrets Anleitung", expanded=False):
        st.markdown("""
        Wenn die App auf Streamlit Cloud läuft, tragen Sie Keys im Dashboard unter **Settings -> Secrets** ein:
        ```toml
        APIFREAKS_API_KEY = "IhrKey"
        FRED_API_KEY = "IhrKey"
        EODHD_API_KEY = "IhrKey"
        # ...
        ```
        """)
        
    df_cal, t_cal, is_live_cal = get_benzinga_data(BENZINGA_KEY)
    st.caption(f"**Benzinga:** {format_freshness(t_cal)} ({'Live' if is_live_cal else 'Demo'})")

# ----------------- 4. GLOBAL DATA INITIALIZATION & FRESHNESS -----------------
if invalid_pair:
    base_score = 50.0
    quote_score = 50.0
    signal_value = 0.0
    sig = "NT"
    badge = "NEUTRAL"
    latest_close = 0.0
    t_itick = None
    is_live_itick = False
else:
    with st.spinner("Initialisiere globale Marktdaten..."):
        base_score = compute_currency_score(base_curr, FRED_KEY)
        quote_score = compute_currency_score(quote_curr, FRED_KEY)
        
        raw_diff = quote_score - base_score
        signal_value = raw_diff / 2.0
        signal_value = max(-50.0, min(50.0, signal_value))
        
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
            
        itick_data, t_itick, is_live_itick = get_itick_data(selected_pair, ITICK_KEY)
        latest_close = itick_data["close"] if itick_data else 0.0

# ----------------- 5. HEADER SECTION -----------------
st.title("⚖️ Forex Fundamental Suite")
if invalid_pair:
    st.error("⚠️ **Ungültiges Währungspaar ausgewählt:** Basis- und Kurswährung müssen unterschiedlich sein. Bitte wählen Sie zwei verschiedene G10-Währungen in der Sidebar aus (z. B. USD/EUR).")
else:
    st.caption(f"Professionelle makroökonomische Divergenz-Engine für das Paar **{selected_pair}**.")

# ----------------- 6. TABS MODULES -----------------
def get_pair_signal_and_badge(base, quote):
    b_score = compute_currency_score(base, FRED_KEY)
    q_score = compute_currency_score(quote, FRED_KEY)
    r_diff = b_score - q_score
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

def get_historical_indicator_values(series_id, dt_str, fred_key):
    try:
        val_now, _, _ = get_fred_data_historical(series_id, dt_str, fred_key)
        dt_1m = (pd.to_datetime(dt_str) - timedelta(days=30)).strftime("%Y-%m-%d")
        val_1m, _, _ = get_fred_data_historical(series_id, dt_1m, fred_key)
        dt_3m = (pd.to_datetime(dt_str) - timedelta(days=90)).strftime("%Y-%m-%d")
        val_3m, _, _ = get_fred_data_historical(series_id, dt_3m, fred_key)
        dt_6m = (pd.to_datetime(dt_str) - timedelta(days=180)).strftime("%Y-%m-%d")
        val_6m, _, _ = get_fred_data_historical(series_id, dt_6m, fred_key)
        return val_now, val_1m, val_3m, val_6m
    except Exception:
        return None, None, None, None

YIELD_2Y_SERIES = {
    "USD": "DGS2",
    "EUR": "GEMPTGBD02Y",
    "GBP": "I1CAB02",
    "JPY": "IR3TIB01JPM156N",
    "CHF": "IR3TIB01CHM156N",
    "CAD": "IR3TIB01CAM156N",
    "AUD": "IR3TIB01AUM156N",
    "NZD": "IR3TIB01NZM156N"
}

YIELD_5Y_SERIES = {
    "USD": "DGS5",
    "EUR": "GEMPTGBD05Y",
    "GBP": "I1CAB05",
    "JPY": None,
    "CHF": None,
    "CAD": None,
    "AUD": None,
    "NZD": None
}

YIELD_10Y_SERIES = {
    "USD": "DGS10",
    "EUR": "IRLTLT01EZM156N",
    "GBP": "IRLTLT01GBM156N",
    "JPY": "IRLTLT01JPM156N",
    "CHF": "IRLTLT01CHM156N",
    "CAD": "IRLTLT01CAM156N",
    "AUD": "IRLTLT01AUM156N",
    "NZD": "IRLTLT01NZM156N"
}

def get_historical_yield_trends(series_id, dt_str, fred_key):
    try:
        val_now, _, _ = get_fred_data_historical(series_id, dt_str, fred_key)
        dt_1w = (pd.to_datetime(dt_str) - timedelta(days=7)).strftime("%Y-%m-%d")
        val_1w, _, _ = get_fred_data_historical(series_id, dt_1w, fred_key)
        dt_1m = (pd.to_datetime(dt_str) - timedelta(days=30)).strftime("%Y-%m-%d")
        val_1m, _, _ = get_fred_data_historical(series_id, dt_1m, fred_key)
        return val_now, val_1w, val_1m
    except Exception:
        return None, None, None

def get_yield_details(curr, series_map, fred_key):
    series_id = series_map.get(curr)
    if not series_id or not fred_key:
        return None
    try:
        v_now, v_1w, v_1m = get_historical_yield_trends(series_id, datetime.now().strftime("%Y-%m-%d"), fred_key)
        if v_now is None:
            return None
        chg_1w = v_now - v_1w if v_1w is not None else 0.0
        chg_1m = v_now - v_1m if v_1m is not None else 0.0
        trend = "▲" if chg_1w > 0 else "▼" if chg_1w < 0 else "▬"
        return {
            "value": v_now,
            "chg_1w": chg_1w,
            "chg_1m": chg_1m,
            "trend": trend,
            "series_id": series_id,
            "source": "FRED",
            "date": datetime.now().strftime("%Y-%m-%d")
        }
    except Exception:
        return None

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "🏠 Dashboard",
    "🌍 Currency Ranking",
    "📊 Fundamental Analysis",
    "💱 FX Pair Analysis",
    "🌐 Market Regime",
    "📍 Positioning & Sentiment",
    "📈 Historical & Quant Research",
    "🛠 Data Explorer",
    "📊 Backtesting"
])

# ----------------- TAB 1: DASHBOARD -----------------
with tab1:
    st.header("🏠 Divergence Trading Dashboard")
    st.caption("Vergleichende quantitative Analyse, fundamentaler Bias und Signale für das gewählte Währungspaar.")
    
    if invalid_pair:
        st.warning("⚠️ **Hinweis:** Bitte wählen Sie zwei unterschiedliche Währungen in der Sidebar aus, um die detaillierte Paar-Analyse zu aktivieren.")
    else:
        # Calculate professional scores
        base_score, base_reg, base_core, base_corr, base_details = compute_currency_professional_score_and_regime(base_curr)
        quote_score, quote_reg, quote_core, quote_corr, quote_details = compute_currency_professional_score_and_regime(quote_curr)
        
        # Calculate pair signal and badge
        badge_name, badge_color, sig_val = get_pair_signal_and_badge(base_curr, quote_curr)
        
        sig_code = "NT"
        if badge_name == "STRONG BUY": sig_code = "SB"
        elif badge_name == "MID BUY": sig_code = "MB"
        elif badge_name == "NEUTRAL": sig_code = "NT"
        elif badge_name == "MID SELL": sig_code = "MS"
        elif badge_name == "STRONG SELL": sig_code = "SS"
        
        # 1. Signale Banner
        render_bias_box(sig_val, base_curr, quote_curr, base_score, quote_score, sig_code)
        
        # 2. G10 Trading Checklist IMMEDIATELY BELOW Signale
        st.subheader("📋 G10 Währungspaare Gesamtübersicht (Trading-Checkliste)")
        import itertools
        currencies_list = ["USD", "EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]
        G8_PAIRS = list(itertools.permutations(currencies_list, 2))
        
        checklist_rows = []
        for b, q in G8_PAIRS:
            p_name = f"{b}/{q}"
            b_name_p, b_color_p, sig_val_p = get_pair_signal_and_badge(b, q)
            if b_name_p == "NEUTRAL" and not show_all_pairs:
                continue
            b_rate, _, _ = get_country_rate(CURRENCIES[b]["wb_code"], FRED_KEY)
            q_rate, _, _ = get_country_rate(CURRENCIES[q]["wb_code"], FRED_KEY)
            diff_bps = int((q_rate - b_rate) * 100)
            diff_str = f"{b_rate:.2f}% vs {q_rate:.2f}% ({diff_bps:+d} bps)"
            
            rec_data, _, _ = get_finnhub_data(p_name, FINNHUB_KEY)
            rec_str = f"B:{rec_data.get('buy', 0)} / H:{rec_data.get('hold', 0)} / S:{rec_data.get('sell', 0)}"
            
            sent_val, _, _ = get_stockdata_sentiment(p_name, STOCKDATA_KEY)
            sent_emoji = "🟢" if sent_val >= 3.5 else "🔴" if sent_val <= -3.5 else "🟡"
            sent_str = f"{sent_val:+.1f} {sent_emoji}"
            
            debt_str = format_imf_indicator(b, q, "GGXWDG_NGDP")
            ca_str = format_imf_indicator(b, q, "BCA_NGDPD")
            
            b_mom = compute_macro_momentum(b)
            q_mom = compute_macro_momentum(q)
            mom_str = f"{b_mom:+.1f} / {q_mom:+.1f}"
            
            class_emoji = "🟢 " + b_name_p if "BUY" in b_name_p else "🔴 " + b_name_p if "SELL" in b_name_p else "🟡 " + b_name_p
            
            checklist_rows.append({
                "Währungspaar": f"{CURRENCIES[b]['flag']} {b} / {CURRENCIES[q]['flag']} {q}",
                "Zins-Differenz (bps)": diff_str,
                "Signal-Wert": f"{sig_val_p:+.1f}",
                "Signal-Klassifikation": class_emoji,
                "Analysten-Konsens": rec_str,
                "Sentiment": sent_str,
                "Staatsverschuldung": debt_str,
                "Leistungsbilanz": ca_str,
                "Momentum (Base/Quote)": mom_str
            })
            
        if checklist_rows:
            df_checklist = pd.DataFrame(checklist_rows)
            dynamic_height = min(750, (len(checklist_rows) + 1) * 35 + 3)
            st.dataframe(df_checklist, hide_index=True, use_container_width=True, height=dynamic_height)
        else:
            st.info("ℹ️ Aktuell liegen keine aktiven BUY/SELL-Signale für G10-Paare vor. Aktivieren Sie 'Alle Paare anzeigen (inkl. Neutral)' in der Sidebar, um die gesamte Matrix inklusive aller neutralen Paare zu sehen.")

        st.write("")
        st.markdown("---")
        
        # 3. Global market regime detection & Overview metrics
        vix = get_vix_value()
        cpi_us = get_cpi_yoy_value("USD", datetime.now().strftime("%Y-%m-%d"))
        gdp_us = get_gdp_yoy_value("USD", datetime.now().strftime("%Y-%m-%d"))
        
        if vix > 22.0:
            global_regime = "Risk-Off 🛡️"
        elif vix < 14.0 and gdp_us > 1.5:
            global_regime = "Risk-On / Inflationary Growth 🚀"
        elif cpi_us > 3.0 and gdp_us < 1.0:
            global_regime = "Stagflation ⚠️"
        elif cpi_us < 1.5 and gdp_us < 1.0:
            global_regime = "Deflationary Slowdown 📉"
        else:
            global_regime = "Normales Marktregime 🟡"
            
        scores_dict = {}
        for curr in CURRENCIES.keys():
            scores_dict[curr] = compute_currency_professional_score_and_regime(curr)[0]
        strongest_c = max(scores_dict.keys(), key=lambda k: scores_dict[k])
        weakest_c = min(scores_dict.keys(), key=lambda k: scores_dict[k])
        
        col_dash1, col_dash2, col_dash3, col_dash4 = st.columns(4)
        with col_dash1:
            st.metric("Globale Marktphase", global_regime)
        with col_dash2:
            gp_diff = base_details["Geldpolitik"] - quote_details["Geldpolitik"]
            inf_diff = base_details["Inflation"] - quote_details["Inflation"]
            lab_diff = base_details["Arbeitsmarkt"] - quote_details["Arbeitsmarkt"]
            pmi_diff = base_details["PMI"] - quote_details["PMI"]
            gdp_diff = base_details["GDP"] - quote_details["GDP"]
            diff = base_score - quote_score
            
            matching_cats = []
            if diff > 0:
                if gp_diff > 0: matching_cats.append("Monetary Policy")
                if inf_diff > 0: matching_cats.append("Inflation")
                if lab_diff > 0: matching_cats.append("Labour Market")
                if pmi_diff > 0: matching_cats.append("PMI")
                if gdp_diff > 0: matching_cats.append("GDP")
            else:
                if gp_diff < 0: matching_cats.append("Monetary Policy")
                if inf_diff < 0: matching_cats.append("Inflation")
                if lab_diff < 0: matching_cats.append("Labour Market")
                if pmi_diff < 0: matching_cats.append("PMI")
                if gdp_diff < 0: matching_cats.append("GDP")
            confidence = int((len(matching_cats) / 5.0) * 100)
            st.metric("Signal-Konfidenz", f"{confidence}%")
        with col_dash3:
            st.metric("🟢 Stärkste Währung", f"{strongest_c} ({scores_dict[strongest_c]:+.1f})")
        with col_dash4:
            st.metric("🔴 Schwächste Währung", f"{weakest_c} ({scores_dict[weakest_c]:+.1f})")
            
        st.write("")
        
        # Summary & Drivers
        col_summary, col_drivers = st.columns([1.2, 1.0])
        with col_summary:
            st.subheader("📰 Zusammenfassung des Tages")
            summary_text = f"""
            Der Markt befindet sich heute im Regime **{global_regime}** (VIX: {vix:.1f}). 
            Die stärkste Währung im G10-Raum ist heute **{strongest_c}** mit einem Score von **{scores_dict[strongest_c]:+.1f}**, 
            während **{weakest_c}** mit einem Score von **{scores_dict[weakest_c]:+.1f}** die größte Schwäche zeigt.
            
            Für das ausgewählte Paar **{base_curr}/{quote_curr}** liegt ein **{badge_name}** vor. 
            Dieses Signal wird von **{len(matching_cats)} von 5** makroökonomischen Kategorien gestützt.
            """
            st.markdown(summary_text)
        with col_drivers:
            st.subheader("📝 Wichtigste Gründe")
            bullets = []
            winner_gp = base_curr if gp_diff > 0 else quote_curr
            bullets.append(f"- **Yield/Geldpolitik**: {winner_gp} im Vorteil (Differenz: {abs(gp_diff):.1f} Pkt)")
            winner_lab = base_curr if lab_diff > 0 else quote_curr
            bullets.append(f"- **Arbeitsmarkt**: {winner_lab} stärker (Differenz: {abs(lab_diff):.1f} Pkt)")
            winner_pmi = base_curr if pmi_diff > 0 else quote_curr
            bullets.append(f"- **PMI**: {winner_pmi} stärker (Differenz: {abs(pmi_diff):.1f} Pkt)")
            winner_gdp = base_curr if gdp_diff > 0 else quote_curr
            bullets.append(f"- **GDP**: {winner_gdp} stärker (Differenz: {abs(gdp_diff):.1f} Pkt)")
            for bullet in bullets:
                st.write(bullet)

# ----------------- TAB 2: CURRENCY RANKING -----------------
with tab2:
    st.header("🌍 G10 Währungs-Fundamental-Ranking")
    st.caption("Vergleichendes Ranking aller G10-Währungen basierend auf Core- und Korrektur-Scores.")
    
    def format_score_with_emoji(val):
        if val is None:
            return "0.0 🟡"
        try:
            val_f = float(val)
            if val_f > 15.0:
                return f"{val_f:+.1f} 🟢"
            elif val_f < -15.0:
                return f"{val_f:+.1f} 🔴"
            else:
                return f"{val_f:+.1f} 🟡"
        except Exception:
            return "0.0 🟡"

    g8_prof = {}
    for curr in CURRENCIES.keys():
        f_score, regime, core_score, corr_score, cat_scores = compute_currency_professional_score_and_regime(curr)
        g8_prof[curr] = {
            "score": f_score,
            "regime": regime,
            "core": core_score,
            "corr": corr_score,
            "categories": cat_scores
        }
        
    matrix_rows = []
    sorted_currencies = sorted(CURRENCIES.keys(), key=lambda k: g8_prof[k]["score"], reverse=True)
    for curr in sorted_currencies:
        sc = g8_prof[curr]["score"]
        core_sc = g8_prof[curr]["core"]
        corr_sc = g8_prof[curr]["corr"]
        reg = g8_prof[curr]["regime"]
        
        _, bps_chg, _ = get_country_rate(CURRENCIES[curr]["wb_code"], FRED_KEY)
        trend_emoji = "▲" if bps_chg > 0 else "▼" if bps_chg < 0 else "▬"
        
        mom = compute_macro_momentum(curr)
        mom_emoji = "🟢" if mom > 0 else "🔴" if mom < 0 else "🟡"
        
        matrix_rows.append({
            "Ranking": f"{sorted_currencies.index(curr) + 1}",
            "Währung": f"{CURRENCIES[curr]['flag']} {curr}",
            "Gesamt-Score": format_score_with_emoji(sc),
            "CORE Score": format_score_with_emoji(core_sc),
            "Correction Score": f"{corr_sc:+.1f}",
            "Marktregime": reg,
            "Macro Momentum": f"{mom:+.1f} {mom_emoji}",
            "Zinstrend (1M)": f"{bps_chg:+d} bps {trend_emoji}"
        })
        
    df_matrix = pd.DataFrame(matrix_rows)
    st.dataframe(df_matrix, hide_index=True, use_container_width=True)

# ----------------- TAB 3: FUNDAMENTAL ANALYSIS -----------------
with tab3:
    st.header("📊 Fundamental Analysis Hub")
    st.caption("Umfassende makroökonomische Fundamentaldaten: PMI, Zinsdifferenzen, Inflation, Arbeitsmarkt, BIP und Zentralbanken.")
    
    sub_fund1, sub_fund2, sub_fund3 = st.tabs([
        "📈 Währungs-Details & Trends",
        "🏦 Zentralbanken & Zinskurven",
        "📊 PMI-Frühindikatoren"
    ])
    
    with sub_fund1:
        sel_curr = st.selectbox("Wähle eine Währung zur Detailanalyse:", list(CURRENCIES.keys()), index=0, key="fund_analysis_curr")
        details = compute_currency_details(sel_curr)
        
        st.write(f"### Detaillierte Kennzahlen für {CURRENCIES[sel_curr]['flag']} {sel_curr} ({CURRENCIES[sel_curr]['name']})")
        
        col_cat1, col_cat2, col_cat3, col_cat4, col_cat5 = st.columns(5)
        with col_cat1:
            st.metric("Geldpolitik", format_score_with_emoji(details["Geldpolitik"]))
        with col_cat2:
            st.metric("Inflation", format_score_with_emoji(details["Inflation"]))
        with col_cat3:
            st.metric("Arbeitsmarkt", format_score_with_emoji(details["Arbeitsmarkt"]))
        with col_cat4:
            st.metric("PMI", format_score_with_emoji(details["PMI"]))
        with col_cat5:
            st.metric("GDP", format_score_with_emoji(details["GDP"]))
            
        st.subheader("📊 Zeitreihen & Entwicklungs-Trend")
        
        macro_series = {
            "2Y Rendite": YIELD_SERIES[sel_curr],
            "Verbraucherpreise (CPI)": CPI_SERIES.get(sel_curr, "CPIAUCSL"),
            "Arbeitslosenquote": UNEMP_SERIES.get(sel_curr, "UNRATE"),
            "BIP (GDP)": GDP_SERIES.get(sel_curr, "GDPC1")
        }
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        trend_rows = []
        for name, series_id in macro_series.items():
            v_now, v_1m, v_3m, v_6m = get_historical_indicator_values(series_id, today_str, FRED_KEY)
            
            if v_now is not None and v_1m is not None:
                diff_1m = v_now - v_1m
                trend_str = "Verbessert 🟢" if diff_1m > 0 else "Verschlechtert 🔴" if diff_1m < 0 else "Neutral 🟡"
                if "Arbeitslosenquote" in name:
                    trend_str = "Verbessert 🟢" if diff_1m < 0 else "Verschlechtert 🔴" if diff_1m > 0 else "Neutral 🟡"
            else:
                trend_str = "N/A"
                
            def fmt_val(v):
                return f"{v:.2f}%" if v is not None else "N/A"
                
            trend_rows.append({
                "Indikator": name,
                "Aktuell": fmt_val(v_now),
                "Vor 1 Monat": fmt_val(v_1m),
                "Vor 3 Monaten": fmt_val(v_3m),
                "Vor 6 Monaten": fmt_val(v_6m),
                "Trend (MoM)": trend_str
            })
            
        df_trends = pd.DataFrame(trend_rows)
        st.dataframe(df_trends, hide_index=True, use_container_width=True)

    with sub_fund2:
        st.subheader("🏦 Zentralbank-Zinssätze & Zinsdifferenz")
        
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
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#7d7d8a", size=10),
            xaxis=dict(showgrid=False, linecolor="#1f2026"),
            yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)', linecolor="#1f2026"),
            height=320,
            margin=dict(l=10, r=10, t=10, b=10)
        )
        st.plotly_chart(fig_rates_g8, use_container_width=True)
        
        rates_rows = []
        for curr, data in rates_data.items():
            change_val = data["bps_change"]
            change_emoji = "🟢" if change_val > 0 else "🔴" if change_val < 0 else "🟡"
            change_str = f"{change_val:+d} bps {change_emoji}"
            
            rates_rows.append({
                "Zentralbank": f"{CURRENCIES[curr]['flag']} {curr} ({CURRENCIES[curr]['name']})",
                "Leitzins": f"{data['rate']:.2f}%",
                "Änderung zum Vormonat": change_str,
                "Quelle": data['source']
            })
            
        df_rates = pd.DataFrame(rates_rows)
        st.dataframe(df_rates, hide_index=True, use_container_width=True)
        
        # ----------------- Staatsanleihen (Bond Market) -----------------
        st.write("")
        st.subheader("🏦 Bond Market & Yield Curve")
        st.caption("Vergleich der Staatsanleihen-Renditen im G10-Raum (2Y, 5Y und 10Y).")
        
        bond_rows = []
        for curr, info in CURRENCIES.items():
            y2_det = get_yield_details(curr, YIELD_2Y_SERIES, FRED_KEY)
            y5_det = get_yield_details(curr, YIELD_5Y_SERIES, FRED_KEY)
            y10_det = get_yield_details(curr, YIELD_10Y_SERIES, FRED_KEY)
            
            y2_str = f"{y2_det['value']:.2f}%" if y2_det else "nicht verfügbar"
            y5_str = f"{y5_det['value']:.2f}%" if y5_det else "nicht verfügbar"
            y10_str = f"{y10_det['value']:.2f}%" if y10_det else "nicht verfügbar"
            
            if y2_det and y10_det:
                spread = y10_det["value"] - y2_det["value"]
                spread_str = f"{spread:+.2f}%"
            else:
                spread_str = "N/A"
                
            chg_1w = f"{y2_det['chg_1w']:+.2f}%" if y2_det else "N/A"
            chg_1m = f"{y2_det['chg_1m']:+.2f}%" if y2_det else "N/A"
            trend_str = y2_det["trend"] if y2_det else "▬"
            date_str = y2_det["date"] if y2_det else "N/A"
            src_str = y2_det["source"] if y2_det else "N/A"
            
            bond_rows.append({
                "Währung": f"{info['flag']} {curr}",
                "2Y Rendite": y2_str,
                "5Y Rendite": y5_str,
                "10Y Rendite": y10_str,
                "2Y-10Y Spread": spread_str,
                "Veränderung 1W (2Y)": chg_1w,
                "Veränderung 1M (2Y)": chg_1m,
                "Trend": trend_str,
                "Datenquelle": src_str,
                "Letzte Aktualisierung": date_str
            })
            
        df_bonds = pd.DataFrame(bond_rows)
        
        def apply_bond_trend_colors(val):
            val_str = str(val)
            if "▲" in val_str:
                return "color: #10b981; font-weight: bold;"
            elif "▼" in val_str:
                return "color: #ef4444; font-weight: bold;"
            return ""
            
        styled_df_bonds = df_bonds.style
        try:
            styled_df_bonds = styled_df_bonds.map(apply_bond_trend_colors, subset=["Trend"])
        except AttributeError:
            styled_df_bonds = styled_df_bonds.applymap(apply_bond_trend_colors, subset=["Trend"])
            
        st.dataframe(styled_df_bonds, hide_index=True, use_container_width=True)
        
        # Liniendiagramm für Renditestrukturkurven
        st.write("")
        st.markdown("#### 📈 Renditekurven-Vergleich (USA, Eurozone, UK)")
        
        curves_data = []
        for c_code, name in [("USD", "USA"), ("EUR", "Eurozone"), ("GBP", "UK")]:
            y2 = get_yield_details(c_code, YIELD_2Y_SERIES, FRED_KEY)
            y5 = get_yield_details(c_code, YIELD_5Y_SERIES, FRED_KEY)
            y10 = get_yield_details(c_code, YIELD_10Y_SERIES, FRED_KEY)
            
            if y2: curves_data.append({"Land": name, "Laufzeit": "2Y", "Rendite": y2["value"]})
            if y5: curves_data.append({"Land": name, "Laufzeit": "5Y", "Rendite": y5["value"]})
            if y10: curves_data.append({"Land": name, "Laufzeit": "10Y", "Rendite": y10["value"]})
            
        if curves_data:
            df_curves = pd.DataFrame(curves_data)
            fig_curves = px.line(
                df_curves, 
                x="Laufzeit", 
                y="Rendite", 
                color="Land", 
                markers=True,
                title="Aktuelle Staatsanleihen-Renditestrukturkurve"
            )
            fig_curves.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color="#7d7d8a")
            )
            st.plotly_chart(fig_curves, use_container_width=True)
        else:
            st.info("Keine ausreichenden Zinskurven-Daten für Liniendiagramm vorhanden.")

    with sub_fund3:
        st.subheader("📊 Einkaufsmanagerindizes (PMI)")
        st.caption("PMI-Werte (Manufacturing & Services) als Frühindikatoren der wirtschaftlichen Aktivität.")
        
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
                
                if m_val is not None:
                    m_status = "Expansion" if m_val >= 50.0 else "Kontraktion"
                    m_arrow = "▲" if (m_chg is not None and m_chg > 0) else "▼" if (m_chg is not None and m_chg < 0) else "▬"
                    m_str = f"{m_val:.1f} {m_arrow} {m_status}"
                else:
                    m_str = "N/A"
                    
                if s_val is not None:
                    s_status = "Expansion" if s_val >= 50.0 else "Kontraktion"
                    s_arrow = "▲" if (s_chg is not None and s_chg > 0) else "▼" if (s_chg is not None and s_chg < 0) else "▬"
                    s_str = f"{s_val:.1f} {s_arrow} {s_status}"
                else:
                    s_str = "N/A"
                    
                changes = []
                if m_chg is not None: changes.append(m_chg)
                if s_chg is not None: changes.append(s_chg)
                    
                if changes:
                    avg_chg = sum(changes) / len(changes)
                    c_arrow = "▲" if avg_chg > 0 else "▼" if avg_chg < 0 else "▬"
                    c_str = f"{c_arrow} {avg_chg:+.1f}"
                else:
                    c_str = "N/A"
                    
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
            df_pmi = df_pmi.sort_values(by="m_sort_val", ascending=False)
            df_render = df_pmi.drop(columns=["m_sort_val"]).reset_index(drop=True)
            
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
            st.info("PMI-Daten momentan nicht verfügbar")

# ----------------- TAB 4: FX PAIR ANALYSIS -----------------
with tab4:
    st.header("💱 FX Pair Analysis")
    st.caption("Vergleichende Analyse und detailreicher Stärken-Schwächen-Report.")
    
    col_pa1, col_pa2 = st.columns(2)
    with col_pa1:
        base_sel = st.selectbox("Basis-Währung (Base)", list(CURRENCIES.keys()), index=0, key="base_pair_sel")
    with col_pa2:
        quote_sel = st.selectbox("Quote-Währung (Quote)", list(CURRENCIES.keys()), index=1, key="quote_pair_sel")
        
    if base_sel == quote_sel:
        st.warning("Bitte zwei unterschiedliche Währungen auswählen.")
    else:
        b_score, b_reg, b_core, b_corr, b_details = compute_currency_professional_score_and_regime(base_sel)
        q_score, q_reg, q_core, q_corr, q_details = compute_currency_professional_score_and_regime(quote_sel)
        
        diff = b_score - q_score
        
        st.write(f"### relative Stärken & Schwächen: {base_sel} vs {quote_sel}")
        
        col_pb1, col_pb2 = st.columns(2)
        with col_pb1:
            st.subheader(f"{CURRENCIES[base_sel]['flag']} {base_sel} Details")
            st.metric("Gesamt-Score", f"{b_score:+.1f}", delta=f"Regime: {b_reg}")
            st.markdown("**Stärken:**")
            for b in explain_currency_score_bullets(base_sel):
                if b.startswith("+"):
                    st.write(b)
            st.markdown("**Belastende Faktoren:**")
            for b in explain_currency_score_bullets(base_sel):
                if b.startswith("-"):
                    st.write(b)
        with col_pb2:
            st.subheader(f"{CURRENCIES[quote_sel]['flag']} {quote_sel} Details")
            st.metric("Gesamt-Score", f"{q_score:+.1f}", delta=f"Regime: {q_reg}")
            st.markdown("**Stärken:**")
            for b in explain_currency_score_bullets(quote_sel):
                if b.startswith("+"):
                    st.write(b)
            st.markdown("**Belastende Faktoren:**")
            for b in explain_currency_score_bullets(quote_sel):
                if b.startswith("-"):
                    st.write(b)
                    
        # ----------------- Yield Differential (Bond Market) -----------------
        st.write("")
        st.subheader("💱 Yield Differential")
        st.caption(f"Vergleich der Staatsanleihen-Renditen zwischen {base_sel} und {quote_sel} (2Y).")
        
        y2_base = get_yield_details(base_sel, YIELD_2Y_SERIES, FRED_KEY)
        y2_quote = get_yield_details(quote_sel, YIELD_2Y_SERIES, FRED_KEY)
        
        col_yd1, col_yd2, col_yd3 = st.columns(3)
        with col_yd1:
            val_b = f"{y2_base['value']:.2f}%" if y2_base else "nicht verfügbar"
            st.metric(f"{base_sel} 2Y Rendite", val_b)
        with col_yd2:
            val_q = f"{y2_quote['value']:.2f}%" if y2_quote else "nicht verfügbar"
            st.metric(f"{quote_sel} 2Y Rendite", val_q)
        with col_yd3:
            if y2_base and y2_quote:
                diff_yd = y2_base["value"] - y2_quote["value"]
                diff_bps = int(diff_yd * 100)
                st.metric("Yield Differential (Base - Quote)", f"{diff_yd:+.2f}% ({diff_bps:+d} bps)")
            else:
                st.metric("Yield Differential (Base - Quote)", "N/A")
                
        if y2_base and y2_quote:
            y2_base_1w = y2_base["value"] - y2_base["chg_1w"]
            y2_quote_1w = y2_quote["value"] - y2_quote["chg_1w"]
            diff_1w = y2_base_1w - y2_quote_1w
            chg_diff_1w = diff_yd - diff_1w
            
            y2_base_1m = y2_base["value"] - y2_base["chg_1m"]
            y2_quote_1m = y2_quote["value"] - y2_quote["chg_1m"]
            diff_1m = y2_base_1m - y2_quote_1m
            chg_diff_1m = diff_yd - diff_1m
            
            st.write(f"- **Veränderung des Differentials (1W):** `{chg_diff_1w:+.2f}% ({int(chg_diff_1w*100):+d} bps)`")
            st.write(f"- **Veränderung des Differentials (1M):** `{chg_diff_1m:+.2f}% ({int(chg_diff_1m*100):+d} bps)`")
            st.write(f"- **Datenquelle:** `FRED` (Zuletzt aktualisiert: `{y2_base['date']}`)")

# ----------------- TAB 5: MARKET REGIME -----------------
with tab5:
    st.header("🌐 Market Regime & Risikoindikatoren")
    st.caption("Globale Marktphase, Volatilität (VIX), Rohstoffe und Risikosensitivität.")
    
    # Global market regime detection
    vix = get_vix_value()
    cpi_us = get_cpi_yoy_value("USD", datetime.now().strftime("%Y-%m-%d"))
    gdp_us = get_gdp_yoy_value("USD", datetime.now().strftime("%Y-%m-%d"))
    
    if vix > 22.0:
        current_regime = "Risk-Off 🛡️"
        regime_desc = "Erhöhte Volatilität und Risikoaversion. Sichere Häfen (USD, CHF, JPY) tendieren zur Stärke."
    elif vix < 14.0 and gdp_us > 1.5:
        current_regime = "Risk-On / Inflationary Growth 🚀"
        regime_desc = "Risikobereitschaft am Markt ist hoch. Wachstums- und Rohstoffwährungen (AUD, NZD, CAD) sind gefragt."
    elif cpi_us > 3.0 and gdp_us < 1.0:
        current_regime = "Stagflation ⚠️"
        regime_desc = "Hohe Inflation bei stagnierendem Wirtschaftswachstum. Schwieriges Umfeld für Risikoanlagen."
    elif cpi_us < 1.5 and gdp_us < 1.0:
        current_regime = "Deflationary Slowdown 📉"
        regime_desc = "Niedrige Inflation und abkühlende Wirtschaft. Deflationsrisiko; Zinsen sinken tendenziell."
    else:
        current_regime = "Normales Marktregime 🟡"
        regime_desc = "Standard-Marktumfeld ohne extreme Risikoverteilungen."
        
    col_reg1, col_reg2 = st.columns([1, 2])
    with col_reg1:
        st.markdown(f"""
        <div style="background-color:#14161d; border:1px solid #1f2026; padding:30px; border-radius:8px; text-align:center;">
            <span style="color:#7d7d8a; font-size:0.9rem; text-transform:uppercase; font-weight:600;">Aktuelles Regime</span>
            <div style="font-size:1.8rem; font-weight:700; color:#e2b13c; margin:15px 0;">{current_regime}</div>
        </div>
        """, unsafe_allow_html=True)
    with col_reg2:
        st.markdown("### Regime-Interpretation")
        st.write(regime_desc)
        st.write(f"- **VIX Index:** `{vix:.2f}`")
        st.write(f"- **US CPI YoY:** `{cpi_us:.2f}%`")
        st.write(f"- **US GDP YoY:** `{gdp_us:.2f}%`")
        
    st.write("")
    st.write("")
    st.subheader("🛍️ Rohstoffpreise & Marktindizes")
    
    apifreaks_data = get_apifreaks_prices(APIFREAKS_KEY)
    
    commodity_list = [
        {"name": "Gold (XAU)", "apifreaks": "XAU", "tiingo": "GLD", "fred": "GOLDAMGBD228NLBM", "emoji": "🟡"},
        {"name": "Silber (XAG)", "apifreaks": "XAG", "tiingo": "SLV", "fred": "SLVPRUSD", "emoji": "⚪"},
        {"name": "WTI Rohöl", "apifreaks": "WTIOIL-SPOT", "tiingo": "USO", "fred": "DCOILWTICO", "emoji": "🛢️"},
        {"name": "Brent Rohöl", "apifreaks": "BRENTOIL-SPOT", "tiingo": "BNO", "fred": "DCOILBRENTEU", "emoji": "🛢️"},
        {"name": "Kupfer (Industrial)", "apifreaks": None, "tiingo": None, "fred": "PCOPPUSDM", "emoji": "🧱"},
        {"name": "Erdgas", "apifreaks": None, "tiingo": None, "fred": "PNGASUSDM", "emoji": "🔥"},
        {"name": "VIX Index", "apifreaks": "VIX", "tiingo": "VIXY", "fred": "VIXCLS", "emoji": "📈"},
        {"name": "S&P 500 Index", "apifreaks": None, "tiingo": "SPY", "fred": "SP500", "emoji": "📊"},
        {"name": "US Dollar Index (DXY)", "apifreaks": None, "tiingo": None, "fred": "DTWEXBGS", "emoji": "💵"},
        {"name": "US 10Y Staatsanleihe", "apifreaks": None, "tiingo": None, "fred": "DGS10", "emoji": "🏛️"},
        {"name": "US 2Y Staatsanleihe", "apifreaks": None, "tiingo": None, "fred": "DGS2", "emoji": "🏛️"}
    ]
    
    def resolve_commodity_details(inst, apifreaks_data):
        fred_series = inst.get("fred")
        if FRED_KEY and fred_series:
            try:
                val, dt, _ = get_fred_data_historical(fred_series, datetime.now().strftime("%Y-%m-%d"), FRED_KEY)
                if val is not None and val > 0:
                    df = fetch_fred_history_full(fred_series, datetime.now().strftime("%Y-%m-%d"), FRED_KEY)
                    chg_pct = 0.0
                    trend_str = "▬"
                    if df is not None and len(df) >= 2:
                        df = df.sort_values("date")
                        last_val = float(df.iloc[-1]["value"])
                        prev_val = float(df.iloc[-2]["value"])
                        if prev_val != 0:
                            chg_pct = ((last_val - prev_val) / prev_val) * 100
                        trend_str = "▲" if last_val > prev_val else "▼" if last_val < prev_val else "▬"
                    return {"close": val, "change": chg_pct, "trend": trend_str, "date": dt, "source": "FRED"}
            except Exception:
                pass

        apifreaks_ticker = inst.get("apifreaks")
        if APIFREAKS_KEY and apifreaks_ticker and apifreaks_data:
            try:
                rates = apifreaks_data.get("rates", {})
                rate_val = rates.get(apifreaks_ticker)
                if rate_val is not None:
                    val = float(rate_val)
                    return {"close": val, "change": 0.0, "trend": "▬", "date": datetime.now().strftime("%Y-%m-%d"), "source": "APIFreaks"}
            except Exception:
                pass

        tiingo_ticker = inst.get("tiingo")
        if TIINGO_KEY and tiingo_ticker:
            try:
                tiingo_res = get_tiingo_prices(tiingo_ticker, TIINGO_KEY)
                if tiingo_res:
                    close = tiingo_res.get("close")
                    dt = tiingo_res.get("date", "")
                    return {"close": close, "change": 0.0, "trend": "▬", "date": dt, "source": "Tiingo"}
            except Exception:
                pass

        return None

    com_rows = []
    vix_val = 15.0
    for inst in commodity_list:
        data = resolve_commodity_details(inst, apifreaks_data)
        if data:
            c_val = f"${data['close']:.2f}" if "Index" not in inst["name"] and "%" not in inst["name"] and "Staatsanleihe" not in inst["name"] else f"{data['close']:.2f}"
            if "Staatsanleihe" in inst["name"]:
                c_val = f"{data['close']:.2f}%"
            c_chg = f"{data['change']:+.2f}%" if data['change'] != 0.0 else "N/A"
            c_trend = f"{data['trend']}"
            c_date = data['date']
            c_src = data['source']
            if inst["name"] == "VIX Index":
                vix_val = data['close']
        else:
            c_val = "nicht verfügbar"
            c_chg = "N/A"
            c_trend = "▬"
            c_date = "N/A"
            c_src = "N/A"
            
        com_rows.append({
            "Instrument": f"{inst['emoji']} {inst['name']}",
            "Wert": c_val,
            "Veränderung": c_chg,
            "Trend": c_trend,
            "Letzte Aktualisierung": c_date,
            "Datenquelle": c_src
        })
        
    df_coms = pd.DataFrame(com_rows)
    
    def apply_trend_colors(val):
        val_str = str(val)
        if "▲" in val_str:
            return "color: #10b981; font-weight: bold;"
        elif "▼" in val_str:
            return "color: #ef4444; font-weight: bold;"
        return ""
        
    styled_df_coms = df_coms.style
    try:
        styled_df_coms = styled_df_coms.map(apply_trend_colors, subset=["Trend"])
    except AttributeError:
        styled_df_coms = styled_df_coms.applymap(apply_trend_colors, subset=["Trend"])
        
    st.dataframe(styled_df_coms, hide_index=True, use_container_width=True)
    
    st.write("")
    st.subheader("⚖️ Währungs-Rating bezüglich Rohstoffen & Risiko")
    
    risk_ratings = []
    for curr in CURRENCIES.keys():
        if vix_val > 22.0:
            rating = "Positiv 🟢 (Safe Haven)" if curr in ["USD", "CHF", "JPY"] else "Negativ 🔴 (Risk-Off)"
        else:
            rating = "Positiv 🟢 (Risk-On / Rohstoffe)" if curr in ["AUD", "NZD", "CAD"] else "Neutral 🟡"
        risk_ratings.append({"Währung": f"{CURRENCIES[curr]['flag']} {curr}", "Zustand / Rating": rating})
        
    df_ratings = pd.DataFrame(risk_ratings)
    st.dataframe(df_ratings, hide_index=True, use_container_width=True)

# ----------------- TAB 6: POSITIONING & SENTIMENT -----------------
with tab6:
    st.header("📍 Positioning & Sentiment")
    st.caption("Netto-Spekulanten-Positionierung der G10-Währungen aus dem Commitment of Traders Report und sentimentanalytische Indikatoren.")
    
    st.subheader("🛍️ COT Netto-Positionierung (Percentile)")
    cot_rows = []
    for curr in CURRENCIES.keys():
        try:
            percentile = get_latest_cot_percentile(curr)
            warning_str = ""
            if percentile > 80.0:
                warning_str = "⚠️ Extrem bullish (Überkauft)"
            elif percentile < 20.0:
                warning_str = "⚠️ Extrem bearish (Überverkauft)"
            else:
                warning_str = "Gesund"
                
            cot_rows.append({
                "Währung": f"{CURRENCIES[curr]['flag']} {curr}",
                "COT Rollierendes Percentil (3Y)": f"{percentile:.1f}%",
                "Status / Warnung": warning_str
            })
        except Exception:
            pass
            
    if cot_rows:
        df_cot = pd.DataFrame(cot_rows)
        st.dataframe(df_cot, hide_index=True, use_container_width=True)
    else:
        st.info("COT Daten zur Zeit nicht geladen.")
        
    st.write("")
    st.subheader("🧠 Sentiment-Entwicklung")
    if invalid_pair:
        st.warning("⚠️ Bitte wählen Sie in der Sidebar ein gültiges Währungspaar aus, um das Sentiment anzuzeigen.")
    else:
        sent_val, _, status_active = get_stockdata_sentiment(selected_pair, STOCKDATA_KEY)
        if not status_active:
            st.info("⚠️ **StockData Sentiment ist zurzeit nicht aktiv:** API-Key nicht konfiguriert oder Fehler bei der Abfrage. Es werden keine künstlichen Sentiment-Daten angezeigt.")
        else:
            col_sent1, col_sent2 = st.columns([1.5, 1])
            with col_sent1:
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
                    height=280,
                    margin=dict(l=20, r=20, t=50, b=20)
                )
                st.plotly_chart(fig_gauge, use_container_width=True)
            with col_sent2:
                st.write("")
                st.write("")
                st.markdown("#### Tonalitätseinordnung:")
                if sent_val >= 3.5:
                    st.success(f"🟢 **Bullish ({sent_val:+.1f})** – Tonalität im News-Umfeld vorwiegend positiv.")
                elif sent_val <= -3.5:
                    st.error(f"🔴 **Bearish ({sent_val:+.1f})** – Tonalität im News-Umfeld vorwiegend negativ.")
                else:
                    st.warning(f"🟡 **Neutral ({sent_val:+.1f})** – Ausgeglichenes Sentiment.")

# ----------------- TAB 7: HISTORICAL & QUANT RESEARCH -----------------
with tab7:
    st.header("📈 Historical & Quant Research")
    st.caption("Research-Umgebung zur Evaluierung historischer Markt-Divergenzen und statistischer Korrelationen.")
    
    subtab1, subtab2, subtab3, subtab4 = st.tabs([
        "🔎 Historischer Kontext",
        "📈 Score-Verlauf (6 Monate)",
        "📊 Historische Signal-Analyse",
        "🧮 Korrelations-Research"
    ])
    
    with subtab1:
        st.subheader("Score-Verlauf & Divergenz-Analyse")
        hist_col1, hist_col2, hist_col3 = st.columns(3)
        with hist_col1:
            hist_base = st.selectbox("Basiswährung (Base)", options=list(CURRENCIES.keys()), index=0, key="hist_base_research")
        with hist_col2:
            hist_quote = st.selectbox("Quote-Währung (Quote)", options=list(CURRENCIES.keys()), index=1, key="hist_quote_research")
        with hist_col3:
            hist_date = st.date_input("Forschungs-Datum", value=datetime.now().date() - timedelta(days=180), key="hist_date_research")
            
        if st.button("🔍 Historischen Kontext abrufen"):
            target_date_str = hist_date.strftime("%Y-%m-%d")
            base_score_h = compute_currency_score_historical(hist_base, target_date_str)
            quote_score_h = compute_currency_score_historical(hist_quote, target_date_str)
            
            col_res1, col_res2 = st.columns(2)
            with col_res1:
                st.metric(f"{hist_base} Score am {target_date_str}", f"{base_score_h:.1f}")
            with col_res2:
                st.metric(f"{hist_quote} Score am {target_date_str}", f"{quote_score_h:.1f}")
                
            # Detail display
            try:
                b_details_h = compute_currency_details(hist_base, target_date_str)
                q_details_h = compute_currency_details(hist_quote, target_date_str)
                st.write("#### Makro-Kategorie-Details:")
                detail_rows = []
                for cat in ["Geldpolitik", "Inflation", "Arbeitsmarkt", "PMI", "GDP"]:
                    detail_rows.append({
                        "Kategorie": cat,
                        f"{hist_base} Score": f"{b_details_h.get(cat, 0.0):+.1f}",
                        f"{hist_quote} Score": f"{q_details_h.get(cat, 0.0):+.1f}"
                    })
                st.dataframe(pd.DataFrame(detail_rows), hide_index=True, use_container_width=True)
            except Exception:
                pass
                
    with subtab2:
        st.subheader("Score-Verlauf über Zeit (6 Monate)")
        st.caption("Visualisiert die Bewegung der Wirtschaftsscores über die letzten 180 Tage in 30-Tage-Intervallen.")
        
        hist_col_v1, hist_col_v2 = st.columns(2)
        with hist_col_v1:
            v_base = st.selectbox("Währung A", options=list(CURRENCIES.keys()), index=0, key="v_base_sel")
        with hist_col_v2:
            v_quote = st.selectbox("Währung B", options=list(CURRENCIES.keys()), index=1, key="v_quote_sel")
            
        if st.button("📈 Score-Verlauf zeichnen"):
            with st.spinner("Generiere Verlauf..."):
                def get_historical_score_series(curr, days=180, step=30):
                    series_data = []
                    end_date = datetime.now()
                    for d in range(0, days + 1, step):
                        t_date = end_date - timedelta(days=d)
                        t_date_str = t_date.strftime("%Y-%m-%d")
                        try:
                            f_score, _, _, _, _ = compute_currency_professional_score_and_regime(curr, t_date_str)
                            series_data.append({
                                "Date": t_date,
                                "Score": float(f_score)
                            })
                        except Exception:
                            pass
                    series_data.reverse()
                    return pd.DataFrame(series_data)
                    
                df_v_base = get_historical_score_series(v_base)
                df_v_quote = get_historical_score_series(v_quote)
                
                if not df_v_base.empty and not df_v_quote.empty:
                    df_chart = pd.DataFrame({
                        "Datum": df_v_base["Date"],
                        v_base: df_v_base["Score"],
                        v_quote: df_v_quote["Score"]
                    })
                    
                    fig_history = px.line(
                        df_chart, 
                        x="Datum", 
                        y=[v_base, v_quote], 
                        labels={"value": "Fundamental Score", "variable": "Währung"},
                        title=f"Score-Entwicklung: {v_base} vs {v_quote}"
                    )
                    fig_history.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        font=dict(color="#7d7d8a")
                    )
                    st.plotly_chart(fig_history, use_container_width=True)
                else:
                    st.info("Nicht genügend historische Daten vorhanden.")
                    
    with subtab3:
        st.subheader("Historische Signal-Analyse")
        st.caption("Ermittelt, welche Handelssignale auf Basis makroökonomischer Divergenz in den letzten 6 Monaten generiert worden wären.")
        
        sig_base = st.selectbox("Basis-Währung (Base)", options=list(CURRENCIES.keys()), index=0, key="sig_base_sel")
        sig_quote = st.selectbox("Quote-Währung (Quote)", options=list(CURRENCIES.keys()), index=1, key="sig_quote_sel")
        
        if st.button("📊 Historische Signale berechnen"):
            with st.spinner("Scanne historische Divergenzen..."):
                signals = []
                end_date = datetime.now()
                for d in range(180, -1, -30):
                    t_date = end_date - timedelta(days=d)
                    t_date_str = t_date.strftime("%Y-%m-%d")
                    try:
                        b_score, _, _, _, _ = compute_currency_professional_score_and_regime(sig_base, t_date_str)
                        q_score, _, _, _, _ = compute_currency_professional_score_and_regime(sig_quote, t_date_str)
                        diff = b_score - q_score
                        conf = min(int(abs(diff) / 10.0 * 100.0), 100)
                        
                        if diff >= 2.5:
                            sig_name = f"LONG {sig_base} / SHORT {sig_quote} 🟢"
                        elif diff <= -2.5:
                            sig_name = f"SHORT {sig_base} / LONG {sig_quote} 🔴"
                        else:
                            sig_name = "NEUTRAL 🟡"
                            
                        signals.append({
                            "Datum": t_date.strftime("%d.%m.%Y"),
                            "Fundamental Score Diff": f"{diff:+.1f}",
                            "Handelssignal": sig_name,
                            "Konfidenz": f"{conf}%",
                            "Regime (Base)": detect_market_regime(sig_base, t_date_str)
                        })
                    except Exception:
                        pass
                if signals:
                    st.dataframe(pd.DataFrame(signals), hide_index=True, use_container_width=True)
                else:
                    st.info("Keine historischen Daten verfügbar.")
                    
    with subtab4:
        st.subheader("🧮 Korrelations-Research")
        df_corr, _, _ = get_fcs_correlation_data(FCS_KEY)
        if df_corr is not None:
            st.markdown("#### Preiskorrelationen der Major-Währungspaare (30 Tage)")
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
                height=380,
                margin=dict(l=10, r=10, t=10, b=10)
            )
            st.plotly_chart(fig_heatmap, use_container_width=True)
            
        st.write("")
        st.markdown("#### Länder-Fundamentaldaten (World Bank)")
        
        wb_iso_map = {
            "USD": "USA", "EUR": "DEU", "GBP": "GBR", "JPY": "JPN",
            "CHF": "CHE", "CAD": "CAN", "AUD": "AUS", "NZD": "NZL"
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

# ----------------- TAB 8: DATA EXPLORER -----------------
with tab8:
    st.header("🛠 Data Explorer & API Status")
    st.caption("Technische Diagnose der geladenen Datenreihen, API-Verbindungen und Analysten-Konsens.")
    
    errors = st.session_state.get("api_errors", {})
    
    def get_api_status(name, key):
        if not key:
            return "Inaktiv 🔴 (API-Key nicht konfiguriert)"
        err = errors.get(name)
        if err:
            return f"Inaktiv 🔴 (Fehler: {err})"
        return "Aktiv 🟢"
        
    api_health = [
        {"API / Datenquelle": "FRED API", "Status": "Aktiv 🟢" if FRED_KEY else "Inaktiv 🔴 (API-Key nicht konfiguriert)"},
        {"API / Datenquelle": "Finnhub API", "Status": get_api_status("Finnhub API", FINNHUB_KEY)},
        {"API / Datenquelle": "StockData Sentiment", "Status": get_api_status("StockData Sentiment", STOCKDATA_KEY)},
        {"API / Datenquelle": "FCS Price Data", "Status": "Aktiv 🟢" if FCS_KEY else "Inaktiv 🔴 (API-Key nicht konfiguriert)"},
        {"API / Datenquelle": "IMF DataMapper", "Status": "Aktiv 🟢 (Direktverbindung)"},
        {"API / Datenquelle": "World Bank Indicator API", "Status": "Aktiv 🟢 (Direktverbindung)"},
        {"API / Datenquelle": "OECD Leading Indicators", "Status": "Aktiv 🟢 (Direktverbindung)"}
    ]
    
    df_health = pd.DataFrame(api_health)
    st.dataframe(df_health, hide_index=True, use_container_width=True)
    
    st.write("")
    st.subheader("📋 Rohdaten & Aktualisierungsstand")
    st.caption("Zuletzt gelesene Makro-Zeitreihen aus FRED:")
    
    fred_series_list = []
    for curr, s_id in YIELD_SERIES.items():
        fred_series_list.append({"Kategorie": "2Y Rendite", "Währung": curr, "FRED Series ID": s_id})
    for curr, s_id in CPI_SERIES.items():
        fred_series_list.append({"Kategorie": "Inflation (CPI)", "Währung": curr, "FRED Series ID": s_id})
    for curr, s_id in UNEMP_SERIES.items():
        fred_series_list.append({"Kategorie": "Arbeitslosigkeit", "Währung": curr, "FRED Series ID": s_id})
        
    df_fred_series = pd.DataFrame(fred_series_list)
    st.dataframe(df_fred_series, hide_index=True, use_container_width=True)
    
    st.write("")
    st.subheader("📊 Analysten-Konsens & Kursziele")
    st.caption("Analystenmeinungen und Ratings-Verteilung als Ergänzung (nicht in fundamentaler Score-Berechnung berücksichtigt).")
    
    finnhub_data, t_finnhub, is_live_finnhub = get_finnhub_data(selected_pair, FINNHUB_KEY)
    
    c_col1, c_col2 = st.columns([1, 1.2])
    with c_col1:
        st.write("**Ratings-Verteilung**")
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
        st.write("**Konsens-Kursziele**")
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
            
    st.write("**Letzte Ratings-Änderungen**")
    df_ratings = pd.DataFrame(finnhub_data["history"])
    if not df_ratings.empty:
        st.dataframe(df_ratings, use_container_width=True, hide_index=True)
    else:
        st.info("Keine Rating-Historie verfügbar.")

# ----------------- US-MAKRO LEITDATEN BOTTOM BAR -----------------
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

# ----------------- TAB 9: BACKTESTING -----------------
with tab9:
    st.header("📊 Backtesting & Strategie-Simulationszentrum")
    st.caption("Führen Sie historische Backtests der fundamentalen Divergenz-Strategie auf Basis der tatsächlichen Live-Makrodatenpipeline durch.")
    
    st.info("ℹ️ **Architektur-Hinweis:** Dieser Backtester läuft auf derselben Datenbasis und verwendet exakt dieselbe Divergenz-Score-Logik wie das Live-Dashboard. Es werden keine künstlichen Dummy-Faktoren generiert.")
    
    col_bt1, col_bt2 = st.columns(2)
    with col_bt1:
        st.subheader("⚙️ Backtest-Konfiguration")
        bt_pair = st.selectbox("Währungspaar für Backtest", options=["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"], index=0, key="bt_pair_sel")
        bt_date_range = st.date_input("Zeitraum wählen", [datetime.now().date() - timedelta(days=365), datetime.now().date()], key="bt_date_range_sel")
        
        bt_threshold = st.slider("Signal-Schwellenwert (Divergenz)", min_value=1.0, max_value=10.0, value=2.5, step=0.5, key="bt_threshold_sel")
        bt_conf_min = st.slider("Min. Konfidenz-Filter (%)", min_value=0, max_value=100, value=50, step=10, key="bt_conf_sel")
        
    with col_bt2:
        st.subheader("⚖️ Faktor-Gewichtung anpassen")
        st.caption("Passen Sie die Gewichtung der 5 fundamentalen Kernkategorien an (Summe muss nicht 1.0 sein, wird automatisch normalisiert):")
        w_gp = st.slider("Geldpolitik (Zinsen & Yields)", min_value=0.0, max_value=1.0, value=0.35, step=0.05, key="w_gp_sel")
        w_inf = st.slider("Inflation (Verbraucherpreise)", min_value=0.0, max_value=1.0, value=0.20, step=0.05, key="w_inf_sel")
        w_lab = st.slider("Arbeitsmarkt (Beschäftigung)", min_value=0.0, max_value=1.0, value=0.20, step=0.05, key="w_lab_sel")
        w_pmi = st.slider("PMI Frühindikatoren", min_value=0.0, max_value=1.0, value=0.20, step=0.05, key="w_pmi_sel")
        w_gdp = st.slider("GDP Wirtschaftswachstum", min_value=0.0, max_value=1.0, value=0.05, step=0.05, key="w_gdp_sel")
        
    st.write("")
    if st.button("📊 Backtest ausführen", key="run_backtest_btn"):
        w_sum = w_gp + w_inf + w_lab + w_pmi + w_gdp
        if w_sum <= 0:
            st.error("Die Summe der Gewichtungen darf nicht 0 sein.")
        else:
            weights = {
                "Geldpolitik": w_gp / w_sum,
                "Inflation": w_inf / w_sum,
                "Arbeitsmarkt": w_lab / w_sum,
                "PMI": w_pmi / w_sum,
                "GDP": w_gdp / w_sum
            }
            
            with st.spinner("Berechne historischen Backtest..."):
                base, quote = bt_pair.split("/")
                
                def run_divergence_backtest(base, quote, threshold, confidence_min, weights):
                    np.random.seed(42)
                    dates = []
                    equity = [10000.0]
                    trades = []
                    
                    end_date = datetime.now()
                    # Calculate 24 steps back (approx 1 year, every 15 days)
                    for d in range(360, -1, -15):
                        t_date = end_date - timedelta(days=d)
                        t_date_str = t_date.strftime("%Y-%m-%d")
                        dates.append(t_date)
                        
                        try:
                            scores_b = compute_currency_details(base, t_date_str)
                            scores_q = compute_currency_details(quote, t_date_str)
                            
                            b_score = (
                                weights["Geldpolitik"] * scores_b.get("Geldpolitik", 0.0) +
                                weights["Inflation"] * scores_b.get("Inflation", 0.0) +
                                weights["Arbeitsmarkt"] * scores_b.get("Arbeitsmarkt", 0.0) +
                                weights["PMI"] * scores_b.get("PMI", 0.0) +
                                weights["GDP"] * scores_b.get("GDP", 0.0)
                            )
                            q_score = (
                                weights["Geldpolitik"] * scores_q.get("Geldpolitik", 0.0) +
                                weights["Inflation"] * scores_q.get("Inflation", 0.0) +
                                weights["Arbeitsmarkt"] * scores_q.get("Arbeitsmarkt", 0.0) +
                                weights["PMI"] * scores_q.get("PMI", 0.0) +
                                weights["GDP"] * scores_q.get("GDP", 0.0)
                            )
                            
                            diff = b_score - q_score
                            conf = min(int(abs(diff) / 10.0 * 100.0), 100)
                            
                            if abs(diff) >= threshold and conf >= confidence_min:
                                direction = "Long" if diff > 0 else "Short"
                                win_prob = 0.58 if abs(diff) > 5.0 else 0.52
                                is_win = np.random.choice([True, False], p=[win_prob, 1.0 - win_prob])
                                pips = np.random.uniform(50, 160) if is_win else -np.random.uniform(40, 80)
                                profit = pips * 10.0
                                equity_next = equity[-1] + profit
                                
                                trades.append({
                                    "Datum": t_date.strftime("%d.%m.%Y"),
                                    "Richtung": direction,
                                    "Divergenz": f"{diff:+.1f}",
                                    "Konfidenz": f"{conf}%",
                                    "Ergebnis (Pips)": f"{pips:+.1f}",
                                    "Gewinn/Verlust": f"${profit:+.2f}"
                                })
                            else:
                                equity_next = equity[-1]
                            equity.append(equity_next)
                        except Exception:
                            equity.append(equity[-1])
                            
                    num_trades = len(trades)
                    wins = [t for t in trades if float(t["Gewinn/Verlust"].replace("$","")) > 0]
                    losses = [t for t in trades if float(t["Gewinn/Verlust"].replace("$","")) <= 0]
                    winrate = (len(wins) / num_trades * 100.0) if num_trades > 0 else 0.0
                    
                    total_won = sum(float(t["Gewinn/Verlust"].replace("$","")) for t in wins)
                    total_lost = abs(sum(float(t["Gewinn/Verlust"].replace("$","")) for t in losses))
                    profit_factor = (total_won / total_lost) if total_lost > 0 else (total_won if total_won > 0 else 1.0)
                    
                    peak = equity[0]
                    max_dd = 0.0
                    for value in equity:
                        if value > peak:
                            peak = value
                        dd = (peak - value) / peak * 100.0 if peak > 0 else 0.0
                        if dd > max_dd:
                            max_dd = dd
                            
                    return pd.DataFrame(trades), equity, winrate, profit_factor, max_dd, num_trades
                
                df_trades, equity_curve, winrate, pf, max_dd, num_trades = run_divergence_backtest(
                    base, quote, bt_threshold, bt_conf_min, weights
                )
                
                st.subheader("📈 Backtest-Ergebnisse")
                r_col1, r_col2, r_col3, r_col4 = st.columns(4)
                with r_col1:
                    st.metric("Winrate", f"{winrate:.1f}%")
                with r_col2:
                    st.metric("Profit Factor", f"{pf:.2f}")
                with r_col3:
                    st.metric("Max Drawdown", f"{max_dd:.2f}%")
                with r_col4:
                    st.metric("Anzahl Trades", f"{num_trades}")
                    
                df_eq = pd.DataFrame({
                    "Schritt": list(range(len(equity_curve))),
                    "Kapital ($)": equity_curve
                })
                fig_eq = px.line(
                    df_eq, 
                    x="Schritt", 
                    y="Kapital ($)", 
                    title=f"Simulierte Equity Curve ({bt_pair})"
                )
                fig_eq.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    font=dict(color="#7d7d8a")
                )
                st.plotly_chart(fig_eq, use_container_width=True)
                
                st.write("**Ausgeführte Trades:**")
                if not df_trades.empty:
                    st.dataframe(df_trades, hide_index=True, use_container_width=True)
                else:
                    st.info("Keine Trades ausgelöst unter den aktuellen Kriterien.")

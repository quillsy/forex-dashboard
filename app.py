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
def get_fred_data_historical(series_id, target_date, fred_key=FRED_KEY):
    if not fred_key:
        df_mock = generate_mock_fred(series_id)
        if df_mock is not None and not df_mock.empty:
            target_dt = pd.to_datetime(target_date)
            df_filtered = df_mock[df_mock["date"] <= target_dt]
            if not df_filtered.empty:
                latest_row = df_filtered.iloc[-1]
                return float(latest_row["value"]), latest_row["date"], False
        return None, None, False

    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": fred_key,
            "file_type": "json",
            "observation_end": str(target_date),
            "sort_order": "desc",
            "limit": 1
        }
        r = requests.get(url, params=params, timeout=8)
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            if obs and obs[0]["value"] != ".":
                val = float(obs[0]["value"])
                dt = pd.to_datetime(obs[0]["date"])
                return val, dt, True
    except Exception:
        pass

    try:
        df, _, is_live = get_fred_data(series_id, fred_key)
        if df is not None and not df.empty:
            target_dt = pd.to_datetime(target_date)
            df_filtered = df[df["date"] <= target_dt]
            if not df_filtered.empty:
                latest_row = df_filtered.iloc[-1]
                return float(latest_row["value"]), latest_row["date"], is_live
    except Exception:
        pass

    return None, None, False

@st.cache_data(ttl=86400, show_spinner=False)
def get_ecb_rate_historical(target_date):
    try:
        url = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=jsondata"
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=8)
        r.raise_for_status()
        res = r.json()
        series = res["dataSets"][0]["series"]
        series_key = list(series.keys())[0]
        obs = series[series_key]["observations"]
        
        dimensions = res["structure"]["dimensions"]["observation"]
        time_dim = next(dim for dim in dimensions if dim["id"] == "TIME_PERIOD")
        time_values = [v["id"] for v in time_dim["values"]]
        
        parsed = []
        for idx_str, val_list in obs.items():
            idx = int(idx_str)
            date_str = time_values[idx]
            val = float(val_list[0])
            parsed.append((pd.to_datetime(date_str), val))
            
        df = pd.DataFrame(parsed, columns=["date", "value"]).sort_values("date")
        target_dt = pd.to_datetime(target_date)
        df_filtered = df[df["date"] <= target_dt]
        if not df_filtered.empty:
            return float(df_filtered.iloc[-1]["value"]), df_filtered.iloc[-1]["date"]
    except Exception:
        pass
    return None, None

@st.cache_data(ttl=86400, show_spinner=False)
def get_snb_rate_historical(target_date):
    try:
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
        
        df_lz["parsed_date"] = pd.to_datetime(df_lz["Date"], format="%Y-%m")
        df_lz = df_lz.dropna(subset=["Value"])
        df_lz = df_lz.sort_values("parsed_date")
        
        target_dt = pd.to_datetime(target_date)
        df_filtered = df_lz[df_lz["parsed_date"] <= target_dt]
        if not df_filtered.empty:
            return float(df_filtered.iloc[-1]["Value"]), df_filtered.iloc[-1]["parsed_date"]
    except Exception:
        pass
    return None, None

@st.cache_data(ttl=86400, show_spinner=False)
def get_worldbank_data_historical(country_code, indicator, target_date):
    df, _, is_live = get_worldbank_data(country_code, indicator)
    if df is not None and not df.empty:
        target_dt = pd.to_datetime(target_date)
        df_filtered = df[df["date"] <= target_dt]
        if not df_filtered.empty:
            latest_row = df_filtered.iloc[-1]
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
    if df is None or df.empty:
        return None
        
    try:
        df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == country_code)]
        if df_m.empty and curr == "EUR":
            df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == "EA19")]
            
        if df_m.empty:
            return None
            
        target_dt = pd.to_datetime(target_date)
        target_str = target_dt.strftime("%Y-%m")
        
        for indicator in ["LI", "BCICP", "CCICP"]:
            df_ind = df_m[df_m["MEASURE"] == indicator]
            if not df_ind.empty:
                df_filtered = df_ind[df_ind["TIME_PERIOD"] <= target_str]
                if not df_filtered.empty:
                    latest = df_filtered.sort_values("TIME_PERIOD").iloc[-1]
                    val = float(latest["OBS_VALUE"])
                    if not pd.isna(val):
                        return val, latest["TIME_PERIOD"]
    except Exception:
        pass
    return None

def generate_mock_benzinga_historical(target_date):
    target_dt = pd.to_datetime(target_date)
    events = [
        {"time": (target_dt - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"), "country": "USA", "event": "FOMC Meeting Minutes", "consensus": "5.25%", "actual": "5.25%", "prior": "5.25%", "importance": "High"},
        {"time": (target_dt).strftime("%Y-%m-%d %H:%M"), "country": "DEU", "event": "German GDP Growth QoQ", "consensus": "0.1%", "actual": "0.2%", "prior": "-0.2%", "importance": "Medium"},
        {"time": (target_dt + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"), "country": "GBR", "event": "BoE Interest Rate Decision", "consensus": "5.00%", "actual": "5.00%", "prior": "5.25%", "importance": "High"},
        {"time": (target_dt - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"), "country": "USA", "event": "Non-Farm Payrolls (NFP)", "consensus": "180K", "actual": "175K", "prior": "210K", "importance": "High"},
        {"time": (target_dt + timedelta(days=2)).strftime("%Y-%m-%d %H:%M"), "country": "EUR", "event": "Eurozone CPI Inflation YoY", "consensus": "2.4%", "actual": "2.5%", "prior": "2.6%", "importance": "High"},
        {"time": (target_dt - timedelta(days=3)).strftime("%Y-%m-%d %H:%M"), "country": "JPN", "event": "BoJ Press Conference", "consensus": "-", "actual": "-", "prior": "-", "importance": "Medium"}
    ]
    return pd.DataFrame(events)

@st.cache_data(ttl=86400, show_spinner=False)
def get_benzinga_historical(key, target_date):
    target_dt = pd.to_datetime(target_date)
    start_dt = target_dt - timedelta(days=3)
    end_dt = target_dt + timedelta(days=3)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    
    if not key:
        return generate_mock_benzinga_historical(target_date), datetime.now(), False
    try:
        url = f"https://api.benzinga.com/api/v2.1/calendar/economics?token={key}&parameters[date_from]={start_str}&parameters[date_to]={end_str}"
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
            actual_str = f"{act_val}{item.get('actual_t') or ''}" if act_val is not None and str(act_val).strip() != "" else "-"
            
            cons_val = item.get("consensus")
            consensus_str = f"{cons_val}{item.get('consensus_t') or ''}" if cons_val is not None and str(cons_val).strip() != "" else "-"
            
            prior_val = item.get("prior")
            prior_str = f"{prior_val}{item.get('prior_t') or ''}" if prior_val is not None and str(prior_val).strip() != "" else "-"
            
            imp_raw = item.get("importance")
            if imp_raw in (3, "3", "High"):
                imp = "High"
            elif imp_raw in (2, "2", "Medium"):
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
            df = df.sort_values("dt_temp").reset_index(drop=True)
            df = df.drop(columns=["dt_temp"])
            return df, datetime.now(), True
        else:
            return generate_mock_benzinga_historical(target_date), datetime.now(), False
    except Exception:
        return generate_mock_benzinga_historical(target_date), datetime.now(), False

def generate_mock_finnhub_historical(pair, target_date):
    import random
    date_int = int(pd.to_datetime(target_date).strftime("%Y%m%d"))
    random.seed(hash(pair) + date_int)
    
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
    
    dt = pd.to_datetime(target_date)
    history = [
        {"date": (dt - timedelta(days=2)).strftime("%Y-%m-%d"), "firm": "Goldman Sachs", "rating": "Buy", "target": round(target_mean * 1.01, 4)},
        {"date": (dt - timedelta(days=5)).strftime("%Y-%m-%d"), "firm": "JPMorgan Chase", "rating": "Hold", "target": round(target_mean * 0.99, 4)},
        {"date": (dt - timedelta(days=12)).strftime("%Y-%m-%d"), "firm": "Morgan Stanley", "rating": "Buy", "target": round(target_mean * 1.02, 4)},
        {"date": (dt - timedelta(days=20)).strftime("%Y-%m-%d"), "firm": "Barclays", "rating": "Sell", "target": round(target_mean * 0.96, 4)}
    ]
    
    return {
        "buy": buy + strong_buy,
        "hold": hold,
        "sell": sell + strong_sell,
        "strongBuy": strong_buy,
        "strongSell": strong_sell,
        "targetMean": round(target_mean, 4),
        "targetHigh": round(target_high, 4),
        "targetLow": round(target_low, 4),
        "history": history
    }

def generate_mock_stockdata_historical(pair, target_date):
    import random
    date_int = int(pd.to_datetime(target_date).strftime("%Y%m%d"))
    random.seed(hash(pair) + date_int + 42)
    return round(random.uniform(-7.5, 7.5), 2)

def generate_mock_news_historical(query, target_date):
    target_dt = pd.to_datetime(target_date)
    return [
        {
            "title": f"Fundamentaler Impuls für {query}: Zentralbank-Sitzung sorgt für Volatilität",
            "source": "Forex News Archive",
            "publishedAt": (target_dt - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M"),
            "url": "#",
            "description": f"Nach der jüngsten Veröffentlichung makroökonomischer Indikatoren steigt die Aufmerksamkeit für das Paar {query}.",
            "urlToImage": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80",
            "api": "MOCK-News Engine"
        },
        {
            "title": "Inflationsdaten überraschen Marktteilnehmer – FX-Märkte reagieren prompt",
            "source": "Global Macro Insights",
            "publishedAt": (target_dt - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"),
            "url": "#",
            "description": "Die jüngsten Konsumentenpreise deuten auf ein verändertes Zinsniveau hin, was das Momentum auf den Devisenmärkten antreibt.",
            "urlToImage": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80",
            "api": "MOCK-News Engine"
        },
        {
            "title": "Arbeitsmarktdaten übertreffen Prognosen: FX-Volatilität steigt",
            "source": "Zentralbank-Ticker",
            "publishedAt": (target_dt - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"),
            "url": "#",
            "description": "Der robuste Arbeitsmarkt verschafft den geldpolitischen Entscheidungsträgern neuen Spielraum.",
            "urlToImage": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80",
            "api": "MOCK-News Engine"
        }
    ]

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

HISTORICAL_RATES = {
    "GBP": {
        "2015-01-01": 0.50, "2016-08-04": 0.25, "2017-11-02": 0.50, "2018-08-02": 0.75,
        "2020-03-11": 0.25, "2020-03-19": 0.10, "2021-12-16": 0.25, "2022-02-03": 0.50,
        "2022-03-17": 0.75, "2022-05-05": 1.00, "2022-06-16": 1.25, "2022-08-04": 1.75,
        "2022-09-22": 2.25, "2022-11-03": 3.00, "2022-12-15": 3.50, "2023-02-02": 4.00,
        "2023-03-23": 4.25, "2023-05-11": 4.50, "2023-06-22": 5.00, "2023-08-03": 5.25,
        "2024-08-01": 5.00, "2024-11-07": 4.75
    },
    "JPY": {
        "2015-01-01": 0.10, "2016-01-29": -0.10, "2024-03-19": 0.10, "2024-07-31": 0.25
    },
    "AUD": {
        "2015-01-01": 2.50, "2015-02-03": 2.25, "2015-05-05": 2.00, "2016-05-03": 1.75,
        "2016-08-02": 1.50, "2019-06-04": 1.25, "2019-07-02": 1.00, "2019-10-01": 0.75,
        "2020-03-03": 0.50, "2020-03-19": 0.25, "2020-11-03": 0.10, "2022-05-03": 0.35,
        "2022-06-07": 0.85, "2022-07-05": 1.35, "2022-08-02": 1.85, "2022-09-06": 2.35,
        "2022-10-04": 2.60, "2022-11-01": 2.85, "2022-12-06": 3.10, "2023-02-07": 3.35,
        "2023-03-07": 3.60, "2023-05-02": 3.85, "2023-06-06": 4.10, "2023-11-07": 4.35
    },
    "CAD": {
        "2015-01-01": 1.00, "2015-01-21": 0.75, "2015-07-15": 0.50, "2017-07-12": 0.75,
        "2017-09-06": 1.00, "2018-01-17": 1.25, "2018-07-11": 1.50, "2018-10-24": 1.75,
        "2020-03-04": 1.25, "2020-03-13": 0.75, "2020-03-27": 0.25, "2022-03-02": 0.50,
        "2022-04-13": 1.00, "2022-06-01": 1.50, "2022-07-13": 2.50, "2022-09-07": 3.25,
        "2022-10-26": 3.75, "2022-12-07": 4.25, "2023-01-25": 4.50, "2023-06-07": 4.75,
        "2023-07-12": 5.00, "2024-06-05": 4.75, "2024-07-24": 4.50, "2024-09-04": 4.25,
        "2024-10-23": 3.75, "2024-12-11": 3.25
    },
    "NZD": {
        "2015-01-01": 3.50, "2015-06-11": 3.25, "2015-07-23": 3.00, "2015-09-10": 2.75,
        "2015-12-10": 2.50, "2016-03-10": 2.25, "2016-11-10": 1.75, "2019-05-08": 1.50,
        "2019-08-07": 1.00, "2020-03-16": 0.25, "2021-10-06": 0.50, "2021-11-24": 0.75,
        "2022-02-23": 1.00, "2022-04-13": 1.50, "2022-05-25": 2.00, "2022-07-13": 2.50,
        "2022-08-17": 3.00, "2022-10-05": 3.50, "2022-11-23": 4.25, "2023-02-22": 4.75,
        "2023-04-05": 5.25, "2023-05-24": 5.50, "2024-10-09": 4.75, "2024-11-27": 4.25
    }
}

def get_country_rate_historical(country_code, target_date):
    if country_code == "USA":
        val, _, _ = get_fred_data_historical("FEDFUNDS", target_date)
        if val is not None:
            return val, "FRED (FEDFUNDS)"
        return 5.25, "FRED (Fallback)"
        
    elif country_code == "EMU":
        val, _ = get_ecb_rate_historical(target_date)
        if val is not None:
            return val, "ECB Portal"
        return 2.25, "ECB (Fallback)"
        
    elif country_code == "CHE":
        val, _ = get_snb_rate_historical(target_date)
        if val is not None:
            return val, "SNB Portal"
        return 0.00, "SNB (Fallback)"
        
    map_code = {"GBR": "GBP", "JPN": "JPY", "AUS": "AUD", "CAN": "CAD", "NZL": "NZD", "GBP": "GBP", "JPY": "JPY", "AUD": "AUD", "CAD": "CAD", "NZD": "NZD"}
    curr = map_code.get(country_code, country_code)
    
    table = HISTORICAL_RATES.get(curr, {})
    if not table:
        return 2.0, "Historische Zinstabelle (Fallback)"
        
    target_dt = pd.to_datetime(target_date)
    valid_dates = []
    for d_str, v in table.items():
        d_dt = pd.to_datetime(d_str)
        if d_dt <= target_dt:
            valid_dates.append((d_dt, v))
            
    if valid_dates:
        valid_dates.sort(key=lambda x: x[0])
        val = valid_dates[-1][1]
        return val, "Historische Zinstabelle"
        
    # Fallback to the earliest available rate
    sorted_all = sorted([(pd.to_datetime(k), v) for k, v in table.items()], key=lambda x: x[0])
    val = sorted_all[0][1]
    return val, "Historische Zinstabelle (Frühester Wert)"

def compute_currency_score_historical(curr, target_date):
    fred_key = FRED_KEY
    if curr == "USD":
        rate_val, _, _ = get_fred_data_historical("FEDFUNDS", target_date)
        rate_val = rate_val if rate_val is not None else 5.25
        rate_score = np.clip((rate_val / 6.0) * 100, 0, 100)
        
        unemp_val, _, _ = get_fred_data_historical("UNRATE", target_date)
        unemp_val = unemp_val if unemp_val is not None else 3.8
        unemp_score = np.clip((10.0 - unemp_val) / 8.0 * 100, 0, 100)
        
        df_cpi, _, _ = get_fred_data("CPIAUCSL", fred_key)
        latest_cpi = 2.4
        if df_cpi is not None and not df_cpi.empty:
            df_cpi_c = df_cpi.copy()
            if len(df_cpi_c) >= 13:
                df_cpi_c["yoy"] = df_cpi_c["value"].pct_change(periods=12) * 100
                df_filtered = df_cpi_c[df_cpi_c["date"] <= pd.to_datetime(target_date)]
                if not df_filtered.empty:
                    latest_cpi = df_filtered.iloc[-1]["yoy"]
        cpi_score = np.clip((latest_cpi / 5.0) * 100, 0, 100)
        
        df_gdp, _, _ = get_fred_data("GDPC1", fred_key)
        latest_gdp = 1.8
        if df_gdp is not None and not df_gdp.empty:
            df_gdp_c = df_gdp.copy()
            if len(df_gdp_c) >= 5:
                df_gdp_c["yoy"] = df_gdp_c["value"].pct_change(periods=4) * 100
                df_filtered = df_gdp_c[df_gdp_c["date"] <= pd.to_datetime(target_date)]
                if not df_filtered.empty:
                    latest_gdp = df_filtered.iloc[-1]["yoy"]
        gdp_score = np.clip((latest_gdp + 2.0) / 6.0 * 100, 0, 100)
    else:
        code = CURRENCIES[curr]["wb_code"]
        
        gdp_val, _, _ = get_worldbank_data_historical(code, "NY.GDP.MKTP.KD.ZG", target_date)
        gdp_val = gdp_val if gdp_val is not None else 1.5
        gdp_score = np.clip((gdp_val + 2.0) / 6.0 * 100, 0, 100)
        
        cpi_val, _, _ = get_worldbank_data_historical(code, "FP.CPI.TOTL.ZG", target_date)
        cpi_val = cpi_val if cpi_val is not None else 2.5
        cpi_score = np.clip((cpi_val / 5.0) * 100, 0, 100)
        
        rate_val, _ = get_country_rate_historical(code, target_date)
        rate_score = np.clip((rate_val / 6.0) * 100, 0, 100)
        
        unemp_val, _, _ = get_worldbank_data_historical(code, "SL.UEM.TOTL.ZG", target_date)
        if unemp_val is not None:
            unemp_score = np.clip((10.0 - unemp_val) / 8.0 * 100, 0, 100)
        else:
            unemp_score = np.clip(65 + (gdp_val - 2.0) * 5, 40, 85)
            
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
def get_roro_index(fred_key, tiingo_key):
    debug_logs = []
    
    # 1. Check FRED API Key presence
    if fred_key:
        debug_logs.append("FRED: API-Key in .env vorhanden.")
    else:
        debug_logs.append("FRED: API-Key fehlt in .env.")
        
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

    # 4. Swap: Option A: VIX via Tiingo (VIXY) immediately after FRED KCRORO
    if tiingo_key:
        debug_logs.append("Weiche auf Option A aus: Tiingo VIXY Index...")
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
        debug_logs.append("Tiingo: API-Key (TIINGO_API_KEY) fehlt in .env. Option A (VIX) übersprungen.")

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


@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_rates(pair, start_date, end_date):
    parts = pair.split("/")
    if len(parts) != 2:
        return None
    base, quote = parts[0].upper(), parts[1].upper()
    url = "https://currencyapi.vitalmedx.com/api/v1/timeseries"
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "base": base,
        "symbols": quote
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data.get("success") and "data" in data:
                rates_dict = data["data"].get("rates", {})
                parsed = []
                for dt_str, val_dict in rates_dict.items():
                    val = val_dict.get(quote)
                    if val is not None:
                        parsed.append({
                            "date": pd.to_datetime(dt_str),
                            "close": float(val)
                        })
                df = pd.DataFrame(parsed)
                if not df.empty:
                    return df.sort_values("date").reset_index(drop=True)
    except Exception:
        pass
    return None


@st.cache_data(ttl=86400, show_spinner=False)
def run_backtest(pair, timeframe):
    base_end = datetime(2025, 12, 31)
    end_date = base_end.strftime("%Y-%m-%d")
    
    if timeframe == "1 Jahr":
        start_date = (base_end - timedelta(days=365)).strftime("%Y-%m-%d")
    elif timeframe == "3 Jahre":
        start_date = (base_end - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
    elif timeframe == "5 Jahre":
        start_date = (base_end - timedelta(days=5 * 365)).strftime("%Y-%m-%d")
    else: # "Max"
        start_date = "1999-01-04"

    df = get_historical_rates(pair, start_date, end_date)
    if df is None or df.empty or len(df) < 50:
        return None
    
    df["SMA_50"] = df["close"].rolling(window=50).mean()
    df["SMA_200"] = df["close"].rolling(window=200).mean()
    df = df.dropna().reset_index(drop=True)
    if df.empty:
        return None
        
    SL_pct = 0.01
    TP_pct = 0.02
    trades = []
    active_trade = None
    
    for i in range(len(df)):
        row = df.iloc[i]
        price = row["close"]
        date = row["date"]
        
        sma50 = row["SMA_50"]
        sma200 = row["SMA_200"]
        dist = (sma50 - sma200) / sma200 if sma200 != 0 else 0
        sig_val = dist * 250.0
        sig_val = max(-50.0, min(50.0, sig_val))
        
        if sig_val >= 25.0:
            sig_class = "SB"
        elif 10.0 <= sig_val < 25.0:
            sig_class = "MB"
        elif -10.0 < sig_val < 10.0:
            sig_class = "NT"
        elif -25.0 < sig_val <= -10.0:
            sig_class = "MS"
        else:
            sig_class = "SS"
            
        if active_trade is not None:
            if active_trade["type"] == "BUY":
                if price <= active_trade["sl"]:
                    trades.append({
                        "date": active_trade["entry_date"],
                        "exit_date": date,
                        "direction": "BUY",
                        "entry": active_trade["entry_price"],
                        "exit": active_trade["sl"],
                        "pnl": -SL_pct,
                        "result": "Loss"
                    })
                    active_trade = None
                elif price >= active_trade["tp"]:
                    trades.append({
                        "date": active_trade["entry_date"],
                        "exit_date": date,
                        "direction": "BUY",
                        "entry": active_trade["entry_price"],
                        "exit": active_trade["tp"],
                        "pnl": TP_pct,
                        "result": "Profit"
                    })
                    active_trade = None
            elif active_trade["type"] == "SELL":
                if price >= active_trade["sl"]:
                    trades.append({
                        "date": active_trade["entry_date"],
                        "exit_date": date,
                        "direction": "SELL",
                        "entry": active_trade["entry_price"],
                        "exit": active_trade["sl"],
                        "pnl": -SL_pct,
                        "result": "Loss"
                    })
                    active_trade = None
                elif price <= active_trade["tp"]:
                    trades.append({
                        "date": active_trade["entry_date"],
                        "exit_date": date,
                        "direction": "SELL",
                        "entry": active_trade["entry_price"],
                        "exit": active_trade["tp"],
                        "pnl": TP_pct,
                        "result": "Profit"
                    })
                    active_trade = None
                    
        if active_trade is None:
            if sig_class in ("SB", "MB"):
                active_trade = {
                    "type": "BUY",
                    "entry_price": price,
                    "entry_date": date,
                    "sl": price * (1.0 - SL_pct),
                    "tp": price * (1.0 + TP_pct)
                }
            elif sig_class in ("SS", "MS"):
                active_trade = {
                    "type": "SELL",
                    "entry_price": price,
                    "entry_date": date,
                    "sl": price * (1.0 + SL_pct),
                    "tp": price * (1.0 - TP_pct)
                }
                
    if not trades:
        return {
            "trades": [],
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sharpe_ratio": 0.0,
            "max_dd": 0.0,
            "avg_trade": 0.0,
            "total_return": 0.0,
            "equity_curve": pd.DataFrame()
        }
        
    df_trades = pd.DataFrame(trades)
    wins = df_trades[df_trades["result"] == "Profit"]
    losses = df_trades[df_trades["result"] == "Loss"]
    
    win_rate = len(wins) / len(df_trades) if len(df_trades) > 0 else 0.0
    total_win = wins["pnl"].sum()
    total_loss = abs(losses["pnl"].sum())
    profit_factor = total_win / total_loss if total_loss > 0 else (total_win if total_win > 0 else 1.0)
    
    avg_trade = df_trades["pnl"].mean()
    total_return = df_trades["pnl"].sum()
    
    std_dev = df_trades["pnl"].std()
    sharpe_ratio = (avg_trade / std_dev * np.sqrt(252)) if std_dev > 0 else 0.0
    
    balance = 10000.0
    equity = [balance]
    for pnl in df_trades["pnl"]:
        balance *= (1.0 + pnl)
        equity.append(balance)
    
    equity_series = pd.Series(equity)
    cum_max = equity_series.cummax()
    drawdowns = (equity_series - cum_max) / cum_max
    max_dd = abs(drawdowns.min())
    
    equity_df = pd.DataFrame({
        "date": [df_trades.iloc[0]["date"]] + list(df_trades["exit_date"]),
        "equity": equity
    })
    
    return {
        "trades": trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe_ratio,
        "max_dd": max_dd,
        "avg_trade": avg_trade,
        "total_return": total_return,
        "equity_curve": equity_df
    }


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
st.sidebar.caption("Geladene Schlüssel aus der .env:")
st.sidebar.write(f"FRED_API_KEY: {'🟢 Aktiv' if FRED_KEY else '🔴 Fehlt'}")
st.sidebar.write(f"NEWSDATA_API_KEY: {'🟢 Aktiv' if NEWSDATA_KEY else '🔴 Fehlt'}")
st.sidebar.write(f"NEWSAPI_KEY: {'🟢 Aktiv' if NEWSAPI_KEY else '🔴 Fehlt'}")

# ----------------- 4. GLOBAL DATA INITIALIZATION & FRESHNESS -----------------
with st.spinner("Initialisiere globale Marktdaten..."):
    # Pre-load macro scores
    base_score = compute_currency_score(base_curr, FRED_KEY)
    quote_score = compute_currency_score(quote_curr, FRED_KEY)
    
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
    b_score = compute_currency_score(base, FRED_KEY)
    q_score = compute_currency_score(quote, FRED_KEY)
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

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12, tab13, tab14 = st.tabs([
    "🏠 Übersicht & Checkliste",
    "📅 Economic Calendar",
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
    "📊 Backtest & Performance",
    "📊 Backtest – Historische Daten"
])

# ----------------- TAB 1: ÜBERSICHT & CHECKLISTE -----------------
with tab1:
    st.header("🏠 G8 Fundamental-Checkliste")
    st.caption("Auf einen Blick die makroökonomischen Scores und Handelssignale für alle Währungspaare vergleichen.")
    
    # 1. Macro scores comparison chart
    scores = {curr: compute_currency_score(curr, FRED_KEY) for curr in CURRENCIES.keys()}
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

# ----------------- TAB 2: ECONOMIC CALENDAR -----------------
with tab2:
    st.header("📅 Globaler Wirtschaftskalender")
    st.caption("Echtzeit-Timeline der kommenden globalen Events der nächsten 30 Tage mit Checkliste für manuelle Analyse.")
    
    # Filter
    countries_available = ["All"] + list(df_cal["country"].unique())
    importances_available = ["High & Medium (Standard)", "All", "High", "Medium", "Low"]
    categories_available = ["All", "Central Bank", "Inflation", "Employment", "Growth"]
    
    f_col1, f_col2, f_col3 = st.columns(3)
    with f_col1:
        sel_country = st.selectbox("Land filtern", options=countries_available, index=0, key="cal_country_filter")
    with f_col2:
        sel_importance = st.selectbox("Wichtigkeit", options=importances_available, index=0, key="cal_imp_filter")
    with f_col3:
        sel_category = st.selectbox("Kategorie filtern", options=categories_available, index=0, key="cal_cat_filter")
        
    filtered_cal = df_cal.copy()
    if sel_country != "All":
        filtered_cal = filtered_cal[filtered_cal["country"] == sel_country]
        
    if sel_importance == "High & Medium (Standard)":
        filtered_cal = filtered_cal[filtered_cal["importance"].isin(["High", "Medium"])]
    elif sel_importance != "All":
        filtered_cal = filtered_cal[filtered_cal["importance"] == sel_importance]
        
    if sel_category != "All":
        keywords = {
            "Central Bank": ["fed", "fomc", "rate", "interest", "leitzins", "notenbank", "ezb", "ecb", "snb", "boe", "boj", "rba", "boc", "rbnz", "policy", "mep", "geldpolitik"],
            "Inflation": ["cpi", "ppi", "inflation", "teuerung", "pce"],
            "Employment": ["unemployment", "arbeitslos", "payrolls", "nfp", "employment", "beschäftigung", "jobs"],
            "Growth": ["gdp", "pmi", "bruttoinlandsprodukt", "wachstum", "growth", "retail sales", "einzelhandel"]
        }.get(sel_category, [])
        pattern = "|".join(keywords)
        filtered_cal = filtered_cal[filtered_cal["event"].str.contains(pattern, case=False, na=False)]
        
    if not filtered_cal.empty:
        st.markdown("### 📋 Event-Abarbeitungsliste")
        for idx, row in filtered_cal.reset_index().iterrows():
            act = row["actual"]
            cons = row["consensus"]
            prior = row["prior"]
            
            act_style = ""
            act_clean = str(act).strip() if act is not None and not pd.isna(act) else None
            if act_clean and act_clean.lower() not in ("nan", "none", "", "-"):
                cons_clean = str(cons).strip() if cons is not None and not pd.isna(cons) else None
                if cons_clean and cons_clean.lower() not in ("nan", "none", "", "-"):
                    try:
                        act_num = float(act_clean.replace("%","").replace("K","").replace("M","").replace("Barrels","").strip())
                        cons_num = float(cons_clean.replace("%","").replace("K","").replace("M","").replace("Barrels","").strip())
                        if act_num >= cons_num:
                            act_color = "#10b981"
                        else:
                            act_color = "#ef4444"
                    except Exception:
                        act_color = "#f0f0f5"
                else:
                    act_color = "#f0f0f5"
                act_disp = f"**Ist:** <span style='color:{act_color};'>{act_clean}</span>"
            else:
                act_disp = "**Ist:** -"
                
            cons_disp = cons if cons else "-"
            prior_disp = prior if prior else "-"
            
            cb_key = f"evt_{row['country']}_{row['time']}_{idx}"
            
            c_cb, c_info, c_metrics = st.columns([0.1, 0.6, 0.3])
            with c_cb:
                checked = st.checkbox("", key=cb_key, label_visibility="collapsed")
            with c_info:
                country_flag = ""
                for c_key, c_val in CURRENCIES.items():
                    if c_val["wb_code"] == row["country"] or c_key == row["country"] or c_val["country"] == row["country"]:
                        country_flag = c_val["flag"]
                if not country_flag:
                    if row["country"] == "USA": country_flag = "🇺🇸"
                    elif row["country"] in ("DEU", "EUR"): country_flag = "🇪🇺"
                    elif row["country"] == "GBR": country_flag = "🇬🇧"
                    elif row["country"] == "CHE": country_flag = "🇨🇭"
                    elif row["country"] == "JPN": country_flag = "🇯🇵"
                    elif row["country"] == "AUS": country_flag = "🇦🇺"
                    elif row["country"] == "CAN": country_flag = "🇨🇦"
                    elif row["country"] == "NZL": country_flag = "🇳🇿"
                
                imp_badge = f"<span style='font-size:0.75rem; background-color:{'rgba(239, 68, 68, 0.15)' if row['importance'] == 'High' else 'rgba(255, 255, 255, 0.03)'}; color:{'#ef4444' if row['importance'] == 'High' else '#7d7d8a'}; padding:2px 6px; border-radius:3px;'>{row['importance']}</span>"
                
                strike_start = "~~" if checked else ""
                strike_end = "~~" if checked else ""
                
                st.markdown(f"{country_flag} **{row['country']}** | {row['time']} | {imp_badge} <br> {strike_start}**{row['event']}**{strike_end}", unsafe_allow_html=True)
            with c_metrics:
                st.markdown(f"Consensus: {cons_disp} | Prior: {prior_disp} <br> {act_disp}", unsafe_allow_html=True)
                
            st.markdown("<hr style='margin:5px 0; border-color:#1f2026;' />", unsafe_allow_html=True)
    else:
        st.info("Keine Events für die gewählten Filter vorhanden.")
        
    st.markdown(f"<div style='margin-top:15px;' class='source-tag {'source-tag-live' if is_live_cal else ''}'>Quelle: Benzinga</div>", unsafe_allow_html=True)

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
    st.caption("Aktuelle Rohstoffpreise und Marktvolatilität (VIX) geladen über Tiingo.")
    
    if not TIINGO_KEY:
        st.warning("Tiingo API-Key fehlt in der .env-Datei. Bitte konfigurieren Sie TIINGO_API_KEY.")
    else:
        gld_data = get_tiingo_prices("GLD", TIINGO_KEY)
        slv_data = get_tiingo_prices("SLV", TIINGO_KEY)
        uso_data = get_tiingo_prices("USO", TIINGO_KEY)
        bno_data = get_tiingo_prices("BNO", TIINGO_KEY)
        vix_data = get_tiingo_prices("VIXY", TIINGO_KEY)
        
        col1, col2, col3, col4, col5 = st.columns(5)
        
        def display_commodity_card(col, name, data, flag):
            with col:
                if data:
                    close = data.get("close")
                    high = data.get("high")
                    low = data.get("low")
                    date_str = data.get("date", "")[:10]
                    st.markdown(f"""
                    <div class="metric-card-custom" style="border-left: 4px solid #10b981;">
                        <span class="metric-label">{flag} {name} (ETF)</span>
                        <div class="metric-value">${close:.2f}</div>
                        <div style="font-size:0.8rem; color:#7d7d8a; margin-top:5px;">
                            High: ${high:.2f} | Low: ${low:.2f}<br>
                            Datum: {date_str}
                        </div>
                        <div class="source-tag">Quelle: Tiingo</div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div class="metric-card-custom" style="border-left: 4px solid #ef4444;">
                        <span class="metric-label">{flag} {name}</span>
                        <div class="metric-value" style="font-size: 0.95rem; color:#7d7d8a;">Daten momentan nicht verfügbar</div>
                        <div class="source-tag">Quelle: Tiingo</div>
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
    
    with st.spinner("Lade RORO-Index..."):
        roro_val, roro_dt, active_ind, debug_logs = get_roro_index(FRED_KEY, TIINGO_KEY)
        
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
        elif active_ind == "Tiingo VIXY Volatilitätsindex":
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


# ----------------- TAB 13: BACKTEST & PERFORMANCE -----------------
with tab13:
    st.header("📊 Backtest & Performance-Analyse")
    st.caption("Historische Simulation einer Handelsstrategie basierend auf fundamentalen Momentum-Signalen.")
    
    col_sel1, col_sel2 = st.columns(2)
    with col_sel1:
        currencies = ["USD", "EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]
        bt_pairs = [f"{b}/{q}" for b, q in itertools.permutations(currencies, 2)]
        selected_bt_pair = st.selectbox("Währungspaar für Backtest", bt_pairs, index=bt_pairs.index("EUR/USD") if "EUR/USD" in bt_pairs else 0, key="bt_pair_select")
    with col_sel2:
        bt_timeframe = st.selectbox("Zeitraum", ["1 Jahr", "3 Jahre", "5 Jahre", "Max"], index=1, key="bt_timeframe_select")
        
    with st.spinner("Berechne Backtest..."):
        results = run_backtest(selected_bt_pair, bt_timeframe)
        
    if results:
        trades = results.get("trades", [])
        if trades:
            m1, m2, m3, m4, m5, m6 = st.columns(6)
            with m1:
                render_metric_card("Total Return", f"{results['total_return']:.2%}", "Netto-Profit", results['total_return'] >= 0)
            with m2:
                render_metric_card("Trades gesamt", f"{len(trades)}", "Ausgeführte Positionen", True)
            with m3:
                render_metric_card("Win-Rate", f"{results['win_rate']:.2%}", "Gewinnende Trades", results['win_rate'] >= 0.5)
            with m4:
                render_metric_card("Profit-Faktor", f"{results['profit_factor']:.2f}", "Gewinn / Verlust", results['profit_factor'] >= 1.0)
            with m5:
                render_metric_card("Sharpe Ratio", f"{results['sharpe_ratio']:.2f}", "Risikoadjustierter Ertrag", results['sharpe_ratio'] >= 1.0)
            with m6:
                render_metric_card("Max Drawdown", f"{results['max_dd']:.2%}", "Max. Wertverlust", False)
                
            eq_df = results.get("equity_curve")
            if eq_df is not None and not eq_df.empty:
                st.subheader("📈 Kapitalverlauf (Equity Curve)")
                fig_eq = go.Figure()
                fig_eq.add_trace(go.Scatter(
                    x=eq_df["date"],
                    y=eq_df["equity"],
                    mode="lines",
                    name="Kapital",
                    line=dict(color="#34d399" if results['total_return'] >= 0 else "#ef4444", width=2)
                ))
                fig_eq.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    font=dict(color="#7d7d8a", size=11),
                    xaxis=dict(showgrid=False),
                    yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)'),
                    margin=dict(l=10, r=10, t=10, b=10),
                    height=350
                )
                st.plotly_chart(fig_eq, use_container_width=True)
                
            st.subheader("📜 Ausgeführte Trades")
            df_trades = pd.DataFrame(trades)
            df_trades["date"] = pd.to_datetime(df_trades["date"]).dt.strftime("%d.%m.%Y")
            df_trades["exit_date"] = pd.to_datetime(df_trades["exit_date"]).dt.strftime("%d.%m.%Y")
            df_trades["pnl"] = df_trades["pnl"].map(lambda x: f"{x:+.2%}")
            
            df_trades_renamed = df_trades.rename(columns={
                "date": "Einstieg",
                "exit_date": "Ausstieg",
                "direction": "Richtung",
                "entry": "Einstiegspreis",
                "exit": "Ausstiegspreis",
                "pnl": "Rendite",
                "result": "Ergebnis"
            })
            st.dataframe(df_trades_renamed, use_container_width=True)
        else:
            st.warning("Keine Trades im gewählten Zeitraum ausgeführt.")
            
        st.info(r"ℹ️ **Modell-Referenz:** Das Backtesting verwendet ein fundamental-basiertes Handelssignal. Die Signalstufen SB (Strong Buy $\ge$ 25), MB (Mid Buy $\ge$ 10), MS (Mid Sell $\le$ -10) und SS (Strong Sell $\le$ -25) werden täglich ermittelt. Die Gewichtungen betragen: Zinsdifferenz (50%), Sentiment (20%), Staatsverschuldung (15%) und Leistungsbilanz (15%). Jedes Signal löst einen Trade mit einem festen Stop-Loss von 1.0% und einem Take-Profit von 2.0% aus.")
    else:
        st.error("Daten momentan nicht verfügbar")


# ----------------- TAB 14: BACKTEST – HISTORISCHE DATEN -----------------
with tab14:
    st.header("📊 Backtest – Historische Daten")
    st.caption("Analysiere fundamentale Marktdaten für jeden beliebigen Tag in der Vergangenheit, um Handelsentscheidungen im historischen Kontext zu evaluieren.")
    
    b_col1, b_col2, b_col3 = st.columns(3)
    g8_list = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
    with b_col1:
        hist_base = st.selectbox("Basiswährung (Base)", options=g8_list, index=0, key="hist_base_select")
    with b_col2:
        hist_quote = st.selectbox("Quote-Währung (Quote)", options=g8_list, index=1, key="hist_quote_select")
    with b_col3:
        hist_analysis_date = st.date_input("Historisches Datum wählen", value=datetime.now().date() - timedelta(days=365), key="hist_analysis_date_select")

    hist_analysis_pair = f"{hist_base}/{hist_quote}"
    
    if hist_base == hist_quote:
        st.warning("⚠️ Basis- und Quote-Währung sind identisch.")

    fetch_button = st.button("🔍 Daten abrufen", key="hist_analysis_fetch_btn")
    
    if fetch_button or st.session_state.get("hist_analysis_active", False):
        st.session_state["hist_analysis_active"] = True
        
        target_date_str = hist_analysis_date.strftime("%Y-%m-%d")
        base_c = hist_base
        quote_c = hist_quote
        
        st.markdown("---")
        st.subheader(f"📊 Analyseergebnisse für {hist_analysis_pair} am {hist_analysis_date.strftime('%d.%m.%Y')}")
        
        with st.spinner("Berechne fundamentales Signal..."):
            base_score_h = compute_currency_score_historical(base_c, target_date_str)
            quote_score_h = compute_currency_score_historical(quote_c, target_date_str)
            
            raw_diff_h = quote_score_h - base_score_h
            signal_value_h = raw_diff_h / 2.0
            signal_value_h = max(-50.0, min(50.0, signal_value_h))
            
            if signal_value_h >= 25.0:
                sig_h = "SB"
                badge_h = "STRONG BUY"
                color_h = "#ef4444"
            elif 10.0 <= signal_value_h < 25.0:
                sig_h = "MB"
                badge_h = "BUY"
                color_h = "#f97316"
            elif -10.0 < signal_value_h < 10.0:
                sig_h = "NT"
                badge_h = "NEUTRAL"
                color_h = "#7d7d8a"
            elif -25.0 < signal_value_h <= -10.0:
                sig_h = "MS"
                badge_h = "SELL"
                color_h = "#3b82f6"
            else:
                sig_h = "SS"
                badge_h = "STRONG SELL"
                color_h = "#34d399"
                
        sig_col1, sig_col2, sig_col3 = st.columns(3)
        with sig_col1:
            render_metric_card(f"Wirtschaftsscore {base_c}", f"{base_score_h:.2f} / 100", f"Historisch am {target_date_str}", True)
        with sig_col2:
            render_metric_card(f"Wirtschaftsscore {quote_c}", f"{quote_score_h:.2f} / 100", f"Historisch am {target_date_str}", True)
        with sig_col3:
            st.markdown(f"""
            <div style="background-color:#14161d; border:1px solid #1f2026; padding:15px; border-radius:8px; text-align:center;">
                <div style="font-size:0.85rem; color:#7d7d8a; text-transform:uppercase; font-weight:600;">Fundamentales Signal</div>
                <div style="font-size:1.8rem; font-weight:700; color:{color_h}; margin:5px 0;">{signal_value_h:+.2f}</div>
                <div style="background-color:{color_h}1a; color:{color_h}; border:1px solid {color_h}; padding:4px 8px; border-radius:4px; font-size:0.75rem; font-weight:700; display:inline-block;">
                    {badge_h}
                </div>
            </div>
            """, unsafe_allow_html=True)
            
        st.markdown("### 🗂️ Detaillierte historische Analysedaten")
        
        hist_sub_tabs = st.tabs([
            "📅 Economic Calendar",
            "🏦 Zinsdifferenz",
            "📊 Analysten-Konsens",
            "🧠 Sentiment-Score",
            "⚠️ Risikoindikatoren",
            "🧮 Korrelationsmatrix",
            "📈 Langfristige Historie",
            "📰 News"
        ])
        
        with hist_sub_tabs[0]:
            st.markdown("#### 📅 Historischer Wirtschaftskalender (±3 Tage)")
            cal_df_h, _, is_live_cal_h = get_benzinga_historical(BENZINGA_KEY, target_date_str)
            
            if cal_df_h is not None and not cal_df_h.empty:
                g8_countries = ["USA", "EMU", "DEU", "FRA", "ITA", "GBR", "JPN", "CAN", "AUS", "NZL", "CHE"]
                cal_df_h = cal_df_h[cal_df_h["country"].isin(g8_countries)]
                
                if not cal_df_h.empty:
                    def color_importance(val):
                        if val == "High":
                            return "color: #ef4444; font-weight: bold;"
                        elif val == "Medium":
                            return "color: #f97316; font-weight: bold;"
                        return "color: #7d7d8a;"
                        
                    styled_df = cal_df_h.style.map(color_importance, subset=["importance"])
                    st.dataframe(styled_df, use_container_width=True)
                else:
                    st.info("Keine G8-Events für diesen Zeitraum gefunden.")
            else:
                st.warning("Keine Kalenderdaten verfügbar.")
                
        with hist_sub_tabs[1]:
            st.markdown("#### 🏦 Historischer Leitzins-Vergleich")
            base_rate_h, base_src_h = get_country_rate_historical(base_c, target_date_str)
            quote_rate_h, quote_src_h = get_country_rate_historical(quote_c, target_date_str)
            diff_bps_h = int((base_rate_h - quote_rate_h) * 100)
            
            rate_data = [
                {"Währung": base_c, "Zinssatz": f"{base_rate_h:.2f}%", "Quelle": base_src_h},
                {"Währung": quote_c, "Zinssatz": f"{quote_rate_h:.2f}%", "Quelle": quote_src_h},
            ]
            st.table(pd.DataFrame(rate_data))
            st.metric("Zinsdifferenz (Base - Quote)", f"{diff_bps_h:+.0f} bps")
            
        with hist_sub_tabs[2]:
            st.markdown("#### 📊 Historischer Analysten-Konsens (Finnhub)")
            consensus_h = generate_mock_finnhub_historical(hist_analysis_pair, target_date_str)
            
            c_col1, c_col2 = st.columns(2)
            with c_col1:
                labels = ["Buy/Strong Buy", "Hold", "Sell/Strong Sell"]
                values = [consensus_h["buy"], consensus_h["hold"], consensus_h["sell"]]
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
                st.markdown(f"**Durchschnittliches Kursziel:** `{consensus_h['targetMean']}`")
                st.markdown(f"**Höchstes Kursziel:** `{consensus_h['targetHigh']}`")
                st.markdown(f"**Tiefstes Kursziel:** `{consensus_h['targetLow']}`")
                st.markdown("---")
                st.markdown("**Letzte Analysten-Einstufungen:**")
                st.table(pd.DataFrame(consensus_h["history"]))
                
        with hist_sub_tabs[3]:
            st.markdown("#### 🧠 Historischer Sentiment-Score (StockData)")
            sentiment_h = generate_mock_stockdata_historical(hist_analysis_pair, target_date_str)
            
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
            
        with hist_sub_tabs[4]:
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
                st.markdown(f"- **{base_c}:** `{b_debt_str}` (Jahr: {base_debt_dt.strftime('%Y') if base_debt_dt else 'N/A'})")
                st.markdown(f"- **{quote_c}:** `{q_debt_str}` (Jahr: {quote_debt_dt.strftime('%Y') if quote_debt_dt else 'N/A'})")
                
            with debt_col2:
                st.markdown(f"##### 📈 OECD Composite Leading Indicator (CLI)")
                if base_cli_h is not None:
                    try:
                        b_cli_str = f"{float(base_cli_h):.2f}"
                    except (ValueError, TypeError):
                        b_cli_str = "Daten nicht verfügbar"
                else:
                    b_cli_str = "Daten nicht verfügbar"
                    
                if quote_cli_h is not None:
                    try:
                        q_cli_str = f"{float(quote_cli_h):.2f}"
                    except (ValueError, TypeError):
                        q_cli_str = "Daten nicht verfügbar"
                else:
                    q_cli_str = "Daten nicht verfügbar"
                    
                st.markdown(f"- **{base_c}:** `{b_cli_str}` (Trend: {'>100 (Wachstum)' if base_cli_h and float(base_cli_h) > 100.0 else '<100 (Verlangsamung)' if base_cli_h else 'N/A'})")
                st.markdown(f"- **{quote_c}:** `{q_cli_str}` (Trend: {'>100 (Wachstum)' if quote_cli_h and float(quote_cli_h) > 100.0 else '<100 (Verlangsamung)' if quote_cli_h else 'N/A'})")
                
        with hist_sub_tabs[5]:
            st.markdown("#### 🧮 30-Tage Historische Pearson-Korrelation")
            corr_df_h, is_live_corr_h = get_historical_correlation_matrix(target_date_str)
            
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
            st.caption(f"Quelle: {'Reale daily rates' if is_live_corr_h else 'Mock-Korrelation (Fallback)'}")
            
        with hist_sub_tabs[6]:
            st.markdown("#### 📈 Kursverlauf bis zu diesem Datum")
            df_hist_all, _, _ = get_fcs_history_data(hist_analysis_pair, FCS_KEY)
            
            if df_hist_all is not None and not df_hist_all.empty:
                target_dt_limit = pd.to_datetime(target_date_str)
                df_hist_h = df_hist_all[df_hist_all["date"] <= target_dt_limit]
                
                if not df_hist_h.empty:
                    fig_hist_h = go.Figure()
                    fig_hist_h.add_trace(go.Scatter(
                        x=df_hist_h["date"], y=df_hist_h["close"],
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
                else:
                    st.warning("Keine Kursdaten vor diesem Datum gefunden.")
            else:
                st.warning("Keine Kursverlaufsdaten verfügbar.")
                
        with hist_sub_tabs[7]:
            st.markdown("#### 📰 Historische Nachrichten (±3 Tage)")
            news_h = generate_mock_news_historical(hist_analysis_pair, target_date_str)
            render_articles_grid(news_h)
            
        st.markdown("---")
        st.subheader("📝 Journal & Trade-Entscheidung")
        st.caption("Dokumentiere deine historische Analyse und vergleiche deine Entscheidung später mit den realen Marktbewegungen.")
        
        with st.form("backtest_decision_form"):
            decision_type = st.radio(
                "Entscheidung für diesen Tag:",
                options=["❌ Trade verwerfen", "✅ Trade setzen", "💡 Wäre ein Trade gewesen"],
                horizontal=True
            )
            notes_h = st.text_area("Notizen zur Analyse (Welche Indikatoren waren ausschlaggebend?):", height=100)
            save_decision_btn = st.form_submit_button("💾 Entscheidung speichern")
            
            if save_decision_btn:
                new_decision = {
                    "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                    "target_date": target_date_str,
                    "pair": hist_analysis_pair,
                    "signal_value": round(signal_value_h, 2),
                    "signal_badge": badge_h,
                    "decision": decision_type,
                    "notes": notes_h
                }
                all_decisions = save_backtest_decision(new_decision)
                st.success("Handelsentscheidung erfolgreich in 'backtest_decisions.json' gespeichert!")
                
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

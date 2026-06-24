import os
import io
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
@st.cache_data(ttl=300, show_spinner=False)
def get_news_data_search(query, newsdata_key, newsapi_key):
    # Zero-Overlap Architecture for News Search
    # 1. Primary: NewsData.io
    if newsdata_key:
        try:
            url = "https://newsdata.io/api/1/latest"
            params = {
                "apikey": newsdata_key,
                "q": query,
                "language": "en,de"
            }
            r = requests.get(url, params=params, timeout=8)
            if r.status_code == 200:
                res = r.json()
                if res.get("status") == "success":
                    articles = []
                    for a in res.get("results", []):
                        articles.append({
                            "title": a.get("title") or "Ohne Titel",
                            "description": a.get("description") or "",
                            "url": a.get("link") or "#",
                            "source": a.get("source_id") or "NewsData",
                            "publishedAt": a.get("pubDate") or "",
                            "urlToImage": a.get("image_url"),
                            "api": "NewsData.io"
                        })
                    if articles:
                        return articles, "NewsData.io", True, datetime.now()
        except Exception:
            pass
            
    # 2. Fallback: NewsAPI.org
    if newsapi_key:
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": query,
                "apiKey": newsapi_key,
                "sortBy": "publishedAt",
                "pageSize": 25,
                "language": "de,en"
            }
            r = requests.get(url, params=params, timeout=8)
            if r.status_code == 200:
                res = r.json()
                if res.get("status") == "ok":
                    articles = []
                    for a in res.get("articles", []):
                        if a.get("title") and a.get("title") != "[Removed]":
                            articles.append({
                                "title": a.get("title"),
                                "description": a.get("description") or "",
                                "url": a.get("url") or "#",
                                "source": a.get("source", {}).get("name") or "NewsAPI",
                                "publishedAt": a.get("publishedAt") or "",
                                "urlToImage": a.get("urlToImage"),
                                "api": "NewsAPI.org"
                            })
                    if articles:
                        return articles, "NewsAPI.org (Fallback)", True, datetime.now()
        except Exception:
            pass
            
    # 3. Last resort mock
    mock_articles = []
    base_mock = generate_mock_news()
    for m in base_mock:
        m_copy = m.copy()
        m_copy["title"] = f"[{query}] " + m_copy["title"]
        m_copy["urlToImage"] = "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80"
        m_copy["api"] = "MOCK-News Engine"
        mock_articles.append(m_copy)
    return mock_articles, "MOCK-News Engine", False, datetime.now()


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
    cb_names = {
        'USD': 'Fed OR FOMC OR "Federal Reserve" OR Powell',
        'EUR': 'ECB OR EZB OR Lagarde OR Eurozone',
        'GBP': 'BoE OR "Bank of England" OR Bailey',
        'CHF': 'SNB OR "Swiss National Bank" OR Jordan',
        'CAD': 'BoC OR "Bank of Canada" OR Macklem',
        'AUD': 'RBA OR "Reserve Bank of Australia" OR Bullock',
        'NZD': 'RBNZ OR "Reserve Bank of New Zealand" OR Orr',
        'JPY': 'BoJ OR "Bank of Japan" OR Ueda'
    }
    base_term = cb_names.get(base, base)
    quote_term = cb_names.get(quote, quote)
    return f"({base} OR {quote} OR {base_term} OR {quote_term}) AND (inflation OR interest OR GDP OR Leitzins)"

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
        "NZD": st.session_state.get("manual_rate_NZD", 5.50)
    }
    
    fallback_rates = {"USA": 5.25, "EMU": 4.25, "GBR": 5.25, "JPN": 0.10, "CHE": 1.25, "AUS": 4.35, "CAN": 5.00, "NZL": 5.50}
    
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
            return 0.0, 0, "SNB (Fallback)"
            
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
def render_bias_box(signal_val, base_curr, quote_curr, base_total_score, quote_total_score, sig, override_reason=None):
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

    if override_reason:
        desc += f"<br><br><span style='color:#e2b13c; font-weight:600;'>⚠️ Signal-Filter:</span> {override_reason}"

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

# Manual cache clear
st.sidebar.button("🔄 System-Cache leeren", on_click=st.cache_data.clear)

# Zins-Kontrollzentrum (Manual inputs)
st.sidebar.markdown("---")
st.sidebar.markdown("### 🏦 Zins-Kontrollzentrum")
st.sidebar.caption("Manuelle Leitzins-Vorgaben für G8-Notenbanken:")

st.session_state["manual_rate_GBP"] = st.sidebar.number_input(
    "Bank of England (GBP) %", min_value=0.0, max_value=15.0, value=5.25, step=0.05
)
st.session_state["manual_rate_JPY"] = st.sidebar.number_input(
    "Bank of Japan (JPY) %", min_value=-5.0, max_value=15.0, value=0.10, step=0.05
)
st.session_state["manual_rate_AUD"] = st.sidebar.number_input(
    "Reserve Bank of Australia (AUD) %", min_value=0.0, max_value=15.0, value=4.35, step=0.05
)
st.session_state["manual_rate_CAD"] = st.sidebar.number_input(
    "Bank of Canada (CAD) %", min_value=0.0, max_value=15.0, value=5.00, step=0.05
)
st.session_state["manual_rate_NZD"] = st.sidebar.number_input(
    "Reserve Bank of New Zealand (NZD) %", min_value=0.0, max_value=15.0, value=5.50, step=0.05
)

st.sidebar.date_input("Letzte Aktualisierung", value=datetime.now().date())

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
        
    override_reason = None
    if base_score > 60.0 and sig in ["MS", "SS"]:
        sig = "NT"
        badge = "NEUTRAL"
        override_reason = f"Wirtschaftsscore von {base_curr} ({base_score:.1f}/100) ist stark (> 60), das negative Signal wurde auf Neutral (NT) angehoben."
    elif base_score < 40.0 and sig in ["MB", "SB"]:
        sig = "NT"
        badge = "NEUTRAL"
        override_reason = f"Wirtschaftsscore von {base_curr} ({base_score:.1f}/100) ist schwach (< 40), das positive Signal wurde auf Neutral (NT) abgesenkt."
    elif quote_score > 60.0 and sig in ["MB", "SB"]:
        sig = "NT"
        badge = "NEUTRAL"
        override_reason = f"Wirtschaftsscore von {quote_curr} ({quote_score:.1f}/100) ist stark (> 60), das positive Signal wurde auf Neutral (NT) abgesenkt."
    elif quote_score < 40.0 and sig in ["MS", "SS"]:
        sig = "NT"
        badge = "NEUTRAL"
        override_reason = f"Wirtschaftsscore von {quote_curr} ({quote_score:.1f}/100) ist schwach (< 40), das negative Signal wurde auf Neutral (NT) angehoben."

    # Load iTick close price
    itick_data, t_itick, is_live_itick = get_itick_data(selected_pair, ITICK_KEY)
    latest_close = itick_data["close"] if itick_data else 0.0

# ----------------- 5. HEADER SECTION -----------------
st.title("⚖️ Forex Fundamental Suite")
st.markdown(f"Professionelle makroökonomische Divergenz-Engine für das Paar **{selected_pair}**.")

# Always show bias banner and economy scores at the top
render_bias_box(signal_value, base_curr, quote_curr, base_score, quote_score, sig, override_reason)

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
        
    # Overrides
    if b_score > 60.0 and s in ["MS", "SS"]:
        b = "NEUTRAL"
        c = "#8b949e"
        s = "NT"
    elif b_score < 40.0 and s in ["MB", "SB"]:
        b = "NEUTRAL"
        c = "#8b949e"
        s = "NT"
    elif q_score > 60.0 and s in ["MB", "SB"]:
        b = "NEUTRAL"
        c = "#8b949e"
        s = "NT"
    elif q_score < 40.0 and s in ["MS", "SS"]:
        b = "NEUTRAL"
        c = "#8b949e"
        s = "NT"
        
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

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "🏠 Übersicht & Checkliste",
    "📅 Economic Calendar",
    "🏦 Zinsdifferenz",
    "📊 Analysten-Konsens",
    "🧠 Sentiment-Score",
    "🧮 Korrelationsmatrix",
    "📈 Langfristige Historie",
    "📰 News & Research Hub"
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
<th style="padding:12px 10px;">Nächstes Event</th>
</tr>
</thead>
<tbody>"""
    
    G8_PAIRS = [
        ("USD", "EUR"),
        ("USD", "GBP"),
        ("USD", "CHF"),
        ("USD", "CAD"),
        ("USD", "AUD"),
        ("USD", "NZD"),
        ("USD", "JPY"),
        ("EUR", "GBP")
    ]
    
    rows = []
    for base, quote in G8_PAIRS:
        p_name = f"{base}/{quote}"
        badge_name, badge_color, sig_val = get_pair_signal_and_badge(base, quote)
        
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
<td style="padding:12px 10px; color:#8c8c9a; font-size:0.8rem;">{next_ev}</td>
</tr>""")
        
    html_table += "".join(rows) + "</tbody></table>"
    st.markdown(html_table, unsafe_allow_html=True)
    st.markdown("<div class='source-tag'>Gesamte Suite-Zusammenfassung</div>", unsafe_allow_html=True)

# ----------------- TAB 2: ECONOMIC CALENDAR -----------------
with tab2:
    st.header("📅 Globaler Wirtschaftskalender")
    st.caption("Echtzeit-Timeline der kommenden globalen Events der nächsten 30 Tage mit Checkliste für manuelle Analyse.")
    
    # Filter
    countries_available = ["All"] + list(df_cal["country"].unique())
    importances_available = ["All", "High", "Medium", "Low"]
    
    f_col1, f_col2 = st.columns(2)
    with f_col1:
        sel_country = st.selectbox("Land filtern", options=countries_available, index=0, key="cal_country_filter")
    with f_col2:
        sel_importance = st.selectbox("Wichtigkeit", options=importances_available, index=0, key="cal_imp_filter")
        
    filtered_cal = df_cal.copy()
    if sel_country != "All":
        filtered_cal = filtered_cal[filtered_cal["country"] == sel_country]
    if sel_importance != "All":
        filtered_cal = filtered_cal[filtered_cal["importance"] == sel_importance]
        
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
    hist_pair = st.selectbox("Historisches Paar wählen", options=major_pairs, index=major_pairs.index(selected_pair) if selected_pair in major_pairs else 0)
    
    df_hist, t_hist, is_live_hist = get_fcs_history_data(hist_pair, FCS_KEY)
    
    if not df_hist.empty:
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
        
    st.markdown(f"<div class='source-tag {'source-tag-live' if is_live_hist else ''}'>Quelle: FCS API</div>", unsafe_allow_html=True)

# ----------------- TAB 8: NEWS & RESEARCH HUB -----------------
with tab8:
    st.header("📰 News & Research Hub")
    st.caption(f"Aktuelle fundamentale Marktnachrichten für das Paar **{selected_pair}** mit thematischer Gruppierung.")
    
    default_q = get_default_query(base_curr, quote_curr)
    search_q = st.text_input("🔍 Nachrichten durchsuchen", value=default_q, help="Nutze Stichworte wie Inflation, Leitzins, Fed, EZB etc.", key="news_search_query_input")
    
    if search_q:
        with st.spinner("Suche aktuelle Nachrichten..."):
            raw_articles, news_source, is_news_live, t_news = get_news_data_search(search_q, NEWSDATA_KEY, NEWSAPI_KEY)
            st.sidebar.caption(f"**News Hub:** {format_freshness(t_news)} ({'Live' if is_news_live else 'Demo'})")
            
            news_articles = deduplicate_articles(raw_articles)
            
        if news_articles:
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

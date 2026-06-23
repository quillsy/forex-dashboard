import os
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
AV_KEY = os.getenv("AV_API_KEY")
NEWSDATA_KEY = os.getenv("NEWSDATA_KEY")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
BENZINGA_KEY = os.getenv("BENZINGA_KEY")
FMP_KEY = os.getenv("FMP_KEY")
FCS_KEY = os.getenv("FCS_API_KEY")
STOCKDATA_KEY = os.getenv("STOCKDATA_KEY")

# ----------------- Constants & Configuration -----------------
CURRENCIES = {
    "USD": {"name": "US Dollar", "flag": "🇺🇸", "country": "United States", "wb_code": "USA"},
    "EUR": {"name": "Euro", "flag": "🇪🇺", "country": "Euro area", "wb_code": "EMU"},
    "GBP": {"name": "British Pound", "flag": "🇬🇧", "country": "United Kingdom", "wb_code": "GBR"},
    "CHF": {"name": "Swiss Franc", "flag": "🇨🇭", "country": "Switzerland", "wb_code": "CHE"},
    "JPY": {"name": "Japanese Yen", "flag": "🇯🇵", "country": "Japan", "wb_code": "JPN"},
    "CAD": {"name": "Canadian Dollar", "flag": "🇨🇦", "country": "Canada", "wb_code": "CAN"},
    "AUD": {"name": "Australian Dollar", "flag": "🇦🇺", "country": "Australia", "wb_code": "AUS"},
    "NZD": {"name": "New Zealand Dollar", "flag": "🇳🇿", "country": "New Zealand", "wb_code": "NZL"}
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

def generate_mock_fmp():
    return {
        "buy": 14, "hold": 8, "sell": 3,
        "target_high": 1.1400, "target_low": 1.0500, "target_mean": 1.1020,
        "history": [
            {"date": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"), "firm": "Goldman Sachs", "rating": "Buy", "target": 1.1200},
            {"date": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"), "firm": "JPMorgan Chase", "rating": "Hold", "target": 1.0900},
            {"date": (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d"), "firm": "Morgan Stanley", "rating": "Buy", "target": 1.1300},
            {"date": (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d"), "firm": "Barclays", "rating": "Sell", "target": 1.0600}
        ]
    }

def generate_mock_fcs_history(from_symbol, to_symbol):
    np.random.seed(95)
    dates = pd.date_range(start="1995-01-01", end=datetime.now(), freq="D")
    pair = f"{from_symbol}/{to_symbol}"
    base_prices = {"EUR/USD": 1.15, "GBP/USD": 1.55, "USD/JPY": 105.0, "USD/CHF": 1.12, "AUD/USD": 0.72, "USD/CAD": 1.25, "NZD/USD": 0.65}
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
    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"]
    matrix = [
        [1.0, 0.78, -0.45, -0.68, 0.58, -0.52, 0.61],
        [0.78, 1.0, -0.38, -0.59, 0.52, -0.48, 0.55],
        [-0.45, -0.38, 1.0, 0.72, -0.31, 0.35, -0.28],
        [-0.68, -0.59, 0.72, 1.0, -0.49, 0.44, -0.42],
        [0.58, 0.52, -0.31, -0.49, 1.0, -0.65, 0.85],
        [-0.52, -0.48, 0.35, 0.44, -0.65, 1.0, -0.59],
        [0.61, 0.55, -0.28, -0.42, 0.85, -0.59, 1.0]
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
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    res = r.json()
    calendar = res.get("economics", [])
    parsed = []
    for item in calendar:
        parsed.append({
            "time": item.get("date") or "",
            "country": item.get("country") or "",
            "event": item.get("event_name") or "",
            "consensus": item.get("consensus") or "-",
            "actual": item.get("actual") or None,
            "prior": item.get("prior") or "-",
            "importance": item.get("importance") or "Medium"
        })
    return pd.DataFrame(parsed)

def fetch_fmp_live(pair, key):
    symbol = pair.replace("/", "")
    url = f"https://financialmodelingprep.com/api/v3/analyst-stock-recommendations/{symbol}?apikey={key}"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    res = r.json()
    if not isinstance(res, list) or len(res) == 0:
        raise ValueError("No FMP ratings data")
    latest = res[0]
    # Estimate buy/hold/sell counts
    b = int(latest.get("analystRatingsbuy", 10))
    h = int(latest.get("analystRatingsHold", 5))
    s = int(latest.get("analystRatingsSell", 2))
    
    url_target = f"https://financialmodelingprep.com/api/v3/price-target-consensus?symbol={symbol}&apikey={key}"
    r_target = requests.get(url_target, timeout=5)
    mean_val = None
    if r_target.status_code == 200:
        target_res = r_target.json()
        if isinstance(target_res, list) and len(target_res) > 0:
            mean_val = float(target_res[0].get("targetConsensus", 1.0))
            
    history = []
    for item in res[:5]:
        history.append({
            "date": item.get("date"),
            "firm": "Consensus Rating",
            "rating": "Buy" if b > h else "Hold",
            "target": mean_val
        })
        
    return {
        "buy": b, "hold": h, "sell": s,
        "target_high": mean_val * 1.05 if mean_val else None,
        "target_low": mean_val * 0.95 if mean_val else None,
        "target_mean": mean_val,
        "history": history
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
def get_fmp_data(pair, key):
    if not key:
        return generate_mock_fmp(), datetime.now(), False
    try:
        data = fetch_fmp_live(pair, key)
        return data, datetime.now(), True
    except Exception:
        return generate_mock_fmp(), datetime.now(), False

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
        pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD"]
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
        title_trunc = title_clean[:35]
        
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
    fallback_rates = {"USA": 5.25, "EMU": 4.25, "GBR": 5.00, "JPN": 0.25, "CHE": 1.25}
    series_map = {
        "USA": "FEDFUNDS",
        "EMU": "ECBMAINVAL",
        "GBR": "IUDSOIA",
        "JPN": "INTGSTJP",
        "CHE": "INTGSTCH"
    }
    
    if country_code in series_map:
        sid = series_map[country_code]
        df, t, is_live = get_fred_data(sid, fred_key)
        if not df.empty:
            latest = df.iloc[-1]["value"]
            prev = df.iloc[-2]["value"] if len(df) > 1 else latest
            bps_change = int((latest - prev) * 100)
            return latest, bps_change, f"FRED ({'Live' if is_live else 'Demo'})"
            
    val = fallback_rates.get(country_code, 2.00)
    return val, 0, "Statische Fallback-Quelle"

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
        df_gdp, _, _ = get_worldbank_data(code, "NY.GDP.MKTP.KD.ZG")
        df_cpi, _, _ = get_worldbank_data(code, "FP.CPI.TOTL.ZG")
        
        rate_val, _, _ = get_country_rate(code, fred_key)
        rate_score = np.clip((rate_val / 6.0) * 100, 0, 100)
        
        unemp_score = 65.0
        
        latest_cpi = df_cpi.iloc[-1]["value"] if not df_cpi.empty else 2.5
        cpi_score = np.clip((latest_cpi / 5.0) * 100, 0, 100)
        
        latest_gdp = df_gdp.iloc[-1]["value"] if not df_gdp.empty else 1.5
        gdp_score = np.clip((latest_gdp + 2.0) / 6.0 * 100, 0, 100)

    total_score = 0.3 * rate_score + 0.3 * cpi_score + 0.2 * unemp_score + 0.2 * gdp_score
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
base_curr = st.sidebar.selectbox("Basiswährung (Base)", options=list(CURRENCIES.keys()), index=1) # Default EUR
quote_curr = st.sidebar.selectbox("Quote-Währung (Quote)", options=list(CURRENCIES.keys()), index=0) # Default USD
selected_pair = f"{base_curr}/{quote_curr}"

if base_curr == quote_curr:
    st.sidebar.error("Basis- und Quote-Währung dürfen nicht identisch sein.")
    st.stop()

# Manual cache clear
st.sidebar.button("🔄 System-Cache leeren", on_click=st.cache_data.clear)

# ----------------- 4. GLOBAL DATA INITIALIZATION & FRESHNESS -----------------
with st.spinner("Initialisiere globale Marktdaten..."):
    # Pre-load macro scores
    base_score = compute_currency_score(base_curr, FRED_KEY)
    quote_score = compute_currency_score(quote_curr, FRED_KEY)
    
    # Calculate corrected signal value (scaled to range -50 to +50)
    raw_diff = base_score - quote_score
    signal_value = raw_diff / 2.0
    signal_value = max(-50.0, min(50.0, signal_value))
    
    # Calculate filtered trading signal based on new boundaries
    if signal_value >= 35.0:
        sig = "SB"
        badge = "STRONG BUY"
    elif 15.0 <= signal_value < 35.0:
        sig = "MB"
        badge = "MID BUY"
    elif -15.0 < signal_value < 15.0:
        sig = "NT"
        badge = "NEUTRAL"
    elif -35.0 < signal_value <= -15.0:
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

    # Load AV close price
    df_av, t_av, is_live_av = get_av_data(base_curr, quote_curr, AV_KEY)
    latest_close = df_av.iloc[-1]["close"] if not df_av.empty else 0.0

# ----------------- 5. HEADER SECTION -----------------
st.title("⚖️ Forex Fundamental Suite")
st.markdown(f"Professionelle makroökonomische Divergenz-Engine für das Paar **{selected_pair}**.")

# Always show bias banner and economy scores at the top
render_bias_box(signal_value, base_curr, quote_curr, base_score, quote_score, sig, override_reason)

col_score_b, col_score_q = st.columns(2)
with col_score_b:
    st.markdown(f"""
    <div class="metric-card-custom" style="border-left: 4px solid #10b981;">
        <span class="metric-label">{CURRENCIES[base_curr]['flag']} {base_curr} Wirtschaftsscore</span>
        <div class="metric-value">{base_score:.1f} / 100</div>
        <div class="source-tag">Zusammengesetzter Score</div>
    </div>
    """, unsafe_allow_html=True)
with col_score_q:
    st.markdown(f"""
    <div class="metric-card-custom" style="border-left: 4px solid #444c56;">
        <span class="metric-label">{CURRENCIES[quote_curr]['flag']} {quote_curr} Wirtschaftsscore</span>
        <div class="metric-value">{quote_score:.1f} / 100</div>
        <div class="source-tag">Zusammengesetzter Score</div>
    </div>
    """, unsafe_allow_html=True)


# ----------------- 6. TABS MODULES -----------------
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📅 Economic Calendar",
    "🏦 Zinsdifferenz",
    "📊 Analysten-Konsens",
    "🧠 Sentiment-Score",
    "🧮 Korrelationsmatrix",
    "📈 Langfristige Historie",
    "📰 News & Research"
])

# ----------------- TAB 1: ECONOMIC CALENDAR (Benzinga) -----------------
with tab1:
    st.header("📅 Globaler Wirtschaftskalender")
    st.caption("Echtzeit-Timeline der kommenden globalen Events der nächsten 30 Tage.")
    
    df_cal, t_cal, is_live_cal = get_benzinga_data(BENZINGA_KEY)
    st.sidebar.caption(f"**Benzinga:** {format_freshness(t_cal)} ({'Live' if is_live_cal else 'Demo'})")
    
    # Filter
    countries_available = ["All"] + list(df_cal["country"].unique())
    importances_available = ["All", "High", "Medium", "Low"]
    
    f_col1, f_col2 = st.columns(2)
    with f_col1:
        sel_country = st.selectbox("Land filtern", options=countries_available, index=0)
    with f_col2:
        sel_importance = st.selectbox("Wichtigkeit", options=importances_available, index=0)
        
    filtered_cal = df_cal.copy()
    if sel_country != "All":
        filtered_cal = filtered_cal[filtered_cal["country"] == sel_country]
    if sel_importance != "All":
        filtered_cal = filtered_cal[filtered_cal["importance"] == sel_importance]
        
    if not filtered_cal.empty:
        styled_rows = []
        for idx, row in filtered_cal.iterrows():
            act = row["actual"]
            cons = row["consensus"]
            
            act_style = ""
            if act is not None and cons is not None and cons != "-":
                try:
                    act_num = float(str(act).replace("%","").replace("K","").replace("M","").strip())
                    cons_num = float(str(cons).replace("%","").replace("K","").replace("M","").strip())
                    if act_num >= cons_num:
                        act_style = f"<span style='color:#10b981; font-weight:700;'>{act}</span>"
                    else:
                        act_style = f"<span style='color:#ef4444; font-weight:700;'>{act}</span>"
                except Exception:
                    act_style = f"<span>{act}</span>"
            else:
                act_style = f"<span style='color:#7d7d8a;'>-</span>"
                
            styled_rows.append(f"""
            <tr>
                <td style="padding:10px 5px; border-bottom:1px solid #1f2026;">{row['time']}</td>
                <td style="padding:10px 5px; border-bottom:1px solid #1f2026; font-weight:600;">{row['country']}</td>
                <td style="padding:10px 5px; border-bottom:1px solid #1f2026;">{row['event']}</td>
                <td style="padding:10px 5px; border-bottom:1px solid #1f2026;">{cons}</td>
                <td style="padding:10px 5px; border-bottom:1px solid #1f2026;">{act_style}</td>
                <td style="padding:10px 5px; border-bottom:1px solid #1f2026;">{row['prior']}</td>
                <td style="padding:10px 5px; border-bottom:1px solid #1f2026;"><span style="font-size:0.75rem; background-color:{'rgba(239, 68, 68, 0.1)' if row['importance'] == 'High' else 'rgba(255, 255, 255, 0.03)'}; color:{'#ef4444' if row['importance'] == 'High' else '#7d7d8a'}; padding:2px 6px; border-radius:3px;">{row['importance']}</span></td>
            </tr>
            """)
            
        html_table = f"""
        <table style="width:100%; border-collapse:collapse; text-align:left; font-size:0.85rem;">
            <thead>
                <tr style="border-bottom: 2px solid #1f2026; color:#7d7d8a; text-transform:uppercase; font-size:0.7rem; font-weight:700;">
                    <th style="padding:10px 5px;">Datum/Uhrzeit</th>
                    <th style="padding:10px 5px;">Land</th>
                    <th style="padding:10px 5px;">Event</th>
                    <th style="padding:10px 5px;">Konsensus</th>
                    <th style="padding:10px 5px;">Ist-Wert</th>
                    <th style="padding:10px 5px;">Vorherig</th>
                    <th style="padding:10px 5px;">Wichtigkeit</th>
                </tr>
            </thead>
            <tbody>
                {"".join(styled_rows)}
            </tbody>
        </table>
        """
        st.markdown(html_table, unsafe_allow_html=True)
    else:
        st.info("Keine Events für die gewählten Filter vorhanden.")
        
    st.markdown(f"<div style='margin-top:15px;' class='source-tag {'source-tag-live' if is_live_cal else ''}'>Quelle: Benzinga</div>", unsafe_allow_html=True)

# ----------------- TAB 2: ZINSDIFFERENZ (FRED & World Bank) -----------------
with tab2:
    st.header("🏦 Zinsdifferenz & Notenbanken")
    st.caption("Vergleich der aktuellen Leitzinsen der wichtigsten Notenbanken weltweit.")
    
    df_f_funds, t_fred, is_live_fred = get_fred_data("FEDFUNDS", FRED_KEY)
    st.sidebar.caption(f"**FRED:** {format_freshness(t_fred)} ({'Live' if is_live_fred else 'Demo'})")
    
    rate_usd, change_usd, source_usd = get_country_rate("USA", FRED_KEY)
    rate_eur, change_eur, source_eur = get_country_rate("EMU", FRED_KEY)
    rate_gbp, change_gbp, source_gbp = get_country_rate("GBR", FRED_KEY)
    rate_jpy, change_jpy, source_jpy = get_country_rate("JPN", FRED_KEY)
    rate_chf, change_chf, source_chf = get_country_rate("CHE", FRED_KEY)
    
    z_col1, z_col2, z_col3, z_col4, z_col5 = st.columns(5)
    with z_col1:
        render_metric_card("Fed Rates (USD)", f"{rate_usd:.2f}%", source_usd, is_live_fred)
        st.metric("Veränderung", f"{rate_usd:.2f}%", f"{change_usd:+d} Bps", label_visibility="collapsed")
    with z_col2:
        render_metric_card("ECB Rates (EUR)", f"{rate_eur:.2f}%", source_eur, is_live_fred)
        st.metric("Veränderung", f"{rate_eur:.2f}%", f"{change_eur:+d} Bps", label_visibility="collapsed")
    with z_col3:
        render_metric_card("BoE Rates (GBP)", f"{rate_gbp:.2f}%", source_gbp, is_live_fred)
        st.metric("Veränderung", f"{rate_gbp:.2f}%", f"{change_gbp:+d} Bps", label_visibility="collapsed")
    with z_col4:
        render_metric_card("BoJ Rates (JPY)", f"{rate_jpy:.2f}%", source_jpy, is_live_fred)
        st.metric("Veränderung", f"{rate_jpy:.2f}%", f"{change_jpy:+d} Bps", label_visibility="collapsed")
    with z_col5:
        render_metric_card("SNB Rates (CHF)", f"{rate_chf:.2f}%", source_chf, is_live_fred)
        st.metric("Veränderung", f"{rate_chf:.2f}%", f"{change_chf:+d} Bps", label_visibility="collapsed")
        
    banks = ["USD (Fed)", "EUR (ECB)", "GBP (BoE)", "CHF (SNB)", "JPY (BoJ)"]
    rates = [rate_usd, rate_eur, rate_gbp, rate_chf, rate_jpy]
    
    fig_rates = go.Figure()
    fig_rates.add_trace(go.Bar(
        x=banks, y=rates,
        marker_color=['#10b981', '#e2b13c', '#ffd166', '#8b949e', '#ef4444'],
        text=[f"{r:.2f}%" for r in rates],
        textposition='auto',
        name="Zinssatz"
    ))
    fig_rates.update_layout(
        title="Aktuelle Leitzinsen im Vergleich",
        yaxis_title="Zinssatz (%)",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color="#7d7d8a", size=10),
        xaxis=dict(showgrid=False, linecolor="#1f2026"),
        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)', linecolor="#1f2026"),
        height=320,
        margin=dict(l=10, r=10, t=40, b=10)
    )
    st.plotly_chart(fig_rates, use_container_width=True)
    
    st.markdown("*(Zusätzlich unterstützte Länder außerhalb der USA beziehen historische Makrodaten wie BIP & Inflation aus der World Bank API).*")
    st.markdown("<div class='source-tag'>Quelle: FRED & World Bank</div>", unsafe_allow_html=True)

# ----------------- TAB 3: ANALYSTEN-KONSENS (FMP) -----------------
with tab3:
    st.header("📊 Analysten-Konsens & Kursziele")
    st.caption(f"Konsens-Ratings und durchschnittliche Kursziele für das Währungspaar **{selected_pair}**.")
    
    fmp_data, t_fmp, is_live_fmp = get_fmp_data(selected_pair, FMP_KEY)
    st.sidebar.caption(f"**FMP:** {format_freshness(t_fmp)} ({'Live' if is_live_fmp else 'Demo'})")
    
    c_col1, c_col2 = st.columns([1, 1.2])
    with c_col1:
        st.subheader("Verteilung der Analysten-Ratings")
        labels = ["Buy", "Hold", "Sell"]
        counts = [fmp_data["buy"], fmp_data["hold"], fmp_data["sell"]]
        
        fig_recs = go.Figure(data=[go.Pie(
            labels=labels, values=counts,
            hole=.4,
            marker_colors=["#10b981", "#e2b13c", "#ef4444"],
            textinfo='value+percent',
            textfont=dict(color="#ffffff")
        )])
        fig_recs.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#7d7d8a"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.1, xanchor="center", x=0.5),
            height=280,
            margin=dict(l=10, r=10, t=10, b=10)
        )
        st.plotly_chart(fig_recs, use_container_width=True)
        
    with c_col2:
        st.subheader("Konsens-Kursziele")
        avg_t = fmp_data["target_mean"]
        high_t = fmp_data["target_high"]
        low_t = fmp_data["target_low"]
        
        t_col1, t_col2 = st.columns(2)
        with t_col1:
            st.metric("Mittleres Kursziel", f"{avg_t:.4f}" if avg_t else "N/A")
            st.metric("Höchstes Kursziel", f"{high_t:.4f}" if high_t else "N/A")
        with t_col2:
            st.metric("Aktueller Kurs", f"{latest_close:.4f}" if latest_close else "N/A")
            st.metric("Tiefstes Kursziel", f"{low_t:.4f}" if low_t else "N/A")
            
    st.subheader("Letzte Ratings-Änderungen")
    df_ratings = pd.DataFrame(fmp_data["history"])
    if not df_ratings.empty:
        st.dataframe(df_ratings, use_container_width=True, hide_index=True)
    else:
        st.info("Keine Rating-Historie verfügbar.")
        
    st.markdown(f"<div class='source-tag {'source-tag-live' if is_live_fmp else ''}'>Quelle: Financial Modeling Prep</div>", unsafe_allow_html=True)

# ----------------- TAB 4: SENTIMENT-SCORE (StockData.org) -----------------
with tab4:
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

# ----------------- TAB 5: KORRELATIONSMATRIX (FCS API) -----------------
with tab5:
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

# ----------------- TAB 6: LANGFRISTIGE HISTORIE (FCS API) -----------------
with tab6:
    st.header("📈 Langfristige Historie & Zyklen (seit 1995)")
    st.caption(f"Langfristiger Kursverlauf von **{selected_pair}** ab 1995 zur Analyse übergeordneter wirtschaftlicher Zyklen.")
    
    df_hist, t_hist, is_live_hist = get_fcs_history_data(selected_pair, FCS_KEY)
    
    if not df_hist.empty:
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(
            x=df_hist["date"], y=df_hist["close"],
            line=dict(color="#e2b13c", width=2),
            name="Schlusskurs"
        ))
        fig_hist.update_layout(
            title=f"Historischer Langzeit-Kurs ({selected_pair})",
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

# ----------------- TAB 7: NEWS & RESEARCH HUB (Restored News Pages) -----------------
with tab7:
    st.header("📰 News & Research Hub")
    st.caption(f"Aktuelle fundamentale Marktnachrichten für das Paar **{selected_pair}** mit thematischer Gruppierung.")
    
    # Pre-calculated default query
    default_q = get_default_query(base_curr, quote_curr)
    
    # Search input
    search_q = st.text_input("🔍 Nachrichten durchsuchen", value=default_q, help="Nutze Stichworte wie Inflation, Leitzins, Fed, EZB etc.")
    
    if search_q:
        with st.spinner("Suche aktuelle Nachrichten..."):
            raw_articles, news_source, is_news_live, t_news = get_news_data_search(search_q, NEWSDATA_KEY, NEWSAPI_KEY)
            st.sidebar.caption(f"**News Hub:** {format_freshness(t_news)} ({'Live' if is_news_live else 'Demo'})")
            
            # Deduplicate articles
            news_articles = deduplicate_articles(raw_articles)
            
        if news_articles:
            st.info(f"Es wurden {len(news_articles)} relevante und einzigartige Artikel gefunden. (Aktiv: {news_source})")
            
            # Categorize articles
            grouped_articles = {
                "🏦 Geldpolitik & Zinsen": [],
                "🚢 Import & Export": [],
                "🌍 Länder-Analysen": [],
                "📊 Sonstige Makro-News": []
            }
            
            for art in news_articles:
                cat = categorize_article(art)
                grouped_articles[cat].append(art)
                
            # Create sub-tabs
            sub_tabs = st.tabs([
                "📋 Alle News", 
                "🏦 Geldpolitik & Zinsen", 
                "🚢 Import & Export", 
                "🌍 Länder-Analysen", 
                "📊 Sonstige Makro-News"
            ])
            
            with sub_tabs[0]:
                render_articles_grid(news_articles)
            with sub_tabs[1]:
                render_articles_grid(grouped_articles["🏦 Geldpolitik & Zinsen"])
            with sub_tabs[2]:
                render_articles_grid(grouped_articles["🚢 Import & Export"])
            with sub_tabs[3]:
                render_articles_grid(grouped_articles["🌍 Länder-Analysen"])
            with sub_tabs[4]:
                render_articles_grid(grouped_articles["📊 Sonstige Makro-News"])
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

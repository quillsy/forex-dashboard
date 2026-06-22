import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.express as px

# Set up page config
st.set_page_config(
    page_title="Forex Zero-Overlap Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for Obsidian Dark Theme & institutional design
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Roboto+Mono:wght@400;700&display=swap');
    
    /* Global modifications */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif !important;
    }
    
    /* Obsidian Dark theme background overrides */
    .stApp {
        background-color: #070708 !important;
        color: #b2b2be !important;
    }
    
    /* Header typography */
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Inter', sans-serif !important;
        color: #f0f0f5 !important;
        font-weight: 600 !important;
    }
    
    /* Style sidebar */
    section[data-testid="stSidebar"] {
        background-color: #0c0c0e !important;
        border-right: 1px solid #1f2026 !important;
    }
    
    /* Static cards */
    .metric-card {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 18px;
        margin-bottom: 12px;
        font-family: 'Inter', sans-serif;
    }
    
    /* News Card style */
    .news-card {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 16px;
        margin-bottom: 15px;
        transition: border-color 0.2s, background-color 0.2s;
    }
    .news-card:hover {
        border-color: #e66400;
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
        color: #e66400 !important;
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
    .source-tag {
        font-family: 'Roboto Mono', monospace;
        font-size: 0.65rem;
        background-color: rgba(230, 100, 0, 0.08);
        color: #e66400;
        border: 1px solid rgba(230, 100, 0, 0.2);
        padding: 2px 6px;
        border-radius: 3px;
        display: inline-block;
        margin-top: 4px;
    }
    .source-tag-gray {
        font-family: 'Roboto Mono', monospace;
        font-size: 0.65rem;
        background-color: rgba(255, 255, 255, 0.03);
        color: #8c8c9a;
        border: 1px solid #1f2026;
        padding: 2px 6px;
        border-radius: 3px;
        display: inline-block;
        margin-top: 4px;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- 0. MOCK DATA GENERATORS -----------------
def generate_mock_fred(series_id):
    np.random.seed(42)
    dates = pd.date_range(start="2015-01-01", end=datetime.now(), freq="ME")
    if series_id == "FEDFUNDS":
        values = np.linspace(0.25, 5.25, len(dates)) + np.random.normal(0, 0.1, len(dates))
    elif series_id == "CPIAUCSL":
        values = np.linspace(235.0, 312.0, len(dates)) + np.random.normal(0, 0.5, len(dates))
    elif series_id == "GDPC1":
        dates = pd.date_range(start="2015-01-01", end=datetime.now(), freq="QE")
        values = np.linspace(17500.0, 22500.0, len(dates)) + np.random.normal(0, 100.0, len(dates))
    elif series_id == "UNRATE":
        values = np.linspace(5.5, 4.0, len(dates)) + np.random.normal(0, 0.2, len(dates))
    else:
        values = [1.0] * len(dates)
    return pd.DataFrame({"date": dates, "value": values})

def generate_mock_fx(from_symbol, to_symbol):
    np.random.seed(31)
    dates = pd.date_range(end=datetime.now(), periods=250, freq="D")
    pair = f"{from_symbol}/{to_symbol}"
    base_prices = {
        "EUR/USD": 1.08,
        "GBP/USD": 1.26,
        "USD/JPY": 158.0,
        "USD/CHF": 0.89,
        "AUD/USD": 0.66
    }
    base = base_prices.get(pair, 1.0)
    prices = [base]
    for _ in range(249):
        prices.append(prices[-1] * (1 + np.random.normal(0, 0.003)))
    df = pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p * 1.002 for p in prices],
        "low": [p * 0.998 for p in prices],
        "close": prices
    })
    return df

def generate_mock_news():
    return [
        {
            "title": "FED signalisiert Zinswende: Dollar gewinnt an Stärke gegenüber dem Euro",
            "source": "ForexMOCK",
            "publishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "url": "#",
            "description": "Die US-Notenbank Federal Reserve deutet eine längere Phase hoher Leitzinsen an, was den USD beflügelt.",
            "api_source": "MOCK-News Engine"
        },
        {
            "title": "EZB hält Leitzins unverändert: EUR/USD gerät unter Druck",
            "source": "MacroMOCK",
            "publishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "url": "#",
            "description": "Die Europäische Zentralbank hat den Zinssatz bestätigt. Analysten erwarten schwächere Euro-Notierungen.",
            "api_source": "MOCK-News Engine"
        },
        {
            "title": "Wechselkurse im Fokus: Volatilität beim USD/JPY erreicht Mehrmonatshoch",
            "source": "JpyNewsMOCK",
            "publishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "url": "#",
            "description": "Interventionen der Bank of Japan stützen den Yen temporär, doch Zinsdivergenz zum USD bleibt hoch.",
            "api_source": "MOCK-News Engine"
        }
    ]

# ----------------- 1. FRED DATA LOADING -----------------
def fetch_fred_metric(series_id, api_key):
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": "2015-01-01"
    }
    response = requests.get(url, params=params, timeout=12)
    response.raise_for_status()
    data = response.json()
    observations = data.get("observations", [])
    parsed = []
    for obs in observations:
        date_str = obs.get("date")
        val_str = obs.get("value")
        if val_str and val_str != ".":
            try:
                parsed.append({
                    "date": date_str,
                    "value": float(val_str)
                })
            except ValueError:
                pass
    df = pd.DataFrame(parsed)
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)

@st.cache_data(ttl=86400, show_spinner=False)
def get_fred_data_cached(series_id, api_key, use_demo):
    if use_demo or not api_key:
        df = generate_mock_fred(series_id)
        return df, datetime.now(), True
    try:
        df = fetch_fred_metric(series_id, api_key)
        return df, datetime.now(), False
    except Exception:
        df = generate_mock_fred(series_id)
        return df, datetime.now(), True

# ----------------- 2. ALPHA VANTAGE FX & SMAS -----------------
def fetch_alpha_vantage_fx(from_symbol, to_symbol, api_key):
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "FX_DAILY",
        "from_symbol": from_symbol,
        "to_symbol": to_symbol,
        "outputsize": "full",
        "apikey": api_key
    }
    response = requests.get(url, params=params, timeout=12)
    response.raise_for_status()
    data = response.json()
    if "Time Series FX (Daily)" not in data:
        raise ValueError(data.get("Information") or data.get("Note") or data.get("Error Message") or "Keine Kursdaten gefunden.")
    time_series = data["Time Series FX (Daily)"]
    parsed = []
    for date_str, ohlc in time_series.items():
        parsed.append({
            "date": date_str,
            "open": float(ohlc["1. open"]),
            "high": float(ohlc["2. high"]),
            "low": float(ohlc["3. low"]),
            "close": float(ohlc["4. close"])
        })
    df = pd.DataFrame(parsed)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df

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

@st.cache_data(ttl=900, show_spinner=False)
def get_av_data_cached(from_symbol, to_symbol, api_key, use_demo):
    if use_demo or not api_key:
        df = generate_mock_fx(from_symbol, to_symbol)
        df = calculate_smas(df)
        return df, datetime.now(), True
    try:
        df = fetch_alpha_vantage_fx(from_symbol, to_symbol, api_key)
        df = calculate_smas(df)
        return df, datetime.now(), False
    except Exception:
        df = generate_mock_fx(from_symbol, to_symbol)
        df = calculate_smas(df)
        return df, datetime.now(), True

# ----------------- 3. NEWS LOADER & FALLBACKS -----------------
def fetch_newsdata(api_key):
    url = "https://newsdata.io/api/1/latest"
    params = {
        "apikey": api_key,
        "q": "Forex OR Dollar",
        "language": "en,de"
    }
    response = requests.get(url, params=params, timeout=12)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "success":
        raise ValueError(f"NewsData status error: {data.get('results') or 'Unknown'}")
    results = data.get("results", [])
    articles = []
    for art in results:
        articles.append({
            "title": art.get("title") or "Ohne Titel",
            "source": art.get("source_id") or "NewsData",
            "publishedAt": art.get("pubDate") or "",
            "url": art.get("link") or "#",
            "description": art.get("description") or "",
            "api_source": "NewsData.io"
        })
    return articles

def fetch_newsapi(api_key):
    url = "https://newsapi.org/v2/everything"
    params = {
        "apiKey": api_key,
        "q": "Forex OR Dollar",
        "sortBy": "publishedAt",
        "pageSize": 10,
        "language": "de,en"
    }
    response = requests.get(url, params=params, timeout=12)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "ok":
        raise ValueError(f"NewsAPI status error: {data.get('message') or 'Unknown'}")
    results = data.get("articles", [])
    articles = []
    for art in results:
        articles.append({
            "title": art.get("title") or "Ohne Titel",
            "source": art.get("source", {}).get("name") or "NewsAPI",
            "publishedAt": art.get("publishedAt") or "",
            "url": art.get("url") or "#",
            "description": art.get("description") or "",
            "api_source": "NewsAPI.org"
        })
    return articles

def load_all_news(newsdata_key, newsapi_key, use_demo):
    if use_demo:
        return generate_mock_news(), "MOCK-News Engine", True
        
    # Attempt NewsData.io first (Primary)
    if newsdata_key:
        try:
            articles = fetch_newsdata(newsdata_key)
            if articles:
                return articles, "NewsData.io", False
        except Exception as e:
            st.sidebar.warning(f"NewsData.io fehlgeschlagen: {str(e)}. Fallback aktiv.")
            
    # Attempt NewsAPI.org (Fallback)
    if newsapi_key:
        try:
            articles = fetch_newsapi(newsapi_key)
            if articles:
                return articles, "NewsAPI.org", False
        except Exception as e:
            st.sidebar.error(f"Fallback NewsAPI.org ebenfalls fehlgeschlagen: {str(e)}")
            
    # Last resort mock
    return generate_mock_news(), "MOCK-News Engine (Fallback)", True

def deduplicate_news(articles):
    seen = set()
    deduped = []
    for art in articles:
        # Standardize the first 50 characters to catch duplicates
        title_norm = "".join(art["title"].split()).lower()[:50]
        if title_norm not in seen:
            seen.add(title_norm)
            deduped.append(art)
    return deduped

@st.cache_data(ttl=300, show_spinner=False)
def get_news_cached(newsdata_key, newsapi_key, use_demo):
    articles, source, is_mock = load_all_news(newsdata_key, newsapi_key, use_demo)
    return articles, source, is_mock, datetime.now()

# Helper for elapsed time
def format_freshness(timestamp):
    elapsed = datetime.now() - timestamp
    seconds = int(elapsed.total_seconds())
    if seconds < 60:
        return f"vor {seconds}s"
    minutes = seconds // 60
    return f"vor {minutes}m {seconds % 60}s"

# ----------------- 4. SIDEBAR CONFIGURATION -----------------
st.sidebar.title("⚙️ Einstellungen")

# API Keys Expander
with st.sidebar.expander("🔑 API Schlüssel"):
    try:
        fred_default = st.secrets.get("FRED_API_KEY", "16a7c7fcd052b9da3b801f2691a37d3b")
        av_default = st.secrets.get("AV_API_KEY", "BATX15WEXQJY7SS5")
        newsdata_default = st.secrets.get("NEWSDATA_KEY", "pub_de1743243cb64703ac59bf87ae1566b7")
        newsapi_default = st.secrets.get("NEWSAPI_KEY", "498a4855604345789b4a6eb4757f6ce8")
    except Exception:
        fred_default = "16a7c7fcd052b9da3b801f2691a37d3b"
        av_default = "BATX15WEXQJY7SS5"
        newsdata_default = "pub_de1743243cb64703ac59bf87ae1566b7"
        newsapi_default = "498a4855604345789b4a6eb4757f6ce8"

    fred_key = st.text_input("FRED API-Key", type="password", value=fred_default)
    av_key = st.text_input("Alpha Vantage API-Key", type="password", value=av_default)
    newsdata_key = st.text_input("NewsData.io API-Key (Primär)", type="password", value=newsdata_default)
    newsapi_key = st.text_input("NewsAPI.org API-Key (Fallback)", type="password", value=newsapi_default)
    
    use_demo = st.checkbox("Demo-Modus erzwingen", value=False)

# Currency Selector
st.sidebar.markdown("### 💱 Währungspaar wählen")
fx_pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD"]
selected_pair = st.sidebar.selectbox("Paar", options=fx_pairs, index=0)
from_sym, to_sym = selected_pair.split("/")

st.sidebar.button("🔄 Alle Caches löschen", on_click=st.cache_data.clear)

# ----------------- 5. MAIN DATA LOADING -----------------
with st.spinner("Lade Daten..."):
    # Load FRED
    df_fed, t_fed, mock_fed = get_fred_data_cached("FEDFUNDS", fred_key, use_demo)
    df_cpi, t_cpi, mock_cpi = get_fred_data_cached("CPIAUCSL", fred_key, use_demo)
    df_gdp, t_gdp, mock_gdp = get_fred_data_cached("GDPC1", fred_key, use_demo)
    df_unemp, t_unemp, mock_unemp = get_fred_data_cached("UNRATE", fred_key, use_demo)
    
    # Load Alpha Vantage
    df_fx, t_fx, mock_fx = get_av_data_cached(from_sym, to_sym, av_key, use_demo)
    
    # Load News
    news_articles, news_source, mock_news, t_news = get_news_cached(newsdata_key, newsapi_key, use_demo)

# ----------------- 6. SIDEBAR FRESHNESS PANEL -----------------
st.sidebar.markdown("---")
st.sidebar.markdown("### ⏱️ Daten-Aktualität")
st.sidebar.caption(f"**FRED US-Makro:** {format_freshness(t_fed)} (Typ: {'Demo' if mock_fed else 'Live'})")
st.sidebar.caption(f"**Alpha Vantage FX:** {format_freshness(t_fx)} (Typ: {'Demo' if mock_fx else 'Live'})")
st.sidebar.caption(f"**News Hub:** {format_freshness(t_news)} (Typ: {'Demo' if mock_news else 'Live'})")

# ----------------- 7. DASHBOARD LAYOUT -----------------
st.title("📊 Forex Zero-Overlap Suite")
st.markdown("Strikte Zero-Overlap-Architektur mit sauberer Datenquellenzuordnung.")

col_macro, col_chart, col_news = st.columns([1, 1.2, 1])

# --- COLUMN 1: US MACRO DATA (FRED) ---
with col_macro:
    st.header("🇺🇸 US-Makrodaten")
    
    # 1. Interest Rate
    latest_fed = df_fed.iloc[-1]["value"] if not df_fed.empty else 0.0
    st.markdown(f"""
    <div class="metric-card">
        <span style="font-size: 0.72rem; text-transform: uppercase; color: #7d7d8a; font-weight: 600;">Federal Funds Rate</span>
        <div style="font-size: 1.8rem; font-weight: 700; color: #f0f0f5; margin: 4px 0;">{latest_fed:.2f}%</div>
        <div class="source-tag-gray">Datenquelle: FRED</div>
    </div>
    """, unsafe_allow_html=True)
    
    # 2. Unemployment
    latest_unemp = df_unemp.iloc[-1]["value"] if not df_unemp.empty else 0.0
    st.markdown(f"""
    <div class="metric-card">
        <span style="font-size: 0.72rem; text-transform: uppercase; color: #7d7d8a; font-weight: 600;">Arbeitslosenquote</span>
        <div style="font-size: 1.8rem; font-weight: 700; color: #f0f0f5; margin: 4px 0;">{latest_unemp:.2f}%</div>
        <div class="source-tag-gray">Datenquelle: FRED</div>
    </div>
    """, unsafe_allow_html=True)
    
    # 3. Inflation (YoY computed from index)
    if not df_cpi.empty and len(df_cpi) >= 13:
        df_cpi_calc = df_cpi.copy()
        df_cpi_calc["yoy"] = df_cpi_calc["value"].pct_change(periods=12) * 100
        latest_cpi = df_cpi_calc.iloc[-1]["yoy"]
        latest_cpi_str = f"{latest_cpi:.2f}% (YoY)"
    else:
        latest_cpi_str = "N/A"
    st.markdown(f"""
    <div class="metric-card">
        <span style="font-size: 0.72rem; text-transform: uppercase; color: #7d7d8a; font-weight: 600;">CPI Inflation</span>
        <div style="font-size: 1.8rem; font-weight: 700; color: #f0f0f5; margin: 4px 0;">{latest_cpi_str}</div>
        <div class="source-tag-gray">Datenquelle: FRED</div>
    </div>
    """, unsafe_allow_html=True)
    
    # 4. GDP Growth (YoY computed from quarterly index)
    if not df_gdp.empty and len(df_gdp) >= 5:
        df_gdp_calc = df_gdp.copy()
        df_gdp_calc["yoy"] = df_gdp_calc["value"].pct_change(periods=4) * 100
        latest_gdp = df_gdp_calc.iloc[-1]["yoy"]
        latest_gdp_str = f"{latest_gdp:+.2f}% (YoY)"
    else:
        latest_gdp_str = "N/A"
    st.markdown(f"""
    <div class="metric-card">
        <span style="font-size: 0.72rem; text-transform: uppercase; color: #7d7d8a; font-weight: 600;">GDP Wirtschaftswachstum</span>
        <div style="font-size: 1.8rem; font-weight: 700; color: #f0f0f5; margin: 4px 0;">{latest_gdp_str}</div>
        <div class="source-tag-gray">Datenquelle: FRED</div>
    </div>
    """, unsafe_allow_html=True)

# --- COLUMN 2: FX CHART & TECHNICAL INDICATORS (Alpha Vantage) ---
with col_chart:
    st.header(f"📈 Chart & SMAs ({selected_pair})")
    
    if not df_fx.empty:
        latest_close = df_fx.iloc[-1]["close"]
        latest_sma50 = df_fx.iloc[-1]["SMA_50"]
        latest_sma200 = df_fx.iloc[-1]["SMA_200"]
        
        # Display OHLCV values
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            st.markdown(f"""
            <div class="metric-card" style="text-align: center;">
                <span style="font-size: 0.72rem; text-transform: uppercase; color: #7d7d8a; font-weight: 600;">Kurs</span>
                <div style="font-size: 1.5rem; font-weight: 700; color: #f0f0f5; margin: 4px 0;">{latest_close:.4f}</div>
                <div class="source-tag">Datenquelle: Alpha Vantage</div>
            </div>
            """, unsafe_allow_html=True)
        with cc2:
            sma50_str = f"{latest_sma50:.4f}" if not np.isnan(latest_sma50) else "Lade Daten..."
            st.markdown(f"""
            <div class="metric-card" style="text-align: center;">
                <span style="font-size: 0.72rem; text-transform: uppercase; color: #7d7d8a; font-weight: 600;">SMA 50</span>
                <div style="font-size: 1.5rem; font-weight: 700; color: #e66400; margin: 4px 0;">{sma50_str}</div>
                <div class="source-tag">Datenquelle: Alpha Vantage</div>
            </div>
            """, unsafe_allow_html=True)
        with cc3:
            sma200_str = f"{latest_sma200:.4f}" if not np.isnan(latest_sma200) else "Lade Daten..."
            st.markdown(f"""
            <div class="metric-card" style="text-align: center;">
                <span style="font-size: 0.72rem; text-transform: uppercase; color: #7d7d8a; font-weight: 600;">SMA 200</span>
                <div style="font-size: 1.5rem; font-weight: 700; color: #8c8c9a; margin: 4px 0;">{sma200_str}</div>
                <div class="source-tag">Datenquelle: Alpha Vantage</div>
            </div>
            """, unsafe_allow_html=True)
            
        # Draw chart
        chart_df = df_fx.tail(100).copy() # last 100 days
        fig = px.line(chart_df, x="date", y=["close", "SMA_50", "SMA_200"],
                      color_discrete_map={"close": "#f0f0f5", "SMA_50": "#e66400", "SMA_200": "#8c8c9a"})
        fig.update_traces(line_width=1.8)
        fig.update_layout(
            xaxis_title="Datum",
            yaxis_title="Kurs",
            margin=dict(l=10, r=10, t=30, b=10),
            height=340,
            hovermode="x unified",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#7d7d8a", size=10),
            xaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.08)', zeroline=False),
            yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.08)', zeroline=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#f0f0f5", size=10))
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Keine Wechselkursdaten verfügbar.")

# --- COLUMN 3: NEWS & RESEARCH (NewsData.io with NewsAPI.org Fallback) ---
with col_news:
    st.header("📰 News & Research Hub")
    
    # Active News source info banner
    st.caption(f"**Aktive Quelle:** `{news_source}` (Dedupliziert: Ja)")
    
    # Deduplicate news articles
    clean_articles = deduplicate_news(news_articles)
    
    if clean_articles:
        for art in clean_articles[:8]: # show latest 8
            st.markdown(f"""
            <div class="news-card">
                <a class="news-title" href="{art['url']}" target="_blank">{art['title']}</a>
                <div class="news-meta">Quelle: <strong>{art['source']}</strong> | {art['publishedAt'][:16]}</div>
                <div class="news-desc">{art['description'][:150]}...</div>
                <div class="source-tag-gray">Datenquelle: {art['api_source']}</div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("Keine aktuellen Nachrichten gefunden.")

import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import plotly.express as px
import base64
import os

# Set up page config
st.set_page_config(
    page_title="Forex Fundamental Dashboard",
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
    
    /* Style tabs to look professional and flat */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        background-color: #0c0c0e;
        padding: 4px 8px;
        border-radius: 4px;
        border: 1px solid #1f2026;
        margin-bottom: 20px;
    }
    
    .stTabs [data-baseweb="tab"] {
        height: 36px;
        background-color: transparent;
        border-radius: 3px;
        color: #7d7d8a !important;
        font-size: 0.85rem;
        font-weight: 500;
        border: none !important;
        padding: 0 14px;
    }
    
    .stTabs [data-baseweb="tab"]:hover {
        color: #f0f0f5 !important;
        background-color: rgba(255, 255, 255, 0.03);
    }
    
    .stTabs [aria-selected="true"] {
        color: #e66400 !important; /* Elegant accent dark orange */
        background-color: rgba(230, 100, 0, 0.08) !important;
        border-bottom: 2px solid #e66400 !important;
    }
    
    /* Static TV cards */
    .region-card-clickable {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 16px;
        margin-bottom: 12px;
        font-family: 'Inter', sans-serif;
        cursor: default;
        transition: border-color 0.2s, background-color 0.2s;
    }
    .region-card-clickable:hover {
        border-color: #e66400;
        background-color: #111114;
    }
    
    /* Container Card */
    .region-card-tv {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 16px 20px;
        margin-bottom: 15px;
    }

    .region-title-base {
        border-left: 4px solid #e66400; /* Accent orange */
        padding-left: 10px;
        font-weight: 600;
        font-size: 1.1rem;
        margin: 0;
    }

    .region-title-quote {
        border-left: 4px solid #4a4b57; /* Muted gray for quote currency */
        padding-left: 10px;
        font-weight: 600;
        font-size: 1.1rem;
        margin: 0;
    }

    /* News Card style */
    .news-card {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 16px;
        margin-bottom: 20px;
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
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
        margin-bottom: 10px;
    }
    .news-desc {
        font-size: 0.82rem;
        color: #b2b2be;
        margin-bottom: 10px;
        line-height: 1.4;
        display: -webkit-box;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
</style>
""", unsafe_allow_html=True)

CURRENCIES = {
    'USD': {
        'name': 'US-Dollar',
        'flag': '🇺🇸',
        'rate': 'FEDFUNDS', 'rate_label': 'FEDFUNDS',
        'unemp': 'UNRATE',
        'cpi': 'CPIAUCSL',
        'gdp': 'GDPC1',
        'leading': 'NAPM', # type PMI
        'trade': 'BOPGSTB',
        'sentiment': 'UMCSENT',
        'av_country': 'united_states'
    },
    'EUR': {
        'name': 'Euro',
        'flag': '🇪🇺',
        'rate': 'IRSTCI01EZM156N', 'rate_label': 'Call Money Rate',
        'unemp': 'LRHUTTTTEZQ156S',
        'cpi': 'CP0000EZ18M086NEST',
        'gdp': 'CPMEURNSAB1GQEA19',
        'leading': 'EZIP', # type IP
        'trade': 'XTNTVA01DEM664N', # Germany as proxy
        'sentiment': 'CSCICP02EZM460S',
        'av_country': 'eurozone'
    },
    'GBP': {
        'name': 'Britisches Pfund',
        'flag': '🇬🇧',
        'rate': 'IRSTCI01GBA156N', 'rate_label': 'Call Money Rate',
        'unemp': 'LRUNTTTTGBQ156S',
        'cpi': 'CPIUKA',
        'gdp': 'GBRGDPNQDSMEI',
        'leading': 'GBRPROINDMISMEI', # type IP
        'trade': 'XTNTVA01GBQ667S',
        'sentiment': None, # AV fallback
        'av_country': 'united_kingdom'
    },
    'CHF': {
        'name': 'Schweizer Franken',
        'flag': '🇨🇭',
        'rate': 'IRSTCI01CHM156N', 'rate_label': 'Call Money Rate',
        'unemp': 'LRUN74TTCHQ156S',
        'cpi': 'CHECPIALLMINMEI',
        'gdp': 'CPMEURNSAB1GQCH',
        'leading': None, # AV fallback
        'trade': 'XTNTVA01CHQ667S',
        'sentiment': 'CSCICP02CHQ460S',
        'av_country': 'switzerland'
    },
    'CAD': {
        'name': 'Kanadischer Dollar',
        'flag': '🇨🇦',
        'rate': 'IRSTCB01CAA156N', 'rate_label': 'Central Bank Rate',
        'unemp': 'LRUNTTTTCAM156S',
        'cpi': 'CANCPIALLMINMEI',
        'gdp': 'CANGDPNQDSMEI',
        'leading': None, # AV fallback
        'trade': 'XTNTVA01CAA664S',
        'sentiment': 'CSCICP03CAM665S',
        'av_country': 'canada'
    },
    'AUD': {
        'name': 'Australischer Dollar',
        'flag': '🇦🇺',
        'rate': 'IRSTCI01AUA156N', 'rate_label': 'Call Money Rate',
        'unemp': 'LRUN64TTAUM156S',
        'cpi': 'AUSCPIALLQINMEI',
        'gdp': 'AUSGDPNQDSMEI',
        'leading': None, # AV fallback
        'trade': 'XTNTVA01AUA664N',
        'sentiment': 'CSESFT02AUM460S',
        'av_country': 'australia'
    },
    'NZD': {
        'name': 'Neuseeland-Dollar',
        'flag': '🇳🇿',
        'rate': 'IRSTCI01NZM156N', 'rate_label': 'Call Money Rate',
        'unemp': 'LRUNTTTTNZQ156S',
        'cpi': 'NZLCPIALLQINMEI',
        'gdp': 'NZLGDPNADSMEI',
        'leading': None, # AV fallback
        'trade': 'XTNTVA01NZA664N',
        'sentiment': None, # AV fallback
        'av_country': 'new_zealand'
    },
    'JPY': {
        'name': 'Japanischer Yen',
        'flag': '🇯🇵',
        'rate': 'IRSTCI01JPM156N', 'rate_label': 'Call Money Rate',
        'unemp': 'LRHUTTTTJPM156S',
        'cpi': 'JPNCPIALLMINMEI',
        'gdp': 'JPNNGDP',
        'leading': None, # AV fallback
        'trade': 'XTNTVA01JPM664S',
        'sentiment': 'CSCICP02JPQ460S',
        'av_country': 'japan'
    }
}

# ----------------- 1. MOCK DATA GENERATOR -----------------
MOCK_DEFAULTS = {
    # Rates
    'FEDFUNDS': {'start': 5.25, 'type': 'rate', 'noise': 0.05, 'freq': 'M', 'trend': -0.01},
    'IRSTCI01EZM156N': {'start': 3.75, 'type': 'rate', 'noise': 0.04, 'freq': 'M', 'trend': -0.01},
    'IRSTCI01GBA156N': {'start': 5.00, 'type': 'rate', 'noise': 0.04, 'freq': 'M', 'trend': -0.02},
    'IRSTCI01CHM156N': {'start': 1.25, 'type': 'rate', 'noise': 0.02, 'freq': 'M', 'trend': -0.005},
    'IRSTCB01CAA156N': {'start': 4.75, 'type': 'rate', 'noise': 0.05, 'freq': 'M', 'trend': -0.015},
    'IRSTCI01AUA156N': {'start': 4.35, 'type': 'rate', 'noise': 0.03, 'freq': 'M', 'trend': 0.0},
    'IRSTCI01NZM156N': {'start': 5.50, 'type': 'rate', 'noise': 0.05, 'freq': 'M', 'trend': -0.02},
    'IRSTCI01JPM156N': {'start': 0.25, 'type': 'rate', 'noise': 0.01, 'freq': 'M', 'trend': 0.005},
    
    # Unemployment
    'UNRATE': {'start': 4.0, 'type': 'unemp', 'noise': 0.1, 'freq': 'M', 'trend': 0.01},
    'LRHUTTTTEZQ156S': {'start': 6.5, 'type': 'unemp', 'noise': 0.08, 'freq': 'M', 'trend': -0.01},
    'LRUNTTTTGBQ156S': {'start': 4.3, 'type': 'unemp', 'noise': 0.1, 'freq': 'M', 'trend': 0.01},
    'LRUN74TTCHQ156S': {'start': 4.2, 'type': 'unemp', 'noise': 0.05, 'freq': 'M', 'trend': -0.005},
    'LRUNTTTTCAM156S': {'start': 6.2, 'type': 'unemp', 'noise': 0.12, 'freq': 'M', 'trend': 0.02},
    'LRUN64TTAUM156S': {'start': 4.0, 'type': 'unemp', 'noise': 0.08, 'freq': 'M', 'trend': 0.015},
    'LRUNTTTTNZQ156S': {'start': 4.6, 'type': 'unemp', 'noise': 0.15, 'freq': 'Q', 'trend': 0.02},
    'LRHUTTTTJPM156S': {'start': 2.5, 'type': 'unemp', 'noise': 0.05, 'freq': 'M', 'trend': -0.002},
    
    # CPI Indices
    'CPIAUCSL': {'start': 312.0, 'type': 'cpi', 'noise': 0.4, 'freq': 'M', 'trend': 0.6},
    'CP0000EZ18M086NEST': {'start': 124.0, 'type': 'cpi', 'noise': 0.2, 'freq': 'M', 'trend': 0.3},
    'CPIUKA': {'start': 131.0, 'type': 'cpi', 'noise': 0.25, 'freq': 'M', 'trend': 0.4},
    'CHECPIALLMINMEI': {'start': 105.0, 'type': 'cpi', 'noise': 0.1, 'freq': 'M', 'trend': 0.15},
    'CANCPIALLMINMEI': {'start': 158.0, 'type': 'cpi', 'noise': 0.3, 'freq': 'M', 'trend': 0.4},
    'AUSCPIALLQINMEI': {'start': 135.0, 'type': 'cpi_q', 'noise': 0.6, 'freq': 'Q', 'trend': 0.8},
    'NZLCPIALLQINMEI': {'start': 138.0, 'type': 'cpi_q', 'noise': 0.7, 'freq': 'Q', 'trend': 0.9},
    'JPNCPIALLMINMEI': {'start': 112.0, 'type': 'cpi', 'noise': 0.15, 'freq': 'M', 'trend': 0.2},
    
    # GDP
    'GDPC1': {'start': 22500.0, 'type': 'gdp', 'noise': 40.0, 'freq': 'Q', 'trend': 110.0},
    'CPMEURNSAB1GQEA19': {'start': 3100.0, 'type': 'gdp', 'noise': 8.0, 'freq': 'Q', 'trend': 12.0},
    'GBRGDPNQDSMEI': {'start': 680000.0, 'type': 'gdp', 'noise': 1200.0, 'freq': 'Q', 'trend': 2500.0},
    'CPMEURNSAB1GQCH': {'start': 195000.0, 'type': 'gdp', 'noise': 300.0, 'freq': 'Q', 'trend': 600.0},
    'CANGDPNQDSMEI': {'start': 2350000.0, 'type': 'gdp', 'noise': 4000.0, 'freq': 'Q', 'trend': 8000.0},
    'AUSGDPNQDSMEI': {'start': 610000.0, 'type': 'gdp', 'noise': 1200.0, 'freq': 'Q', 'trend': 2200.0},
    'NZLGDPNADSMEI': {'start': 72000.0, 'type': 'gdp', 'noise': 200.0, 'freq': 'Q', 'trend': 300.0},
    'JPNNGDP': {'start': 142000000.0, 'type': 'gdp', 'noise': 400000.0, 'freq': 'Q', 'trend': 800000.0},
    
    # Leading
    'NAPM': {'start': 49.5, 'type': 'pmi', 'noise': 0.9, 'freq': 'M', 'trend': 0.05},
    'EZIP': {'start': 101.0, 'type': 'ip', 'noise': 0.8, 'freq': 'M', 'trend': -0.05},
    'GBRPROINDMISMEI': {'start': 98.5, 'type': 'ip', 'noise': 0.7, 'freq': 'M', 'trend': -0.02},
    
    # Trade
    'BOPGSTB': {'start': -68000.0, 'type': 'trade', 'noise': 2000.0, 'freq': 'M', 'trend': -150.0},
    'XTNTVA01DEM664N': {'start': 19500.0, 'type': 'trade', 'noise': 1100.0, 'freq': 'M', 'trend': 80.0},
    'XTNTVA01GBQ667S': {'start': -14500.0, 'type': 'trade', 'noise': 900.0, 'freq': 'Q', 'trend': -200.0},
    'XTNTVA01CHQ667S': {'start': 3200.0, 'type': 'trade', 'noise': 180.0, 'freq': 'Q', 'trend': 15.0},
    'XTNTVA01CAA664S': {'start': 1100.0, 'type': 'trade', 'noise': 250.0, 'freq': 'M', 'trend': 8.0},
    'XTNTVA01AUA664N': {'start': 6500.0, 'type': 'trade', 'noise': 450.0, 'freq': 'M', 'trend': 50.0},
    'XTNTVA01NZA664N': {'start': -1800.0, 'type': 'trade', 'noise': 200.0, 'freq': 'Q', 'trend': -20.0},
    'XTNTVA01JPM664S': {'start': -4500.0, 'type': 'trade', 'noise': 350.0, 'freq': 'M', 'trend': -10.0},
    
    # Sentiment
    'UMCSENT': {'start': 72.0, 'type': 'sentiment', 'noise': 1.5, 'freq': 'M', 'trend': 0.08},
    'CSCICP02EZM460S': {'start': -14.0, 'type': 'sentiment', 'noise': 0.7, 'freq': 'M', 'trend': 0.05},
    'CSCICP02CHQ460S': {'start': -8.0, 'type': 'sentiment', 'noise': 0.5, 'freq': 'Q', 'trend': 0.02},
    'CSCICP03CAM665S': {'start': 82.0, 'type': 'sentiment', 'noise': 1.2, 'freq': 'M', 'trend': 0.05},
    'CSESFT02AUM460S': {'start': -10.0, 'type': 'sentiment', 'noise': 0.6, 'freq': 'M', 'trend': 0.04},
    'CSCICP02JPQ460S': {'start': 36.5, 'type': 'sentiment', 'noise': 0.8, 'freq': 'Q', 'trend': 0.06}
}

# ----------------- 0.5 ANALYST FORECAST DATABASE -----------------
FORECASTS = {
    'USD': {
        'rate': {
            'Q3 2026': {'Goldman Sachs': 3.75, 'J.P. Morgan': 3.75, 'Deutsche Bank': 3.75, 'Morgan Stanley': 3.50},
            'Q4 2026': {'Goldman Sachs': 3.50, 'J.P. Morgan': 3.75, 'Deutsche Bank': 3.75, 'Morgan Stanley': 3.50},
            'Q1 2027': {'Goldman Sachs': 3.25, 'J.P. Morgan': 3.50, 'Deutsche Bank': 3.50, 'Morgan Stanley': 3.25}
        },
        'cpi': {
            '2026 (YoY)': {'Goldman Sachs': 2.8, 'J.P. Morgan': 3.0, 'Deutsche Bank': 2.9, 'Morgan Stanley': 2.7},
            '2027 (YoY)': {'Goldman Sachs': 2.2, 'J.P. Morgan': 2.4, 'Deutsche Bank': 2.3, 'Morgan Stanley': 2.1}
        },
        'gdp': {
            '2026 (Real)': {'Goldman Sachs': 2.1, 'J.P. Morgan': 1.8, 'Deutsche Bank': 1.9, 'Morgan Stanley': 2.0},
            '2027 (Real)': {'Goldman Sachs': 1.9, 'J.P. Morgan': 1.6, 'Deutsche Bank': 1.7, 'Morgan Stanley': 1.8}
        }
    },
    'EUR': {
        'rate': {
            'Q3 2026': {'Goldman Sachs': 2.25, 'J.P. Morgan': 2.25, 'Deutsche Bank': 2.50, 'Morgan Stanley': 2.25},
            'Q4 2026': {'Goldman Sachs': 2.00, 'J.P. Morgan': 2.25, 'Deutsche Bank': 2.25, 'Morgan Stanley': 2.00},
            'Q1 2027': {'Goldman Sachs': 1.75, 'J.P. Morgan': 2.00, 'Deutsche Bank': 2.00, 'Morgan Stanley': 1.75}
        },
        'cpi': {
            '2026 (YoY)': {'Goldman Sachs': 2.1, 'J.P. Morgan': 2.3, 'Deutsche Bank': 2.2, 'Morgan Stanley': 2.0},
            '2027 (YoY)': {'Goldman Sachs': 1.9, 'J.P. Morgan': 2.0, 'Deutsche Bank': 1.9, 'Morgan Stanley': 1.8}
        },
        'gdp': {
            '2026 (Real)': {'Goldman Sachs': 0.8, 'J.P. Morgan': 0.7, 'Deutsche Bank': 0.6, 'Morgan Stanley': 0.8},
            '2027 (Real)': {'Goldman Sachs': 1.2, 'J.P. Morgan': 1.1, 'Deutsche Bank': 1.0, 'Morgan Stanley': 1.2}
        }
    },
    'GBP': {
        'rate': {
            'Q3 2026': {'Goldman Sachs': 3.75, 'J.P. Morgan': 3.75, 'Deutsche Bank': 3.75, 'Morgan Stanley': 3.50},
            'Q4 2026': {'Goldman Sachs': 3.50, 'J.P. Morgan': 3.50, 'Deutsche Bank': 3.50, 'Morgan Stanley': 3.25},
            'Q1 2027': {'Goldman Sachs': 3.25, 'J.P. Morgan': 3.25, 'Deutsche Bank': 3.25, 'Morgan Stanley': 3.00}
        },
        'cpi': {
            '2026 (YoY)': {'Goldman Sachs': 2.6, 'J.P. Morgan': 2.8, 'Deutsche Bank': 2.7, 'Morgan Stanley': 2.5},
            '2027 (YoY)': {'Goldman Sachs': 2.1, 'J.P. Morgan': 2.3, 'Deutsche Bank': 2.2, 'Morgan Stanley': 2.0}
        },
        'gdp': {
            '2026 (Real)': {'Goldman Sachs': 1.0, 'J.P. Morgan': 0.8, 'Deutsche Bank': 0.9, 'Morgan Stanley': 1.1},
            '2027 (Real)': {'Goldman Sachs': 1.4, 'J.P. Morgan': 1.2, 'Deutsche Bank': 1.3, 'Morgan Stanley': 1.4}
        }
    },
    'CHF': {
        'rate': {
            'Q3 2026': {'Goldman Sachs': 0.00, 'J.P. Morgan': 0.00, 'Deutsche Bank': 0.00, 'Morgan Stanley': 0.00},
            'Q4 2026': {'Goldman Sachs': 0.00, 'J.P. Morgan': 0.00, 'Deutsche Bank': 0.25, 'Morgan Stanley': 0.00},
            'Q1 2027': {'Goldman Sachs': 0.25, 'J.P. Morgan': 0.00, 'Deutsche Bank': 0.25, 'Morgan Stanley': 0.25}
        },
        'cpi': {
            '2026 (YoY)': {'Goldman Sachs': 1.2, 'J.P. Morgan': 1.4, 'Deutsche Bank': 1.3, 'Morgan Stanley': 1.1},
            '2027 (YoY)': {'Goldman Sachs': 1.0, 'J.P. Morgan': 1.1, 'Deutsche Bank': 1.0, 'Morgan Stanley': 0.9}
        },
        'gdp': {
            '2026 (Real)': {'Goldman Sachs': 1.3, 'J.P. Morgan': 1.1, 'Deutsche Bank': 1.2, 'Morgan Stanley': 1.4},
            '2027 (Real)': {'Goldman Sachs': 1.5, 'J.P. Morgan': 1.3, 'Deutsche Bank': 1.4, 'Morgan Stanley': 1.5}
        }
    },
    'CAD': {
        'rate': {
            'Q3 2026': {'Goldman Sachs': 3.50, 'J.P. Morgan': 3.75, 'Deutsche Bank': 3.50, 'Morgan Stanley': 3.50},
            'Q4 2026': {'Goldman Sachs': 3.25, 'J.P. Morgan': 3.50, 'Deutsche Bank': 3.25, 'Morgan Stanley': 3.25},
            'Q1 2027': {'Goldman Sachs': 3.00, 'J.P. Morgan': 3.00, 'Deutsche Bank': 3.00, 'Morgan Stanley': 3.00}
        },
        'cpi': {
            '2026 (YoY)': {'Goldman Sachs': 2.4, 'J.P. Morgan': 2.6, 'Deutsche Bank': 2.5, 'Morgan Stanley': 2.3},
            '2027 (YoY)': {'Goldman Sachs': 2.0, 'J.P. Morgan': 2.1, 'Deutsche Bank': 2.0, 'Morgan Stanley': 1.9}
        },
        'gdp': {
            '2026 (Real)': {'Goldman Sachs': 1.5, 'J.P. Morgan': 1.3, 'Deutsche Bank': 1.4, 'Morgan Stanley': 1.6},
            '2027 (Real)': {'Goldman Sachs': 1.8, 'J.P. Morgan': 1.6, 'Deutsche Bank': 1.7, 'Morgan Stanley': 1.9}
        }
    },
    'AUD': {
        'rate': {
            'Q3 2026': {'Goldman Sachs': 3.85, 'J.P. Morgan': 4.10, 'Deutsche Bank': 3.85, 'Morgan Stanley': 3.85},
            'Q4 2026': {'Goldman Sachs': 3.60, 'J.P. Morgan': 3.85, 'Deutsche Bank': 3.60, 'Morgan Stanley': 3.60},
            'Q1 2027': {'Goldman Sachs': 3.35, 'J.P. Morgan': 3.60, 'Deutsche Bank': 3.35, 'Morgan Stanley': 3.35}
        },
        'cpi': {
            '2026 (YoY)': {'Goldman Sachs': 3.1, 'J.P. Morgan': 3.3, 'Deutsche Bank': 3.2, 'Morgan Stanley': 3.0},
            '2027 (YoY)': {'Goldman Sachs': 2.5, 'J.P. Morgan': 2.7, 'Deutsche Bank': 2.6, 'Morgan Stanley': 2.4}
        },
        'gdp': {
            '2026 (Real)': {'Goldman Sachs': 1.6, 'J.P. Morgan': 1.4, 'Deutsche Bank': 1.5, 'Morgan Stanley': 1.7},
            '2027 (Real)': {'Goldman Sachs': 2.1, 'J.P. Morgan': 1.9, 'Deutsche Bank': 2.0, 'Morgan Stanley': 2.2}
        }
    },
    'NZD': {
        'rate': {
            'Q3 2026': {'Goldman Sachs': 4.50, 'J.P. Morgan': 4.75, 'Deutsche Bank': 4.50, 'Morgan Stanley': 4.25},
            'Q4 2026': {'Goldman Sachs': 4.25, 'J.P. Morgan': 4.25, 'Deutsche Bank': 4.25, 'Morgan Stanley': 4.00},
            'Q1 2027': {'Goldman Sachs': 3.75, 'J.P. Morgan': 3.75, 'Deutsche Bank': 4.00, 'Morgan Stanley': 3.75}
        },
        'cpi': {
            '2026 (YoY)': {'Goldman Sachs': 2.8, 'J.P. Morgan': 3.0, 'Deutsche Bank': 2.9, 'Morgan Stanley': 2.7},
            '2027 (YoY)': {'Goldman Sachs': 2.2, 'J.P. Morgan': 2.3, 'Deutsche Bank': 2.2, 'Morgan Stanley': 2.1}
        },
        'gdp': {
            '2026 (Real)': {'Goldman Sachs': 1.2, 'J.P. Morgan': 1.0, 'Deutsche Bank': 1.1, 'Morgan Stanley': 1.3},
            '2027 (Real)': {'Goldman Sachs': 1.7, 'J.P. Morgan': 1.5, 'Deutsche Bank': 1.6, 'Morgan Stanley': 1.8}
        }
    },
    'JPY': {
        'rate': {
            'Q3 2026': {'Goldman Sachs': 0.25, 'J.P. Morgan': 0.25, 'Deutsche Bank': 0.25, 'Morgan Stanley': 0.25},
            'Q4 2026': {'Goldman Sachs': 0.25, 'J.P. Morgan': 0.50, 'Deutsche Bank': 0.25, 'Morgan Stanley': 0.25},
            'Q1 2027': {'Goldman Sachs': 0.50, 'J.P. Morgan': 0.50, 'Deutsche Bank': 0.50, 'Morgan Stanley': 0.50}
        },
        'cpi': {
            '2026 (YoY)': {'Goldman Sachs': 2.3, 'J.P. Morgan': 2.5, 'Deutsche Bank': 2.4, 'Morgan Stanley': 2.2},
            '2027 (YoY)': {'Goldman Sachs': 1.8, 'J.P. Morgan': 2.0, 'Deutsche Bank': 1.9, 'Morgan Stanley': 1.7}
        },
        'gdp': {
            '2026 (Real)': {'Goldman Sachs': 0.9, 'J.P. Morgan': 0.7, 'Deutsche Bank': 0.8, 'Morgan Stanley': 0.9},
            '2027 (Real)': {'Goldman Sachs': 1.1, 'J.P. Morgan': 1.0, 'Deutsche Bank': 1.0, 'Morgan Stanley': 1.1}
        }
    }
}

# ----------------- 1. MOCK DATA GENERATOR -----------------
def deterministic_seed(string_val):
    # Simple deterministic hash for stable mock paths
    h = 0
    for char in string_val:
        h = (31 * h + ord(char)) & 0xFFFFFFFF
    return h % 1000000

def generate_mock_series(series_id):
    np.random.seed(deterministic_seed(series_id))
    end_date = datetime(2026, 6, 1)
    start_date = datetime(2015, 1, 1)
    
    if series_id not in MOCK_DEFAULTS:
        if series_id.startswith("AV_"):
            parts = series_id.split("_")
            func = parts[1]
            if func == "PMI":
                MOCK_DEFAULTS[series_id] = {'start': 51.2, 'type': 'pmi', 'noise': 0.8, 'freq': 'M', 'trend': -0.02}
            elif func == "CPI":
                MOCK_DEFAULTS[series_id] = {'start': 118.0, 'type': 'cpi', 'noise': 0.15, 'freq': 'M', 'trend': 0.25}
            elif func == "CONSUMER": # AV_CONSUMER_SENTIMENT_country
                MOCK_DEFAULTS[series_id] = {'start': 82.0, 'type': 'sentiment', 'noise': 1.1, 'freq': 'M', 'trend': 0.05}
            else:
                MOCK_DEFAULTS[series_id] = {'start': 50.0, 'type': 'generic', 'noise': 1.0, 'freq': 'M', 'trend': 0.0}
        else:
            dates = pd.date_range(start=start_date, end=end_date, freq='ME')
            return pd.DataFrame({"date": dates, "value": [1.0] * len(dates)})
            
    cfg = MOCK_DEFAULTS[series_id]
    
    if cfg.get('freq') == 'D':
        dates = pd.date_range(start=start_date, end=end_date, freq='D')
        values = []
        current = cfg['start']
        for d in dates:
            current += np.random.normal(cfg['trend'], cfg['noise'])
            # clamp rates
            if cfg['type'] == 'rate':
                if current < -0.75: current = -0.75
                elif current > 6.5: current = 6.5
            values.append(round(current, 3))
        return pd.DataFrame({"date": dates, "value": values})
        
    elif cfg.get('freq') == 'M':
        dates = pd.date_range(start=start_date, end=end_date, freq='ME')
        values = []
        current = cfg['start']
        for i, d in enumerate(dates):
            current += cfg['trend'] + np.random.normal(0, cfg['noise'])
            # clamp
            if cfg['type'] == 'rate':
                if current < -0.75: current = -0.75
                elif current > 6.5: current = 6.5
            elif cfg['type'] == 'unemp':
                if current < 1.5: current = 1.5
                elif current > 12.0: current = 12.0
            elif cfg['type'] == 'pmi':
                if current < 35.0: current = 35.0
                elif current > 65.0: current = 65.0
            values.append(round(current, 3))
        return pd.DataFrame({"date": dates, "value": values})
        
    elif cfg.get('freq') == 'Q' or cfg.get('type') == 'cpi_q':
        dates = pd.date_range(start=start_date, end=end_date, freq='QE')
        values = []
        current = cfg['start']
        for i, d in enumerate(dates):
            current += cfg['trend'] + np.random.normal(0, cfg['noise'])
            values.append(round(current, 1))
        return pd.DataFrame({"date": dates, "value": values})
        
    return None

# ----------------- 2. FRED API FETCHING -----------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_fred_data(series_id, api_key):
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": "2015-01-01"
    }
    try:
        response = requests.get(url, params=params, timeout=12)
        if response.status_code == 400:
            return None
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
        
        if not parsed:
            return None
            
        df = pd.DataFrame(parsed)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        return df
    except Exception:
        return None

def get_macro_data(series_id, api_key, use_demo):
    if use_demo or not api_key:
        return generate_mock_series(series_id)
    df = fetch_fred_data(series_id, api_key)
    if df is None:
        return generate_mock_series(series_id)
    return df

# ----------------- 2.1 ALPHA VANTAGE API FETCHING -----------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_alphavantage_data(function, country, api_key):
    if not api_key:
        return None
    url = "https://www.alphavantage.co/query"
    params = {
        "function": function,
        "country": country,
        "apikey": api_key
    }
    try:
        response = requests.get(url, params=params, timeout=12)
        if response.status_code != 200:
            return None
        data = response.json()
        
        # Check for error or rate limits
        if "Information" in data or "Error Message" in data:
            return None
            
        observations = data.get("data", [])
        if not observations:
            return None
            
        parsed = []
        for obs in observations:
            date_str = obs.get("date")
            val_str = obs.get("value")
            if date_str and val_str and val_str != ".":
                try:
                    parsed.append({
                        "date": date_str,
                        "value": float(val_str)
                    })
                except ValueError:
                    pass
        if not parsed:
            return None
            
        df = pd.DataFrame(parsed)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        return df
    except Exception:
        return None

def get_av_macro_data(function, country, api_key, use_demo):
    series_id = f"AV_{function}_{country}"
    if use_demo or not api_key:
        return generate_mock_series(series_id)
    df = fetch_alphavantage_data(function, country, api_key)
    if df is None:
        return generate_mock_series(series_id)
    return df

# ----------------- 2.2 QUANTITATIVE SCORING ENGINE -----------------
def get_indicator_change_details(df, indicator_type):
    if df is None or len(df) < 2:
        return {"latest_val": 0.0, "prev_val": 0.0, "change": 0.0, "change_pct": 0.0, "details": "Keine ausreichenden Daten"}
    
    df = df.sort_values('date').reset_index(drop=True)
    latest_val = df.iloc[-1]['value']
    latest_date = df.iloc[-1]['date']
    prev_val = df.iloc[-2]['value']
    prev_date = df.iloc[-2]['date']
    
    change = latest_val - prev_val
    change_pct = (change / prev_val * 100) if prev_val != 0 else 0.0
    
    if indicator_type == 'rate':
        details = f"Zins vs. Vorperiode ({prev_date.strftime('%b %Y')}: {prev_val:.2f}%)"
    elif indicator_type == 'unemp':
        details = f"Quote vs. Vorperiode ({prev_date.strftime('%b %Y')}: {prev_val:.2f}%)"
    elif indicator_type == 'cpi':
        details = f"Index vs. Vorperiode ({prev_date.strftime('%b %Y')}: {prev_val:.2f})"
    elif indicator_type == 'gdp':
        details = f"GDP vs. Vorperiode ({prev_date.strftime('%b %Y')}: {prev_val:,.1f})"
    elif indicator_type == 'pmi':
        details = f"PMI vs. Vorperiode ({prev_date.strftime('%b %Y')}: {prev_val:.1f})"
    elif indicator_type == 'ip':
        details = f"Index vs. Vorperiode ({prev_date.strftime('%b %Y')}: {prev_val:.1f})"
    elif indicator_type == 'trade':
        details = f"Handelsbilanz vs. Vorperiode ({prev_date.strftime('%b %Y')}: {prev_val:,.1f})"
    elif indicator_type == 'sentiment':
        details = f"Stimmung vs. Vorperiode ({prev_date.strftime('%b %Y')}: {prev_val:.1f})"
    else:
        details = f"Wert vs. Vorperiode ({prev_date.strftime('%b %Y')}: {prev_val:.2f})"
        
    return {
        "latest_val": latest_val,
        "prev_val": prev_val,
        "change": change,
        "change_pct": change_pct,
        "details": details
    }

def compute_currency_fundamental_suite(curr, fred_key, av_key, use_demo):
    # Fetch all dataframes
    df_rate = get_macro_data(CURRENCIES[curr]['rate'], fred_key, use_demo)
    latest_rate = df_rate.iloc[-1]['value'] if (df_rate is not None and not df_rate.empty) else 0.0
    rate_score = float(np.clip((latest_rate / 6.0) * 100, 0, 100))
    
    df_unemp = get_macro_data(CURRENCIES[curr]['unemp'], fred_key, use_demo)
    latest_unemp = df_unemp.iloc[-1]['value'] if (df_unemp is not None and not df_unemp.empty) else 5.0
    unemp_score = float(np.clip((10.0 - latest_unemp) / 8.0 * 100, 0, 100))
    
    df_cpi = get_macro_data(CURRENCIES[curr]['cpi'], fred_key, use_demo)
    latest_cpi_yoy = 2.0
    if df_cpi is not None and len(df_cpi) >= 13:
        avg_gap = (df_cpi['date'].diff().mean()).days if len(df_cpi) > 1 else 30
        periods = 4 if 80 <= avg_gap <= 100 else 12
        df_cpi['yoy'] = df_cpi['value'].pct_change(periods=periods) * 100
        latest_cpi_yoy = df_cpi.iloc[-1]['yoy']
        if np.isnan(latest_cpi_yoy):
            latest_cpi_yoy = 2.0
            
    df_av_cpi = None
    if curr == 'USD':
        df_av_cpi = get_av_macro_data('CPI', CURRENCIES[curr]['av_country'], av_key, use_demo)
    latest_av_cpi_yoy = None
    if df_av_cpi is not None and not df_av_cpi.empty:
        latest_av_val = df_av_cpi.iloc[-1]['value']
        if latest_av_val > 30.0:
            avg_gap_av = (df_av_cpi['date'].diff().mean()).days if len(df_av_cpi) > 1 else 30
            periods_av = 4 if 80 <= avg_gap_av <= 100 else 12
            df_av_cpi['yoy'] = df_av_cpi['value'].pct_change(periods=periods_av) * 100
            latest_av_cpi_yoy = df_av_cpi.iloc[-1]['yoy']
        else:
            latest_av_cpi_yoy = latest_av_val
            
    if latest_av_cpi_yoy is not None and not np.isnan(latest_av_cpi_yoy):
        inflation_val = (latest_cpi_yoy + latest_av_cpi_yoy) / 2.0
    else:
        inflation_val = latest_cpi_yoy
    cpi_score = float(np.clip((inflation_val / 5.0) * 100, 0, 100))
    
    df_gdp = get_macro_data(CURRENCIES[curr]['gdp'], fred_key, use_demo)
    latest_gdp_yoy = 1.5
    if df_gdp is not None and len(df_gdp) >= 5:
        df_gdp['yoy'] = df_gdp['value'].pct_change(periods=4) * 100
        latest_gdp_yoy = df_gdp.iloc[-1]['yoy']
        if np.isnan(latest_gdp_yoy):
            latest_gdp_yoy = 1.5
    gdp_score = float(np.clip((latest_gdp_yoy + 2.0) / 6.0 * 100, 0, 100))
    
    is_pmi = True
    leading_val = 50.0
    if CURRENCIES[curr]['leading'] is not None:
        df_leading = get_macro_data(CURRENCIES[curr]['leading'], fred_key, use_demo)
        if df_leading is not None and not df_leading.empty:
            if CURRENCIES[curr]['leading'] == 'NAPM':
                leading_val = df_leading.iloc[-1]['value']
                is_pmi = True
            else:
                if len(df_leading) >= 13:
                    df_leading['yoy'] = df_leading['value'].pct_change(periods=12) * 100
                    leading_val = df_leading.iloc[-1]['yoy']
                else:
                    leading_val = 1.0
                is_pmi = False
    else:
        df_leading = get_av_macro_data('PMI', CURRENCIES[curr]['av_country'], av_key, use_demo)
        if df_leading is not None and not df_leading.empty:
            leading_val = df_leading.iloc[-1]['value']
            is_pmi = True
            
    if is_pmi:
        leading_score = float(np.clip((leading_val - 40.0) / 20.0 * 100, 0, 100))
    else:
        leading_score = float(np.clip((leading_val + 5.0) / 10.0 * 100, 0, 100))
        
    df_trade = get_macro_data(CURRENCIES[curr]['trade'], fred_key, use_demo)
    latest_trade = 0.0
    trade_score = 50.0
    if df_trade is not None and not df_trade.empty:
        latest_trade = df_trade.iloc[-1]['value']
        min_trade = df_trade['value'].min()
        max_trade = df_trade['value'].max()
        if max_trade != min_trade:
            trade_score = float(np.clip((latest_trade - min_trade) / (max_trade - min_trade) * 100, 0, 100))
            
    latest_sentiment = 50.0
    sentiment_score = 50.0
    if CURRENCIES[curr]['sentiment'] is not None:
        df_sentiment = get_macro_data(CURRENCIES[curr]['sentiment'], fred_key, use_demo)
        if df_sentiment is not None and not df_sentiment.empty:
            latest_sentiment = df_sentiment.iloc[-1]['value']
            min_sent = df_sentiment['value'].min()
            max_sent = df_sentiment['value'].max()
            if max_sent != min_sent:
                sentiment_score = float(np.clip((latest_sentiment - min_sent) / (max_sent - min_sent) * 100, 0, 100))
    else:
        df_sentiment = get_av_macro_data('CONSUMER_SENTIMENT', CURRENCIES[curr]['av_country'], av_key, use_demo)
        if df_sentiment is not None and not df_sentiment.empty:
            latest_sentiment = df_sentiment.iloc[-1]['value']
            min_sent = df_sentiment['value'].min()
            max_sent = df_sentiment['value'].max()
            if max_sent != min_sent:
                sentiment_score = float(np.clip((latest_sentiment - min_sent) / (max_sent - min_sent) * 100, 0, 100))
                
    total_score = 0.30 * rate_score + 0.20 * unemp_score + 0.15 * cpi_score + 0.10 * gdp_score + 0.10 * leading_score + 0.10 * trade_score + 0.05 * sentiment_score
    
    return {
        "rate_val": latest_rate, "rate_score": rate_score, "df_rate": df_rate,
        "unemp_val": latest_unemp, "unemp_score": unemp_score, "df_unemp": df_unemp,
        "cpi_val": inflation_val, "cpi_score": cpi_score, "df_cpi": df_cpi, "df_av_cpi": df_av_cpi,
        "gdp_val": latest_gdp_yoy, "gdp_score": gdp_score, "df_gdp": df_gdp,
        "leading_val": leading_val, "leading_score": leading_score, "df_leading": df_leading, "is_pmi": is_pmi,
        "trade_val": latest_trade, "trade_score": trade_score, "df_trade": df_trade,
        "sentiment_val": latest_sentiment, "sentiment_score": sentiment_score, "df_sentiment": df_sentiment,
        "total_score": round(total_score, 1)
    }

# ----------------- 2.5 NEWS FETCHING ENGINE -----------------
@st.cache_data(ttl=900, show_spinner=False)
def fetch_news(query, newsapi_key, newsdata_key):
    articles = []
    
    # 1. Fetch from NewsAPI (using German & English queries)
    if newsapi_key:
        try:
            url = "https://newsapi.org/v2/everything"
            # Try German news
            params_de = {
                "q": query,
                "apiKey": newsapi_key,
                "sortBy": "publishedAt",
                "language": "de",
                "pageSize": 25
            }
            r = requests.get(url, params=params_de, timeout=8)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    for art in data.get("articles", []):
                        if art.get("title") and art.get("title") != "[Removed]":
                            articles.append({
                                "title": art.get("title"),
                                "description": art.get("description"),
                                "url": art.get("url"),
                                "source": art.get("source", {}).get("name", "NewsAPI"),
                                "publishedAt": art.get("publishedAt"),
                                "urlToImage": art.get("urlToImage"),
                                "api": "NewsAPI (DE)"
                            })
            # Try English news to ensure we have a robust list
            params_en = params_de.copy()
            params_en["language"] = "en"
            params_en["pageSize"] = 25
            r = requests.get(url, params=params_en, timeout=8)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    for art in data.get("articles", []):
                        if art.get("title") and art.get("title") != "[Removed]":
                            articles.append({
                                "title": art.get("title"),
                                "description": art.get("description"),
                                "url": art.get("url"),
                                "source": art.get("source", {}).get("name", "NewsAPI"),
                                "publishedAt": art.get("publishedAt"),
                                "urlToImage": art.get("urlToImage"),
                                "api": "NewsAPI (EN)"
                            })
        except Exception:
            pass

    # 2. Fetch from NewsData.io (if key is available) to expand the news pool
    if newsdata_key:
        try:
            url = "https://newsdata.io/api/1/latest"
            params = {
                "apikey": newsdata_key,
                "q": query,
                "language": "en,de",
                "size": 10
            }
            r = requests.get(url, params=params, timeout=8)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "success":
                    for art in data.get("results", []):
                        if art.get("title"):
                            articles.append({
                                "title": art.get("title"),
                                "description": art.get("description"),
                                "url": art.get("link"),
                                "source": art.get("source_id", "NewsData"),
                                "publishedAt": art.get("pubDate"),
                                "urlToImage": art.get("image_url"),
                                "api": "NewsData.io"
                            })
        except Exception:
            pass

    return articles

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
        
        # Clean title: remove common publisher suffixes to catch cross-publisher duplicates
        for suffix in [" - reuters", " - bloomberg", " - cnbc", " - marketwatch", " | reuters", " | bloomberg", " | cnbc"]:
            if title.endswith(suffix):
                title = title[:-len(suffix)].strip()
                
        # Remove all non-alphanumeric characters for strict title normalization
        title_clean = "".join(c for c in title if c.isalnum())
        # Compare first 35 alphanumeric characters to catch near-duplicates with slightly different wording
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
    
    # 1. Trade/Import/Export
    trade_keywords = ["export", "import", "trade", "handel", "zoll", "tariffs", "lieferkette", "supply chain", "bilanz", "freihandel"]
    if any(kw in title_desc for kw in trade_keywords):
        return "🚢 Import & Export"
        
    # 2. Monetary Policy & Rates
    rates_keywords = ["fed", "fomc", "leitzins", "zins", "interest", "ecb", "ezb", "rate", "central bank", "zentralbank", "powell", "lagarde", "geldpolitik"]
    if any(kw in title_desc for kw in rates_keywords):
        return "🏦 Geldpolitik & Zinsen"
        
    # 3. Countries
    country_keywords = ["usa", "us-dollar", "america", "eurozone", "deutsch", "germany", "schweiz", "swiss", "kanada", "canada", "australi", "neuseeland", "new zealand", "japan", "england", "britain", "uk ", "gbp"]
    if any(kw in title_desc for kw in country_keywords):
        return "🌍 Länder-Analysen"
        
    # 4. Growth & General Macro (Fallback)
    return "📊 Sonstige Makro-News"

# ----------------- 3. SCORING ENGINE -----------------
def compute_indicator_details(df, series_type):
    if df is None or len(df) < 2:
        return {
            "latest_val": 0.0, "latest_date": datetime.now(),
            "prev_val": 0.0, "prev_date": datetime.now(),
            "change": 0.0, "change_pct": 0.0,
            "score": 0, "label": "N/A", "details": "Keine ausreichenden Daten vorhanden"
        }
    
    df = df.sort_values('date').reset_index(drop=True)
    
    latest_row = df.iloc[-1]
    latest_val = latest_row['value']
    latest_date = latest_row['date']
    
    if series_type == 'rate_daily':
        target_date = latest_date - timedelta(days=30)
        past_df = df[df['date'] <= target_date]
        if not past_df.empty:
            prev_row = past_df.iloc[-1]
        else:
            prev_row = df.iloc[0]
            
        prev_val = prev_row['value']
        prev_date = prev_row['date']
        
        change = latest_val - prev_val
        change_pct = (change / prev_val * 100) if prev_val != 0 else 0
        score = 1 if change > 0.001 else (-1 if change < -0.001 else 0)
        details = f"Zins vs. Vormonat ({prev_date.strftime('%d.%m.%Y')}: {prev_val:.3f}%)"
        label = f"{latest_val:.3f}%"
        
    elif series_type == 'rate_monthly':
        prev_row = df.iloc[-2]
        prev_val = prev_row['value']
        prev_date = prev_row['date']
        
        change = latest_val - prev_val
        change_pct = (change / prev_val * 100) if prev_val != 0 else 0
        score = 1 if change > 0.001 else (-1 if change < -0.001 else 0)
        details = f"Zins vs. Vormonat ({prev_date.strftime('%B %Y')}: {prev_val:.2f}%)"
        label = f"{latest_val:.2f}%"
        
    elif series_type == 'cpi':
        avg_gap = (df['date'].diff().mean()).days if len(df) > 1 else 30
        is_quarterly = 80 <= avg_gap <= 100
        periods = 4 if is_quarterly else 12
        min_len = periods + 2
        
        if len(df) >= min_len:
            df['yoy'] = df['value'].pct_change(periods=periods) * 100
            latest_yoy = df.iloc[-1]['yoy']
            prev_yoy = df.iloc[-2]['yoy']
            
            latest_val = latest_yoy
            prev_val = prev_yoy
            prev_date = df.iloc[-2]['date']
            
            change = latest_val - prev_val
            change_pct = change
            
            score = 1 if change > 0.01 else (-1 if change < -0.01 else 0)
            freq_label = "QoQ-YoY" if is_quarterly else "YoY"
            details = f"Inflation vs. Vorperiode ({prev_date.strftime('%d.%m.%Y') if is_quarterly else prev_date.strftime('%b %Y')}: {prev_val:.2f}%)"
            label = f"{latest_val:.2f}% ({freq_label})"
        else:
            prev_row = df.iloc[-2]
            prev_val = prev_row['value']
            prev_date = prev_row['date']
            change = latest_val - prev_val
            change_pct = (change / prev_val * 100) if prev_val != 0 else 0
            score = 1 if change > 0 else (-1 if change < 0 else 0)
            details = f"Index vs. Vormonat ({prev_date.strftime('%b %Y')}: {prev_val:.2f})"
            label = f"{latest_val:.2f} Pkt."
            
    elif series_type == 'gdp':
        prev_row = df.iloc[-2]
        prev_val = prev_row['value']
        prev_date = prev_row['date']
        
        change = latest_val - prev_val
        change_pct = (change / prev_val * 100) if prev_val != 0 else 0
        score = 1 if change > 0 else (-1 if change < 0 else 0)
        q_label_prev = f"Q{prev_date.quarter} {prev_date.year}"
        details = f"BIP vs. Vorquartal ({q_label_prev}: {prev_val:,.1f})"
        label = f"{change_pct:+.2f}% QoQ ({latest_val:,.1f})"
        
    return {
        "latest_val": latest_val,
        "latest_date": latest_date,
        "prev_val": prev_val,
        "prev_date": prev_date,
        "change": change,
        "change_pct": change_pct,
        "score": score,
        "label": label,
        "details": details
    }

def render_indicator_card_tv(title, val_str, score, change, change_pct, details):
    # Trend description
    if change > 0.0001:
        trend_html = f'<span style="color: #e66400; font-weight: 500;">↗ Steigend ({change:+.2f})</span>'
    elif change < -0.0001:
        trend_html = f'<span style="color: #8c8c9a; font-weight: 500;">↘ Fallend ({change:.2f})</span>'
    else:
        trend_html = '<span style="color: #5c5c66; font-weight: 500;">→ Unverändert</span>'
        
    # Score color mapping based on 0-100 scale:
    # High score (>= 60) gets orange accent, low score (<= 40) gets gray, middle gets off-white
    if score >= 60.0:
        score_color = "#e66400"
        score_bg = "rgba(230, 100, 0, 0.08)"
    elif score <= 40.0:
        score_color = "#8c8c9a"
        score_bg = "rgba(255, 255, 255, 0.03)"
    else:
        score_color = "#b2b2be"
        score_bg = "rgba(255, 255, 255, 0.05)"

    card_html = f"""
    <div class="region-card-clickable" style="
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 16px;
        margin-bottom: 12px;
        font-family: 'Inter', sans-serif;
        cursor: default;
        transition: all 0.2s ease-in-out;
    ">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.8px; color: #7d7d8a; font-weight: 600;">{title}</span>
            <span style="
                font-size: 0.72rem;
                font-weight: 700;
                color: {score_color};
                background-color: {score_bg};
                padding: 1px 6px;
                border-radius: 3px;
                border: 1px solid rgba({score_color}, 0.1);
            ">Punkte: {score:.0f}/100</span>
        </div>
        <div style="font-size: 1.6rem; font-weight: 700; color: #f0f0f5; margin: 8px 0 4px 0; font-family: 'Roboto Mono', monospace;">
            {val_str}
        </div>
        <div style="font-size: 0.75rem; margin-bottom: 6px;">
            {trend_html}
        </div>
        <div style="font-size: 0.68rem; color: #5c5c66; border-top: 1px solid #1f2026; padding-top: 6px; margin-top: 6px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap;" title="{details}">
            {details}
        </div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

def render_bias_box(divergence, base_curr, quote_curr, base_total_score, quote_total_score, sig, override_reason=None):
    """Renders the Divergence Trading Bias banner with dynamic G8 quantitative signaling."""
    if sig == "SB":
        bg_color = "rgba(16, 185, 129, 0.08)" # Emerald Green
        border_color = "#10b981"
        text_color = "#10b981"
        title = f"STARKER BUY-BIAS (STRONG BUY für {base_curr}/{quote_curr})"
        desc = f"Die makroökonomische Divergenz spricht deutlich für den {base_curr} (Divergenz: {divergence:+.1f} Punkte). Suche primär nach bullishen Einstiegen (SMC / FVG) im Chart."
        badge = "STRONG BUY"
    elif sig == "MB":
        bg_color = "rgba(226, 177, 60, 0.05)" # Premium Golden Yellow
        border_color = "#e2b13c"
        text_color = "#e2b13c"
        title = f"MITTLERER BUY-BIAS (MID BUY für {base_curr}/{quote_curr})"
        desc = f"Milder fundamentaler Vorteil für {base_curr} (Divergenz: {divergence:+.1f} Punkte). Nutze charttechnische Bestätigung vor Einstiegen."
        badge = "MID BUY"
    elif sig == "NT":
        bg_color = "rgba(132, 142, 156, 0.05)" # Gray
        border_color = "#444c56"
        text_color = "#8b949e"
        title = f"NEUTRAL / NO TRADE ({base_curr}/{quote_curr})"
        desc = f"Keine signifikante fundamentale Divergenz zwischen {base_curr} und {quote_curr} (Divergenz: {divergence:+.1f} Punkte). Seitwärtsbewegung wahrscheinlich. Neutraler Bias."
        badge = "NEUTRAL"
    elif sig == "MS":
        bg_color = "rgba(226, 177, 60, 0.05)" # Premium Golden Yellow
        border_color = "#e2b13c"
        text_color = "#e2b13c"
        title = f"MITTLERER SELL-BIAS (MID SELL für {base_curr}/{quote_curr})"
        desc = f"Milder fundamentaler Vorteil für {quote_curr} (Divergenz: {divergence:+.1f} Punkte). Suche nach charttechnischen Bestätigungen für Short-Setups."
        badge = "MID SELL"
    elif sig == "SS":
        bg_color = "rgba(16, 185, 129, 0.08)" # Emerald Green
        border_color = "#10b981"
        text_color = "#10b981"
        title = f"STARKER SELL-BIAS (STRONG SELL für {base_curr}/{quote_curr})"
        desc = f"Die makroökonomische Divergenz spricht deutlich für den {quote_curr} (Divergenz: {divergence:+.1f} Punkte). Suche primär nach bearishen Einstiegen im Chart."
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
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
    ">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
            <span style="
                font-size: 0.75rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 1.2px;
                color: #8b949e;
            ">{base_curr}/{quote_curr} Fundamentale Divergenz: {divergence:+.1f}</span>
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

def create_plotly_chart(df, title, y_label, color):
    if df is None or df.empty:
        return None
    
    fig = px.line(df, x="date", y="value", markers=False)
    fig.update_traces(line_color=color, line_width=2)
    fig.update_layout(
        title=dict(text=title, font=dict(size=12, color="#f0f6fc")),
        xaxis_title="Datum",
        yaxis_title=y_label,
        margin=dict(l=20, r=20, t=40, b=20),
        height=240,
        hovermode="x unified",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color="#8b949e", size=10),
        xaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.08)', zeroline=False),
        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.08)', zeroline=False)
    )
    return fig

def create_combined_chart(df_base, df_quote, base_label, quote_label, title, y_label, slice_date='2023-01-01'):
    if df_base is None or df_quote is None:
        return None
        
    df_b = df_base.copy().rename(columns={"value": base_label})
    df_q = df_quote.copy().rename(columns={"value": quote_label})
    
    merged = pd.merge(df_b[['date', base_label]], df_q[['date', quote_label]], on="date", how="outer")
    merged = merged.sort_values("date").ffill().bfill()
    
    # Slice down to starting date to prevent stretching if base and quote starts differ too much
    if slice_date:
        merged = merged[merged['date'] >= slice_date]
    
    melted = pd.melt(merged, id_vars=['date'], value_vars=[base_label, quote_label], 
                     var_name='Währung', value_name='Wert')
    
    fig = px.line(melted, x="date", y="Wert", color="Währung", 
                  color_discrete_map={base_label: "#e66400", quote_label: "#7d7d8a"})
    fig.update_traces(line_width=2.2)
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color="#f0f0f5")),
        xaxis_title="Datum",
        yaxis_title=y_label,
        margin=dict(l=20, r=20, t=45, b=20),
        height=320,
        hovermode="x unified",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color="#7d7d8a", size=10),
        xaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.08)', zeroline=False),
        yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.08)', zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#f0f0f5", size=10))
    )
    return fig

# Title
st.title("📊 Forex Fundamental Suite")
st.markdown("Macro Divergence Engine & Quantitative Scoring System for G5 Pairs.")

# Sidebar Settings
st.sidebar.header("⚙️ Einstellungen")

# Selectors for Base and Quote Currency
st.sidebar.markdown("### 💱 Währungspaar wählen")
base_curr = st.sidebar.selectbox("Basiswährung (Base)", options=list(CURRENCIES.keys()), index=1) # Default EUR
quote_curr = st.sidebar.selectbox("Quote-Währung (Quote)", options=list(CURRENCIES.keys()), index=0) # Default USD

if base_curr == quote_curr:
    st.error("Bitte wähle zwei unterschiedliche Währungen aus (z. B. EUR/USD).")
    st.stop()

st.sidebar.markdown("---")
st.sidebar.markdown("""
### 🧮 Scoring System
- **Zinsen, Inflation & BIP**
- **Vergleich zur Vorperiode:**
  - 📈 Steigerung: `+1 Punkt`
  - 📉 Senkung / Schrumpfung: `-1 Punkt`
  - ➡️ Keine Änderung: `0 Punkte`
- **Divergenz-Score (Base minus Quote):**
  - Spanne von `-6` bis `+6`
""")

# Keys expander at the very bottom
st.sidebar.markdown("---")
with st.sidebar.expander("🔑 keys"):
    try:
        default_fred = st.secrets.get("FRED_API_KEY", "16a7c7fcd052b9da3b801f2691a37d3b")
        default_av = st.secrets.get("AV_API_KEY", "BATX15WEXQJY7SS5")
        default_newsapi = st.secrets.get("NEWSAPI_KEY", "498a4855604345789b4a6eb4757f6ce8")
        default_newsdata = st.secrets.get("NEWSDATA_KEY", "pub_de1743243cb64703ac59bf87ae1566b7")
    except Exception:
        default_fred = "16a7c7fcd052b9da3b801f2691a37d3b"
        default_av = "BATX15WEXQJY7SS5"
        default_newsapi = "498a4855604345789b4a6eb4757f6ce8"
        default_newsdata = "pub_de1743243cb64703ac59bf87ae1566b7"

    api_key = st.text_input(
        "FRED API-Key",
        type="password",
        value=default_fred,
        help="Erstelle einen kostenlosen Key unter https://fred.stlouisfed.org",
        placeholder="Eingabe für Live-Modus..."
    )
    av_key = st.text_input(
        "Alpha Vantage API-Key",
        type="password",
        value=default_av,
        help="Erstelle einen Key unter https://www.alphavantage.co",
        placeholder="Eingabe für Live-Modus..."
    )
    newsapi_key = st.text_input(
        "NewsAPI.org Key",
        type="password",
        value=default_newsapi,
        help="Erstelle einen Key auf newsapi.org"
    )
    newsdata_key = st.text_input(
        "NewsData.io Key",
        type="password",
        value=default_newsdata,
        help="Erstelle einen Key auf newsdata.io"
    )
    use_demo = st.checkbox(
        "Demo-Modus verwenden (Mock-Daten)",
        value=not bool(api_key),
        help="Deaktiviere dies, um Live-Daten abzufragen."
    )

if not api_key and not use_demo:
    st.sidebar.warning("Gib einen API-Key ein oder aktiviere den Demo-Modus.")

# Loading indicator
with st.spinner(f"Lade G8-Makrodaten für {base_curr}/{quote_curr}..."):
    base_fundamental = compute_currency_fundamental_suite(base_curr, api_key, av_key, use_demo)
    quote_fundamental = compute_currency_fundamental_suite(quote_curr, api_key, av_key, use_demo)

# Validate if we have any data
data_loaded = base_fundamental is not None and quote_fundamental is not None

if data_loaded:
    base_total_score = base_fundamental['total_score']
    quote_total_score = quote_fundamental['total_score']
    
    # Divergence Score
    divergence = base_total_score - quote_total_score
    
    # Calculate trading signal and apply rules
    if divergence >= 35.0:
        sig = "SB"
        badge = "STRONG BUY"
    elif 15.0 <= divergence < 35.0:
        sig = "MB"
        badge = "MID BUY"
    elif -15.0 < divergence < 15.0:
        sig = "NT"
        badge = "NEUTRAL"
    elif -35.0 < divergence <= -15.0:
        sig = "MS"
        badge = "MID SELL"
    else:
        sig = "SS"
        badge = "STRONG SELL"

    override_reason = None
    # Base currency filters
    if base_total_score > 60.0 and sig in ["MS", "SS"]:
        sig = "NT"
        badge = "NEUTRAL"
        override_reason = f"Wirtschaftsscore von {base_curr} ({base_total_score:.1f}/100) ist stark (> 60), daher wurde das negative Signal auf Neutral (NT) angehoben."
    elif base_total_score < 40.0 and sig in ["MB", "SB"]:
        sig = "NT"
        badge = "NEUTRAL"
        override_reason = f"Wirtschaftsscore von {base_curr} ({base_total_score:.1f}/100) ist schwach (< 40), daher wurde das positive Signal auf Neutral (NT) abgesenkt."
    # Quote currency filters
    elif quote_total_score > 60.0 and sig in ["MB", "SB"]:
        sig = "NT"
        badge = "NEUTRAL"
        override_reason = f"Wirtschaftsscore von {quote_curr} ({quote_total_score:.1f}/100) ist stark (> 60), daher wurde das positive Signal auf Neutral (NT) abgesenkt."
    elif quote_total_score < 40.0 and sig in ["MS", "SS"]:
        sig = "NT"
        badge = "NEUTRAL"
        override_reason = f"Wirtschaftsscore von {quote_curr} ({quote_total_score:.1f}/100) ist schwach (< 40), daher wurde das negative Signal auf Neutral (NT) angehoben."
    
    # Pre-calculate indicator change details
    base_rate_details = get_indicator_change_details(base_fundamental['df_rate'], 'rate')
    base_unemp_details = get_indicator_change_details(base_fundamental['df_unemp'], 'unemp')
    base_cpi_details = get_indicator_change_details(base_fundamental['df_cpi'], 'cpi')
    base_gdp_details = get_indicator_change_details(base_fundamental['df_gdp'], 'gdp')
    base_leading_details = get_indicator_change_details(base_fundamental['df_leading'], 'pmi' if base_fundamental['is_pmi'] else 'ip')
    base_trade_details = get_indicator_change_details(base_fundamental['df_trade'], 'trade')
    base_sent_details = get_indicator_change_details(base_fundamental['df_sentiment'], 'sentiment')

    quote_rate_details = get_indicator_change_details(quote_fundamental['df_rate'], 'rate')
    quote_unemp_details = get_indicator_change_details(quote_fundamental['df_unemp'], 'unemp')
    quote_cpi_details = get_indicator_change_details(quote_fundamental['df_cpi'], 'cpi')
    quote_gdp_details = get_indicator_change_details(quote_fundamental['df_gdp'], 'gdp')
    quote_leading_details = get_indicator_change_details(quote_fundamental['df_leading'], 'pmi' if quote_fundamental['is_pmi'] else 'ip')
    quote_trade_details = get_indicator_change_details(quote_fundamental['df_trade'], 'trade')
    quote_sent_details = get_indicator_change_details(quote_fundamental['df_sentiment'], 'sentiment')

    # Render Mode info badge in sidebar
    if use_demo:
        st.sidebar.success(f"📊 Aktiv: Demo-Modus ({base_curr}/{quote_curr})")
    else:
        st.sidebar.success(f"⚡ Aktiv: Live FRED & AV API ({base_curr}/{quote_curr})")
        
    st.sidebar.button("🔄 Daten aktualisieren", on_click=st.cache_data.clear)
    st.sidebar.info("💡 Die Daten werden automatisch alle 5 Minuten im Hintergrund aktualisiert.")
    
    # ----------------- TABS SYSTEM (REITER) -----------------
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "🏠 Dashboard Overview", 
        "🏦 Zinspolitik (Rates)", 
        "🎯 Inflation (CPI)", 
        "📊 BIP & Wachstum (GDP)",
        "🧠 SMC Trading-Strategie",
        "📰 News & Research",
        "📜 Historische Daten",
        "🔮 Analysten-Prognosen"
    ])
    
    # TAB 1: DASHBOARD OVERVIEW
    with tab1:
        # Bias alert box
        render_bias_box(divergence, base_curr, quote_curr, base_total_score, quote_total_score, sig, override_reason)
        
        # Base vs Quote columns
        col1, col2 = st.columns(2)
        
        with col1:
            score_class = "score-positive" if base_total_score >= 60.0 else ("score-negative" if base_total_score <= 40.0 else "score-neutral")
            st.markdown(f"""
            <div class="region-card-tv">
                <h3 class="region-title-base">{CURRENCIES[base_curr]['flag']} {CURRENCIES[base_curr]['name']} ({base_curr})</h3>
                <div style="font-size: 0.85rem; color: #8b949e; margin-top: 5px;">
                    Gesamtstärke: <span class="score-badge {score_class}" style="margin: 0 5px;">{base_total_score:.1f} / 100</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            render_indicator_card_tv(f"Zinssatz ({CURRENCIES[base_curr]['rate_label']})", f"{base_fundamental['rate_val']:.2f}%", base_fundamental['rate_score'], base_rate_details['change'], base_rate_details['change_pct'], base_rate_details['details'])
            render_indicator_card_tv("Arbeitslosenquote", f"{base_fundamental['unemp_val']:.2f}%", base_fundamental['unemp_score'], base_unemp_details['change'], base_unemp_details['change_pct'], base_unemp_details['details'])
            render_indicator_card_tv("Inflation (CPI YoY)", f"{base_fundamental['cpi_val']:.2f}%", base_fundamental['cpi_score'], base_cpi_details['change'], base_cpi_details['change_pct'], base_cpi_details['details'])
            render_indicator_card_tv("BIP Wachstum (GDP YoY)", f"{base_fundamental['gdp_val']:+.2f}%", base_fundamental['gdp_score'], base_gdp_details['change'], base_gdp_details['change_pct'], base_gdp_details['details'])
            
            leading_title = "Vorlaufindikator (PMI)" if base_fundamental['is_pmi'] else "Industrieproduktion (IP YoY)"
            leading_val_str = f"{base_fundamental['leading_val']:.1f} Pkt." if base_fundamental['is_pmi'] else f"{base_fundamental['leading_val']:+.2f}%"
            render_indicator_card_tv(leading_title, leading_val_str, base_fundamental['leading_score'], base_leading_details['change'], base_leading_details['change_pct'], base_leading_details['details'])
            
            render_indicator_card_tv("Handelsbilanz (Trade)", f"{base_fundamental['trade_val']:,.1f} Mio.", base_fundamental['trade_score'], base_trade_details['change'], base_trade_details['change_pct'], base_trade_details['details'])
            render_indicator_card_tv("Verbraucherstimmung", f"{base_fundamental['sentiment_val']:.1f} Pkt.", base_fundamental['sentiment_score'], base_sent_details['change'], base_sent_details['change_pct'], base_sent_details['details'])
            
        with col2:
            score_class = "score-positive" if quote_total_score >= 60.0 else ("score-negative" if quote_total_score <= 40.0 else "score-neutral")
            st.markdown(f"""
            <div class="region-card-tv">
                <h3 class="region-title-quote">{CURRENCIES[quote_curr]['flag']} {CURRENCIES[quote_curr]['name']} ({quote_curr})</h3>
                <div style="font-size: 0.85rem; color: #8b949e; margin-top: 5px;">
                    Gesamtstärke: <span class="score-badge {score_class}" style="margin: 0 5px;">{quote_total_score:.1f} / 100</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            render_indicator_card_tv(f"Zinssatz ({CURRENCIES[quote_curr]['rate_label']})", f"{quote_fundamental['rate_val']:.2f}%", quote_fundamental['rate_score'], quote_rate_details['change'], quote_rate_details['change_pct'], quote_rate_details['details'])
            render_indicator_card_tv("Arbeitslosenquote", f"{quote_fundamental['unemp_val']:.2f}%", quote_fundamental['unemp_score'], quote_unemp_details['change'], quote_unemp_details['change_pct'], quote_unemp_details['details'])
            render_indicator_card_tv("Inflation (CPI YoY)", f"{quote_fundamental['cpi_val']:.2f}%", quote_fundamental['cpi_score'], quote_cpi_details['change'], quote_cpi_details['change_pct'], quote_cpi_details['details'])
            render_indicator_card_tv("BIP Wachstum (GDP YoY)", f"{quote_fundamental['gdp_val']:+.2f}%", quote_fundamental['gdp_score'], quote_gdp_details['change'], quote_gdp_details['change_pct'], quote_gdp_details['details'])
            
            leading_title = "Vorlaufindikator (PMI)" if quote_fundamental['is_pmi'] else "Industrieproduktion (IP YoY)"
            leading_val_str = f"{quote_fundamental['leading_val']:.1f} Pkt." if quote_fundamental['is_pmi'] else f"{quote_fundamental['leading_val']:+.2f}%"
            render_indicator_card_tv(leading_title, leading_val_str, quote_fundamental['leading_score'], quote_leading_details['change'], quote_leading_details['change_pct'], quote_leading_details['details'])
            
            render_indicator_card_tv("Handelsbilanz (Trade)", f"{quote_fundamental['trade_val']:,.1f} Mio.", quote_fundamental['trade_score'], quote_trade_details['change'], quote_trade_details['change_pct'], quote_trade_details['details'])
            render_indicator_card_tv("Verbraucherstimmung", f"{quote_fundamental['sentiment_val']:.1f} Pkt.", quote_fundamental['sentiment_score'], quote_sent_details['change'], quote_sent_details['change_pct'], quote_sent_details['details'])
            
        # Detailed Raw data expander
        with st.expander("📂 Rohdaten und Tabellen anzeigen"):
            tab_d1, tab_d2 = st.columns(2)
            with tab_d1:
                st.markdown(f"#### {base_curr} Historische Daten")
                combined_base = base_fundamental['df_rate'][['date', 'value']].rename(columns={"value": "Zinssatz"})
                combined_base = pd.merge(combined_base, base_fundamental['df_unemp'][['date', 'value']].rename(columns={"value": "Arbeitslosigkeit"}), on="date", how="outer")
                combined_base = pd.merge(combined_base, base_fundamental['df_cpi'][['date', 'value']].rename(columns={"value": "CPI_Index"}), on="date", how="outer")
                combined_base = pd.merge(combined_base, base_fundamental['df_gdp'][['date', 'value']].rename(columns={"value": "GDP_Stand"}), on="date", how="outer")
                combined_base = pd.merge(combined_base, base_fundamental['df_leading'][['date', 'value']].rename(columns={"value": "Vorlauf"}), on="date", how="outer")
                combined_base = pd.merge(combined_base, base_fundamental['df_trade'][['date', 'value']].rename(columns={"value": "Handelsbilanz"}), on="date", how="outer")
                combined_base = pd.merge(combined_base, base_fundamental['df_sentiment'][['date', 'value']].rename(columns={"value": "Stimmung"}), on="date", how="outer")
                combined_base = combined_base.sort_values("date", ascending=False).reset_index(drop=True)
                st.dataframe(combined_base.head(20), use_container_width=True)
            with tab_d2:
                st.markdown(f"#### {quote_curr} Historische Daten")
                combined_quote = quote_fundamental['df_rate'][['date', 'value']].rename(columns={"value": "Zinssatz"})
                combined_quote = pd.merge(combined_quote, quote_fundamental['df_unemp'][['date', 'value']].rename(columns={"value": "Arbeitslosigkeit"}), on="date", how="outer")
                combined_quote = pd.merge(combined_quote, quote_fundamental['df_cpi'][['date', 'value']].rename(columns={"value": "CPI_Index"}), on="date", how="outer")
                combined_quote = pd.merge(combined_quote, quote_fundamental['df_gdp'][['date', 'value']].rename(columns={"value": "GDP_Stand"}), on="date", how="outer")
                combined_quote = pd.merge(combined_quote, quote_fundamental['df_leading'][['date', 'value']].rename(columns={"value": "Vorlauf"}), on="date", how="outer")
                combined_quote = pd.merge(combined_quote, quote_fundamental['df_trade'][['date', 'value']].rename(columns={"value": "Handelsbilanz"}), on="date", how="outer")
                combined_quote = pd.merge(combined_quote, quote_fundamental['df_sentiment'][['date', 'value']].rename(columns={"value": "Stimmung"}), on="date", how="outer")
                combined_quote = combined_quote.sort_values("date", ascending=False).reset_index(drop=True)
                st.dataframe(combined_quote.head(20), use_container_width=True)

    # TAB 2: ZINSPOLITIK
    with tab2:
        st.markdown(f"### 🏦 Zinspolitik-Vergleich ({base_curr} vs. {quote_curr})")
        st.markdown("""
        Zinssätze sind der stärkste Hebel für Währungsbewertungen. Höhere Zinsen ziehen ausländisches Kapital an (Carry Trade), 
        was die Währung stärkt, während Zinssenkungen sie tendenziell schwächen.
        """)
        
        # Combined Chart
        fig_rate = create_combined_chart(
            base_fundamental['df_rate'], quote_fundamental['df_rate'], 
            f"{base_curr} ({CURRENCIES[base_curr]['rate_label']})", 
            f"{quote_curr} ({CURRENCIES[quote_curr]['rate_label']})", 
            "Historischer Zinsvergleich (2023 - Heute)", "Prozent (%)"
        )
        if fig_rate:
            st.plotly_chart(fig_rate, use_container_width=True)
            
        # Zins details columns
        zc1, zc2 = st.columns(2)
        with zc1:
            st.markdown(f"#### {base_curr} Zins-Details")
            st.write(f"- **Aktueller Satz:** {base_fundamental['rate_val']:.2f}%")
            st.write(f"- **Letzte Änderung:** {base_rate_details['change']:+.2f}%")
            st.write(f"- **Bezugsschreiben:** {base_rate_details['details']}")
        with zc2:
            st.markdown(f"#### {quote_curr} Zins-Details")
            st.write(f"- **Aktueller Satz:** {quote_fundamental['rate_val']:.2f}%")
            st.write(f"- **Letzte Änderung:** {quote_rate_details['change']:+.2f}%")
            st.write(f"- **Bezugsschreiben:** {quote_rate_details['details']}")

    # TAB 3: INFLATION (CPI)
    with tab3:
        st.markdown(f"### 🎯 Inflationsentwicklung (CPI YoY)")
        st.markdown("""
        Steigende Inflation zwingt Zentralbanken meist zu restriktiver Geldpolitik (Zinserhöhungen), was die Währung stützt. 
        Sinkende Inflation erlaubt Zinssenkungen (Dovish), was die Währung schwächt.
        """)
        
        # Compute YoY dataframes for combined plot
        df_b_cpi = base_fundamental['df_cpi'].copy()
        avg_gap_b = (df_b_cpi['date'].diff().mean()).days if len(df_b_cpi) > 1 else 30
        periods_b = 4 if 80 <= avg_gap_b <= 100 else 12
        df_b_cpi['value'] = df_b_cpi['value'].pct_change(periods=periods_b) * 100
        df_b_cpi = df_b_cpi.dropna().reset_index(drop=True)
        
        df_q_cpi = quote_fundamental['df_cpi'].copy()
        avg_gap_q = (df_q_cpi['date'].diff().mean()).days if len(df_q_cpi) > 1 else 30
        periods_q = 4 if 80 <= avg_gap_q <= 100 else 12
        df_q_cpi['value'] = df_q_cpi['value'].pct_change(periods=periods_q) * 100
        df_q_cpi = df_q_cpi.dropna().reset_index(drop=True)
        
        fig_cpi = create_combined_chart(
            df_b_cpi, df_q_cpi,
            f"{base_curr} Inflation Rate (YoY)",
            f"{quote_curr} Inflation Rate (YoY)",
            "Historischer Inflationsvergleich (YoY %)", "Prozent (%)"
        )
        if fig_cpi:
            st.plotly_chart(fig_cpi, use_container_width=True)
            
        # Inflation details
        ic1, ic2 = st.columns(2)
        with ic1:
            st.markdown(f"#### {base_curr} CPI")
            st.write(f"- **YoY Rate:** {base_fundamental['cpi_val']:.2f}%")
            st.write(f"- **Änderung vs. Vorperiode:** {base_cpi_details['change']:+.2f}%-Punkte")
        with ic2:
            st.markdown(f"#### {quote_curr} CPI")
            st.write(f"- **YoY Rate:** {quote_fundamental['cpi_val']:.2f}%")
            st.write(f"- **Änderung vs. Vorperiode:** {quote_cpi_details['change']:+.2f}%-Punkte")

    # TAB 4: BIP & WACHSTUM (GDP)
    with tab4:
        st.markdown(f"### 📊 BIP-Wachstum (Real GDP YoY)")
        st.markdown("""
        Starkes Wirtschaftswachstum (BIP-Zuwachs) signalisiert wirtschaftliche Gesundheit. 
        Zentralbanken können die Zinsen anheben, ohne die Wirtschaft abzuwürgen, was die Währung stärkt (Hawkish).
        Schrumpfendes BIP deutet auf Rezession hin, was Zinssenkungen fordert und die Währung schwächt (Dovish).
        """)
        
        # Calculate growth dataframes (YoY)
        df_b_gdp_yoy = base_fundamental['df_gdp'].copy()
        df_b_gdp_yoy['yoy'] = df_b_gdp_yoy['value'].pct_change(periods=4) * 100
        df_b_gdp_yoy = df_b_gdp_yoy.rename(columns={'yoy': 'value'}).dropna().reset_index(drop=True)
        
        df_q_gdp_yoy = quote_fundamental['df_gdp'].copy()
        df_q_gdp_yoy['yoy'] = df_q_gdp_yoy['value'].pct_change(periods=4) * 100
        df_q_gdp_yoy = df_q_gdp_yoy.rename(columns={'yoy': 'value'}).dropna().reset_index(drop=True)
        
        fig_gdp = create_combined_chart(
            df_b_gdp_yoy, df_q_gdp_yoy,
            f"{base_curr} Real GDP Growth (YoY)",
            f"{quote_curr} Real GDP Growth (YoY)",
            "Historischer BIP-Wachstumsvergleich (% YoY)", "Prozent (%)"
        )
        if fig_gdp:
            st.plotly_chart(fig_gdp, use_container_width=True)
            
        # GDP Details
        gc1, gc2 = st.columns(2)
        with gc1:
            st.markdown(f"#### {base_curr} Real GDP")
            st.write(f"- **Aktuelle Dynamik:** {base_fundamental['gdp_val']:+.2f}% (YoY)")
            st.write(f"- **Quartals-Stand:** {base_gdp_details['details'].split(': ')[-1] if ':' in base_gdp_details['details'] else 'N/A'}")
        with gc2:
            st.markdown(f"#### {quote_curr} Real GDP")
            st.write(f"- **Aktuelle Dynamik:** {quote_fundamental['gdp_val']:+.2f}% (YoY)")
            st.write(f"- **Quartals-Stand:** {quote_gdp_details['details'].split(': ')[-1] if ':' in quote_gdp_details['details'] else 'N/A'}")

    # TAB 5: SMC TRADING-STRATEGIE
    with tab5:
        st.markdown("### 🧠 SMC & FVG Handelsstrategie")
        st.markdown(f"""
        Dieses Dashboard liefert dir die **fundamentale Richtung (GVA - Gerechter Wert)** für das Währungspaar **{base_curr}/{quote_curr}**.
        Um diese Richtung profitabel im Chart umzusetzen, nutzen professionelle Trader Smart Money Concepts (SMC) und Fair Value Gaps (FVG).
        """)
        
        # Grid layout for strategy steps
        sc1, sc2 = st.columns(2)
        
        with sc1:
            bias_text = "LONG (Kauf)" if sig in ["SB", "MB"] else ("SHORT (Verkauf)" if sig in ["SS", "MS"] else "NEUTRAL (Kein Trade)")
            st.markdown(f"""
            #### 1. Den fundamentalen Bias bestimmen
            - Prüfe den aktuellen **Divergenz-Score ({divergence:+.1f})**.
            - **Signal:** {badge} ({bias_text})
            - Dies bestimmt deine ausschließliche Trading-Richtung für die kommenden Tage/Wochen. Bei einem **LONG-Bias** suchst du *ausschließlich* nach Kaufmöglichkeiten.
            
            #### 2. Liquidity Sweep abwarten
            - Schalte auf den **15-Minuten- (15m)** oder **1-Stunden-Chart (1h)**.
            - **Bei Long-Bias:** Warte, bis der Preis ein markantes vorheriges Tief (Sells-Stop Liquidity) abholt (engl. *Sweep*).
            - **Bei Short-Bias:** Warte, bis der Preis ein markantes vorheriges Hoch (Buy-Stop Liquidity) abholt.
            """)
            
        with sc2:
            st.markdown(f"""
            #### 3. Market Structure Shift & FVG Entry
            - Nach dem Sweep wartest du auf eine impulsive Umkehr in deine fundamentale Richtung.
            - Dies erzeugt einen **Market Structure Shift (MSS)** (Bruch des letzten entgegengesetzten Hochs/Tiefs).
            - Suche nach dem **Fair Value Gap (FVG)**, der durch diese impulsive Bewegung entstanden ist (eine Ineffizienz aus 3 Kerzen).
            
            #### 4. Ausführung & Risk Management
            - Platziere deine Limit-Order am **Anfang des FVG (Gerechter Wert Einstieg)**.
            - **Stop Loss (SL):** Knapp unter das Tief des Liquidity Sweeps.
            - **Take Profit (TP):** Targetiere die nächstgelegenen Liquiditätspools (gegenüberliegende Hochs/Tiefs).
            """)
            
        st.markdown("---")
        st.markdown("#### 📋 Einstiegs-Checkliste")
        
        # Display checklist using Streamlit checkboxes
        has_divergence = sig in ["SB", "MB", "MS", "SS"]
        st.checkbox("1. Hat das Währungspaar eine klare fundamentale Divergenz (Signal ist nicht NEUTRAL)?", value=has_divergence, disabled=True)
        st.checkbox("2. Stimmt die aktuelle charttechnische Ausrichtung mit dem fundamentalen Bias überein?", value=False)
        st.checkbox("3. Wurde auf dem 15m/1h-Chart ein signifikanter Liquiditätspool (High/Low) gesweept?", value=False)
        st.checkbox("4. Gab es einen Market Structure Shift (Strukturbruch) mit starkem Displacement?", value=False)
        st.checkbox("5. Ist ein FVG (Fair Value Gap) vorhanden, in den der Preis zurückkehren kann?", value=False)

    # TAB 6: NEWS & RESEARCH
    with tab6:
        st.markdown("### 📰 News & Research Hub")
        st.markdown(f"Aktuelle fundamentale Marktnachrichten für das Paar **{base_curr}/{quote_curr}**.")
        
        default_q = get_default_query(base_curr, quote_curr)
        
        # Search bar
        search_q = st.text_input("🔍 Nachrichten durchsuchen", value=default_q, help="Nutze Stichworte wie Inflation, Leitzins, Fed, EZB etc.")
        
        if search_q:
            with st.spinner("Suche aktuelle Nachrichten..."):
                raw_articles = fetch_news(search_q, newsapi_key, newsdata_key)
                # Apply deduplication
                news_articles = deduplicate_articles(raw_articles)
                
            if news_articles:
                st.info(f"Es wurden {len(news_articles)} relevante und einzigartige Artikel gefunden.")
                
                # Group articles by category
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
                
                # Function to render articles list in a grid
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
                            
                            # Image tag (using no-referrer and a reliable Unsplash fallback on error)
                            fallback_img = "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80"
                            img_html = ""
                            if art['urlToImage']:
                                img_html = f'<img src="{art["urlToImage"]}" referrerpolicy="no-referrer" onerror="this.onerror=null; this.src=\'{fallback_img}\';" style="width:100%; height:140px; object-fit:cover; border-radius:6px; margin-bottom:10px; border: 1px solid #1f2026;">'
                            else:
                                img_html = f'<div style="width:100%; height:140px; background-color:#0c0c0e; border-radius:6px; margin-bottom:10px; display:flex; justify-content:center; align-items:center; border: 1px solid #1f2026;"><span style="font-size:2rem;">📊</span></div>'
                                
                            desc_str = art['description'] if art['description'] else "Keine Kurzbeschreibung verfügbar. Bitte folge dem Link, um den vollständigen Artikel zu lesen."
                            if len(desc_str) > 200:
                                desc_str = desc_str[:197] + "..."
                                
                            # Card HTML
                            st.markdown(f"""
                            <div class="news-card">
                                <div>
                                    {img_html}
                                    <a class="news-title" href="{art['url']}" target="_blank">{art['title']}</a>
                                    <div class="news-meta">Quelle: <strong>{art['source']}</strong> | {pub_date_str}</div>
                                    <p class="news-desc">{desc_str}</p>
                                </div>
                                <div style="border-top:1px solid #2a2e39; padding-top:8px; margin-top:8px; display:flex; justify-content:space-between; align-items:center;">
                                    <span style="font-size:0.68rem; color:#8b949e; background-color:#21262d; padding:2px 6px; border-radius:3px;">{art['api']}</span>
                                    <a href="{art['url']}" target="_blank" style="font-size:0.75rem; color:#58a6ff; text-decoration:none; font-weight:600;">Lesen ↗</a>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                
                # Render sub-tabs content
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
                st.warning("Keine aktuellen Nachrichten gefunden. Versuche es mit einfacheren Suchbegriffen (z. B. 'Inflation' oder 'Fed').")

    # TAB 7: HISTORISCHE DATEN
    with tab7:
        st.markdown("### 📜 G8 Historische Daten & Export")
        st.markdown("Analysiere die langfristige historische Entwicklung aller 7 makroökonomischen Indikatoren und exportiere die rohen Daten als CSV.")
        
        # Timeframe slider
        hist_years = st.slider("Zeitraum wählen", min_value=2015, max_value=2026, value=(2015, 2026))
        start_dt = pd.to_datetime(f"{hist_years[0]}-01-01")
        end_dt = pd.to_datetime(f"{hist_years[1]}-12-31")
        
        # Rates
        h_df_base_rate = base_fundamental['df_rate'][(base_fundamental['df_rate']['date'] >= start_dt) & (base_fundamental['df_rate']['date'] <= end_dt)]
        h_df_quote_rate = quote_fundamental['df_rate'][(quote_fundamental['df_rate']['date'] >= start_dt) & (quote_fundamental['df_rate']['date'] <= end_dt)]
        
        # Unemployment
        h_df_base_unemp = base_fundamental['df_unemp'][(base_fundamental['df_unemp']['date'] >= start_dt) & (base_fundamental['df_unemp']['date'] <= end_dt)]
        h_df_quote_unemp = quote_fundamental['df_unemp'][(quote_fundamental['df_unemp']['date'] >= start_dt) & (quote_fundamental['df_unemp']['date'] <= end_dt)]
        
        # CPI (compute YoY inflation series for both)
        df_b_cpi_hist = base_fundamental['df_cpi'].copy()
        avg_gap_b = (df_b_cpi_hist['date'].diff().mean()).days if len(df_b_cpi_hist) > 1 else 30
        periods_b = 4 if 80 <= avg_gap_b <= 100 else 12
        df_b_cpi_hist['yoy'] = df_b_cpi_hist['value'].pct_change(periods=periods_b) * 100
        h_df_base_cpi = df_b_cpi_hist[(df_b_cpi_hist['date'] >= start_dt) & (df_b_cpi_hist['date'] <= end_dt)]
        
        df_q_cpi_hist = quote_fundamental['df_cpi'].copy()
        avg_gap_q = (df_q_cpi_hist['date'].diff().mean()).days if len(df_q_cpi_hist) > 1 else 30
        periods_q = 4 if 80 <= avg_gap_q <= 100 else 12
        df_q_cpi_hist['yoy'] = df_q_cpi_hist['value'].pct_change(periods=periods_q) * 100
        h_df_quote_cpi = df_q_cpi_hist[(df_q_cpi_hist['date'] >= start_dt) & (df_q_cpi_hist['date'] <= end_dt)]
        
        # GDP (compute YoY growth)
        df_b_gdp_hist = base_fundamental['df_gdp'].copy()
        df_b_gdp_hist['yoy'] = df_b_gdp_hist['value'].pct_change(periods=4) * 100
        h_df_base_gdp = df_b_gdp_hist[(df_b_gdp_hist['date'] >= start_dt) & (df_b_gdp_hist['date'] <= end_dt)]
        
        df_q_gdp_hist = quote_fundamental['df_gdp'].copy()
        df_q_gdp_hist['yoy'] = df_q_gdp_hist['value'].pct_change(periods=4) * 100
        h_df_quote_gdp = df_q_gdp_hist[(df_q_gdp_hist['date'] >= start_dt) & (df_q_gdp_hist['date'] <= end_dt)]
        
        # Leading
        h_df_base_leading = base_fundamental['df_leading'][(base_fundamental['df_leading']['date'] >= start_dt) & (base_fundamental['df_leading']['date'] <= end_dt)]
        h_df_quote_leading = quote_fundamental['df_leading'][(quote_fundamental['df_leading']['date'] >= start_dt) & (quote_fundamental['df_leading']['date'] <= end_dt)]
        
        # Trade
        h_df_base_trade = base_fundamental['df_trade'][(base_fundamental['df_trade']['date'] >= start_dt) & (base_fundamental['df_trade']['date'] <= end_dt)]
        h_df_quote_trade = quote_fundamental['df_trade'][(quote_fundamental['df_trade']['date'] >= start_dt) & (quote_fundamental['df_trade']['date'] <= end_dt)]
        
        # Sentiment
        h_df_base_sent = base_fundamental['df_sentiment'][(base_fundamental['df_sentiment']['date'] >= start_dt) & (base_fundamental['df_sentiment']['date'] <= end_dt)]
        h_df_quote_sent = quote_fundamental['df_sentiment'][(quote_fundamental['df_sentiment']['date'] >= start_dt) & (quote_fundamental['df_sentiment']['date'] <= end_dt)]
        
        # Merge for export
        export_df = h_df_base_rate[['date', 'value']].rename(columns={'value': f'{base_curr}_Rate'})
        export_df = pd.merge(export_df, h_df_quote_rate[['date', 'value']].rename(columns={'value': f'{quote_curr}_Rate'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_base_unemp[['date', 'value']].rename(columns={'value': f'{base_curr}_Unemployment'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_quote_unemp[['date', 'value']].rename(columns={'value': f'{quote_curr}_Unemployment'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_base_cpi[['date', 'yoy']].rename(columns={'yoy': f'{base_curr}_CPI_YoY'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_quote_cpi[['date', 'yoy']].rename(columns={'yoy': f'{quote_curr}_CPI_YoY'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_base_gdp[['date', 'yoy']].rename(columns={'yoy': f'{base_curr}_GDP_YoY'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_quote_gdp[['date', 'yoy']].rename(columns={'yoy': f'{quote_curr}_GDP_YoY'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_base_leading[['date', 'value']].rename(columns={'value': f'{base_curr}_Leading_Indicator'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_quote_leading[['date', 'value']].rename(columns={'value': f'{quote_curr}_Leading_Indicator'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_base_trade[['date', 'value']].rename(columns={'value': f'{base_curr}_TradeBalance'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_quote_trade[['date', 'value']].rename(columns={'value': f'{quote_curr}_TradeBalance'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_base_sent[['date', 'value']].rename(columns={'value': f'{base_curr}_ConsumerSentiment'}), on='date', how='outer')
        export_df = pd.merge(export_df, h_df_quote_sent[['date', 'value']].rename(columns={'value': f'{quote_curr}_ConsumerSentiment'}), on='date', how='outer')
        export_df = export_df.sort_values('date', ascending=False).reset_index(drop=True)
        
        # Display charts in sub-tabs
        hc1, hc2, hc3, hc4, hc5, hc6, hc7 = st.tabs([
            "🏦 Zinssatz-Verlauf", 
            "🎯 Inflations-Verlauf", 
            "📊 BIP-Wachstums-Verlauf (YoY)",
            "💼 Arbeitslosenquote",
            "🏭 Vorlaufindikator",
            "🚢 Handelsbilanz",
            "🧠 Verbraucherstimmung"
        ])
        with hc1:
            fig1 = create_combined_chart(
                h_df_base_rate, h_df_quote_rate,
                f"{base_curr} ({CURRENCIES[base_curr]['rate_label']})",
                f"{quote_curr} ({CURRENCIES[quote_curr]['rate_label']})",
                f"Zins-Historie ({hist_years[0]} - {hist_years[1]})", "Prozent (%)",
                slice_date=f"{hist_years[0]}-01-01"
            )
            if fig1: st.plotly_chart(fig1, use_container_width=True)
        with hc2:
            fig2 = create_combined_chart(
                h_df_base_cpi.rename(columns={'yoy': 'value'}), h_df_quote_cpi.rename(columns={'yoy': 'value'}),
                f"{base_curr} CPI YoY",
                f"{quote_curr} CPI YoY",
                f"Inflations-Historie ({hist_years[0]} - {hist_years[1]})", "Prozent (%)",
                slice_date=f"{hist_years[0]}-01-01"
            )
            if fig2: st.plotly_chart(fig2, use_container_width=True)
        with hc3:
            fig3 = create_combined_chart(
                h_df_base_gdp.rename(columns={'yoy': 'value'}), h_df_quote_gdp.rename(columns={'yoy': 'value'}),
                f"{base_curr} Real GDP Growth YoY",
                f"{quote_curr} Real GDP Growth YoY",
                f"BIP-Wachstums-Historie YoY ({hist_years[0]} - {hist_years[1]})", "Prozent (%)",
                slice_date=f"{hist_years[0]}-01-01"
            )
            if fig3: st.plotly_chart(fig3, use_container_width=True)
        with hc4:
            fig4 = create_combined_chart(
                h_df_base_unemp, h_df_quote_unemp,
                f"{base_curr} Arbeitslosenquote",
                f"{quote_curr} Arbeitslosenquote",
                f"Arbeitslosenquote-Historie ({hist_years[0]} - {hist_years[1]})", "Prozent (%)",
                slice_date=f"{hist_years[0]}-01-01"
            )
            if fig4: st.plotly_chart(fig4, use_container_width=True)
        with hc5:
            base_lbl = f"{base_curr} PMI" if base_fundamental['is_pmi'] else f"{base_curr} IP YoY"
            quote_lbl = f"{quote_curr} PMI" if quote_fundamental['is_pmi'] else f"{quote_curr} IP YoY"
            fig5 = create_combined_chart(
                h_df_base_leading, h_df_quote_leading,
                base_lbl, quote_lbl,
                f"Vorlaufindikator-Historie ({hist_years[0]} - {hist_years[1]})", "Wert",
                slice_date=f"{hist_years[0]}-01-01"
            )
            if fig5: st.plotly_chart(fig5, use_container_width=True)
        with hc6:
            fig6 = create_combined_chart(
                h_df_base_trade, h_df_quote_trade,
                f"{base_curr} Handelsbilanz",
                f"{quote_curr} Handelsbilanz",
                f"Handelsbilanz-Historie ({hist_years[0]} - {hist_years[1]})", "Mio. Einheiten",
                slice_date=f"{hist_years[0]}-01-01"
            )
            if fig6: st.plotly_chart(fig6, use_container_width=True)
        with hc7:
            fig7 = create_combined_chart(
                h_df_base_sent, h_df_quote_sent,
                f"{base_curr} Verbraucherstimmung",
                f"{quote_curr} Verbraucherstimmung",
                f"Verbraucherstimmung-Historie ({hist_years[0]} - {hist_years[1]})", "Indexwert",
                slice_date=f"{hist_years[0]}-01-01"
            )
            if fig7: st.plotly_chart(fig7, use_container_width=True)
                
        st.markdown("#### 📂 Daten-Tabelle & Export")
        st.dataframe(export_df.head(50), use_container_width=True)
        
        csv_data = export_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Historische Daten als CSV exportieren",
            data=csv_data,
            file_name=f"fundamental_history_{base_curr}_{quote_curr}_{hist_years[0]}_{hist_years[1]}.csv",
            mime='text/csv'
        )

    # TAB 8: ANALYSTEN-PROGNOSEN
    with tab8:
        st.markdown("### 🔮 Analysten-Prognosen & Banken-Consensus")
        st.markdown(f"Makroökonomische Erwartungen führender Investmentbanken für das Paar **{base_curr}/{quote_curr}**.")
        
        if base_curr in FORECASTS and quote_curr in FORECASTS:
            f_base = FORECASTS[base_curr]
            f_quote = FORECASTS[quote_curr]
            
            fc1, fc2 = st.columns(2)
            
            with fc1:
                st.markdown(f"#### {CURRENCIES[base_curr]['flag']} {base_curr} Prognosen")
                r_base_df = pd.DataFrame(f_base['rate']).T
                st.markdown("**Leitzins-Prognosen (%)**")
                st.dataframe(r_base_df, use_container_width=True)
                
                cpi_base_df = pd.DataFrame(f_base['cpi']).T
                gdp_base_df = pd.DataFrame(f_base['gdp']).T
                st.markdown("**Inflation & BIP (%)**")
                st.dataframe(pd.concat([cpi_base_df, gdp_base_df]), use_container_width=True)
                
            with fc2:
                st.markdown(f"#### {CURRENCIES[quote_curr]['flag']} {quote_curr} Prognosen")
                r_quote_df = pd.DataFrame(f_quote['rate']).T
                st.markdown("**Leitzins-Prognosen (%)**")
                st.dataframe(r_quote_df, use_container_width=True)
                
                cpi_quote_df = pd.DataFrame(f_quote['cpi']).T
                gdp_quote_df = pd.DataFrame(f_quote['gdp']).T
                st.markdown("**Inflation & BIP (%)**")
                st.dataframe(pd.concat([cpi_quote_df, gdp_quote_df]), use_container_width=True)
                
            st.markdown("---")
            st.markdown("#### 📊 Zins-Consensus Vergleich (Jahresende 2026)")
            
            banks = list(f_base['rate']['Q4 2026'].keys())
            chart_data = []
            for bank in banks:
                chart_data.append({
                    'Bank': bank,
                    f'{base_curr} Target': f_base['rate']['Q4 2026'].get(bank),
                    f'{quote_curr} Target': f_quote['rate']['Q4 2026'].get(bank)
                })
            c_df = pd.DataFrame(chart_data)
            
            fig_fore = px.bar(
                c_df, x='Bank', y=[f'{base_curr} Target', f'{quote_curr} Target'],
                barmode='group',
                color_discrete_map={f'{base_curr} Target': '#58a6ff', f'{quote_curr} Target': '#d29024'}
            )
            fig_fore.update_layout(
                yaxis_title="Prognostizierter Zinssatz (%)",
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color="#8b949e", size=10),
                legend=dict(title="Zieldivergenz", orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_fore, use_container_width=True)
            
            avg_base_2026 = np.mean(list(f_base['rate']['Q4 2026'].values()))
            avg_quote_2026 = np.mean(list(f_quote['rate']['Q4 2026'].values()))
            consensus_divergence = avg_base_2026 - avg_quote_2026
            
            if consensus_divergence > 0.5:
                sent_badge = "HAWKISH BIAS (Kaufempfehlung)"
                sent_color = "#0ecb81"
                sent_desc = f"Die Analysten erwarten einen deutlichen Zinsvorteil für den {base_curr} gegenüber dem {quote_curr}. Dies unterstützt einen Kauf-Bias (Long) auf fundamentaler Ebene."
            elif consensus_divergence < -0.5:
                sent_badge = "DOVISH BIAS (Verkaufsempfehlung)"
                sent_color = "#f6465d"
                sent_desc = f"Die Analysten erwarten einen deutlichen Zinsvorteil für den {quote_curr} gegenüber dem {base_curr}. Dies unterstützt einen Verkauf-Bias (Short) auf fundamentaler Ebene."
            else:
                sent_badge = "NEUTRAL BIAS (Keine klare Richtung)"
                sent_color = "#848e9c"
                sent_desc = f"Die Zinsdifferenz der Consensus-Erwartungen zwischen {base_curr} und {quote_curr} ist minimal. Fundamental liegt ein ausgeglichenes Kräfteverhältnis vor."
                
            st.markdown(f"""
            <div style="
                background-color: rgba(88, 166, 255, 0.05);
                border: 1px solid rgba(88, 166, 255, 0.2);
                border-radius: 6px;
                padding: 16px;
                margin-top: 15px;
            ">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size:0.75rem; text-transform:uppercase; color:#8b949e; font-weight:600;">Consensus Sentiment Indicator (YE 2026)</span>
                    <span style="background-color:{sent_color}22; color:{sent_color}; border:1px solid {sent_color}; font-weight:700; font-size:0.75rem; padding:2px 8px; border-radius:4px;">{sent_badge}</span>
                </div>
                <h4 style="color:#f0f6fc; margin:10px 0 5px 0;">Mittlere Zinsdifferenz: {consensus_divergence:+.2f}%</h4>
                <p style="color:#8b949e; margin:0; font-size:0.9rem;">{sent_desc}</p>
            </div>
            """, unsafe_allow_html=True)
            
        else:
            st.info("Keine Prognose-Daten für dieses Währungspaar verfügbar.")

else:
    st.error("Fehler beim Laden der FRED-Daten. Bitte stelle sicher, dass dein API-Key gültig ist, oder verwende den Demo-Modus.")

# Footnote Warning Box removed

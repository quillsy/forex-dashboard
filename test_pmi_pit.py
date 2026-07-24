import os
import streamlit as st
import pandas as pd
from datetime import datetime

# Mock st.session_state
if "active_live_model_weights" not in st.session_state:
    st.session_state["active_live_model_weights"] = None
if "demo_mode_chk" not in st.session_state:
    st.session_state["demo_mode_chk"] = False

import app

def test_pmi_pit_look_ahead():
    print("=== STARTING PMI POINT-IN-TIME AUDIT TEST ===")
    
    # Test dates
    test_dates = ["2022-06-15", "2023-01-01", "2024-03-20"]
    fred_key = os.getenv("FRED_API_KEY")
    
    for t_date in test_dates:
        t_dt = pd.to_datetime(t_date)
        print(f"\nAuditing target date: {t_date}")
        
        # 1. Fetch PMI historical
        pmi_res = app.get_all_pmi_data_historical(fred_key, None, t_date)
        for curr in ["EUR", "USD"]:
            m_last = pmi_res.get(curr, {}).get("m_last")
            m_ref = pmi_res.get(curr, {}).get("m_ref")
            print(f" -> {curr} PMI: {m_last} (ref: {m_ref})")
            
            # Since S&P Global / ISM PMI is unavailable, m_last must be None
            assert m_last is None, f"Methodology violation: BCI or future PMI leaked into baseline CORE PMI for {curr} on {t_date}!"
            
        # 2. Fetch BCI historical (separate research factor)
        for curr in ["EUR", "USD"]:
            bci_res = app.get_bci_value(curr, t_date)
            if bci_res:
                bci_val = bci_res["value"]
                bci_date = pd.to_datetime(bci_res["date"])
                print(f" -> {curr} BCI: {bci_val} (ref: {bci_res['date']})")
                assert bci_date <= t_dt, f"Look-Ahead Bias detected: BCI reference date {bci_res['date']} is in the future relative to {t_date}!"
                
    print("\n🟢 No PMI Look-Ahead Bias detected! Point-in-Time validation PASSED successfully.")

if __name__ == "__main__":
    test_pmi_pit_look_ahead()

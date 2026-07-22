import os
import streamlit as st

# Setup mock streamlit session state to avoid errors
if "active_live_model_weights" not in st.session_state:
    st.session_state["active_live_model_weights"] = None
if "demo_mode_chk" not in st.session_state:
    st.session_state["demo_mode_chk"] = False

import app

def run_e2e_test():
    print("=== STARTING END-TO-END VALIDATION ===")
    
    # Step 1: Currency details calculation
    print("\n--- Step 1: EUR details calculation ---")
    eur_details = app.compute_currency_details("EUR")
    print(f"EUR completeness: {eur_details.get('_completeness')}%")
    print(f"EUR missing: {eur_details.get('_missing')}")
    print(f"EUR scores: {eur_details}")
    
    print("\n--- Step 2: USD details calculation ---")
    usd_details = app.compute_currency_details("USD")
    print(f"USD completeness: {usd_details.get('_completeness')}%")
    print(f"USD missing: {usd_details.get('_missing')}")
    print(f"USD scores: {usd_details}")
    
    # Step 3: Professional score and regime calculation
    print("\n--- Step 3: EUR Professional Score ---")
    eur_score, eur_reg, eur_core, eur_corr, _ = app.compute_currency_professional_score_and_regime("EUR")
    print(f"EUR Final Score: {eur_score}")
    print(f"EUR Market Regime: {eur_reg}")
    print(f"EUR Core contribution: {eur_core}")
    print(f"EUR Correction contribution: {eur_corr}")
    
    print("\n--- Step 4: USD Professional Score ---")
    usd_score, usd_reg, usd_core, usd_corr, _ = app.compute_currency_professional_score_and_regime("USD")
    print(f"USD Final Score: {usd_score}")
    print(f"USD Market Regime: {usd_reg}")
    print(f"USD Core contribution: {usd_core}")
    print(f"USD Correction contribution: {usd_corr}")
    
    print("\n=== E2E VALIDATION COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    run_e2e_test()

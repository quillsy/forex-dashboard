import os
import json
import sys
from datetime import datetime

# Define status helper functions
def load_status():
    file_path = "data_collection_status.json"
    if not os.path.exists(file_path):
        return {
            "last_run_timestamp": "N/A",
            "last_run_status": "N/A",
            "last_run_error": None,
            "total_successful_runs": 0,
            "total_failed_runs": 0,
            "history": []
        }
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "last_run_timestamp": "N/A",
            "last_run_status": "N/A",
            "last_run_error": None,
            "total_successful_runs": 0,
            "total_failed_runs": 0,
            "history": []
        }

def save_status(status):
    file_path = "data_collection_status.json"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("Failed to save status:", e)

# 1. Load status
status = load_status()
now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

try:
    print("=== STARTING AUTOMATED DATA COLLECTION JOB ===")
    
    # Mock streamlit session state and config to run in bare python mode
    import streamlit as st
    if "active_live_model_weights" not in st.session_state:
        st.session_state["active_live_model_weights"] = None
    if "demo_mode_chk" not in st.session_state:
        st.session_state["demo_mode_chk"] = False

    # Force demo mode to FALSE for data collection (no mock data in live logs!)
    st.session_state["demo_mode_chk"] = False
    
    import app
    
    print("\n[Step 1] Running save_all_g10_live_snapshots()...")
    app.save_all_g10_live_snapshots()
    
    print("\n[Step 2] Running update_open_outcomes()...")
    app.update_open_outcomes()
    
    # Update status
    status["last_run_timestamp"] = now_str
    status["last_run_status"] = "SUCCESS"
    status["last_run_error"] = None
    status["total_successful_runs"] += 1
    
    status["history"].append({"timestamp": now_str, "status": "SUCCESS", "error": None})
    if len(status["history"]) > 50:
        status["history"] = status["history"][-50:]
        
    save_status(status)
    print("\n=== AUTOMATED DATA COLLECTION COMPLETED SUCCESSFULLY ===")
    sys.exit(0)

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"\n❌ ERROR during data collection: {e}")
    status["last_run_timestamp"] = now_str
    status["last_run_status"] = "FAILED"
    status["last_run_error"] = str(e)
    status["total_failed_runs"] += 1
    
    status["history"].append({"timestamp": now_str, "status": "FAILED", "error": str(e)})
    if len(status["history"]) > 50:
        status["history"] = status["history"][-50:]
        
    save_status(status)
    sys.exit(1)

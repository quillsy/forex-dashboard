import importlib
import json
import os
import sys
from datetime import datetime, timezone


def load_status():
    defaults = {
        "last_run_timestamp": "N/A", "last_run_status": "N/A",
        "last_run_error": None, "total_successful_runs": 0,
        "total_partial_runs": 0, "total_failed_runs": 0, "history": []
    }
    try:
        with open("data_collection_status.json", "r", encoding="utf-8") as handle:
            defaults.update(json.load(handle))
    except (OSError, ValueError):
        pass
    return defaults


def save_status(status):
    import tempfile
    descriptor, temporary = tempfile.mkstemp(prefix=".collection-", dir=".")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, "data_collection_status.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def import_collection_app():
    """Skip the existing UI/network guards during import, then enable capture."""
    import streamlit as st
    previous_mock_mode = getattr(st, "_mock_mode", False)
    st._mock_mode = True
    try:
        st.session_state["active_live_model_weights"] = None
        st.session_state["demo_mode_chk"] = False
        return importlib.import_module("app")
    finally:
        st._mock_mode = previous_mock_mode


def _policy_summary(rates):
    states = {currency: rate.get("verification_status", rate.get("status", "UNAVAILABLE"))
              for currency, rate in rates.items() if isinstance(rate, dict)}
    verified = sum("🟢" in state for state in states.values())
    status = "SUCCESS" if len(states) == 8 and verified == 8 else ("PARTIAL" if verified else "FAILED")
    return {"status": status, "currencies": states}


def _run_component(call):
    try:
        result = call()
        if not isinstance(result, dict):
            return {"status": "FAILED", "issues": [{"reason": "MISSING_COLLECTION_SUMMARY"}]}
        return result
    except Exception as error:
        return {"status": "FAILED", "issues": [{"reason": type(error).__name__}]}


def collect(app):
    components = {}
    components["policy_rates"] = _run_component(lambda: _policy_summary(app.refresh_all_verified_policy_rates(fred_key=app.FRED_KEY)))
    components["eodhd"] = _run_component(app.prefetch_eodhd_production_data)
    components["snapshots"] = _run_component(app.save_all_g10_live_snapshots)
    components["outcomes"] = _run_component(app.update_open_outcomes)
    states = [component.get("collection_status", component.get("status", "FAILED")) for component in components.values()]
    overall = "SUCCESS" if all(state == "SUCCESS" for state in states) else ("FAILED" if all(state == "FAILED" for state in states) else "PARTIAL")
    return overall, components


def main():
    import fcntl
    with open(".data_collection.lock", "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Data collection already running; no duplicate collection started.")
            return 1
        status = load_status()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            overall, components = collect(import_collection_app())
            error = None if overall == "SUCCESS" else "One or more collection components are incomplete; see components."
        except Exception as exception:
            overall, components, error = "FAILED", {}, type(exception).__name__
        status.update({"last_run_timestamp": timestamp, "last_run_status": overall,
                       "last_run_error": error, "components": components})
        counter = {"SUCCESS": "total_successful_runs", "PARTIAL": "total_partial_runs", "FAILED": "total_failed_runs"}[overall]
        status[counter] = status.get(counter, 0) + 1
        status["history"] = (status.get("history", []) + [{"timestamp": timestamp, "status": overall, "error": error}])[-50:]
        save_status(status)
        print("Data collection:", overall)
        for name, result in components.items():
            print(name + ":", result.get("collection_status", result.get("status", "FAILED")))
        return 0 if overall == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())

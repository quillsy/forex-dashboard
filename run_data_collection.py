import importlib
import json
import os
import sys
from datetime import datetime, timezone


LIVE_FALLBACK_KEYS = frozenset({"FRED_API_KEY", "ESTAT_APP_ID", "STATS_NZ_API_KEY",
                              "OCP_APIM_SUBSCRIPTION_KEY"})


def _fallback_state(path, state):
    """Persist only operational fields, never subprocess output or credentials."""
    import tempfile
    descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _seed_fallback_status(source, destination):
    """Preserve instance counters and the longer of known provider cooldowns."""
    import re
    import live_data
    def read(path):
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}
    local, remote = read(destination), read(source)
    providers = local.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    incoming = remote.get("providers", {})
    for host, info in (incoming.items() if isinstance(incoming, dict) else []):
        if not isinstance(info, dict) or not re.fullmatch(r"[a-z0-9.-]+", host):
            continue
        deadline = live_data.timestamp(info.get("retry_after_at"))
        old = providers.get(host, {})
        if not isinstance(old, dict):
            old = {}
        previous = live_data.timestamp(old.get("retry_after_at"))
        if deadline and (previous is None or deadline > previous):
            providers[host] = dict(old, retry_after_at=deadline.isoformat())
    local["providers"] = providers
    _fallback_state(destination, local)


def maybe_start_live_fallback(keys):
    """One throttled collector per active app instance, outside its checkout.

    GitHub remains the scheduled collector. This only starts on an active UI
    render when its available dataset is older than 30 minutes. Local locks
    cannot coordinate usage with GitHub; counters remain instance estimates.
    """
    import fcntl
    import shutil
    import subprocess
    import threading
    from pathlib import Path
    import live_data

    if os.environ.get("FX_COLLECTOR") == "1":
        return "disabled"
    now = datetime.now(timezone.utc)
    completed = live_data.timestamp(live_data.load().get("completed_at"))
    if completed and 0 <= (now - completed).total_seconds() < 1800:
        return "fresh"
    # An instance without its live credentials cannot repair missing data.
    if not keys.get("FRED_API_KEY"):
        return "unavailable"
    directory = live_data.runtime_directory()
    lock = None
    try:
        if directory.is_symlink():
            return "unavailable"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = (directory / ".launch.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return "running"
        state_path = directory / ".launch-state.json"
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        attempted = live_data.timestamp(state.get("attempted_at"))
        if attempted and (now - attempted).total_seconds() < 1800:
            lock.close()
            return state.get("state") if state.get("state") in ("failed", "timeout") else "cooldown"
        state = {"attempted_at": now.isoformat(), "state": "running"}
        # Seed only admissible live/public cache files; never .env or secrets.
        source = live_data.selected_live_directory()
        if source.resolve() != directory.resolve():
            for name in ("live_core_data.json", ".policy_rates_cache.json"):
                candidate = source / name
                if candidate.is_file():
                    shutil.copy2(candidate, directory / name)
            _seed_fallback_status(source / "data_collection_status.json", directory / "data_collection_status.json")
        _fallback_state(state_path, state)
        # A minimal child environment also prevents unrelated inherited keys.
        environment = {name: os.environ[name] for name in
                       ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "SSL_CERT_FILE", "SSL_CERT_DIR")
                       if name in os.environ}
        environment.update({name: value for name, value in keys.items()
                            if name in LIVE_FALLBACK_KEYS and isinstance(value, str) and value})
        environment.update(FX_COLLECTOR="1", FX_FALLBACK_MODE="1")
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--live-only"]

        def worker():
            try:
                result = subprocess.run(command, cwd=str(directory), env=environment,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=600, check=False)
                state["state"] = "completed" if result.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                state.update(state="timeout", usage_complete=False)
            except Exception:
                state.update(state="failed", usage_complete=False)
            finally:
                state["finished_at"] = datetime.now(timezone.utc).isoformat()
                try:
                    _fallback_state(state_path, state)
                finally:
                    lock.close()

        threading.Thread(target=worker, name="fx-live-fallback", daemon=True).start()
        return "started"
    except (OSError, ValueError, RuntimeError):
        if lock is not None:
            lock.close()
        return "unavailable"


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
    import live_data
    os.environ["FX_COLLECTOR"] = "1"
    os.environ.pop("FX_READ_CORE_CACHE", None)
    live_only = "--live-only" in sys.argv
    with open(".data_collection.lock", "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Data collection already running; no duplicate collection started.")
            return 1
        status = load_status()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            app = import_collection_app()
            components = {}
            components["policy_rates"] = _run_component(lambda: _policy_summary(app.refresh_all_verified_policy_rates(fred_key=app.FRED_KEY)))
            # EODHD's limited daily budget is handled by its persisted collector.
            # Do not spend it every 30 minutes on unlicensed event datasets.
            components["live_core"] = _run_component(lambda: live_data.collect(app))
            os.environ["FX_READ_CORE_CACHE"] = "1"
            if not live_only:
                components["snapshots"] = _run_component(app.save_all_g10_live_snapshots)
                components["outcomes"] = _run_component(app.update_open_outcomes)
            states = [v.get("collection_status", v.get("status", "FAILED")) for v in components.values()]
            overall = "SUCCESS" if all(v == "SUCCESS" for v in states) else "FAILED" if all(v == "FAILED" for v in states) else "PARTIAL"
            error = None if overall == "SUCCESS" else "One or more collection components are incomplete; see components."
        except Exception as exception:
            overall, components, error = "FAILED", {}, type(exception).__name__
        status.update({"last_run_timestamp": timestamp, "last_run_status": overall,
                       "last_run_error": error, "components": components})
        status["mode"] = "live" if live_only else "daily"
        if "app" in locals():
            old_providers = status.get("providers", {})
            day = timestamp[:10]
            for host, usage in app.requests.usage.items():
                old = old_providers.get(host, {})
                old_count = old.get("requests_observed_utc_day", 0) if old.get("counted_day_utc") == day else 0
                usage["requests_observed_utc_day"] = int(old_count) + usage["requests_this_run"]
                usage["counted_day_utc"] = day
            providers = {}
            for host, old in old_providers.items():
                providers[host] = dict(old, requests_this_run=0, status="NOT_REQUESTED",
                    requests_observed_utc_day=old.get("requests_observed_utc_day", 0) if old.get("counted_day_utc") == day else 0,
                    counted_day_utc=day)
            providers.update(app.requests.usage)
            status["providers"] = providers
        counter = {"SUCCESS": "total_successful_runs", "PARTIAL": "total_partial_runs", "FAILED": "total_failed_runs"}[overall]
        status[counter] = status.get(counter, 0) + 1
        status["history"] = (status.get("history", []) + [{"timestamp": timestamp, "status": overall, "error": error}])[-50:]
        save_status(status)
        print("Data collection:", overall)
        for name, result in components.items():
            print(name + ":", result.get("collection_status", result.get("status", "FAILED")))
        completed_live = live_only and components.get("live_core", {}).get("status") in ("SUCCESS", "PARTIAL")
        return 0 if overall == "SUCCESS" or completed_live else 1


if __name__ == "__main__":
    sys.exit(main())

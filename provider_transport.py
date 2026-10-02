"""Collector-only HTTP with run deduplication and secret-free usage telemetry."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
from urllib.parse import urlparse

import requests as http


COMPROMISED_PROVIDER_HOSTS = frozenset({
    "fcsapi.com", "alphavantage.co", "benzinga.com",
    "financialmodelingprep.com", "stockdata.org",
})
_CREDENTIAL_HOSTS = {
    **dict.fromkeys(("ALPHA_VANTAGE_API_KEY", "AV_API_KEY", "AV_KEY"), "www.alphavantage.co"),
    **dict.fromkeys(("BENZINGA_API_KEY", "BENZINGA_KEY"), "api.benzinga.com"),
    **dict.fromkeys(("FCS_API_KEY", "FCS_KEY"), "api-v4.fcsapi.com"),
    **dict.fromkeys(("FMP_API_KEY", "FMP_KEY"), "financialmodelingprep.com"),
    **dict.fromkeys(("STOCKDATA_API_KEY", "STOCKDATA_KEY", "STOCKDATA_TOKEN",
                     "STOCK_DATA_API_KEY", "STOCKDATA_API_TOKEN", "STOCK_DATA_KEY"), "api.stockdata.org"),
}


def credential_rotation_required(host):
    """Operator acknowledgement is configuration, not proof of provider revocation."""
    host = host.strip().lower()
    root = next((root for root in COMPROMISED_PROVIDER_HOSTS
                 if host == root or host.endswith("." + root)), None)
    rotated = {item.strip().lower() for item in
               os.environ.get("FX_ROTATED_PROVIDER_HOSTS", "").split(",")}
    return bool(root and host not in rotated and root not in rotated)


def credential_key_blocked(name):
    host = _CREDENTIAL_HOSTS.get(name.upper())
    return bool(host and credential_rotation_required(host))


def provider_day_counts(info, day):
    """Local UTC-day counts; older interrupted usage stays explicitly unknown."""
    same_day = info.get("counted_day_utc") == day
    prior_uncertain = (info.get("prior_usage_uncertain") is True
                       or (not same_day and info.get("usage_complete") is False))

    def count(field, default=0):
        value = info.get(field, default) if same_day else 0
        return value if type(value) is int and value >= 0 else None

    observed = count("requests_observed_utc_day")
    uncertain = count("requests_uncertain_utc_day")
    reserved = count("requests_reserved_utc_day", observed)
    return {"counted_day_utc": day, "requests_observed_utc_day": observed,
            "requests_reserved_utc_day": reserved, "requests_uncertain_utc_day": uncertain,
            "prior_usage_uncertain": prior_uncertain,
            "usage_complete": (not prior_uncertain and uncertain == 0
                               and observed is not None and reserved is not None)}


class CollectorTransport:
    RequestException = http.RequestException
    exceptions = http.exceptions

    def __init__(self, client=None, status_path="data_collection_status.json", clock=None,
                 durable=False):
        self.client = client or http
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.status_path = Path(status_path).resolve()
        # Importing the UI, or constructing a transport in an offline test,
        # never enables writes to the production status file.
        self.durable = durable is True
        self.persistence_failed = False
        self.responses = {}
        self.usage = {}
        self.cooldown = set()
        self.retry_after = {}
        self._restore_retry_after()

    def _restore_retry_after(self):
        try:
            providers = json.loads(self.status_path.read_text()).get("providers", {})
            for host, info in providers.items():
                if not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+", host) or not isinstance(info, dict):
                    continue
                deadline = self._deadline(info.get("retry_after_at"))
                if (deadline and deadline > self.clock()
                        and deadline > self.retry_after.get(host, datetime.min.replace(tzinfo=timezone.utc))):
                    self.retry_after[host] = deadline
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def enable_durable_usage(self):
        """Called by the locked collector before its first provider request."""
        if any(item.get("requests_this_run", 0) for item in self.usage.values()):
            raise RuntimeError("PROVIDER_ATTEMPT_ALREADY_STARTED")
        self.durable = True
        self._restore_retry_after()

    def _persist_usage(self, host, *, reserved=False, completed=False, at=None):
        """Reserve before I/O; a returned client call is separate evidence.

        An interrupted reservation remains uncertain. Neither a reservation
        nor a returned HTTP response confirms the provider's consumed quota.
        The collector's process lock serializes writers to this local file.
        """
        if not self.durable:
            return
        import tempfile
        temporary = None
        try:
            try:
                status = json.loads(self.status_path.read_text())
            except FileNotFoundError:
                status = {}
            if not isinstance(status, dict) or not isinstance(status.get("providers", {}), dict):
                raise ValueError("INVALID_PROVIDER_STATUS")
            providers = status.setdefault("providers", {})
            old = providers.get(host, {})
            if not isinstance(old, dict):
                raise ValueError("INVALID_PROVIDER_STATUS")
            if old.get("retry_after_at") is not None and self._deadline(old["retry_after_at"]) is None:
                raise ValueError("INVALID_PROVIDER_STATUS")
            day = (at or self.clock()).astimezone(timezone.utc).date().isoformat()
            counts = provider_day_counts(old, day)
            observed = counts["requests_observed_utc_day"]
            uncertain = counts["requests_uncertain_utc_day"]
            reservations = counts["requests_reserved_utc_day"]
            if reserved:
                uncertain = uncertain + 1 if uncertain is not None else None
                reservations = reservations + 1 if reservations is not None else None
            if completed:
                observed = observed + 1 if observed is not None else None
                uncertain = max(0, uncertain - 1) if uncertain is not None else None
            usage = self.usage[host]
            usage.update(counted_day_utc=day, requests_observed_utc_day=observed,
                         requests_reserved_utc_day=reservations,
                         requests_uncertain_utc_day=uncertain,
                         prior_usage_uncertain=counts["prior_usage_uncertain"],
                         usage_complete=(not counts["prior_usage_uncertain"] and uncertain == 0
                                         and observed is not None and reservations is not None))
            provider = dict(old, **usage)
            if host not in self.retry_after:
                provider.pop("retry_after_at", None)
            providers[host] = provider
            descriptor, temporary = tempfile.mkstemp(prefix=".provider-", dir=self.status_path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(status, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.status_path)
        except (OSError, ValueError, TypeError):
            # No attempt without a durable reservation; no private paths or
            # exception text are exposed to an official-source fallback.
            self.persistence_failed = True
            raise http.RequestException("PROVIDER_ATTEMPT_PERSISTENCE_FAILED") from None
        finally:
            try:
                if temporary is not None and os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass

    @staticmethod
    def _deadline(raw):
        try:
            if not isinstance(raw, str):
                return None
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return value.astimezone(timezone.utc) if value.tzinfo is not None else None
        except (ValueError, OverflowError):
            return None

    def _parse_retry_after(self, raw):
        if not isinstance(raw, str):
            return None
        try:
            raw = raw.strip()
            if re.fullmatch(r"[0-9]{1,10}", raw):
                return self.clock() + timedelta(seconds=int(raw))
            deadline = parsedate_to_datetime(raw)
            if deadline.tzinfo is None:
                return None
            deadline = deadline.astimezone(timezone.utc)
            return deadline if deadline > self.clock() else None
        except (ValueError, TypeError, OverflowError):
            return None

    def _record_outcome(self, usage, category):
        # Fixed categories only; never exception messages, URLs or payloads.
        outcomes = usage["outcomes_this_run"]
        outcomes[category] = outcomes.get(category, 0) + 1
        if category != "SUCCESS":
            usage["last_failure_at"] = self.clock().isoformat()

    def note_official_outage(self, host):
        """Record a recognized provider outage hidden behind an HTTP 200 page."""
        if host != "www150.statcan.gc.ca":
            return
        usage = self.usage.get(host)
        if isinstance(usage, dict):
            # HTTP outcome remains recorded as SUCCESS; this is the separately
            # validated content status shown to the operator.
            usage["status"] = "SOURCE_UNAVAILABLE"
            usage["data_status"] = "OFFICIAL_OUTAGE_PAGE"
            usage["last_failure_at"] = self.clock().isoformat()
            self._persist_usage(host)

    def get(self, url, **kwargs):
        return self.request("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("post", url, **kwargs)

    def request(self, method, url, *, before_request=None, after_request=None, **kwargs):
        if os.environ.get("FX_COLLECTOR") != "1":
            raise http.RequestException("LIVE_DATA_IS_COLLECTED_CENTRALLY")
        host = urlparse(url).hostname or "unknown"
        if (host == "rbnz.govt.nz" or host.endswith(".rbnz.govt.nz")) and os.environ.get("FX_RBNZ_AUTOMATION_APPROVED") != "1":
            raise http.RequestException("PROVIDER_AUTOMATION_PERMISSION_REQUIRED")
        if credential_rotation_required(host):
            raise http.RequestException("CREDENTIAL_ROTATION_REQUIRED")
        # No raw URLs, parameters, tokens, response bodies or credential hashes
        # are exported. Hashes exist only within this process for deduplication.
        identity = hashlib.sha256(json.dumps([method, url, kwargs], sort_keys=True, default=str).encode()).hexdigest()
        if identity in self.responses:
            result = self.responses[identity]
            if result is None:
                raise http.RequestException("PROVIDER_REQUEST_FAILED")
            return result
        if self.durable:
            self._restore_retry_after()
        usage = self.usage.setdefault(host, {"requests_this_run": 0, "status": "NOT_CHECKED",
                                           "remaining": None, "limit": None, "reset_at": None,
                                           "budget_evidence": "unknown", "outcomes_this_run": {}})
        deadline = self.retry_after.get(host)
        if deadline and deadline > self.clock():
            usage.update(status="PROVIDER_COOLDOWN", retry_after_at=deadline.isoformat())
            raise http.RequestException("PROVIDER_COOLDOWN")
        if deadline:
            self.retry_after.pop(host, None)
            usage.pop("retry_after_at", None)
        if host in self.cooldown:
            raise http.RequestException("PROVIDER_COOLDOWN")
        if usage["requests_this_run"] >= 80:
            usage["status"] = "RUN_SAFETY_LIMIT"
            self.cooldown.add(host)
            raise http.RequestException("RUN_SAFETY_LIMIT")
        attempted_at = self.clock()
        if before_request is not None:
            before_request(attempted_at)
        usage["last_attempt_at"] = attempted_at.isoformat()
        usage["status"] = "REQUEST_RESERVED"
        self._persist_usage(host, reserved=True, at=attempted_at)
        usage["requests_this_run"] += 1
        self.responses[identity] = None
        try:
            response = getattr(self.client, method)(url, **kwargs)
        except http.RequestException as error:
            usage["status"] = "NETWORK_ERROR"
            category = ("TLS_ERROR" if isinstance(error, http.exceptions.SSLError) else
                        "TIMEOUT" if isinstance(error, http.exceptions.Timeout) else
                        "CONNECTION_ERROR" if isinstance(error, http.exceptions.ConnectionError) else
                        "NETWORK_ERROR")
            self._record_outcome(usage, category)
            self._persist_usage(host, completed=True, at=attempted_at)
            if after_request is not None:
                after_request(attempted_at)
            # Preserve only safe transient categories for a bounded same-series
            # alternate transport. Never carry request/response objects or URLs.
            if isinstance(error, http.exceptions.SSLError):
                raise http.RequestException("PROVIDER_REQUEST_FAILED") from None
            if isinstance(error, http.exceptions.Timeout):
                raise http.exceptions.Timeout("PROVIDER_REQUEST_FAILED") from None
            if isinstance(error, http.exceptions.ConnectionError):
                raise http.exceptions.ConnectionError("PROVIDER_REQUEST_FAILED") from None
            raise http.RequestException("PROVIDER_REQUEST_FAILED") from None
        usage["last_checked_at"] = self.clock().isoformat()
        usage["status"] = "SUCCESS" if 200 <= response.status_code < 300 else "HTTP_" + str(response.status_code)
        self._record_outcome(usage, usage["status"])
        if response.status_code in (429, 503):
            deadline = self._parse_retry_after(response.headers.get("Retry-After"))
            if deadline:
                self.retry_after[host] = deadline
                usage["retry_after_at"] = deadline.isoformat()
            else:
                self.cooldown.add(host)
        if response.status_code in (401, 403):
            self.cooldown.add(host)
        # In particular, retain a confirmed Retry-After before the caller
        # parses a payload or reaches the collector's final save_status.
        self._persist_usage(host, completed=True, at=attempted_at)
        if after_request is not None:
            after_request(attempted_at)
        if response.status_code >= 400:
            # Existing UI debug code must not echo credentials from an error body.
            clean = http.Response()
            clean.status_code = response.status_code
            clean._content = b""
            clean.url = "https://" + host
            response = clean
        self.responses[identity] = response
        return response


transport = CollectorTransport()

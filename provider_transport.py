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


class CollectorTransport:
    RequestException = http.RequestException
    exceptions = http.exceptions

    def __init__(self, client=None, status_path="data_collection_status.json", clock=None):
        self.client = client or http
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.responses = {}
        self.usage = {}
        self.cooldown = set()
        self.retry_after = {}
        try:
            providers = json.loads(Path(status_path).read_text()).get("providers", {})
            for host, info in providers.items():
                if not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+", host) or not isinstance(info, dict):
                    continue
                deadline = self._deadline(info.get("retry_after_at"))
                if deadline and deadline > self.clock():
                    self.retry_after[host] = deadline
        except (OSError, ValueError, TypeError, AttributeError):
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

    def get(self, url, **kwargs):
        return self.request("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("post", url, **kwargs)

    def request(self, method, url, **kwargs):
        if os.environ.get("FX_COLLECTOR") != "1":
            raise http.RequestException("LIVE_DATA_IS_COLLECTED_CENTRALLY")
        host = urlparse(url).hostname or "unknown"
        if (host == "rbnz.govt.nz" or host.endswith(".rbnz.govt.nz")) and os.environ.get("FX_RBNZ_AUTOMATION_APPROVED") != "1":
            raise http.RequestException("PROVIDER_AUTOMATION_PERMISSION_REQUIRED")
        compromised = {"fcsapi.com", "alphavantage.co", "benzinga.com",
                       "financialmodelingprep.com", "stockdata.org"}
        rotated = set(os.environ.get("FX_ROTATED_PROVIDER_HOSTS", "").split(","))
        compromised_root = next((root for root in compromised if host == root or host.endswith("." + root)), None)
        if compromised_root and host not in rotated and compromised_root not in rotated:
            raise http.RequestException("CREDENTIAL_ROTATION_REQUIRED")
        # No raw URLs, parameters, tokens, response bodies or credential hashes
        # are exported. Hashes exist only within this process for deduplication.
        identity = hashlib.sha256(json.dumps([method, url, kwargs], sort_keys=True, default=str).encode()).hexdigest()
        if identity in self.responses:
            result = self.responses[identity]
            if result is None:
                raise http.RequestException("PROVIDER_REQUEST_FAILED")
            return result
        usage = self.usage.setdefault(host, {"requests_this_run": 0, "status": "NOT_CHECKED",
                                           "remaining": None, "limit": None, "reset_at": None,
                                           "budget_evidence": "unknown"})
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
        usage["requests_this_run"] += 1
        self.responses[identity] = None
        try:
            response = getattr(self.client, method)(url, **kwargs)
        except http.RequestException:
            usage["status"] = "NETWORK_ERROR"
            raise http.RequestException("PROVIDER_REQUEST_FAILED") from None
        usage["last_checked_at"] = self.clock().isoformat()
        usage["status"] = "SUCCESS" if 200 <= response.status_code < 300 else "HTTP_" + str(response.status_code)
        if response.status_code in (429, 503):
            deadline = self._parse_retry_after(response.headers.get("Retry-After"))
            if deadline:
                self.retry_after[host] = deadline
                usage["retry_after_at"] = deadline.isoformat()
            else:
                self.cooldown.add(host)
        if response.status_code in (401, 403):
            self.cooldown.add(host)
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

"""Collector-only HTTP with run deduplication and secret-free usage telemetry."""
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests as http


class CollectorTransport:
    RequestException = http.RequestException
    exceptions = http.exceptions

    def __init__(self, client=None):
        self.client = client or http
        self.responses = {}
        self.usage = {}
        self.cooldown = set()

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
        if host in self.cooldown:
            raise http.RequestException("PROVIDER_COOLDOWN")
        usage = self.usage.setdefault(host, {"requests_this_run": 0, "status": "NOT_CHECKED",
                                           "remaining": None, "limit": None, "reset_at": None,
                                           "budget_evidence": "unknown"})
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
        usage["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        usage["status"] = "SUCCESS" if 200 <= response.status_code < 300 else "HTTP_" + str(response.status_code)
        if response.status_code in (401, 403, 429):
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

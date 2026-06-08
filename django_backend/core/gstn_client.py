"""Minimal, dependency-free client for a GST Suvidha Provider (WhiteBooks) GSTN API.

Standard library only (urllib + json). Covers authentication (with token caching
on the GstnApiConfig row), GSTIN validation, and e-Invoice IRN generate / get /
cancel. All credentials are read from a GstnApiConfig instance (encrypted at
rest) — nothing is hard-coded.

Auth token validity per the GSP docs: ~1 hour (sandbox), ~6 hours (production).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_TIMEOUT = 30
SANDBOX_TOKEN_TTL = 55 * 60          # ~1 hour, with a safety buffer
PRODUCTION_TOKEN_TTL = 5 * 60 * 60   # ~6 hours, with a safety buffer

SUCCESS_STATUSES = {"1", "Sucess", "Success", "success"}


class GstnApiError(Exception):
    """Raised when a GSTN/GSP API call fails or returns a non-success status."""


def token_ttl(config) -> int:
    return PRODUCTION_TOKEN_TTL if config.mode == "production" else SANDBOX_TOKEN_TTL


def base_headers(config) -> dict[str, str]:
    return {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "username": config.username,
        "ip_address": config.ip_address or "127.0.0.1",
        "gstin": config.gstin,
    }


def parse_envelope(raw: str) -> dict[str, Any]:
    """Parse the GSP response envelope {status_cd, status_desc, data, error}.

    Returns the inner ``data`` object on success, raising GstnApiError otherwise.
    """
    try:
        payload = json.loads(raw) if raw else {}
    except (ValueError, TypeError) as exc:
        raise GstnApiError(f"GSTN API returned a non-JSON response: {raw[:200]}") from exc
    status = str(payload.get("status_cd", "")).strip()
    if status and status not in SUCCESS_STATUSES:
        error = payload.get("error") or payload.get("status_desc") or payload
        raise GstnApiError(f"GSTN API error: {json.dumps(error)[:300]}")
    data = payload.get("data", payload)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            pass
    return data if isinstance(data, dict) else {"data": data}


def _request(config, method: str, path: str, *, headers=None, query=None, body=None) -> dict[str, Any]:
    if not config.base_url:
        raise GstnApiError("GSTN API base URL is not configured.")
    params = {"email": config.api_email}
    if query:
        params.update({key: value for key, value in query.items() if value is not None})
    url = f"{config.base_url.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        if value is not None:
            request.add_header(key, str(value))
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # pragma: no cover - best effort
            detail = str(exc)
        raise GstnApiError(f"GSTN API {method} {path} failed ({exc.code}): {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise GstnApiError(f"GSTN API {method} {path} unreachable: {exc.reason}") from exc
    return parse_envelope(raw)


def authenticate(config, *, force: bool = False) -> str:
    """Return a valid auth token, reusing the cached one until it expires."""
    if not force and config.token_valid:
        return config.auth_token
    headers = base_headers(config)
    headers["password"] = config.password
    data = _request(config, "GET", "/einvoice/authenticate", headers=headers)
    token = data.get("AuthToken") or data.get("authToken") or data.get("auth_token") or data.get("token")
    if not token:
        raise GstnApiError(f"Authentication did not return a token: {json.dumps(data)[:200]}")
    config.store_token(str(token), token_ttl(config))
    return str(token)


def authed_headers(config) -> dict[str, str]:
    headers = base_headers(config)
    headers["auth-token"] = authenticate(config)
    return headers


def get_gstin_details(config, gstin: str) -> dict[str, Any]:
    """Validate / fetch details for a GSTIN (read-only, lowest-risk first feature)."""
    return _request(
        config, "GET", "/einvoice/type/GSTNDETAILS/version/V1_03",
        headers=authed_headers(config), query={"param1": gstin},
    )


def generate_irn(config, einvoice_body: dict) -> dict[str, Any]:
    """Generate an Invoice Reference Number (IRN) from an e-invoice JSON body."""
    return _request(
        config, "POST", "/einvoice/type/GENERATE/version/V1_03",
        headers=authed_headers(config), body=einvoice_body,
    )


def get_irn(config, irn: str) -> dict[str, Any]:
    return _request(
        config, "GET", "/einvoice/type/GETIRN/version/V1_03",
        headers=authed_headers(config), query={"param1": irn},
    )


def cancel_irn(config, irn: str, reason_code: str = "1", remark: str = "Cancelled") -> dict[str, Any]:
    body = {"Irn": irn, "CnlRsn": str(reason_code), "CnlRem": remark}
    return _request(
        config, "POST", "/einvoice/type/CANCEL/version/V1_03",
        headers=authed_headers(config), body=body,
    )

"""Minimal, dependency-free Razorpay client.

Uses only the Python standard library (urllib + hmac + hashlib + base64) so the
project needs no extra pip package. Covers the subset required for recurring
subscription billing: create/fetch a plan, create/fetch/cancel a subscription,
and verify webhook + subscription-payment signatures.

Razorpay REST docs: https://razorpay.com/docs/api/
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from typing import Any

API_BASE = "https://api.razorpay.com/v1"
_TIMEOUT = 20


class RazorpayError(Exception):
    """Raised when a Razorpay API call fails or returns a non-2xx status."""


def _auth_header(key_id: str, key_secret: str) -> str:
    token = base64.b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _request(config, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    key_id = config.key_id
    key_secret = config.key_secret
    if not (key_id and key_secret):
        raise RazorpayError("Razorpay gateway is not configured (missing key id/secret).")

    url = f"{API_BASE}/{path.lstrip('/')}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", _auth_header(key_id, key_secret))
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:  # 4xx / 5xx
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:  # pragma: no cover - best effort
            detail = str(exc)
        raise RazorpayError(f"Razorpay API {method} {path} failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:  # network/DNS
        raise RazorpayError(f"Razorpay API {method} {path} unreachable: {exc.reason}") from exc


def ensure_monthly_plan(config) -> str:
    """Return a Razorpay plan id for the monthly Pro plan, creating it once and caching on the config."""
    if config.razorpay_plan_id:
        return config.razorpay_plan_id
    payload = {
        "period": "monthly",
        "interval": 1,
        "item": {
            "name": "RozLedger Pro (monthly)",
            "amount": int(config.subscription_amount),
            "currency": config.subscription_currency,
            "description": "RozLedger Pro recurring subscription",
        },
    }
    plan = _request(config, "POST", "plans", payload)
    plan_id = plan.get("id", "")
    if not plan_id:
        raise RazorpayError("Razorpay did not return a plan id.")
    config.razorpay_plan_id = plan_id
    config.save(update_fields=["razorpay_plan_id", "updated_at"])
    return plan_id


def create_subscription(config, plan_id: str, *, total_count: int = 120, notes: dict[str, str] | None = None) -> dict[str, Any]:
    """Create a recurring subscription and return the Razorpay subscription object (incl. short_url)."""
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "total_count": total_count,
        "customer_notify": 1,
    }
    if notes:
        payload["notes"] = notes
    return _request(config, "POST", "subscriptions", payload)


def fetch_subscription(config, subscription_id: str) -> dict[str, Any]:
    return _request(config, "GET", f"subscriptions/{subscription_id}")


def cancel_subscription(config, subscription_id: str, *, cancel_at_cycle_end: bool = False) -> dict[str, Any]:
    payload = {"cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0}
    return _request(config, "POST", f"subscriptions/{subscription_id}/cancel", payload)


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify the X-Razorpay-Signature header against the raw request body."""
    if not (signature and secret):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_subscription_payment_signature(
    payment_id: str, subscription_id: str, signature: str, key_secret: str
) -> bool:
    """Verify the checkout-callback signature for a subscription payment.

    Razorpay signs ``{payment_id}|{subscription_id}`` with the key secret.
    """
    if not (payment_id and subscription_id and signature and key_secret):
        return False
    message = f"{payment_id}|{subscription_id}".encode("utf-8")
    expected = hmac.new(key_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

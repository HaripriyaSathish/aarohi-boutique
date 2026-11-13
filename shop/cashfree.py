"""Cashfree Payment Gateway (PG API 2023-08-01) — sandbox/test and production.

Flow:
1. `ensure_cashfree_order(order)` creates a Cashfree order and returns a
   payment_session_id for the browser JS SDK (sandbox keys never charge money).
2. The customer pays on Cashfree's hosted checkout and is redirected back to
   `/payment/<pk>/cashfree/return/`.
3. `verify_cashfree(order)` asks Cashfree's server for the real order/payment
   status and amount before marking the order paid. Browser data is never trusted.
4. `/payments/cashfree/webhook/` handles PAYMENT_SUCCESS webhooks (HMAC verified).
"""

import base64
import hashlib
import hmac
import logging
import time
from decimal import Decimal

import requests
from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)
API_VERSION = "2023-08-01"


class CashfreeError(Exception):
    pass


def base_url():
    return (
        "https://api.cashfree.com/pg"
        if settings.CASHFREE_ENV == "production"
        else "https://sandbox.cashfree.com/pg"
    )


def request(method, path, data=None, idempotency=None):
    if not settings.CASHFREE_APP_ID or not settings.CASHFREE_SECRET_KEY:
        raise CashfreeError("Cashfree keys are not configured. Please contact the store.")
    headers = {
        "x-client-id": settings.CASHFREE_APP_ID,
        "x-client-secret": settings.CASHFREE_SECRET_KEY,
        "x-api-version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if idempotency:
        headers["x-idempotency-key"] = str(idempotency)
    try:
        response = requests.request(
            method, base_url() + path, json=data, headers=headers, timeout=20
        )
    except requests.RequestException as exc:
        logger.warning("Cashfree request failed: %s", type(exc).__name__)
        raise CashfreeError("Payment service unavailable. Please retry shortly.") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400:
        logger.warning("Cashfree %s %s -> %s %s", method, path, response.status_code, body)
        raise CashfreeError(body.get("message") or "Cashfree rejected the request.")
    return body


def cashfree_order_id(order):
    # Unique, stable and <= 45 chars. Re-used on retries so we never double-create.
    return "AB_" + order.pk.hex


def ensure_cashfree_order(order, request_obj):
    """Create (or re-read) the Cashfree order; returns payment_session_id."""
    oid = order.provider_order or cashfree_order_id(order)
    if order.provider_order:
        existing = request("GET", f"/orders/{oid}")
        if existing.get("order_status") == "ACTIVE" and existing.get("payment_session_id"):
            return existing["payment_session_id"]
        if existing.get("order_status") == "PAID":
            return ""
    return_url = request_obj.build_absolute_uri(
        reverse("cashfree_return", args=[order.pk])
    ) + "?order_id={order_id}"
    meta = {"return_url": return_url}
    notify = request_obj.build_absolute_uri(reverse("cashfree_webhook"))
    if notify.startswith("https://"):
        meta["notify_url"] = notify  # Cashfree only calls public HTTPS URLs
    name = (order.delivery_data or {}).get("name") or order.user.get_full_name() or order.user.username
    phone = "".join(ch for ch in order.phone if ch.isdigit())[-10:] or "9999999999"
    payload = {
        "order_id": oid,
        "order_amount": float(order.total),
        "order_currency": "INR",
        "customer_details": {
            "customer_id": f"user_{order.user_id}",
            "customer_name": name[:100],
            "customer_email": order.email,
            "customer_phone": phone,
        },
        "order_meta": meta,
        "order_note": order.number,
        "order_tags": {"store_order": order.number},
    }
    if order.provider_order:
        # Existing order expired/terminated on Cashfree: use a fresh id.
        oid = f"{cashfree_order_id(order)}_{int(time.time())}"[:45]
        payload["order_id"] = oid
    created = request("POST", "/orders", payload)
    order.provider_order = created["order_id"]
    order.save(update_fields=["provider_order"])
    return created["payment_session_id"]


def verify_cashfree(order):
    """Mark paid only if Cashfree's server says this exact order/amount is paid."""
    from .services import mark_paid, ShopError

    if not order.provider_order:
        raise ShopError("Cashfree order is missing.")
    try:
        cf = request("GET", f"/orders/{order.provider_order}")
        if cf.get("order_status") != "PAID":
            raise ShopError("Payment is not completed yet. If money was debited, refresh in a minute — do not pay twice.")
        if Decimal(str(cf.get("order_amount"))) != order.total or cf.get("order_currency") != "INR":
            raise ShopError("Payment amount mismatch. Please contact support.")
        payments = request("GET", f"/orders/{order.provider_order}/payments")
    except CashfreeError as exc:
        raise ShopError(str(exc)) from exc
    success = next((p for p in payments if p.get("payment_status") == "SUCCESS"), None)
    if not success:
        raise ShopError("No successful payment found for this order yet.")
    return mark_paid(order.pk, "cf_" + str(success.get("cf_payment_id")))


def valid_webhook(raw_body, timestamp, signature):
    if not (timestamp and signature):
        return False
    digest = hmac.new(
        settings.CASHFREE_SECRET_KEY.encode(), timestamp.encode() + raw_body, hashlib.sha256
    ).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def refund(order, amount, refund_id, note):
    data = request(
        "POST",
        f"/orders/{order.provider_order}/refunds",
        {"refund_amount": float(amount), "refund_id": refund_id, "refund_note": note[:100]},
        idempotency=refund_id,
    )
    return data.get("refund_id", refund_id), data.get("refund_status", "PENDING")


def refund_status(order, refund_id):
    return request("GET", f"/orders/{order.provider_order}/refunds/{refund_id}").get(
        "refund_status", "PENDING"
    )

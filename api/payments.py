"""Cashfree hosted checkout for the mobile app.

Works inside Expo Go (no native SDK needed):

1. App calls POST /api/orders/<id>/pay/ with its deep link (e.g. exp://.../--/payment-result).
2. API creates/reuses the Cashfree order and returns a short-lived signed
   `checkout_url` (/api/pay/start/?t=...).
3. App opens that URL in an in-app browser (expo-web-browser). The page loads
   Cashfree's JS SDK and opens the real Cashfree checkout (sandbox or production).
4. Cashfree redirects to /api/pay/return/<id>/ which verifies the payment
   server-to-server and sends the browser back to the app deep link.
5. App calls POST /api/orders/<id>/verify/ and shows success / failure.

The website's own Cashfree flow (shop/cashfree.py) is not modified.
"""

import time
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.core import signing
from django.urls import reverse

from shop.cashfree import CashfreeError, cashfree_order_id, request as cf_request

SIGN_SALT = "aarohi.mobile.pay"
RETURN_COOKIE = "aarohi_app_return"
ALLOWED_SCHEMES = {"exp", "exps", "vetri", "aarohi"}  # Expo Go dev links + the standalone app scheme


def cashfree_mode():
    return "production" if settings.CASHFREE_ENV == "production" else "sandbox"


def safe_app_redirect(url):
    """Only allow deep links back into our own app (prevents open redirects)."""
    if not url:
        return ""
    parsed = urlparse(url)
    return url if parsed.scheme in ALLOWED_SCHEMES else ""


def android_intent_url(app_url):
    """exp://host/--/path?x=1  ->  intent://host/--/path?x=1#Intent;scheme=exp;end

    Chrome (and Chrome Custom Tabs) only hand custom-scheme links to apps in this
    form; it opens Expo Go during development and the Vetri app once installed.
    """
    parsed = urlparse(app_url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return ""
    rest = app_url.split("://", 1)[1]
    return f"intent://{rest}#Intent;scheme={parsed.scheme};end"


def ensure_mobile_cashfree_order(order, http_request):
    """Create (or re-use) the Cashfree order; return payment_session_id ('' if already paid)."""
    if order.provider_order:
        existing = cf_request("GET", f"/orders/{order.provider_order}")
        if existing.get("order_status") == "ACTIVE" and existing.get("payment_session_id"):
            return existing["payment_session_id"]
        if existing.get("order_status") == "PAID":
            return ""
    return_url = http_request.build_absolute_uri(
        reverse("api:pay_return", args=[order.pk])
    ) + "?order_id={order_id}"
    meta = {"return_url": return_url}
    notify = http_request.build_absolute_uri(reverse("cashfree_webhook"))
    if notify.startswith("https://"):
        meta["notify_url"] = notify  # existing website webhook; Cashfree only calls public HTTPS
    name = (order.delivery_data or {}).get("name") or order.user.get_full_name() or order.user.username
    phone = "".join(ch for ch in order.phone if ch.isdigit())[-10:] or "9999999999"
    oid = cashfree_order_id(order)
    if order.provider_order:
        # Previous Cashfree order expired/terminated: use a fresh id.
        oid = f"{oid}_{int(time.time())}"[:45]
    created = cf_request(
        "POST",
        "/orders",
        {
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
            "order_tags": {"store_order": order.number, "channel": "mobile_app"},
        },
    )
    order.provider_order = created["order_id"]
    order.save(update_fields=["provider_order"])
    return created["payment_session_id"]


def checkout_url(http_request, order, session_id, app_redirect):
    token = signing.dumps(
        {"o": str(order.pk), "s": session_id, "r": safe_app_redirect(app_redirect)},
        salt=SIGN_SALT,
        compress=True,
    )
    return http_request.build_absolute_uri(reverse("api:pay_start")) + "?" + urlencode({"t": token})


def read_checkout_token(token, max_age=30 * 60):
    return signing.loads(token, salt=SIGN_SALT, max_age=max_age)


__all__ = [
    "CashfreeError",
    "RETURN_COOKIE",
    "cashfree_mode",
    "checkout_url",
    "ensure_mobile_cashfree_order",
    "read_checkout_token",
    "safe_app_redirect",
]

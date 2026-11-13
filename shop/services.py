from datetime import timedelta
from decimal import Decimal
import hashlib
import hmac
import logging
import requests
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import (
    Coupon,
    Order,
    OrderEvent,
    OrderItem,
    ReturnRequest,
    StoreSettings,
    Variant,
)

logger = logging.getLogger(__name__)


class ShopError(Exception):
    pass


def cart_lines(cart):
    lines = []
    for key, quantity in cart.items():
        try:
            variant = Variant.objects.select_related("product").get(
                pk=int(key), product__active=True, product__category__active=True
            )
            quantity = int(quantity)
        except (Variant.DoesNotExist, ValueError, TypeError):
            raise ShopError("An item is no longer available. Remove it from your bag.")
        if quantity < 1 or quantity > 10:
            raise ShopError("Choose between 1 and 10 of each item.")
        lines.append(
            {
                "variant": variant,
                "quantity": quantity,
                "total": variant.product.price * quantity,
            }
        )
    return lines


def quote(cart, coupon_code=""):
    lines = cart_lines(cart)
    subtotal = sum((line["total"] for line in lines), Decimal("0.00"))
    discount = Decimal("0.00")
    coupon = None
    if coupon_code:
        coupon = Coupon.objects.filter(
            code__iexact=coupon_code.strip(),
            active=True,
            starts__lte=timezone.now(),
            expires__gt=timezone.now(),
            minimum__lte=subtotal,
            uses__lt=F("max_uses"),
        ).first()
        if not coupon:
            raise ShopError(
                "Coupon is invalid, expired, fully used, or below its minimum spend."
            )
        discount = (subtotal * coupon.percent / 100).quantize(Decimal("0.01"))
    store = StoreSettings.objects.first()
    shipping = Decimal("0.00")
    if lines and store and subtotal - discount < store.free_shipping_over:
        shipping = store.shipping_fee
    return {
        "lines": lines,
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "total": subtotal - discount + shipping,
        "coupon": coupon,
        "shipping_remaining": (
            max(Decimal("0.00"), store.free_shipping_over - subtotal + discount)
            if store
            else Decimal("0.00")
        ),
        "shipping_progress": (
            min(100, (subtotal - discount) / store.free_shipping_over * 100)
            if store and store.free_shipping_over
            else 100
        ),
    }


@transaction.atomic
def create_order(user, cart, coupon_code, cleaned):
    amounts = quote(cart, coupon_code)
    if not amounts["lines"]:
        raise ShopError("Your shopping bag is empty.")
    # The conditional database update prevents two checkouts spending the same stock.
    for line in amounts["lines"]:
        changed = Variant.objects.filter(
            pk=line["variant"].pk, stock__gte=line["quantity"]
        ).update(stock=F("stock") - line["quantity"])
        if not changed:
            raise ShopError(f"Not enough stock for {line['variant']}.")
    coupon = amounts["coupon"]
    if coupon:
        claimed = Coupon.objects.filter(pk=coupon.pk, uses__lt=F("max_uses")).update(
            uses=F("uses") + 1
        )
        if not claimed:
            raise ShopError("This coupon has just reached its usage limit.")
    address = "\n".join(
        filter(
            None,
            [
                cleaned["name"],
                cleaned["line1"],
                cleaned.get("line2"),
                cleaned["city"] + ", " + cleaned["state"] + " " + cleaned["postcode"],
                "India",
            ],
        )
    )
    order = Order.objects.create(
        user=user,
        email=cleaned["email"],
        phone=cleaned["phone"],
        shipping_address=address,
        delivery_data={
            key: cleaned.get(key, "")
            for key in ["name", "line1", "line2", "city", "state", "postcode"]
        },
        subtotal=amounts["subtotal"],
        discount=amounts["discount"],
        shipping=amounts["shipping"],
        total=amounts["total"],
        coupon=coupon,
        payment_mode=settings.PAYMENT_MODE,
        expires_at=timezone.now() + timedelta(minutes=30),
    )
    for line in amounts["lines"]:
        variant = line["variant"]
        OrderItem.objects.create(
            order=order,
            variant=variant,
            name=variant.product.name,
            size=variant.size,
            colour=variant.colour,
            quantity=line["quantity"],
            price=variant.product.price,
            unit_cost=variant.product.cost_price,
        )
    OrderEvent.objects.create(
        order=order,
        message="Order created. Stock reserved for 30 minutes; awaiting payment.",
    )
    return order


def notify_order(order_id):
    order = Order.objects.get(pk=order_id)
    try:
        from .emails import send_order_confirmation

        send_order_confirmation(order)
    except Exception:
        logger.exception("Order confirmation email failed for %s", order.number)


@transaction.atomic
def mark_paid(order_id, payment_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.payment_status != Order.Payment.PENDING:
        return order
    order.provider_payment = payment_id
    if order.status == Order.Status.CANCELLED or order.stock_released:
        order.payment_status = Order.Payment.REFUND_DUE
        message = "Late payment received after cancellation. Staff must refund; order will not be fulfilled."
    else:
        order.payment_status = Order.Payment.PAID
        order.status = Order.Status.CONFIRMED
        message = "Payment verified. Your order is confirmed."
        # Durable jobs created by model signals handle confirmation notifications.
    order.save()
    OrderEvent.objects.create(order=order, message=message)
    return order


@transaction.atomic
def cancel_order(order_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status == Order.Status.CANCELLED:
        return order
    if order.status not in [Order.Status.PENDING, Order.Status.CONFIRMED]:
        raise ShopError("This order has entered fulfillment and cannot be cancelled.")
    if not order.stock_released:
        for item in order.items.all():
            Variant.objects.filter(pk=item.variant_id).update(
                stock=F("stock") + item.quantity
            )
        if order.coupon_id:
            Coupon.objects.filter(pk=order.coupon_id, uses__gt=0).update(
                uses=F("uses") - 1
            )
        order.stock_released = True
    order.status = Order.Status.CANCELLED
    if order.payment_status == Order.Payment.PAID:
        order.payment_status = Order.Payment.REFUND_DUE
    order.save()
    OrderEvent.objects.create(
        order=order,
        message="Order cancelled. Any collected payment awaits staff refund processing.",
    )
    return order


def gateway(method, path, data=None, idempotency=None):
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise ShopError(
            "Payment gateway keys are not configured. Please contact the store."
        )
    headers = {}
    if idempotency:
        headers["X-Refund-Idempotency"] = str(idempotency)
    try:
        response = requests.request(
            method,
            "https://api.razorpay.com/v1/" + path,
            json=data,
            headers=headers,
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Gateway request failed: %s", type(exc).__name__)
        raise ShopError(
            "Payment service unavailable. Please retry or contact support."
        ) from exc


def ensure_gateway_order(order):
    if order.provider_order:
        return order.provider_order
    result = gateway(
        "POST",
        "orders",
        {
            "amount": int(order.total * 100),
            "currency": "INR",
            "receipt": order.number,
        },
    )
    order.provider_order = result["id"]
    order.save(update_fields=["provider_order"])
    return order.provider_order


def verify_payment(order, payment_id, signature):
    if not order.provider_order:
        raise ShopError("Gateway order is missing.")
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        f"{order.provider_order}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ShopError("Payment signature verification failed.")
    payment = gateway("GET", "payments/" + payment_id)
    if (
        payment.get("order_id") != order.provider_order
        or payment.get("amount") != int(order.total * 100)
        or payment.get("currency") != "INR"
        or payment.get("status") != "captured"
    ):
        raise ShopError(
            "Payment is not captured yet. Refresh your order shortly; do not pay twice."
        )
    return mark_paid(order.pk, payment_id)


def request_refund(order):
    if order.payment_status != Order.Payment.REFUND_DUE:
        raise ShopError("Only orders marked Refund required can be refunded.")
    if order.payment_mode == "demo" or order.total == 0:
        order.payment_status = Order.Payment.REFUNDED
        order.provider_refund = "demo-" + str(order.pk)
    elif order.payment_mode == "cashfree":
        from .cashfree import CashfreeError, refund

        try:
            rid, state = refund(order, order.total, "rf_" + order.pk.hex[:30], "Order cancelled " + order.number)
        except CashfreeError as exc:
            raise ShopError(str(exc)) from exc
        order.provider_refund = rid
        order.payment_status = (
            Order.Payment.REFUNDED if state == "SUCCESS" else Order.Payment.REFUND_PENDING
        )
    else:
        result = gateway(
            "POST",
            "payments/" + order.provider_payment + "/refund",
            {"amount": int(order.total * 100), "notes": {"order": order.number}},
            idempotency=order.pk,
        )
        order.provider_refund = result["id"]
        order.payment_status = (
            Order.Payment.REFUNDED
            if result.get("status") == "processed"
            else Order.Payment.REFUND_PENDING
        )
    order.save(update_fields=["payment_status", "provider_refund"])
    OrderEvent.objects.create(
        order=order, message="Refund submitted: " + order.get_payment_status_display()
    )


def sync_refund(order):
    if order.payment_mode == "demo" or not order.provider_refund:
        return
    if order.payment_mode == "cashfree":
        from .cashfree import refund_status

        if refund_status(order, order.provider_refund) == "SUCCESS":
            order.payment_status = Order.Payment.REFUNDED
            order.save(update_fields=["payment_status"])
        return
    result = gateway("GET", "refunds/" + order.provider_refund)
    if result.get("status") == "processed":
        order.payment_status = Order.Payment.REFUNDED
        order.save(update_fields=["payment_status"])


def return_refund_amount(item):
    # Allocate the order discount exactly once across lines, with the remainder
    # on the last line. Delivery fees are non-refundable after delivery.
    order = item.order
    allocated = Decimal("0.00")
    items = list(order.items.order_by("pk"))
    for index, line in enumerate(items):
        if index == len(items) - 1:
            share = order.discount - allocated
        else:
            share = (order.discount * line.total / order.subtotal).quantize(
                Decimal("0.01")
            )
            allocated += share
        if line.pk == item.pk:
            return line.total - share
    raise ShopError("Order item not found.")


@transaction.atomic
def refund_return(return_id):
    result = (
        ReturnRequest.objects.select_for_update()
        .select_related("item__order")
        .get(pk=return_id)
    )
    if result.kind != "return" or result.status != "completed":
        raise ShopError("Receive and complete the return before refunding it.")
    if result.refund_status in ["processing", "refunded"]:
        return result
    order = Order.objects.select_for_update().get(pk=result.item.order_id)
    if order.status != "delivered" or order.payment_status not in [
        "paid",
        "partial_refund",
    ]:
        raise ShopError("Only paid delivered orders can receive item refunds.")
    amount = return_refund_amount(result.item)
    result.refund_amount = amount
    if order.payment_mode == "demo" or amount == 0:
        result.refund_id = "demo-return-" + str(result.pk)
        result.refund_status = "refunded"
    elif order.payment_mode == "cashfree":
        from .cashfree import CashfreeError, refund

        try:
            rid, state = refund(order, amount, f"rr_{result.pk}_{order.pk.hex[:12]}", "Return " + result.item.name)
        except CashfreeError as exc:
            raise ShopError(str(exc)) from exc
        result.refund_id = rid
        result.refund_status = "refunded" if state == "SUCCESS" else "processing"
    else:
        response = gateway(
            "POST",
            "payments/" + order.provider_payment + "/refund",
            {"amount": int(amount * 100), "notes": {"return_request": str(result.pk)}},
            idempotency="return-request-" + str(result.pk).zfill(10),
        )
        result.refund_id = response["id"]
        result.refund_status = (
            "refunded" if response.get("status") == "processed" else "processing"
        )
    result.save(update_fields=["refund_amount", "refund_id", "refund_status"])
    update_return_refund_state(order)
    OrderEvent.objects.create(
        order=order,
        message=f"Item refund {result.refund_status}: INR {amount} for {result.item.name}.",
    )
    return result


def update_return_refund_state(order):
    from django.db.models import Sum

    total = ReturnRequest.objects.filter(
        item__order=order, refund_status="refunded"
    ).aggregate(value=Sum("refund_amount"))["value"] or Decimal("0.00")
    if total:
        order.payment_status = "refunded" if total >= order.total else "partial_refund"
        order.save(update_fields=["payment_status"])


def sync_return_refund(result):
    if not result.refund_id or result.refund_status != "processing":
        return
    if result.item.order.payment_mode == "cashfree":
        from .cashfree import refund_status

        state = refund_status(result.item.order, result.refund_id)
        response = {"status": {"SUCCESS": "processed", "CANCELLED": "failed"}.get(state, "pending")}
    else:
        response = gateway("GET", "refunds/" + result.refund_id)
    if response.get("status") == "processed":
        result.refund_status = "refunded"
    elif response.get("status") == "failed":
        result.refund_status = "failed"
    result.save(update_fields=["refund_status"])
    update_return_refund_state(result.item.order)

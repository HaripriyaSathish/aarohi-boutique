"""Server-authored action proposals with ownership, permission and confirmation.

Language models may propose an allowlisted action; only the authenticated
confirmation POST executes it. All eligibility checks run again at execution.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.urls import reverse
from .models import (
    AgentAction,
    AgentJob,
    Order,
    OrderItem,
    ReturnRequest,
    Variant,
    Product,
    RestockPlan,
    WorkTask,
    Enquiry,
    StoreSettings,
)
from .services import ShopError, cancel_order, quote, refund_return, request_refund
from .automation import audit, complete_return, enqueue, task

from .business_actions import PERMISSIONS as BUSINESS_PERMISSIONS

CUSTOMER_ACTIONS = {
    "add_to_cart",
    "remove_from_cart",
    "apply_coupon",
    "cancel_order",
    "request_return",
    "request_exchange",
}
STAFF_PERMISSIONS = {
    "mark_packed": "shop.change_order",
    "approve_return": "shop.change_returnrequest",
    "complete_return": "shop.change_returnrequest",
    "refund_order": "shop.change_order",
    "refund_return": "shop.change_returnrequest",
    "receive_stock": "shop.change_variant",
    "refresh_tracking": "shop.view_shipment",
    "retry_job": "shop.change_agentjob",
    "update_description": "shop.change_product",
    "update_price": "shop.change_product",
    "resolve_enquiry": "shop.change_enquiry",
}

STAFF_PERMISSIONS.update(BUSINESS_PERMISSIONS)


def check_access(user, kind):
    if not user.is_authenticated:
        raise PermissionDenied
    if kind in STAFF_PERMISSIONS:
        if not user.is_staff or not user.has_perm(STAFF_PERMISSIONS[kind]):
            raise PermissionDenied
    elif kind not in CUSTOMER_ACTIONS:
        raise ShopError("This action is not supported.")


def owned_order(user, reference, staff=False):
    orders = Order.objects.all() if staff else Order.objects.filter(user=user)
    value = str(reference).strip().upper()
    try:
        return orders.get(pk=value)
    except (ValueError, Order.DoesNotExist, ValidationError):
        pass
    result = next(
        (
            order
            for order in orders.order_by("-created")[:1000]
            if order.number == value
        ),
        None,
    )
    if not result:
        raise ShopError("Order not found in the permitted account scope.")
    return result


def validate_action(request, kind, payload):
    user = request.user
    check_access(user, kind)
    if kind in BUSINESS_PERMISSIONS:
        from .business_actions import validate

        return validate(request, kind, payload)
    target = str(payload.get("target", "")).strip()
    numeric_actions = {
        "add_to_cart",
        "remove_from_cart",
        "request_return",
        "request_exchange",
        "approve_return",
        "complete_return",
        "refund_return",
        "receive_stock",
        "refresh_tracking",
        "update_description",
        "update_price",
        "resolve_enquiry",
    }
    if kind in numeric_actions:
        try:
            target = str(int(target))
        except (ValueError, TypeError):
            raise ShopError("Choose a valid numeric target ID.")
    value = str(payload.get("value", "")).strip()
    extra = str(payload.get("extra", "")).strip()
    try:
        quantity = int(payload.get("quantity", 1))
    except (ValueError, TypeError):
        raise ShopError("Enter a valid quantity.")
    if kind in ["add_to_cart", "remove_from_cart"]:
        try:
            variant = Variant.objects.select_related("product").get(
                pk=int(target), product__active=True, product__category__active=True
            )
        except (Variant.DoesNotExist, ValueError):
            raise ShopError("Choose a valid available variant.")
        if kind == "add_to_cart":
            total = (
                int(request.session.get("cart", {}).get(str(variant.pk), 0)) + quantity
            )
            if not 1 <= quantity <= 10 or total > 10 or total > variant.stock:
                raise ShopError(
                    "Quantity exceeds the available stock or per-item limit."
                )
            return f"Add {quantity} × {variant.product.name}, {variant.size}, {variant.colour} to your bag. Unit price ₹{variant.product.price}. This does not charge you."
        return f"Remove {variant.product.name}, {variant.size}, {variant.colour} from your bag."
    if kind == "apply_coupon":
        data = quote(request.session.get("cart", {}), value)
        return f'Apply coupon {value.upper()}. Current discount ₹{data["discount"]}; payable ₹{data["total"]}. Totals are rechecked at checkout.'
    if kind in ["cancel_order", "mark_packed", "refund_order"]:
        order = owned_order(user, target, staff=kind != "cancel_order")
        if kind == "cancel_order":
            if order.status not in ["pending", "confirmed"]:
                raise ShopError("Cancellation is available only before packing.")
            return f"Cancel {order.number}, total ₹{order.total}. Release reserved stock and queue any eligible refund."
        if kind == "mark_packed":
            if order.risk_hold:
                raise ShopError(
                    "Staff must review and release this order verification hold before packing."
                )
            if order.status != "confirmed" or order.payment_status != "paid":
                raise ShopError("Only a confirmed paid order can be marked packed.")
            return f"I confirm {order.number} has been physically picked, checked and packed. Start configured courier-booking automation."
        if order.payment_status != "refund_due":
            raise ShopError("This order does not need a full refund.")
        return f"Submit an actual {order.payment_mode} refund of ₹{order.total} to the original payment method for {order.number}."
    if kind in ["request_return", "request_exchange"]:
        item = (
            OrderItem.objects.filter(pk=target, order__user=user)
            .select_related("order", "variant")
            .first()
        )
        if not item:
            raise ShopError("Order item not found in your account.")
        store = StoreSettings.objects.first()
        days = store.return_days if store else 7
        if (
            item.order.status != "delivered"
            or not item.order.delivered_at
            or timezone.now() > item.order.delivered_at + timedelta(days=days)
            or item.order.payment_status not in ["paid", "partial_refund"]
        ):
            raise ShopError("This item is outside the delivered-order return policy.")
        if ReturnRequest.objects.filter(item=item).exists():
            raise ShopError("A return/exchange request already exists for this item.")
        if not 3 <= len(value) <= 1000:
            raise ShopError("Please provide a return reason (3–1,000 characters).")
        if (
            kind == "request_exchange"
            and not Variant.objects.filter(
                product=item.variant.product, colour=item.colour, size=extra
            ).exists()
        ):
            raise ShopError("The requested size is not offered for this product.")
        return f'Request {"size exchange to " + extra if kind == "request_exchange" else "return"} for all {item.quantity} units of {item.name}. Reason: {value}. Approval and physical inspection are required; no immediate refund.'
    if kind in ["approve_return", "complete_return", "refund_return"]:
        result = (
            ReturnRequest.objects.filter(pk=target)
            .select_related("item__order")
            .first()
        )
        if not result:
            raise ShopError("Return request not found.")
        if kind == "approve_return":
            if result.status != "requested":
                raise ShopError("Only a requested return can be approved.")
            return f"Approve return request #{result.pk} for {result.item.order.number} and create a physical pickup task."
        if kind == "complete_return":
            if result.status not in ["approved", "received"]:
                raise ShopError("Return must be approved or received first.")
            return f"I confirm request #{result.pk} was physically received and inspected. Accept its returned stock and trigger its refund/exchange workflow."
        if result.status != "completed" or result.kind != "return":
            raise ShopError("Receive and complete the return before refunding it.")
        from .services import return_refund_amount

        return f"Submit refund ₹{return_refund_amount(result.item)} to the original payment method for completed return #{result.pk}."
    if kind == "receive_stock":
        plan = (
            RestockPlan.objects.select_related("variant")
            .filter(pk=target, status="proposed")
            .first()
        )
        if not plan or not 1 <= quantity <= 10000:
            raise ShopError(
                "Choose an open restock plan and a valid received quantity."
            )
        return f"I confirm {quantity} units of {plan.variant.sku} have physically arrived. Increase available stock by {quantity} and close restock plan #{plan.pk}."
    if kind == "retry_job":
        job = AgentJob.objects.filter(pk=target, state__in=["review", "failed"]).first()
        if not job:
            raise ShopError("Only review/failed jobs can be retried.")
        return f"Retry {job.agent} job {job.kind}. I have reconciled any uncertain external effects. Courier/email state must be corrected before an ambiguous booking/send can proceed."
    if kind == "refresh_tracking":
        from .models import Shipment

        shipment = Shipment.objects.filter(pk=target, pickup_booked=True).first()
        if not shipment:
            raise ShopError("Choose a booked shipment.")
        return f"Refresh courier tracking for {shipment.awb}."
    if kind in ["update_description", "update_price"]:
        product = Product.objects.filter(pk=target).first()
        if not product:
            raise ShopError("Product not found.")
        if kind == "update_description":
            if not 10 <= len(value) <= 3000:
                raise ShopError("Description must contain 10–3,000 characters.")
            return f"Publish this description for {product.name}:\n{value}"
        try:
            amount = Decimal(value)
            if (
                not amount.is_finite()
                or amount < Decimal("0.01")
                or amount > Decimal("99999999.99")
                or amount.as_tuple().exponent < -2
            ):
                raise ValueError
        except (ValueError, InvalidOperation):
            raise ShopError(
                "Enter a valid positive INR price with at most two decimal places."
            )
        return f"Change {product.name} price from ₹{product.price} to ₹{amount}. Existing orders retain their saved prices."
    if kind == "resolve_enquiry":
        if not Enquiry.objects.filter(pk=target, resolved=False).exists():
            raise ShopError("Open enquiry not found.")
        return f"Mark enquiry #{target} resolved. This action does not send a customer reply."
    raise ShopError("Unsupported action.")


def propose(request, kind, payload, agent="Commerce Assistant"):
    payload = dict(payload)
    if kind not in [
        "cancel_order",
        "mark_packed",
        "refund_order",
        "retry_job",
        "apply_coupon",
        "draft_campaign",
        "release_hold",
    ]:
        try:
            payload["target"] = str(int(payload.get("target", "")))
        except (ValueError, TypeError):
            raise ShopError("Choose a valid numeric target ID.")
    summary = validate_action(request, kind, payload)
    if kind in BUSINESS_PERMISSIONS:
        from .business_actions import prepare

        payload = prepare(kind, payload)
    result = AgentAction.objects.create(
        user=request.user,
        agent=agent,
        kind=kind,
        payload=payload,
        summary=summary,
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    audit(agent, "action_proposed", kind, actor=request.user)
    return {
        "id": str(result.pk),
        "kind": kind,
        "summary": summary,
        "confirm_url": reverse("action_confirm", args=[result.pk]),
    }


@transaction.atomic
def apply_action(request, action_id):
    action = AgentAction.objects.select_for_update().get(
        pk=action_id, user=request.user
    )
    if action.state == "applied":
        return action.result
    if action.state != "pending" or action.expires_at <= timezone.now():
        raise ShopError("This action expired or was rejected. Ask for a new proposal.")
    validate_action(request, action.kind, action.payload)
    payload = action.payload
    target = str(payload.get("target", ""))
    value = str(payload.get("value", "")).strip()
    quantity = int(payload.get("quantity", 1))
    url = reverse("account")
    if action.kind in BUSINESS_PERMISSIONS:
        from .business_actions import execute

        execute(request, action.kind, payload)
    elif action.kind in ["add_to_cart", "remove_from_cart"]:
        cart = request.session.get("cart", {}).copy()
        if action.kind == "add_to_cart":
            cart[target] = int(cart.get(target, 0)) + quantity
        else:
            cart.pop(target, None)
        request.session["cart"] = cart
        # Keep cart and action outcome in the same database transaction; the
        # SessionMiddleware saves the same data again before the response.
        request.session.save()
        url = reverse("cart")
    elif action.kind == "apply_coupon":
        request.session["coupon"] = value.upper()
        request.session.save()
        url = reverse("cart")
    elif action.kind == "cancel_order":
        order = owned_order(request.user, target)
        cancel_order(order.pk)
        url = reverse("order", args=[order.pk])
    elif action.kind in ["request_return", "request_exchange"]:
        item = OrderItem.objects.get(pk=target, order__user=request.user)
        ReturnRequest.objects.create(
            item=item,
            kind="return" if action.kind == "request_return" else "exchange",
            reason=value,
            requested_size=payload.get("extra", ""),
        )
    elif action.kind == "mark_packed":
        order = owned_order(request.user, target, staff=True)
        order.status = "packed"
        order.save()
        from .models import OrderEvent

        OrderEvent.objects.create(
            order=order, message="Staff confirmed packing. Shipping agent queued."
        )
        WorkTask.objects.filter(order=order, kind="pack").update(status="done")
    elif action.kind == "approve_return":
        result = ReturnRequest.objects.get(pk=target)
        result.status = "approved"
        result.save()
        task(
            f"return-pickup:{result.pk}",
            "return_pickup",
            f"Arrange return pickup for {result.item.order.number}",
            result.item.order,
            result,
        )
    elif action.kind == "complete_return":
        complete_return(target)
    elif action.kind == "refund_order":
        request_refund(owned_order(request.user, target, staff=True))
    elif action.kind == "refund_return":
        refund_return(target)
    elif action.kind == "receive_stock":
        plan = RestockPlan.objects.select_for_update().get(pk=target, status="proposed")
        Variant.objects.filter(pk=plan.variant_id).update(stock=F("stock") + quantity)
        plan.quantity = quantity
        plan.status = "received"
        plan.save()
        WorkTask.objects.filter(key=f"restock:{plan.pk}").update(status="done")
    elif action.kind == "retry_job":
        job = AgentJob.objects.get(pk=target)
        job.state = "queued"
        job.available_at = timezone.now()
        job.error = ""
        job.save()
        WorkTask.objects.filter(key=f"job-review:{job.pk}").update(status="done")
    elif action.kind == "refresh_tracking":
        from .models import Shipment

        shipment = Shipment.objects.get(pk=target)
        enqueue(
            "track",
            f"manual-track:{action.pk}",
            {"shipment_id": shipment.pk},
            order=shipment.order,
        )
    elif action.kind == "update_description":
        Product.objects.filter(pk=target).update(description=value)
    elif action.kind == "update_price":
        Product.objects.filter(pk=target).update(price=Decimal(value))
    elif action.kind == "resolve_enquiry":
        Enquiry.objects.filter(pk=target).update(resolved=True)
        WorkTask.objects.filter(key=f"enquiry-task:{target}").update(status="done")
    action.state = "applied"
    action.result = {
        "ok": True,
        "message": "Action completed. Any queued automation continues through the worker.",
        "url": url,
    }
    action.save(update_fields=["state", "result"])
    audit(action.agent, "action_applied", action.summary[:350], actor=request.user)
    return action.result

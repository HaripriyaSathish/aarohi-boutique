"""Database-backed workflow runner; business rules do not depend on an LLM."""

from datetime import timedelta
from decimal import Decimal
import hashlib
import json
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import (
    AutomationPolicy,
    AgentJob,
    AgentAudit,
    WorkTask,
    Shipment,
    RestockPlan,
    CustomerPreference,
    Notification,
    CartSnapshot,
    Order,
    OrderEvent,
    ReturnRequest,
    Variant,
    Enquiry,
)
from .providers import (
    NeedsReview,
    ProviderError,
    book_shipment,
    generate_label,
    generate_manifest,
    track_shipment,
)
from .services import (
    cancel_order,
    refund_return,
    request_refund,
    gateway,
    mark_paid,
    ShopError,
)

AGENT_FOR_JOB = {
    "business_report": "Autonomous Business Manager",
    "send_campaign": "Marketing & Growth",
    "order_paid": "Order Processing",
    "order_cancelled": "Order Processing",
    "status_notice": "Notifications",
    "send_notice": "Notifications",
    "ship_order": "Shipping & Tracking",
    "label": "Shipping & Tracking",
    "manifest": "Shipping & Tracking",
    "track": "Shipping & Tracking",
    "reverse_pickup": "Shipping & Tracking",
    "review_return": "Returns & Refunds",
    "return_completed": "Returns & Refunds",
    "refund_order": "Payments & Refunds",
    "payment_check": "Payments & Refunds",
    "review_request": "Customer Follow-up",
    "cart_reminder": "Cart Recovery",
    "triage_enquiry": "Customer Support",
}


def policy():
    return AutomationPolicy.objects.get_or_create(pk=1)[0]


def audit(agent, event, summary, actor=None, job=None):
    return AgentAudit.objects.create(
        agent=agent, event=event, summary=summary[:400], actor=actor, job=job
    )


def enqueue(kind, key, payload=None, order=None, user=None, due=None):
    job, _ = AgentJob.objects.get_or_create(
        key=key,
        defaults={
            "kind": kind,
            "agent": AGENT_FOR_JOB.get(kind, "Operations"),
            "payload": payload or {},
            "order": order,
            "user": user or (order.user if order else None),
            "available_at": due or timezone.now(),
        },
    )
    return job


def task(key, kind, title, order=None, result=None):
    return WorkTask.objects.get_or_create(
        key=key,
        defaults={
            "kind": kind,
            "title": title,
            "order": order,
            "return_request": result,
        },
    )[0]


def notice(key, user, subject, body, order=None, kind="transactional"):
    notification, _ = Notification.objects.get_or_create(
        key=key,
        defaults={
            "user": user,
            "order": order,
            "subject": subject[:220],
            "kind": kind,
            "body": body,
        },
    )
    enqueue(
        "send_notice",
        "email:" + key,
        {"notification_id": notification.pk},
        user=user,
        order=order,
    )
    return notification


def remember_cart(request):
    if not request.user.is_authenticated:
        return
    cart = request.session.get("cart", {})
    fingerprint = hashlib.sha256(json.dumps(cart, sort_keys=True).encode()).hexdigest()
    snapshot, _ = CartSnapshot.objects.get_or_create(user=request.user)
    if snapshot.fingerprint != fingerprint:
        snapshot.cart = cart
        snapshot.fingerprint = fingerprint
        snapshot.updated = timezone.now()
        snapshot.converted = not bool(cart)
        snapshot.save()


def review_return(result):
    order = result.item.order
    from .models import StoreSettings

    store = StoreSettings.objects.first()
    days = store.return_days if store else 7
    valid = (
        order.status == "delivered"
        and order.delivered_at
        and result.created <= order.delivered_at + timedelta(days=days)
        and order.payment_status in ["paid", "partial_refund"]
    )
    if not valid:
        result.status = "rejected"
        result.staff_note = "Request does not meet the delivered-order/payment/return-window policy. Contact support if you need a manual review."
        result.save()
        return
    if result.status != "requested":
        return
    if not policy().auto_return_approval:
        raise NeedsReview(
            "Automatic eligibility approval is disabled; staff must review the request."
        )
    result.status = "approved"
    result.staff_note = "Eligibility approved automatically. Physical pickup and inspection are still required before replacement/refund."
    result.save()
    task(
        f"return-pickup:{result.pk}",
        "return_pickup",
        f"Arrange return pickup for {order.number}",
        order,
        result,
    )
    notice(
        f"return-approved:{result.pk}",
        order.user,
        "Return request approved",
        result.staff_note,
        order,
    )


@transaction.atomic
def complete_return(result_id):
    result = (
        ReturnRequest.objects.select_for_update()
        .select_related("item__variant", "item__order")
        .get(pk=result_id)
    )
    if result.status == "completed":
        return result
    if result.status not in ["approved", "received"]:
        raise ShopError(
            "Approve the request before confirming physical receipt and inspection."
        )
    if not result.stock_processed:
        item = result.item
        if result.kind == "exchange":
            target = Variant.objects.filter(
                product=item.variant.product,
                colour=item.colour,
                size=result.requested_size,
            ).first()
            if not target or not Variant.objects.filter(
                pk=target.pk, stock__gte=item.quantity
            ).update(stock=F("stock") - item.quantity):
                raise ShopError("The exchange size has insufficient stock.")
        Variant.objects.filter(pk=item.variant_id).update(
            stock=F("stock") + item.quantity
        )
        result.stock_processed = True
    result.status = "completed"
    result.staff_note = "Staff confirmed physical receipt and inspection. Automation will process the approved outcome."
    result.save()
    WorkTask.objects.filter(return_request=result, status="open").update(status="done")
    return result


def allow_refund(order):
    rules = policy()
    if not rules.auto_refunds:
        raise NeedsReview(
            "Automatic refunds are disabled. Review and confirm a refund in the operations dashboard."
        )
    if order.payment_mode != "demo" and not rules.allow_live_refunds:
        raise NeedsReview(
            "Live automatic refunds require the store owner to enable allow_live_refunds, or explicit staff approval per refund."
        )


def handle(job):
    order = job.order
    payload = job.payload
    if job.kind == "order_paid":
        from .intelligence import assess_order

        report = assess_order(order)
        if report.score >= policy().fraud_review_threshold and not report.reviewed:
            order.risk_hold = True
            order.save(update_fields=["risk_hold"])
            task(
                f"risk-review:{order.pk}",
                "risk_review",
                f"Routine verification needed for {order.number}",
                order,
            )
        if order.status == "cancelled" or order.payment_status != "paid":
            return {"skipped": "Order is no longer eligible for fulfillment."}
        from .invoices import issue_invoice
        issue_invoice(order)
        task(
            f"pack:{order.pk}", "pack", f"Pick, inspect and pack {order.number}", order
        )
        notice(
            f"confirmed:{order.pk}",
            order.user,
            f"{order.number} confirmed",
            f"Payment verified. Your order total is INR {order.total}. Track it in My Account.",
            order,
        )
    elif job.kind == "order_cancelled":
        WorkTask.objects.filter(order=order, kind="pack", status="open").update(
            status="done"
        )
        notice(
            f"cancelled:{order.pk}",
            order.user,
            f"{order.number} cancelled",
            "Your order was cancelled. If payment was collected, refund processing follows the store policy.",
            order,
        )
    elif job.kind == "status_notice":
        notice(
            job.key,
            order.user,
            f"{order.number}: {order.get_status_display()}",
            f"Order status: {order.get_status_display()}. Payment: {order.get_payment_status_display()}. Open your order page for details.",
            order,
        )
    elif job.kind == "ship_order":
        from .intelligence import assess_order

        report = assess_order(order)
        if (
            order.risk_hold or report.score >= policy().fraud_review_threshold
        ) and not report.reviewed:
            order.risk_hold = True
            order.save(update_fields=["risk_hold"])
            raise NeedsReview("Order needs staff verification before courier booking.")
        exchange = (
            ReturnRequest.objects.get(pk=payload["return_id"])
            if payload.get("return_id")
            else None
        )
        if not exchange and (
            order.status not in ["packed", "shipped", "out", "delivered"]
            or order.payment_status != "paid"
        ):
            return {"skipped": "Order must be packed and paid."}
        if exchange and (
            exchange.kind != "exchange"
            or exchange.status != "completed"
            or not exchange.stock_processed
        ):
            raise NeedsReview(
                "Exchange must be physically received, inspected and completed before shipping."
            )
        if not policy().auto_shipping:
            raise NeedsReview("Automatic courier booking is disabled.")
        key = f"exchange:{exchange.pk}" if exchange else f"outbound:{order.pk}"
        shipment, _ = Shipment.objects.get_or_create(
            key=key,
            defaults={
                "order": order,
                "return_request": exchange,
                "provider": settings.SHIPPING_MODE,
                "direction": "exchange" if exchange else "outbound",
            },
        )
        book_shipment(shipment)
        if not exchange:
            order.courier = shipment.courier
            order.tracking_number = shipment.awb
            order.tracking_url = shipment.tracking_url
            order.save(update_fields=["courier", "tracking_number", "tracking_url"])
        enqueue(
            "label", f"label:{shipment.pk}", {"shipment_id": shipment.pk}, order=order
        )
        notice(
            f"pickup:{shipment.pk}",
            order.user,
            "Shipment pickup requested",
            f'{"DEMO — no real booking. " if shipment.provider == "demo" else ""}Courier: {shipment.courier}. Tracking reference: {shipment.awb}. Dispatch status updates after courier scans.',
            order,
        )
        return {
            "shipment_id": shipment.pk,
            "awb": shipment.awb,
            "provider": shipment.provider,
        }
    elif job.kind == "reverse_pickup":
        result = ReturnRequest.objects.get(pk=payload["return_id"])
        if result.status not in ["approved", "received"]:
            return {"skipped": "Return is no longer awaiting pickup."}
        if not policy().auto_shipping:
            raise NeedsReview("Automatic courier booking is disabled.")
        shipment, _ = Shipment.objects.get_or_create(
            key=f"reverse:{result.pk}",
            defaults={
                "order": order,
                "return_request": result,
                "provider": settings.SHIPPING_MODE,
                "direction": "reverse",
            },
        )
        book_shipment(shipment)
        enqueue(
            "label", f"label:{shipment.pk}", {"shipment_id": shipment.pk}, order=order
        )
        notice(
            f"reverse-booked:{result.pk}",
            order.user,
            "Return pickup requested",
            f"{'DEMO — no real pickup. ' if shipment.provider == 'demo' else ''}Return tracking reference: {shipment.awb}. Keep the unused item and tags ready. Refund/replacement follows receipt and inspection.",
            order,
        )
        return {"shipment_id": shipment.pk, "awb": shipment.awb}
    elif job.kind == "manifest":
        shipment = Shipment.objects.get(pk=payload["shipment_id"])
        generate_manifest(shipment)
        return {"manifest_url": shipment.manifest_url}
    elif job.kind == "label":
        shipment = Shipment.objects.get(pk=payload["shipment_id"])
        generate_label(shipment)
        if shipment.direction != "reverse":
            enqueue(
                "manifest",
                f"manifest:{shipment.pk}",
                {"shipment_id": shipment.pk},
                order=order,
            )
        return {"label_url": shipment.label_url, "demo": shipment.provider == "demo"}
    elif job.kind == "track":
        shipment = Shipment.objects.get(pk=payload["shipment_id"])
        if shipment.state != "delivered" and shipment.pickup_booked:
            track_shipment(shipment)
        return {"state": shipment.state}
    elif job.kind == "review_return":
        result = ReturnRequest.objects.get(pk=payload["return_id"])
        review_return(result)
    elif job.kind == "return_completed":
        result = ReturnRequest.objects.get(pk=payload["return_id"])
        if not result.stock_processed:
            raise NeedsReview(
                "Stock processing and physical inspection are incomplete."
            )
        if result.kind == "return":
            if result.refund_status in ["refunded", "processing"]:
                return {"already_processed": True}
            allow_refund(order)
            refund_return(result.pk)
            notice(
                f"refund-return:{result.pk}",
                order.user,
                "Return refund update",
                "Your returned-item refund has been submitted. Check My Account for its processing status.",
                order,
            )
        else:
            enqueue(
                "ship_order",
                f"exchange-ship:{result.pk}",
                {"return_id": result.pk},
                order=order,
            )
    elif job.kind == "refund_order":
        if order.payment_status == "refund_due":
            allow_refund(order)
            request_refund(order)
            notice(
                f"refund:{order.pk}",
                order.user,
                "Refund update",
                "Your cancellation refund has been submitted. Your order page shows its processing status.",
                order,
            )
    elif job.kind == "payment_check":
        if order.payment_status != "pending":
            return {"skipped": "Payment is already resolved."}
        if order.payment_mode == "cashfree" and order.provider_order:
            from .cashfree import verify_cashfree

            try:
                verify_cashfree(order)
                return {"reconciled": True}
            except ShopError:
                pass
        if order.payment_mode == "razorpay" and order.provider_order:
            response = gateway("GET", "orders/" + order.provider_order + "/payments")
            for payment in response.get("items", []):
                if (
                    payment.get("status") == "captured"
                    and payment.get("amount") == int(order.total * 100)
                    and payment.get("currency") == "INR"
                    and payment.get("order_id") == order.provider_order
                ):
                    mark_paid(order.pk, payment["id"])
                    return {"reconciled": True}
        if order.status == "pending" and order.expires_at <= timezone.now():
            cancel_order(order.pk)
        elif order.status == "pending":
            notice(
                f"payment-reminder:{order.pk}",
                order.user,
                "Your order is awaiting payment",
                "Your reserved order has not been paid. Open My Account to complete payment before the reservation expires. Do not pay again if your bank shows a debit; contact support.",
                order,
            )
            enqueue(
                "payment_check",
                f"payment-expire:{order.pk}",
                order=order,
                due=order.expires_at,
            )
    elif job.kind == "send_notice":
        notification = Notification.objects.select_related("user").get(
            pk=payload["notification_id"]
        )
        preferences, _ = CustomerPreference.objects.get_or_create(
            user=notification.user
        )
        if notification.email_state in ["sent", "demo", "disabled"]:
            return {"already_processed": True}
        if notification.email_state == "sending":
            raise NeedsReview(
                "Email delivery outcome is uncertain; check SMTP logs before resending."
            )
        if (
            not preferences.email_updates
            or (notification.kind == "marketing" and not preferences.marketing_opt_in)
            or (notification.kind == "review" and not preferences.review_requests)
        ):
            notification.email_state = "disabled"
            notification.save(update_fields=["email_state"])
        elif settings.NOTIFICATION_MODE == "demo":
            notification.email_state = "demo"
            notification.save(update_fields=["email_state"])
        else:
            notification.email_state = "sending"
            notification.save(update_fields=["email_state"])
            try:
                from .emails import send_notification

                send_notification(notification)
            except Exception as exc:
                raise NeedsReview(
                    "Email outcome is uncertain. Check SMTP settings/logs, then explicitly reset notification email_state before retrying."
                ) from exc
            notification.email_state = "sent"
            notification.save(update_fields=["email_state"])
    elif job.kind == "review_request":
        preferences, _ = CustomerPreference.objects.get_or_create(user=order.user)
        if order.status == "delivered" and preferences.review_requests:
            notice(
                job.key,
                order.user,
                "How did you like your order?",
                "Your order has arrived. You can review your purchased products from their product pages.",
                order,
                kind="review",
            )
    elif job.kind == "cart_reminder":
        snapshot = CartSnapshot.objects.filter(user=job.user).first()
        preferences, _ = CustomerPreference.objects.get_or_create(user=job.user)
        if (
            snapshot
            and snapshot.cart
            and not snapshot.converted
            and snapshot.fingerprint == payload.get("fingerprint")
            and preferences.marketing_opt_in
        ):
            from .services import quote

            try:
                bag = quote(snapshot.cart)
                names = ", ".join(
                    line["variant"].product.name for line in bag["lines"][:3]
                )
            except ShopError:
                names = "your saved styles"
            body = f"Your saved bag includes {names}. Return when you are ready; current prices and stock are checked again at checkout."
            notice(
                job.key,
                job.user,
                "Your boutique favourites are waiting",
                body,
                kind="marketing",
            )
    elif job.kind == "business_report":
        from .intelligence import management_plan
        from .models import BusinessReport

        report = management_plan()
        snapshot, _ = BusinessReport.objects.get_or_create(
            day=timezone.localdate(),
            defaults={"metrics": report["metrics"], "plan": report["action_plan"]},
        )
        for index, plan in enumerate(snapshot.plan):
            task(
                f"manager:{snapshot.day}:{index}",
                "business_review",
                plan["issue"] + ": " + plan["action"],
            )
        return {"report_id": snapshot.pk, "tasks": len(snapshot.plan)}
    elif job.kind == "send_campaign":
        from .models import Campaign

        campaign = Campaign.objects.get(pk=payload["campaign_id"])
        if campaign.status not in ["approved", "sent"]:
            return {"skipped": "Campaign has not been approved."}
        from django.contrib.auth import get_user_model

        eligible = get_user_model().objects.filter(
            pk__in=campaign.approved_audience,
            customerpreference__marketing_opt_in=True,
            is_active=True,
        )
        for customer in eligible.iterator():
            notice(
                f"campaign:{campaign.pk}:{customer.pk}",
                customer,
                campaign.name,
                campaign.body,
                kind="marketing",
            )
        campaign.status = "sent"
        campaign.save(update_fields=["status"])
        return {"recipients_rechecked": eligible.count()}
    elif job.kind == "triage_enquiry":
        enquiry = Enquiry.objects.get(pk=payload["enquiry_id"])
        text = enquiry.message.lower()
        category = (
            "orders"
            if any(word in text for word in ["order", "refund", "delivery"])
            else "styling / general"
        )
        task(
            f"enquiry-task:{enquiry.pk}",
            "enquiry",
            f"Review {category} enquiry #{enquiry.pk}",
        )
        return {"category": category, "auto_reply_sent": False}
    else:
        raise NeedsReview("No handler for this job type.")
    return {"processed": True}


def sweep():
    rules = policy()
    if not rules.enabled:
        return
    now = timezone.now()
    enqueue("business_report", f"business-report:{timezone.localdate()}")
    # Expired leases are reviewed rather than replaying an uncertain external effect.
    AgentJob.objects.filter(state="running", lease_until__lt=now).update(
        state="review", error="Worker lease expired; reconcile before retry."
    )
    for variant in Variant.objects.filter(
        stock__lt=rules.low_stock_threshold, product__active=True
    ).select_related("product"):
        plan, created = RestockPlan.objects.get_or_create(
            variant=variant,
            status="proposed",
            defaults={"quantity": max(1, rules.restock_target - variant.stock)},
        )
        if created:
            task(
                f"restock:{plan.pk}",
                "restock",
                f"Restock {variant.sku}: proposed {plan.quantity} units",
            )
            audit(
                "Inventory",
                "restock_proposed",
                f"{variant.sku}: stock {variant.stock}; proposed {plan.quantity}. No purchase or stock increase was made.",
            )
    for shipment in Shipment.objects.filter(pickup_booked=True).exclude(
        state="delivered"
    ):
        delay = settings.DEMO_TRACK_SECONDS if shipment.provider == "demo" else 300
        if not shipment.last_checked or shipment.last_checked <= now - timedelta(
            seconds=delay
        ):
            slot = int(now.timestamp()) // max(delay, 1)
            enqueue(
                "track",
                f"track:{shipment.pk}:{slot}",
                {"shipment_id": shipment.pk},
                order=shipment.order,
            )
    for order in Order.objects.filter(status="pending", expires_at__lte=now):
        enqueue("payment_check", f"payment-expire:{order.pk}", order=order)
    if rules.auto_cart_reminders:
        for snapshot in CartSnapshot.objects.filter(
            converted=False,
            updated__lte=now - timedelta(hours=rules.abandoned_cart_hours),
        ):
            enqueue(
                "cart_reminder",
                f"cart-reminder:{snapshot.user_id}:{snapshot.fingerprint}",
                {"fingerprint": snapshot.fingerprint},
                user=snapshot.user,
            )


def run_jobs(limit=50):
    if not policy().enabled:
        return 0
    processed = 0
    for _ in range(limit):
        candidate = (
            AgentJob.objects.filter(state="queued", available_at__lte=timezone.now())
            .order_by("available_at", "created")
            .first()
        )
        if not candidate:
            break
        claimed = AgentJob.objects.filter(pk=candidate.pk, state="queued").update(
            state="running",
            attempts=F("attempts") + 1,
            lease_until=timezone.now() + timedelta(minutes=10),
        )
        if not claimed:
            continue
        job = AgentJob.objects.select_related("order", "user").get(pk=candidate.pk)
        try:
            result = handle(job)
            job.state = "succeeded"
            job.result = result
            job.error = ""
        except NeedsReview as exc:
            job.state = "review"
            job.error = str(exc)
            task(
                f"job-review:{job.pk}",
                "automation_review",
                f"Review {job.agent}: {job.kind}",
                job.order,
            )
        except (ShopError, ProviderError) as exc:
            job.state = "failed" if job.attempts >= 3 else "queued"
            job.error = str(exc)
            job.available_at = timezone.now() + timedelta(
                seconds=30 * (2 ** (job.attempts - 1))
            )
        except Exception as exc:
            job.state = "review"
            job.error = f"Unexpected {type(exc).__name__}. Inspect server logs and job context before retry."
            task(
                f"job-review:{job.pk}",
                "automation_review",
                f"Investigate {job.kind}",
                job.order,
            )
        job.lease_until = None
        job.save()
        audit(
            job.agent,
            "job_" + job.state,
            f'{job.kind}: {job.error or "completed"}',
            job=job,
        )
        processed += 1
    return processed

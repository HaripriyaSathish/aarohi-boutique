"""Measured store intelligence. Estimates and missing data are explicit."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal
from math import ceil, radians, sin, cos, atan2, sqrt
from statistics import median
from django.contrib.auth import get_user_model
from django.db.models import Sum, Count, Q
from django.utils import timezone
from .models import (
    Order,
    OrderItem,
    ReturnRequest,
    Product,
    Variant,
    SupplierQuote,
    PurchaseOrder,
    CompetitorPrice,
    CustomerPreference,
    Campaign,
    RiskReport,
    BusinessReport,
    WorkTask,
    Warehouse,
    Shipment,
)


def business_metrics():
    orders = Order.objects.exclude(payment_status="pending").exclude(
        provider_payment=""
    )
    collected = sum((order.total for order in orders), Decimal("0"))
    cancelled_refunds = sum(
        (
            order.total
            for order in orders.filter(status="cancelled", payment_status="refunded")
        ),
        Decimal("0"),
    )
    returned_refunds = ReturnRequest.objects.filter(refund_status="refunded").aggregate(
        value=Sum("refund_amount")
    )["value"] or Decimal("0")
    fulfilled = OrderItem.objects.filter(
        order__status__in=["shipped", "out", "delivered"]
    )
    known = fulfilled.filter(unit_cost__isnull=False)
    cogs = sum((item.unit_cost * item.quantity for item in known), Decimal("0"))
    returned_cost = sum(
        (
            result.item.unit_cost * result.item.quantity
            for result in ReturnRequest.objects.filter(
                status="completed", kind="return", item__unit_cost__isnull=False
            ).select_related("item")
        ),
        Decimal("0"),
    )
    cogs -= returned_cost
    units = sum(item.quantity for item in fulfilled)
    known_units = sum(item.quantity for item in known)
    net = collected - cancelled_refunds - returned_refunds
    return {
        "collected_inr": str(collected),
        "processed_refunds_inr": str(cancelled_refunds + returned_refunds),
        "net_collected_inr": str(net),
        "orders": Order.objects.count(),
        "fulfilled_units": units,
        "cost_coverage_units": known_units,
        "cost_of_goods_known_inr": str(cogs),
        "margin_estimate_inr": (
            str(net - cogs) if units and known_units == units else None
        ),
        "margin_note": "Cash less known merchandise costs only; excludes tax, courier costs, overhead and unpaid refunds. Not accounting profit. Missing unit costs prevent a full margin estimate.",
        "segments": customer_segments(),
    }


def customer_segments():
    cutoff = timezone.now() - timedelta(days=90)
    buyers = (
        get_user_model()
        .objects.filter(is_staff=False, is_active=True)
        .annotate(
            purchases=Count(
                "order",
                filter=Q(
                    order__payment_status__in=["paid", "partial_refund", "refunded"]
                ),
                distinct=True,
            ),
        )
    )
    repeat = buyers.filter(purchases__gte=2).count()
    inactive = (
        buyers.filter(date_joined__lt=cutoff)
        .exclude(order__created__gte=cutoff)
        .count()
    )
    return {
        "customers": buyers.count(),
        "repeat_buyers": repeat,
        "inactive_90_days": inactive,
        "marketing_opted_in": CustomerPreference.objects.filter(
            marketing_opt_in=True
        ).count(),
    }


def forecast_demand():
    now = timezone.now()
    results = []
    for product in Product.objects.filter(active=True)[:100]:
        lines = list(
            OrderItem.objects.filter(
                variant__product=product,
                order__created__gte=now - timedelta(days=56),
                order__status__in=[
                    "confirmed",
                    "packed",
                    "shipped",
                    "out",
                    "delivered",
                ],
            ).select_related("order")
        )
        days = Counter()
        for line in lines:
            days[line.order.created.date()] += line.quantity
        history_days = (now.date() - min(days)).days + 1 if days else 0
        if history_days < 28 or sum(days.values()) < 5:
            results.append(
                {
                    "product_id": product.pk,
                    "product": product.name,
                    "status": "insufficient_data",
                    "history_days": history_days,
                    "observed_units": sum(days.values()),
                    "next_14_days_estimate": None,
                }
            )
            continue
        values = [
            days[(now - timedelta(days=offset)).date()] for offset in range(55, -1, -1)
        ]
        level = float(values[0])
        errors = []
        for index, value in enumerate(values[1:], 1):
            if index >= 49:
                errors.append(abs(value - level))
            level = 0.2 * value + 0.8 * level
        results.append(
            {
                "product_id": product.pk,
                "product": product.name,
                "status": "estimate",
                "next_14_days_estimate": round(level * 14, 1),
                "daily_rate": round(level, 3),
                "backtest_daily_mae": round(sum(errors) / len(errors), 2),
                "history_days": history_days,
                "method": "56-day EWMA, alpha 0.2; descriptive estimate, no guaranteed demand.",
            }
        )
    return {"forecasts": results}


def supplier_comparison(quantity=20):
    rows = []
    for quote in SupplierQuote.objects.filter(
        supplier__active=True, valid_until__gte=timezone.localdate()
    ).select_related("supplier", "variant")[:100]:
        units = max(quantity, quote.minimum_quantity)
        history = PurchaseOrder.objects.filter(
            quote__supplier=quote.supplier, status="received"
        )
        delivered = [row for row in history if row.expected_date and row.received_date]
        on_time = sum(row.received_date <= row.expected_date for row in delivered)
        total_units = sum(row.quantity for row in history)
        defective = sum(row.defective_units for row in history)
        rows.append(
            {
                "quote_id": quote.pk,
                "supplier": quote.supplier.name,
                "sku": quote.variant.sku,
                "quantity": units,
                "landed_unit_inr": str(
                    (quote.unit_price + quote.freight / units).quantize(Decimal(".01"))
                ),
                "lead_days": quote.supplier.lead_days,
                "on_time_percent": (
                    round(on_time / len(delivered) * 100, 1) if delivered else None
                ),
                "defect_percent": (
                    round(defective / total_units * 100, 1) if total_units else None
                ),
                "completed_purchase_orders": history.count(),
                "note": "Compare like-for-like SKU, MOQ and quality; missing performance data is not a perfect rating.",
            }
        )
    return {
        "quotes": sorted(
            rows, key=lambda row: (row["sku"], Decimal(row["landed_unit_inr"]))
        )
    }


def pricing_intelligence():
    rows = []
    for product in Product.objects.filter(active=True)[:100]:
        prices = list(
            CompetitorPrice.objects.filter(
                product=product,
                observed__gte=timezone.now() - timedelta(days=30),
                price__gt=0,
            ).values_list("price", flat=True)
        )
        floor = (
            (product.cost_price / Decimal(".65")).quantize(Decimal(".01"))
            if product.cost_price is not None
            else None
        )
        middle = median(prices) if prices else None
        recommendation = (
            max(floor, (middle * Decimal(".99")).quantize(Decimal(".01")))
            if floor is not None and middle is not None
            else None
        )
        rows.append(
            {
                "product_id": product.pk,
                "product": product.name,
                "current_price_inr": str(product.price),
                "cost_inr": (
                    str(product.cost_price) if product.cost_price is not None else None
                ),
                "competitor_median_inr": str(middle) if middle is not None else None,
                "margin_floor_35_percent_inr": (
                    str(floor) if floor is not None else None
                ),
                "suggested_price_inr": (
                    str(recommendation) if recommendation is not None else None
                ),
                "observations": len(prices),
                "note": "Requires actual cost and comparable recent competitor observations. Recommendation only; approval required. No competitor scraping is implied.",
            }
        )
    return {"pricing": rows}


def inventory_optimization():
    forecasts = {row["product_id"]: row for row in forecast_demand()["forecasts"]}
    rows = []
    for variant in Variant.objects.filter(product__active=True).select_related(
        "product"
    )[:150]:
        forecast = forecasts.get(variant.product_id, {})
        variants = max(variant.product.variants.count(), 1)
        daily = forecast.get("daily_rate", 0) / variants
        lead = (
            SupplierQuote.objects.filter(variant=variant, supplier__active=True)
            .order_by("supplier__lead_days")
            .values_list("supplier__lead_days", flat=True)
            .first()
            or 7
        )
        incoming = sum(
            row.quantity
            for row in PurchaseOrder.objects.filter(
                quote__variant=variant, status="approved"
            )
        )
        quantity = (
            max(0, ceil(daily * (lead + 7) - variant.stock - incoming))
            if daily
            else max(0, 10 - variant.stock)
        )
        rows.append(
            {
                "variant_id": variant.pk,
                "sku": variant.sku,
                "stock": variant.stock,
                "approved_incoming": incoming,
                "lead_days": lead,
                "suggested_reorder": quantity,
                "basis": (
                    "Product demand split evenly across variants; validate size mix."
                    if daily
                    else "Low-data minimum-stock heuristic, not a demand forecast."
                ),
            }
        )
    allocation = []
    warehouses = list(Warehouse.objects.filter(active=True)[:20])
    for variant in Variant.objects.filter(product__active=True)[:100]:
        weights = []
        for warehouse in warehouses:
            units = (
                OrderItem.objects.filter(
                    variant=variant,
                    order__created__gte=timezone.now() - timedelta(days=30),
                    order__delivery_data__city__iexact=warehouse.city,
                    order__status__in=[
                        "confirmed",
                        "packed",
                        "shipped",
                        "out",
                        "delivered",
                    ],
                ).aggregate(value=Sum("quantity"))["value"]
                or 0
            )
            weights.append((warehouse, units))
        total = sum(units for _, units in weights)
        if total:
            desired = [
                (warehouse, int(variant.stock * units / total))
                for warehouse, units in weights
            ]
            remainder = variant.stock - sum(quantity for _, quantity in desired)
            largest = max(range(len(weights)), key=lambda index: weights[index][1])
            for index, (warehouse, quantity) in enumerate(desired):
                allocation.append(
                    {
                        "sku": variant.sku,
                        "warehouse": warehouse.name,
                        "desired_units": quantity
                        + (remainder if index == largest else 0),
                        "basis": "Proportional to recorded 30-day city demand; planning only, no stock transfer was executed.",
                    }
                )
    return {
        "warehouse_allocation_suggestions": allocation,
        "inventory": rows,
        "warehouse_note": "Global variant stock is authoritative. Multiple warehouse allocations are planning recommendations, not a separate warehouse stock ledger.",
    }


def campaign_audience(segment):
    users = get_user_model().objects.filter(
        is_active=True, is_staff=False, customerpreference__marketing_opt_in=True
    )
    cutoff = timezone.now() - timedelta(days=90)
    if segment == "inactive":
        users = users.filter(date_joined__lt=cutoff).exclude(order__created__gte=cutoff)
    elif segment == "repeat":
        users = users.annotate(number=Count("order", distinct=True)).filter(
            number__gte=2
        )
    return users.distinct()


def marketing_insights():
    return {
        "segments": customer_segments(),
        "campaigns": list(
            Campaign.objects.values("id", "name", "segment", "status")[:20]
        ),
        "recommendations": [
            "Segment repeat buyers separately from inactive customers.",
            "Use existing approved offers; do not invent discounts.",
            "Send campaigns only to opted-in users and require staff approval.",
        ],
        "audience_sizes": {
            segment: campaign_audience(segment).count()
            for segment in ["all_opted_in", "inactive", "repeat"]
        },
    }


def assess_order(order):
    reasons, score = [], 0
    recent = Order.objects.filter(
        user=order.user,
        created__gte=order.created - timedelta(hours=1),
        created__lte=order.created,
    ).count()
    if recent >= 4:
        score += 35
        reasons.append("Four or more orders in one hour for this account.")
    accounts = (
        Order.objects.filter(phone=order.phone)
        .exclude(user=order.user)
        .values("user")
        .distinct()
        .count()
    )
    if accounts >= 2:
        score += 35
        reasons.append(
            "Delivery phone is shared with at least two other accounts; families may legitimately share numbers."
        )
    if order.total >= Decimal("25000"):
        score += 20
        reasons.append("High order amount requires routine verification.")
    if order.user.date_joined >= order.created - timedelta(
        days=1
    ) and order.total >= Decimal("10000"):
        score += 15
        reasons.append("New account with a larger first-day order.")
    report, _ = RiskReport.objects.update_or_create(
        order=order, defaults={"score": min(score, 100), "signals": reasons}
    )
    return report


def fraud_report():
    return {
        "risk_reports": [
            {
                "order": row.order.number,
                "score": row.score,
                "signals": row.signals,
                "reviewed": row.reviewed,
            }
            for row in RiskReport.objects.select_related("order").order_by("-score")[
                :30
            ]
        ],
        "note": "Explainable screening heuristics, not proof of fraud. Never auto-ban customers; staff reviews holds.",
    }


def return_investigation():
    rows = []
    for result in ReturnRequest.objects.select_related(
        "item__order", "item__variant__product"
    )[:50]:
        order = result.item.order
        purchases = (
            Order.objects.filter(user=order.user)
            .exclude(payment_status="pending")
            .count()
        )
        requests = ReturnRequest.objects.filter(item__order__user=order.user).count()
        rows.append(
            {
                "request_id": result.pk,
                "order": order.number,
                "product": result.item.name,
                "status": result.status,
                "reason": result.reason[:300],
                "paid_orders": purchases,
                "return_requests": requests,
                "recommendation": "Physical inspection required before refund or replacement.",
                "review_note": (
                    "High return frequency is a review signal, not automatic proof of abuse."
                    if requests >= 3
                    else "Apply the published return policy."
                ),
            }
        )
    return {"returns": rows}


def haversine(first, second):
    lat1, lon1, lat2, lon2 = map(radians, [*first, *second])
    value = (
        sin((lat2 - lat1) / 2) ** 2
        + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * atan2(sqrt(value), sqrt(max(0, 1 - value)))


def logistics_analysis():
    rows = []
    for shipment in Shipment.objects.select_related("order").order_by("-created")[:100]:
        elapsed = max(0, (timezone.now() - shipment.created).total_seconds() / 86400)
        rows.append(
            {
                "shipment_id": shipment.pk,
                "order": shipment.order.number,
                "courier": shipment.courier,
                "state": shipment.state,
                "age_days": round(elapsed, 1),
                "review_delay": elapsed > 7 and shipment.state != "delivered",
            }
        )
    warehouse = Warehouse.objects.filter(
        active=True, latitude__isnull=False, longitude__isnull=False
    ).first()
    pending = list(
        Order.objects.filter(
            status__in=["packed", "shipped"],
            delivery_latitude__isnull=False,
            delivery_longitude__isnull=False,
        )
    )
    route, distance = [], 0
    if warehouse:
        current = (warehouse.latitude, warehouse.longitude)
        for _ in range(min(len(pending), 50)):
            next_order = min(
                pending,
                key=lambda order: haversine(
                    current, (order.delivery_latitude, order.delivery_longitude)
                ),
            )
            target = (next_order.delivery_latitude, next_order.delivery_longitude)
            distance += haversine(current, target)
            route.append(next_order.number)
            current = target
            pending.remove(next_order)
    return {
        "shipments": rows,
        "local_delivery_route": route,
        "estimated_straight_line_km": round(distance, 2),
        "route_status": (
            "heuristic_plan" if route else "needs_warehouse_and_order_coordinates"
        ),
        "note": "Local-fleet nearest-neighbour planning only. Straight-line distance is not road travel time; external courier routes cannot be controlled by this application.",
    }


def management_plan():
    metrics = business_metrics()
    plans = []
    if Variant.objects.filter(stock=0, product__active=True).exists():
        plans.append(
            {
                "priority": "high",
                "issue": "Active catalogue has stockouts",
                "action": "Review restock plans and supplier quotes.",
            }
        )
    if Order.objects.filter(payment_status="refund_due").exists():
        plans.append(
            {
                "priority": "high",
                "issue": "Refunds require attention",
                "action": "Review eligible refunds and provider status.",
            }
        )
    if Order.objects.filter(risk_hold=True).exists():
        plans.append(
            {
                "priority": "high",
                "issue": "Orders on verification hold",
                "action": "Inspect risk evidence before release.",
            }
        )
    if (
        Shipment.objects.exclude(state="delivered")
        .filter(created__lt=timezone.now() - timedelta(days=7))
        .exists()
    ):
        plans.append(
            {
                "priority": "medium",
                "issue": "Older undelivered shipments",
                "action": "Contact carrier; review exceptions.",
            }
        )
    if metrics["margin_estimate_inr"] is None:
        plans.append(
            {
                "priority": "medium",
                "issue": "Insufficient unit-cost data",
                "action": "Enter actual product costs before profit/pricing decisions.",
            }
        )
    return {
        "metrics": metrics,
        "action_plan": plans,
        "authority": "Creates internal tasks/reports automatically. Financial, campaign and catalogue changes still require the applicable approval policy.",
    }

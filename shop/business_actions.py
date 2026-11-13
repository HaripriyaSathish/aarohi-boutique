import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from django.db.models import F
from django.utils import timezone
from .models import (
    Campaign,
    Product,
    SupplierQuote,
    PurchaseOrder,
    RiskReport,
    WorkTask,
    RestockPlan,
    Variant,
)
from .services import ShopError
from .automation import enqueue

PERMISSIONS = {
    "create_purchase_order": "shop.add_purchaseorder",
    "approve_purchase_order": "shop.change_purchaseorder",
    "receive_purchase_order": "shop.change_purchaseorder",
    "draft_campaign": "shop.add_campaign",
    "approve_campaign": "shop.change_campaign",
    "release_hold": "shop.change_order",
    "save_seo": "shop.change_product",
}


def campaign_digest(campaign):
    return hashlib.sha256(
        json.dumps([campaign.name, campaign.body, campaign.segment]).encode()
    ).hexdigest()


def validate(request, kind, payload):
    from .actions import owned_order

    target = str(payload.get("target", ""))
    value = str(payload.get("value", ""))
    extra = str(payload.get("extra", ""))
    quantity = int(payload.get("quantity", 1))
    if kind == "create_purchase_order":
        quote = (
            SupplierQuote.objects.select_related("supplier", "variant")
            .filter(
                pk=target, supplier__active=True, valid_until__gte=timezone.localdate()
            )
            .first()
        )
        if (
            not quote
            or not quote.minimum_quantity <= quantity <= 10000
            or quote.unit_price <= 0
            or quote.freight < 0
        ):
            raise ShopError(
                "Choose a valid current quote and a quantity satisfying its MOQ."
            )
        total = quote.unit_price * quantity + quote.freight
        return f"Create a DRAFT purchase order for {quantity} × {quote.variant.sku} from {quote.supplier.name}, total ₹{total}. This records a proposal only; no supplier email or payment will be sent."
    if kind in ["approve_purchase_order", "receive_purchase_order"]:
        order = (
            PurchaseOrder.objects.select_related("quote__variant")
            .filter(pk=target)
            .first()
        )
        if not order:
            raise ShopError("Purchase order not found.")
        if kind == "approve_purchase_order":
            if order.status != "draft":
                raise ShopError("Only draft purchase orders can be approved.")
            return f"Approve internal purchase order #{order.pk}, total ₹{order.total}. Supplier ordering/payment remains a separate business step."
        try:
            defects = int(value or 0)
        except ValueError:
            raise ShopError("Enter the inspected defective-unit count.")
        if (
            order.status != "approved"
            or order.stock_applied
            or quantity != order.quantity
            or not 0 <= defects <= quantity
        ):
            raise ShopError(
                "Receive an approved full purchase order once, with a valid defect count."
            )
        return f"I confirm purchase order #{order.pk} physically arrived in full: {quantity} units, {defects} defective. Add {quantity-defects} saleable units to {order.quote.variant.sku} and close related restock plans."
    if kind == "draft_campaign":
        if (
            not 3 <= len(target) <= 150
            or not 10 <= len(value) <= 3000
            or extra not in ["all_opted_in", "inactive", "repeat"]
        ):
            raise ShopError(
                "Supply a campaign name, content and supported audience segment."
            )
        return f"Save a campaign DRAFT: {target}. Segment {extra}. Nothing will be sent.\n{value}"
    if kind == "approve_campaign":
        campaign = Campaign.objects.filter(pk=target, status="draft").first()
        if not campaign:
            raise ShopError("Choose a draft campaign.")
        if payload.get("campaign_digest") and payload[
            "campaign_digest"
        ] != campaign_digest(campaign):
            raise ShopError(
                "Campaign content changed after the proposal. Review a new approval."
            )
        from .intelligence import campaign_audience

        count = campaign_audience(campaign.segment).count()
        if count > 1000:
            raise ShopError(
                "Split this campaign into smaller approved batches (maximum 1,000 recipients)."
            )
        return f"Approve campaign “{campaign.name}” for up to {count} currently opted-in recipients. The worker will recheck consent and deliver through the configured in-app/email outbox.\n{campaign.body}"
    if kind == "release_hold":
        order = owned_order(request.user, target, staff=True)
        report = RiskReport.objects.filter(order=order).first()
        if not order.risk_hold or not report:
            raise ShopError("This order has no active verification hold.")
        return f"I have reviewed verification evidence for {order.number} and authorize release of its fulfillment hold. Screening score {report.score} is a heuristic, not proof of wrongdoing."
    if kind == "save_seo":
        product = Product.objects.filter(pk=target).first()
        try:
            content = json.loads(value)
            assert isinstance(content, dict) and set(content) == {
                "title",
                "meta",
                "tags",
            }
            assert all(isinstance(item, str) for item in content.values())
            assert (
                len(content["title"]) <= 160
                and len(content["meta"]) <= 320
                and len(content["tags"]) <= 300
            )
        except (ValueError, TypeError, AssertionError):
            raise ShopError(
                "SEO content must be JSON with title, meta and tags within field limits."
            )
        if not product:
            raise ShopError("Product not found.")
        return f'Publish SEO text for {product.name}:\nTitle: {content["title"]}\nMeta: {content["meta"]}\nTags: {content["tags"]}'
    raise ShopError("Unknown business action.")


def prepare(kind, payload):
    payload = dict(payload)
    if kind == "approve_campaign":
        campaign = Campaign.objects.get(pk=payload["target"])
        from .intelligence import campaign_audience

        payload["campaign_digest"] = campaign_digest(campaign)
        payload["audience"] = list(
            campaign_audience(campaign.segment).values_list("pk", flat=True)[:1000]
        )
    return payload


def execute(request, kind, payload):
    from .actions import owned_order

    target, value = payload.get("target", ""), str(payload.get("value", ""))
    quantity = int(payload.get("quantity", 1))
    if kind == "create_purchase_order":
        quote = SupplierQuote.objects.get(pk=target)
        PurchaseOrder.objects.create(
            quote=quote,
            quantity=quantity,
            unit_price=quote.unit_price,
            freight=quote.freight,
            expected_date=timezone.localdate()
            + timedelta(days=quote.supplier.lead_days),
        )
    elif kind == "approve_purchase_order":
        order = PurchaseOrder.objects.get(pk=target)
        order.status = "approved"
        order.save(update_fields=["status"])
    elif kind == "receive_purchase_order":
        order = (
            PurchaseOrder.objects.select_for_update()
            .select_related("quote")
            .get(pk=target)
        )
        defects = int(value or 0)
        Variant.objects.filter(pk=order.quote.variant_id).update(
            stock=F("stock") + quantity - defects
        )
        order.stock_applied = True
        order.status = "received"
        order.received_date = timezone.localdate()
        order.defective_units = defects
        order.save()
        plans = RestockPlan.objects.filter(
            variant=order.quote.variant, status="proposed"
        )
        keys = [f"restock:{plan.pk}" for plan in plans]
        plans.update(status="received")
        WorkTask.objects.filter(key__in=keys).update(status="done")
    elif kind == "draft_campaign":
        Campaign.objects.create(name=target, body=value, segment=payload["extra"])
    elif kind == "approve_campaign":
        campaign = Campaign.objects.get(pk=target)
        campaign.status = "approved"
        campaign.approved_audience = payload.get("audience", [])
        campaign.save()
        enqueue(
            "send_campaign",
            f"campaign:{campaign.pk}",
            {"campaign_id": campaign.pk},
            user=request.user,
        )
    elif kind == "release_hold":
        order = owned_order(request.user, target, staff=True)
        order.risk_hold = False
        order.save(update_fields=["risk_hold"])
        RiskReport.objects.filter(order=order).update(reviewed=True)
        WorkTask.objects.filter(key=f"risk-review:{order.pk}").update(status="done")
    elif kind == "save_seo":
        content = json.loads(value)
        Product.objects.filter(pk=target).update(
            seo_title=content["title"],
            meta_description=content["meta"],
            tags=content["tags"],
        )

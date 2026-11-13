"""Admin Pending-Work Agent.

Scans every store queue and tells staff what is waiting for a human, ordered by
priority. Data is read from the database only; nothing is changed here.
`briefing()` asks Gemini to turn the measured queues into a short to-do plan and
falls back to a rules-based plan when no AI key is configured.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from .automation import policy
from .automation_models import AgentAction, AgentJob, RestockPlan, Shipment, WorkTask
from .intelligence_models import Campaign, PurchaseOrder, RiskReport
from .models import Enquiry, Order, Review, ReturnRequest, Variant

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _admin(name, query=""):
    return reverse(f"admin:{name}") + (f"?{query}" if query else "")


def _age(dt, now):
    hours = int((now - dt).total_seconds() // 3600)
    return f"{hours // 24}d" if hours >= 24 else f"{hours}h"


def collect():
    """Return every pending queue with counts, priority, examples and links."""
    now = timezone.now()
    rules = policy()
    items = []

    def add(key, title, qs, priority, action, url, label):
        count = qs.count()
        if not count:
            return
        examples = [label(obj) for obj in qs[:5]]
        items.append(
            {
                "key": key,
                "title": title,
                "count": count,
                "priority": priority,
                "action": action,
                "url": url,
                "examples": examples,
            }
        )

    # --- Orders ---------------------------------------------------------------
    to_pack = Order.objects.filter(
        payment_status=Order.Payment.PAID, status=Order.Status.CONFIRMED, risk_hold=False
    ).order_by("created")
    add(
        "orders_to_pack",
        "Paid orders waiting to be packed",
        to_pack,
        "high" if to_pack.filter(created__lt=now - timedelta(hours=24)).exists() else "medium",
        "Pick and pack these orders, then mark the pack task done in Operations.",
        _admin("shop_order_changelist", "status__exact=confirmed&payment_status__exact=paid"),
        lambda o: f"{o.number} · ₹{o.total} · waiting {_age(o.created, now)}",
    )
    held = Order.objects.filter(risk_hold=True).exclude(status=Order.Status.CANCELLED)
    add(
        "risk_holds",
        "Orders on fraud/risk hold",
        held,
        "high",
        "Review the risk signals and release or cancel each order.",
        _admin("shop_order_changelist", "risk_hold__exact=1"),
        lambda o: f"{o.number} · ₹{o.total}",
    )
    packed = Order.objects.filter(status=Order.Status.PACKED).order_by("created")
    add(
        "packed_not_shipped",
        "Packed orders not yet shipped",
        packed,
        "medium",
        "Book the courier / confirm pickup so customers get tracking.",
        _admin("shop_order_changelist", "status__exact=packed"),
        lambda o: f"{o.number} · packed, waiting {_age(o.created, now)}",
    )
    refunds = Order.objects.filter(
        payment_status__in=[Order.Payment.REFUND_DUE, Order.Payment.REFUND_PENDING]
    )
    add(
        "refunds_due",
        "Refunds due or processing",
        refunds,
        "high",
        "Issue or reconcile these refunds with the payment gateway.",
        _admin("shop_order_changelist", "payment_status__exact=refund_due"),
        lambda o: f"{o.number} · ₹{o.total} · {o.get_payment_status_display()}",
    )
    stale_unpaid = Order.objects.filter(
        status=Order.Status.PENDING, expires_at__lt=now, stock_released=False
    )
    add(
        "expired_unpaid",
        "Expired unpaid orders still holding stock",
        stale_unpaid,
        "low",
        "Run the worker (or 'expire_orders') to release the reserved stock.",
        _admin("shop_order_changelist", "status__exact=pending"),
        lambda o: f"{o.number} · expired {_age(o.expires_at, now)} ago",
    )

    # --- Returns ---------------------------------------------------------------
    new_returns = ReturnRequest.objects.filter(status="requested").select_related("item__order")
    add(
        "returns_requested",
        "Return / exchange requests to review",
        new_returns,
        "high" if new_returns.filter(created__lt=now - timedelta(hours=48)).exists() else "medium",
        "Approve or reject each request with a note to the customer.",
        _admin("shop_returnrequest_changelist", "status__exact=requested"),
        lambda r: f"{r.item.order.number} · {r.get_kind_display()} · {r.reason[:50]}",
    )
    received = ReturnRequest.objects.filter(status="received").select_related("item__order")
    add(
        "returns_to_inspect",
        "Returned items waiting for inspection",
        received,
        "medium",
        "Inspect the parcel and complete the return to trigger refund/exchange.",
        _admin("shop_returnrequest_changelist", "status__exact=received"),
        lambda r: f"{r.item.order.number} · {r.get_kind_display()}",
    )

    # --- Inventory -------------------------------------------------------------
    out = Variant.objects.filter(stock=0, product__active=True).select_related("product")
    add(
        "out_of_stock",
        "Active variants out of stock",
        out,
        "high",
        "Restock or hide these sizes — customers cannot buy them.",
        _admin("shop_variant_changelist", "stock=0"),
        lambda v: f"{v.product.name} / {v.size} / {v.colour}",
    )
    low = Variant.objects.filter(
        stock__gt=0, stock__lt=rules.low_stock_threshold, product__active=True
    ).select_related("product").order_by("stock")
    add(
        "low_stock",
        f"Low stock (below {rules.low_stock_threshold} units)",
        low,
        "medium",
        "Reorder from suppliers before these sell out.",
        _admin("shop_variant_changelist"),
        lambda v: f"{v.product.name} / {v.size} / {v.colour} · {v.stock} left",
    )
    restock = RestockPlan.objects.filter(status="proposed").select_related("variant__product")
    add(
        "restock_plans",
        "Restock plans waiting for receipt",
        restock,
        "medium",
        "Confirm receipt of stock when the supplier delivers.",
        reverse("operations"),
        lambda p: f"{p.variant.product.name} / {p.variant.size} · +{p.quantity}",
    )
    pos = PurchaseOrder.objects.filter(status__in=["draft", "approved"]).select_related("quote")
    add(
        "purchase_orders",
        "Purchase orders to approve or receive",
        pos,
        "low",
        "Approve drafts and record receipts of approved POs.",
        _admin("shop_purchaseorder_changelist"),
        lambda p: f"PO #{p.pk} · {p.get_status_display()} · qty {p.quantity}",
    )

    # --- Operations / automation ----------------------------------------------
    tasks = WorkTask.objects.filter(status="open").select_related("order")
    add(
        "work_tasks",
        "Open staff tasks (packing, inspection…)",
        tasks,
        "medium",
        "Complete each physical task in the Operations centre.",
        reverse("operations"),
        lambda t: f"{t.title[:70]} · {_age(t.created, now)}",
    )
    failed = AgentJob.objects.filter(state__in=["review", "failed"])
    add(
        "failed_jobs",
        "Automation jobs failed / needing review",
        failed,
        "high",
        "Open Operations, read the error and retry or resolve each job.",
        reverse("operations"),
        lambda j: f"{j.agent}: {j.kind} · {j.error[:60] or j.state}",
    )
    actions = AgentAction.objects.filter(state="pending", expires_at__gt=now)
    add(
        "agent_actions",
        "AI-proposed actions awaiting approval",
        actions,
        "medium",
        "Review and confirm or reject each proposal.",
        _admin("shop_agentaction_changelist", "state__exact=pending"),
        lambda a: a.summary[:80],
    )
    shipments = Shipment.objects.filter(state__in=["failed", "review", "exception", "error"])
    add(
        "shipment_exceptions",
        "Shipment exceptions",
        shipments,
        "high",
        "Contact the courier or rebook these shipments.",
        _admin("shop_shipment_changelist"),
        lambda s: f"{s.order.number} · {s.state} · {s.courier or s.provider}",
    )
    risk = RiskReport.objects.filter(
        reviewed=False, score__gte=rules.fraud_review_threshold
    ).select_related("order")
    add(
        "risk_reports",
        "High-risk orders not reviewed",
        risk,
        "high",
        "Check the risk report signals before shipping.",
        reverse("intelligence"),
        lambda r: f"{r.order.number} · score {r.score}",
    )

    # --- Customers & marketing -------------------------------------------------
    enquiries = Enquiry.objects.filter(resolved=False).order_by("created")
    add(
        "enquiries",
        "Unanswered customer enquiries",
        enquiries,
        "high" if enquiries.filter(created__lt=now - timedelta(hours=24)).exists() else "medium",
        "Reply to the customer by email, then mark the enquiry resolved.",
        _admin("shop_enquiry_changelist", "resolved__exact=0"),
        lambda e: f"{e.name} · {e.message[:60]} · {_age(e.created, now)}",
    )
    if hasattr(Review, "approved"):
        reviews = Review.objects.filter(approved=False)
        add(
            "reviews",
            "Product reviews to moderate",
            reviews,
            "low",
            "Approve or remove new reviews.",
            _admin("shop_review_changelist"),
            lambda r: f"{r.product} · {r.text[:50]}",
        )
    drafts = Campaign.objects.filter(status="draft")
    add(
        "campaign_drafts",
        "Marketing campaign drafts",
        drafts,
        "low",
        "Review the AI-written drafts and approve or discard them.",
        _admin("shop_campaign_changelist"),
        lambda c: str(c)[:70],
    )

    items.sort(key=lambda i: (PRIORITY_ORDER[i["priority"]], -i["count"]))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    paid_today = Order.objects.filter(
        created__gte=today, payment_status=Order.Payment.PAID
    )
    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "total_items": sum(i["count"] for i in items),
        "high": sum(1 for i in items if i["priority"] == "high"),
        "queues": items,
        "today": {
            "paid_orders": paid_today.count(),
            "revenue_inr": str(paid_today.aggregate(s=Sum("total"))["s"] or 0),
        },
    }


def local_briefing(data):
    queues = data["queues"]
    if not queues:
        return "All clear — nothing is waiting for staff right now. 🎉"
    lines = [
        f"{data['total_items']} items are waiting across {len(queues)} queues "
        f"({data['high']} high-priority). Suggested order of work:"
    ]
    for n, q in enumerate(queues[:8], 1):
        lines.append(f"{n}. [{q['priority'].upper()}] {q['title']} ({q['count']}) — {q['action']}")
    return "\n".join(lines)


def briefing(data=None):
    """Return (text, engine). Uses Gemini when configured, else rules."""
    data = data or collect()
    if settings.AI_MODE != "gemini" or not settings.GEMINI_API_KEY or not data["queues"]:
        return local_briefing(data), "Rules-based"
    import json

    from .llm_graph import make_model, text_content

    prompt = (
        "You are the Pending-Work Agent for Aarohi Boutique, an Indian women's clothing store. "
        "Below is a JSON snapshot of every queue waiting for staff. Write a short morning briefing "
        "for the store admin: one opening sentence with totals, then a numbered to-do list (max 7 items) "
        "in priority order, each with the count and the concrete next step. Mention order numbers when "
        "examples are given. Use only the data provided; do not invent numbers. Plain text, no markdown tables.\n\n"
        + json.dumps(data, ensure_ascii=False)[:12000]
    )
    try:
        reply = make_model("gemini").invoke(prompt)
        text = text_content(reply).strip()
        if text:
            return text, f"Gemini ({settings.GEMINI_MODEL})"
    except Exception:  # network/quota problems must not break the admin page
        pass
    return local_briefing(data) + "\n\n(AI briefing unavailable — showing rules-based plan.)", "Rules-based"


def install(module):
    """Register the agent + tool with the existing chat agent registry."""
    from .business_agents import REPORTS
    from .commerce_agents import ACTION_SCOPES, STAFF_AGENTS

    REPORTS["pending_work"] = collect
    module.TOOL_SCHEMAS["pending_work"] = module.function_schema(
        "pending_work",
        "Staff-only: list every queue waiting for a human (orders to pack, returns, refunds, low stock, "
        "failed jobs, enquiries, AI proposals) with counts, priority, examples and admin links.",
        {},
    )
    STAFF_AGENTS.add("pending")
    ACTION_SCOPES.setdefault("pending", set())
    module.AGENTS["pending"] = module.AgentSpec(
        "Pending Work Agent",
        "You tell store staff exactly which work is pending. Always call pending_work first. "
        "Answer with a prioritised to-do list (high → low) giving counts, example order numbers and the "
        "next step for each queue. Use only tool data; if nothing is pending, say so.",
        ("pending_work",),
    )
    admin = module.AGENTS.get("admin")
    if admin and "pending_work" not in admin.tools:
        module.AGENTS["admin"] = module.AgentSpec(
            admin.name, admin.instruction, admin.tools + ("pending_work",)
        )

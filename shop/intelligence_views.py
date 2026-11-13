from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from .models import Product, TryOnRequest
from .business_agents import REPORTS
from .agents import has_staff_access
from .agent_views import consume_quota
from .intelligence import business_metrics
from .vision import clean_image, visual_search, submit_tryon, refresh_tryon, VisionError

CAPABILITIES = [
    (
        "Personal Shopping",
        "Find, compare and select real products by budget and size.",
        "assistant",
        "stylist",
    ),
    (
        "Visual Search",
        "Upload a garment image. Use local colour matching or the Gemini vision model.",
        "ai_studio",
        "",
    ),
    (
        "Virtual Try-On",
        "Generate a private garment preview with the configured image provider.",
        "ai_studio",
        "",
    ),
    (
        "Voice Shopping",
        "Editable browser voice input linked to search and guarded cart actions.",
        "assistant",
        "voice",
    ),
    (
        "Business Intelligence",
        "Collected sales, refunds, known costs and customer purchase segments.",
        "intelligence",
        "business_metrics",
    ),
    (
        "Demand Forecasting",
        "Historical-demand estimates with backtest error; sparse data is flagged.",
        "intelligence",
        "demand_forecast",
    ),
    (
        "Supplier Intelligence",
        "Comparable landed costs, MOQ, lead time and recorded delivery quality.",
        "intelligence",
        "supplier_comparison",
    ),
    (
        "Pricing Intelligence",
        "Recorded competitor observations and a cost-based margin floor.",
        "intelligence",
        "pricing_intelligence",
    ),
    (
        "Inventory Optimization",
        "Reorder suggestions based on demand, lead time and approved incoming stock.",
        "intelligence",
        "inventory_optimization",
    ),
    (
        "Marketing Strategy",
        "Opt-in audience segments, campaign drafts and approved outbox delivery.",
        "intelligence",
        "marketing_strategy",
    ),
    (
        "Customer Retention",
        "Inactive/repeat buyer segments and approved retention campaigns.",
        "intelligence",
        "retention_insights",
    ),
    (
        "Cart Recovery",
        "Consent-based abandoned-bag reminders and eligible existing offers.",
        "assistant",
        "recovery",
    ),
    (
        "Content Generation",
        "Draft product copy, SEO title, metadata and tags; publish after approval.",
        "assistant",
        "content",
    ),
    (
        "Fraud Investigation",
        "Explainable review signals and fulfillment holds; no automatic accusations/bans.",
        "intelligence",
        "fraud_investigation",
    ),
    (
        "Return Investigation",
        "Policy, recorded purchase history and return reasons for staff review.",
        "intelligence",
        "return_investigation",
    ),
    (
        "Logistics Optimization",
        "Shipment delays and a coordinate-based local delivery route heuristic.",
        "intelligence",
        "logistics_optimization",
    ),
    (
        "Autonomous Business Manager",
        "Combine evidence into daily reports, priority tasks and guarded action plans.",
        "intelligence",
        "business_manager",
    ),
]


@login_required
def agent_hub(request):
    return render(
        request,
        "shop/agent_hub.html",
        {
            "capabilities": agent_capabilities(request.user),
            "is_store_staff": has_staff_access(request.user),
        },
    )


@login_required
def intelligence(request):
    if not has_staff_access(request.user):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
    name = request.GET.get("report", "business_metrics")
    if name not in REPORTS:
        name = "business_metrics"
    report = REPORTS[name]()
    rows = next(
        (
            value
            for value in report.values()
            if isinstance(value, list) and value and isinstance(value[0], dict)
        ),
        [],
    )
    columns = list(rows[0]) if rows else []
    values = [
        [
            (
                ", ".join(str(item) for item in row.get(column, []))
                if isinstance(row.get(column), list)
                else row.get(column, "")
            )
            for column in columns
        ]
        for row in rows
    ]
    sections = []
    facts = []
    for key, value in report.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            keys = list(value[0])
            sections.append(
                {
                    "title": key.replace("_", " ").title(),
                    "columns": [item.replace("_", " ").title() for item in keys],
                    "rows": [
                        [
                            (
                                ", ".join(map(str, row.get(column, [])))
                                if isinstance(row.get(column), list)
                                else row.get(column, "")
                            )
                            for column in keys
                        ]
                        for row in value
                    ],
                }
            )
        elif isinstance(value, dict):
            facts += [
                (
                    key.replace("_", " ").title() + " · " + subkey.replace("_", " "),
                    subvalue,
                )
                for subkey, subvalue in value.items()
                if not isinstance(subvalue, (dict, list))
            ]
    return render(
        request,
        "shop/intelligence.html",
        {
            "metrics": business_metrics(),
            "sections": sections,
            "facts": facts,
            "report_name": name.replace("_", " ").title(),
            "report_options": [(key, key.replace("_", " ").title()) for key in REPORTS],
            "selected_report": name,
            "columns": [column.replace("_", " ").title() for column in columns],
            "rows": values,
            "notes": [
                (key.replace("_", " ").title(), value)
                for key, value in report.items()
                if not isinstance(value, (list, dict))
            ],
            "report": report,
        },
    )


@login_required
def ai_studio(request):
    matches, method, query = [], "", ""
    if request.method == "POST":
        try:
            if "image" not in request.FILES:
                raise VisionError("Choose a garment image.")
            if not consume_quota(request.user):
                raise VisionError("Hourly assistant limit reached.")
            if request.POST.get("mode") == "vision" and not request.POST.get("vision_consent"):
                raise VisionError("Consent to Google Gemini image processing is required for vision mode.")
            content = clean_image(request.FILES["image"])
            result = visual_search(
                content, semantic=request.POST.get("mode") == "vision"
            )
            matches, method, query = (
                result["products"],
                result["method"],
                result["query"],
            )
        except VisionError as exc:
            messages.error(request, str(exc))
    return render(
        request,
        "shop/ai_studio.html",
        {
            "matches": matches,
            "method": method,
            "vision_query": query,
            "products": Product.objects.filter(
                active=True, category__active=True
            ).exclude(image=""),
            "tryons": TryOnRequest.objects.filter(user=request.user).order_by(
                "-created"
            )[:10],
            "tryon_configured": bool(settings.FASHN_API_KEY),
        },
    )


@login_required
@require_POST
def start_tryon(request):
    from django.utils import timezone

    if not settings.FASHN_API_KEY:
        messages.error(
            request,
            "Virtual try-on needs the FASHN provider configuration. No preview was generated or billed.",
        )
        return redirect("ai_studio")
    if not request.POST.get("photo_consent"):
        messages.error(
            request,
            "Confirm photo ownership/permission and consent to provider processing.",
        )
        return redirect("ai_studio")
    from datetime import timedelta

    if (
        TryOnRequest.objects.filter(
            user=request.user, created__gte=timezone.now() - timedelta(days=1)
        ).count()
        >= 3
    ):
        messages.error(request, "Daily try-on limit reached (3 requests).")
        return redirect("ai_studio")
    try:
        try:
            product_id = int(request.POST.get("product", ""))
        except (ValueError, TypeError):
            raise VisionError("Choose a valid catalogue product.")
        product = get_object_or_404(Product, pk=product_id, active=True)
        if "person" not in request.FILES:
            raise VisionError("Choose your photo.")
        content = clean_image(request.FILES["person"])
        record = TryOnRequest.objects.create(user=request.user, product=product)
        submit_tryon(record, content)
        messages.success(
            request,
            "Try-on submitted. Refresh its status below; it will not be submitted twice.",
        )
    except VisionError as exc:
        messages.error(request, str(exc))
    return redirect("ai_studio")


@login_required
@require_POST
def poll_tryon(request, pk):
    record = get_object_or_404(TryOnRequest, pk=pk, user=request.user)
    try:
        refresh_tryon(record)
    except VisionError as exc:
        messages.error(request, str(exc))
    return redirect("ai_studio")


@login_required
def tryon_image(request, pk):
    record = get_object_or_404(
        TryOnRequest, pk=pk, user=request.user, status="completed"
    )
    path = Path(settings.PRIVATE_MEDIA_ROOT) / f"tryon-{record.pk}.jpg"
    if not path.is_file():
        raise Http404
    response = FileResponse(path.open("rb"), content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_POST
def delete_tryon(request, pk):
    record = get_object_or_404(TryOnRequest, pk=pk, user=request.user)
    path = Path(settings.PRIVATE_MEDIA_ROOT) / f"tryon-{record.pk}.jpg"
    path.unlink(missing_ok=True)
    record.delete()
    messages.success(
        request,
        "Your saved preview was deleted from this application. Provider retention follows its own policy.",
    )
    return redirect("ai_studio")


def agent_capabilities(user):
    from .agents import AGENTS
    from .autonomous import CUSTOMER_AGENTS, ADMIN_AGENTS
    descriptions = {
        'stylist': 'Discover real stock, compare variants and prepare a personal order.',
        'cart': 'Update your bag, apply eligible offers and prepare secure checkout.',
        'support': 'Track your orders and request supported account actions.',
        'payments': 'Read verified payments and guide secure payment completion.',
        'returns': 'Request eligible returns and exchanges with policy checks.',
        'voice': 'Shop by Tamil, English or Tanglish voice and text.',
        'pending': 'See every task waiting for staff, prioritised into a to-do list.',
        'admin': 'Coordinate paid-order work, exceptions and physical task approvals.',
        'inventory': 'Monitor stock, create restock plans and confirm received goods.',
        'shipping': 'Book configured couriers after packing approval and sync tracking.',
        'content': 'Execute authorized catalogue descriptions and SEO updates.',
        'business': 'Analyze measured sales, margin and recorded business activity.',
        'forecast': 'Estimate demand from historical orders with uncertainty labels.',
        'suppliers': 'Compare suppliers and prepare reviewed purchase orders.',
        'pricing': 'Compare recorded costs and prepare approved price changes.',
        'marketing': 'Save campaign drafts and send only approved opt-in campaigns.',
        'retention': 'Analyze repeat buyers and draft retention campaigns.',
        'fraud': 'Review order risk and require staff approval to release holds.',
        'return_investigator': 'Assess return eligibility and inspection requirements.',
    }
    keys = CUSTOMER_AGENTS + (ADMIN_AGENTS if has_staff_access(user) else ())
    return [(AGENTS[key].name, descriptions[key], 'assistant', key) for key in keys]

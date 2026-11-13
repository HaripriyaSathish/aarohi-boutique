from django.db.models import Sum
from .models import Category, Order, Product, StoreSettings, Variant


def active_nav(request):
    """Which navbar item matches the current page (for highlighting)."""
    match = getattr(request, "resolver_match", None)
    name = match.url_name if match else ""
    if name == "home":
        return "home"
    if name in ("shop", "product", "product_preview"):
        if request.GET.get("sort") == "new":
            return "new"
        if request.GET.get("occasion") == "Festive":
            return "festive"
        return "collections"
    if name == "information" and match.kwargs.get("page") == "about":
        return "story"
    if name in ("agent_hub", "ai_studio", "intelligence"):
        return "studio"
    if name in ("assistant", "journey_review", "action_review"):
        return "assistant"
    if name == "wishlist":
        return "wishlist"
    if name in ("cart", "checkout", "payment"):
        return "cart"
    if name in ("pending_board", "operations"):
        return "tasks"
    if name in ("account", "order", "addresses", "notifications", "password_change", "invoice", "return"):
        return "account"
    return ""


def store_context(request):
    context = {
        "nav_active": active_nav(request),
        "store": StoreSettings.objects.first(),
        "nav_categories": Category.objects.filter(active=True),
        "bag_count": sum(
            int(value) for value in request.session.get("cart", {}).values()
        ),
    }
    if request.user.is_staff:
        context["dashboard_sales"] = (
            Order.objects.filter(payment_status="paid").aggregate(total=Sum("total"))[
                "total"
            ]
            or 0
        )
        context["dashboard_orders"] = Order.objects.count()
        context["dashboard_products"] = Product.objects.filter(active=True).count()
        context["dashboard_low_stock"] = Variant.objects.filter(stock__lt=5).count()
    return context

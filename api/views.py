"""JSON endpoints for the Aarohi mobile app.

Reuses shop models, forms and services so business rules (stock reservation,
coupons, shipping, cancellation, returns) are identical to the website.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core import signing
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from shop.forms import AddressForm, CheckoutForm, ContactForm, RegisterForm, ReturnForm, ReviewForm
from shop.models import (
    Address,
    Banner,
    Category,
    Order,
    OrderItem,
    Product,
    ProductImage,
    ReturnRequest,
    Review,
    StoreSettings,
    Variant,
    Wishlist,
)
from shop.services import ShopError, cancel_order, create_order, mark_paid, quote

from . import payments
from .serializers import (
    AddressSerializer,
    BannerSerializer,
    CategorySerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    ProductCardSerializer,
    ProductDetailSerializer,
    ReturnSerializer,
    StoreSerializer,
    product_image,
)


# ---------------------------------------------------------------- helpers

def error(message, code=status.HTTP_400_BAD_REQUEST, errors=None):
    body = {"detail": message}
    if errors:
        body["errors"] = errors
    return Response(body, status=code)


def form_error(form):
    errors = {k: [str(m) for m in v] for k, v in form.errors.items()}
    first = next(iter(errors.values()), ["Please check the form."])[0]
    return error(first, errors=errors)


def tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def user_data(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


def ctx(request):
    return {"request": request}


def product_queryset():
    return (
        Product.objects.filter(active=True, category__active=True)
        .select_related("category")
        .prefetch_related(
            Prefetch("variants", queryset=Variant.objects.order_by("id")),
            Prefetch("gallery", queryset=ProductImage.objects.order_by("id")),
        )
    )


def parse_cart(items):
    """[{variant, quantity}] -> {"<variant_id>": qty} (merging duplicates)."""
    cart = {}
    if not isinstance(items, list):
        raise ShopError("Cart must be a list of items.")
    for raw in items[:50]:
        try:
            vid = int(raw.get("variant"))
            qty = int(raw.get("quantity", 1))
        except (TypeError, ValueError, AttributeError):
            raise ShopError("Invalid cart item.")
        cart[str(vid)] = cart.get(str(vid), 0) + qty
    return cart


def available_cart(cart):
    """Split cart into purchasable items and unavailable variant ids."""
    ids = [int(k) for k in cart]
    ok = set(
        Variant.objects.filter(
            pk__in=ids, product__active=True, product__category__active=True
        ).values_list("pk", flat=True)
    )
    return {k: v for k, v in cart.items() if int(k) in ok}, [i for i in ids if i not in ok]


def quote_payload(request, data, unavailable=()):
    lines = []
    for line in data["lines"]:
        variant = line["variant"]
        product = variant.product
        lines.append(
            {
                "variant": variant.pk,
                "product": product.pk,
                "slug": product.slug,
                "name": product.name,
                "image": product_image(request, product),
                "size": variant.size,
                "colour": variant.colour,
                "price": str(product.price),
                "quantity": line["quantity"],
                "stock": variant.stock,
                "total": str(line["total"]),
            }
        )
    return {
        "lines": lines,
        "unavailable": list(unavailable),
        "subtotal": str(data["subtotal"]),
        "discount": str(data["discount"]),
        "shipping": str(data["shipping"]),
        "total": str(data["total"]),
        "coupon": data["coupon"].code if data["coupon"] else None,
        "shipping_remaining": str(data["shipping_remaining"]),
        "shipping_progress": float(data["shipping_progress"]),
    }


def expire_if_needed(order):
    if order.status == "pending" and order.payment_status == "pending" and order.expires_at <= timezone.now():
        cancel_order(order.pk)
        order.refresh_from_db()
    return order


# ---------------------------------------------------------------- store & catalog

@api_view(["GET"])
def store(request):
    settings_obj = StoreSettings.objects.first() or StoreSettings()
    data = StoreSerializer(settings_obj, context=ctx(request)).data
    data["payment_mode"] = settings.PAYMENT_MODE
    data["cashfree_env"] = payments.cashfree_mode()
    return Response(data)


@api_view(["GET"])
def home(request):
    products = product_queryset()
    settings_obj = StoreSettings.objects.first() or StoreSettings()
    return Response(
        {
            "store": StoreSerializer(settings_obj, context=ctx(request)).data,
            "banners": BannerSerializer(
                Banner.objects.filter(active=True).select_related("category"), many=True, context=ctx(request)
            ).data,
            "categories": CategorySerializer(
                Category.objects.filter(active=True), many=True, context=ctx(request)
            ).data,
            "featured": ProductCardSerializer(products.filter(featured=True)[:8], many=True, context=ctx(request)).data,
            "new_arrivals": ProductCardSerializer(products[:8], many=True, context=ctx(request)).data,
        }
    )


@api_view(["GET"])
def categories(request):
    return Response(
        CategorySerializer(Category.objects.filter(active=True), many=True, context=ctx(request)).data
    )


class ProductPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50


@api_view(["GET"])
def products(request):
    qs = product_queryset()
    params = request.query_params
    for key, field in {"category": "category__slug", "fabric": "fabric", "occasion": "occasion"}.items():
        value = params.get(key, "").strip()
        if value:
            qs = qs.filter(**{field: value})
    query = params.get("q", "").strip()
    if query:
        qs = qs.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
            | Q(category__name__icontains=query) | Q(tags__icontains=query)
        )
    try:
        for name, lookup in [("min", "price__gte"), ("max", "price__lte")]:
            raw = params.get(name)
            if raw:
                value = Decimal(raw)
                if not value.is_finite() or value < 0:
                    raise ValueError
                qs = qs.filter(**{lookup: value})
    except (InvalidOperation, ValueError):
        return error("Enter valid non-negative prices.")
    if params.get("size"):
        qs = qs.filter(variants__size=params["size"])
    if params.get("colour"):
        qs = qs.filter(variants__colour=params["colour"])
    if params.get("in_stock") in ("1", "true"):
        qs = qs.filter(variants__stock__gt=0)
    if params.get("featured") in ("1", "true"):
        qs = qs.filter(featured=True)
    sorting = {"price": "price", "-price": "-price", "name": "name", "new": "-created"}
    qs = qs.order_by(sorting.get(params.get("sort"), "-created"), "-id").distinct()
    paginator = ProductPagination()
    page = paginator.paginate_queryset(qs, request)
    return paginator.get_paginated_response(ProductCardSerializer(page, many=True, context=ctx(request)).data)


@api_view(["GET"])
def product_filters(request):
    active = Product.objects.filter(active=True, category__active=True)
    return Response(
        {
            "categories": CategorySerializer(Category.objects.filter(active=True), many=True, context=ctx(request)).data,
            "fabrics": sorted(set(active.values_list("fabric", flat=True))),
            "occasions": sorted(set(active.values_list("occasion", flat=True))),
            "sizes": sorted(set(Variant.objects.filter(product__in=active).values_list("size", flat=True))),
            "colours": sorted(set(Variant.objects.filter(product__in=active).values_list("colour", flat=True))),
        }
    )


@api_view(["GET"])
def product_detail(request, slug):
    item = get_object_or_404(product_queryset(), slug=slug)
    data = ProductDetailSerializer(item, context=ctx(request)).data
    related = product_queryset().filter(category=item.category).exclude(pk=item.pk)[:6]
    data["related"] = ProductCardSerializer(related, many=True, context=ctx(request)).data
    return Response(data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def product_review(request, pk):
    item = get_object_or_404(Product, pk=pk, active=True)
    purchased = OrderItem.objects.filter(
        order__user=request.user, order__status="delivered", variant__product=item
    ).exists()
    if not purchased:
        return error("Reviews require a delivered purchase of this product.", status.HTTP_403_FORBIDDEN)
    form = ReviewForm(request.data)
    if not form.is_valid():
        return form_error(form)
    Review.objects.update_or_create(user=request.user, product=item, defaults=form.cleaned_data)
    return Response({"detail": "Thank you. Your review was saved."})


@api_view(["GET"])
def delivery_check(request):
    postcode = request.query_params.get("postcode", "")
    valid = len(postcode) == 6 and postcode.isdigit() and not postcode.startswith("0")
    return Response(
        {
            "valid": valid,
            "message": (
                "Delivery normally takes 4–7 business days."
                if valid
                else "Enter a valid six-digit Indian PIN code."
            ),
        }
    )


@api_view(["POST"])
def contact(request):
    form = ContactForm(request.data)
    if not form.is_valid():
        return form_error(form)
    form.save()
    return Response({"detail": "Your message has been sent to the store."}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- auth

@api_view(["POST"])
@throttle_classes([ScopedRateThrottle])
def register(request):
    data = request.data
    form = RegisterForm(
        {
            "username": data.get("username", ""),
            "email": data.get("email", ""),
            "first_name": data.get("first_name", ""),
            "last_name": data.get("last_name", ""),
            "password1": data.get("password", ""),
            "password2": data.get("password2", data.get("password", "")),
        }
    )
    if not form.is_valid():
        errors = {k: [str(m) for m in v] for k, v in form.errors.items()}
        if "password1" in errors:
            errors["password"] = errors.pop("password1")
        first = next(iter(errors.values()))[0]
        return error(first, errors=errors)
    user = form.save()
    return Response({"user": user_data(user), **tokens_for(user)}, status=status.HTTP_201_CREATED)


register.cls.throttle_scope = "auth"


@api_view(["POST"])
@throttle_classes([ScopedRateThrottle])
def login(request):
    identifier = str(request.data.get("username", "")).strip()
    password = str(request.data.get("password", ""))
    if not identifier or not password:
        return error("Enter your username or email and password.")
    username = identifier
    if "@" in identifier:
        match = User.objects.filter(email__iexact=identifier).first()
        if match:
            username = match.username
    user = authenticate(request, username=username, password=password)
    if not user or not user.is_active:
        return error("Incorrect username/email or password.", status.HTTP_401_UNAUTHORIZED)
    return Response({"user": user_data(user), **tokens_for(user)})


login.cls.throttle_scope = "auth"


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def me(request):
    user = request.user
    if request.method == "PATCH":
        user.first_name = str(request.data.get("first_name", user.first_name))[:150]
        user.last_name = str(request.data.get("last_name", user.last_name))[:150]
        user.save(update_fields=["first_name", "last_name"])
    return Response(user_data(user))


# ---------------------------------------------------------------- cart & checkout

@api_view(["POST"])
def cart_quote(request):
    try:
        cart, unavailable = available_cart(parse_cart(request.data.get("items", [])))
        coupon = str(request.data.get("coupon", "") or "")
        try:
            data = quote(cart, coupon)
            coupon_error = None
        except ShopError as exc:
            if not coupon:
                raise
            data = quote(cart, "")  # show totals without the bad coupon
            coupon_error = str(exc)
    except ShopError as exc:
        return error(str(exc))
    payload = quote_payload(request, data, unavailable)
    payload["coupon_error"] = coupon_error
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def checkout(request):
    data = request.data
    try:
        cart, unavailable = available_cart(parse_cart(data.get("items", [])))
    except ShopError as exc:
        return error(str(exc))
    if unavailable:
        return error("Some items are no longer available. Please review your bag.", errors={"unavailable": unavailable})
    if not cart:
        return error("Your shopping bag is empty.")
    address = data.get("address") or {}
    if data.get("address_id"):
        saved = Address.objects.filter(pk=data["address_id"], user=request.user).first()
        if not saved:
            return error("Address not found.")
        address = AddressSerializer(saved).data
    form = CheckoutForm({**address, "email": data.get("email") or request.user.email})
    if not form.is_valid():
        return form_error(form)
    try:
        order = create_order(request.user, cart, str(data.get("coupon", "") or ""), form.cleaned_data)
    except ShopError as exc:
        return error(str(exc))
    if data.get("save_address") and not data.get("address_id"):
        Address.objects.create(
            user=request.user,
            **{k: form.cleaned_data.get(k, "") for k in ["name", "phone", "line1", "line2", "city", "state", "postcode"]},
        )
    if order.total == 0:
        mark_paid(order.pk, "no-charge-" + str(order.pk))
        order.refresh_from_db()
    return Response(OrderDetailSerializer(order, context=ctx(request)).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- payment

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def order_pay(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    expire_if_needed(order)
    if order.payment_status != "pending" or order.status == "cancelled":
        return error("This order can no longer be paid.")
    if order.payment_mode == "demo":
        return Response({"mode": "demo", "demo_allowed": settings.DEBUG and settings.PAYMENT_MODE == "demo"})
    if order.payment_mode != "cashfree":
        return error("This order's payment method is not supported in the app. Please pay on the website.")
    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=pk)
            session_id = payments.ensure_mobile_cashfree_order(locked, request)
    except payments.CashfreeError as exc:
        return error(str(exc), status.HTTP_502_BAD_GATEWAY)
    if not session_id:
        return Response({"mode": "cashfree", "already_paid": True})
    return Response(
        {
            "mode": "cashfree",
            "environment": payments.cashfree_mode(),
            "payment_session_id": session_id,
            "checkout_url": payments.checkout_url(request, order, session_id, request.data.get("return_url", "")),
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def order_verify(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    message = ""
    if order.payment_status == "pending" and order.payment_mode == "cashfree" and order.provider_order:
        from shop.cashfree import verify_cashfree

        try:
            verify_cashfree(order)
            message = "Payment successful! Your order is confirmed."
        except ShopError as exc:
            message = str(exc)
        order.refresh_from_db()
    expire_if_needed(order)
    return Response(
        {
            "paid": order.payment_status == "paid",
            "message": message,
            "order": OrderDetailSerializer(order, context=ctx(request)).data,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def order_demo_pay(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if not (settings.DEBUG and settings.PAYMENT_MODE == "demo" and order.payment_mode == "demo"):
        return error("Demo payments are disabled.", status.HTTP_403_FORBIDDEN)
    expire_if_needed(order)
    if order.status == "cancelled":
        return error("Order expired or cancelled.")
    if request.data.get("result") == "success":
        mark_paid(order.pk, "demo-" + str(order.pk))
    order.refresh_from_db()
    return Response({"paid": order.payment_status == "paid", "order": OrderDetailSerializer(order, context=ctx(request)).data})


def pay_start(request):
    """HTML page opened in the in-app browser; launches Cashfree checkout."""
    try:
        data = payments.read_checkout_token(request.GET.get("t", ""))
    except signing.BadSignature:
        return HttpResponse("This payment link has expired. Go back to the app and tap Pay again.", status=400)
    order = get_object_or_404(Order, pk=data["o"])
    response = render(
        request,
        "api/pay_start.html",
        {"order": order, "session": data["s"], "mode": payments.cashfree_mode()},
    )
    redirect = payments.safe_app_redirect(data.get("r", ""))
    if redirect:
        response.set_signed_cookie(
            payments.RETURN_COOKIE, redirect, salt=payments.SIGN_SALT,
            max_age=30 * 60, httponly=True, samesite="Lax",
        )
    return response


def pay_return(request, pk):
    """Cashfree redirects here; verify server-to-server, then go back to the app."""
    order = get_object_or_404(Order, pk=pk)
    if order.payment_status == "pending" and order.payment_mode == "cashfree" and order.provider_order:
        from shop.cashfree import verify_cashfree

        try:
            verify_cashfree(order)
        except ShopError:
            pass
        order.refresh_from_db()
    app_url = payments.safe_app_redirect(
        request.get_signed_cookie(payments.RETURN_COOKIE, default="", salt=payments.SIGN_SALT)
    )
    if app_url:
        sep = "&" if "?" in app_url else "?"
        app_url = f"{app_url}{sep}order={order.pk}"
        # Chrome on Android won't open exp:// or vetri:// links from a web page;
        # its intent:// form launches the app (Expo Go or the installed Vetri app).
        if "android" in request.META.get("HTTP_USER_AGENT", "").lower():
            app_url = payments.android_intent_url(app_url)
    return render(
        request,
        "api/pay_return.html",
        {"order": order, "paid": order.payment_status == "paid", "app_url": app_url},
    )


# ---------------------------------------------------------------- orders

def user_orders(request):
    return (
        Order.objects.filter(user=request.user)
        .prefetch_related("items__variant__product__gallery", "events")
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def orders(request):
    paginator = PageNumberPagination()
    page = paginator.paginate_queryset(user_orders(request), request)
    return paginator.get_paginated_response(OrderListSerializer(page, many=True, context=ctx(request)).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_detail(request, pk):
    order = get_object_or_404(user_orders(request), pk=pk)
    expire_if_needed(order)
    return Response(OrderDetailSerializer(order, context=ctx(request)).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def order_cancel(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    try:
        cancel_order(order.pk)
    except ShopError as exc:
        return error(str(exc))
    order.refresh_from_db()
    return Response(
        {
            "detail": "Order cancelled. If you already paid, the store will process your refund.",
            "order": OrderDetailSerializer(order, context=ctx(request)).data,
        }
    )


INVOICE_SALT = "aarohi.mobile.invoice"


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def order_invoice_link(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if order.payment_status not in ("paid", "partial_refund", "refunded"):
        return error("Invoice is available after payment.", status.HTTP_403_FORBIDDEN)
    token = signing.dumps(str(order.pk), salt=INVOICE_SALT)
    return Response({"url": request.build_absolute_uri(reverse("api:invoice_pdf", args=[token]))})


def invoice_pdf(request, token):
    try:
        pk = signing.loads(token, salt=INVOICE_SALT, max_age=10 * 60)
    except signing.BadSignature:
        raise Http404
    order = get_object_or_404(Order, pk=pk)
    from shop.invoices import invoice_pdf as make_pdf, issue_invoice

    try:
        content = make_pdf(issue_invoice(order))
    except ShopError as exc:
        return HttpResponse(str(exc), status=403)
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{order.number}.pdf"'
    return response


# ---------------------------------------------------------------- returns

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def returns(request):
    items = ReturnRequest.objects.filter(item__order__user=request.user).select_related("item__order").order_by("-created")
    return Response(ReturnSerializer(items, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def return_create(request, item_id):
    item = get_object_or_404(OrderItem, pk=item_id, order__user=request.user)
    store_obj = StoreSettings.objects.first()
    days = store_obj.return_days if store_obj else 7
    order = item.order
    if order.status != "delivered" or not order.delivered_at or timezone.now() > order.delivered_at + timedelta(days=days):
        return error(f"Returns are available within {days} days of delivery.")
    if ReturnRequest.objects.filter(item=item).exists():
        return error("A request for this item already exists.")
    form = ReturnForm(request.data)
    if not form.is_valid():
        return form_error(form)
    if form.cleaned_data["kind"] == "exchange" and not Variant.objects.filter(
        product=item.variant.product, colour=item.colour, size=form.cleaned_data["requested_size"]
    ).exists():
        return error("This size is not offered for this product.", errors={"requested_size": ["Size not offered."]})
    result = form.save(commit=False)
    result.item = item
    result.save()
    return Response(ReturnSerializer(result).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- addresses

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def addresses(request):
    if request.method == "POST":
        form = AddressForm(request.data)
        if not form.is_valid():
            return form_error(form)
        address = form.save(commit=False)
        address.user = request.user
        address.save()
        return Response(AddressSerializer(address).data, status=status.HTTP_201_CREATED)
    return Response(AddressSerializer(Address.objects.filter(user=request.user).order_by("-id"), many=True).data)


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def address_detail(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    if request.method == "DELETE":
        address.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    form = AddressForm(request.data, instance=address)
    if not form.is_valid():
        return form_error(form)
    form.save()
    return Response(AddressSerializer(address).data)


# ---------------------------------------------------------------- wishlist

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wishlist(request):
    items = product_queryset().filter(wishlist__user=request.user)
    return Response(ProductCardSerializer(items, many=True, context=ctx(request)).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def wishlist_toggle(request, pk):
    item = get_object_or_404(Product, pk=pk, active=True)
    saved, created = Wishlist.objects.get_or_create(user=request.user, product=item)
    if not created:
        saved.delete()
    return Response({"in_wishlist": created})


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"ok": True, "time": timezone.now()})

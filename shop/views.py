import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Avg
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .forms import (
    AddressForm,
    CheckoutForm,
    ContactForm,
    RegisterForm,
    ReturnForm,
    ReviewForm,
)
from .models import (
    Address,
    Banner,
    Category,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    ReturnRequest,
    Review,
    StoreSettings,
    Variant,
    Wishlist,
)
from .services import (
    ShopError,
    cancel_order,
    create_order,
    ensure_gateway_order,
    gateway,
    mark_paid,
    quote,
    verify_payment,
    update_return_refund_state,
)


def home(request):
    return render(
        request,
        "shop/home.html",
        {
            "featured": Product.objects.filter(
                active=True, category__active=True, featured=True
            )[:8],
            "new_products": Product.objects.filter(active=True, category__active=True)[
                :4
            ],
            "banners": Banner.objects.filter(active=True),
        },
    )


def shop(request):
    products = Product.objects.filter(active=True, category__active=True)
    filters = {
        "category": "category__slug",
        "fabric": "fabric",
        "occasion": "occasion",
    }
    for key, field in filters.items():
        value = request.GET.get(key, "").strip()
        if value:
            products = products.filter(**{field: value})
    query = request.GET.get("q", "").strip()
    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(category__name__icontains=query)
        )
    try:
        for parameter, lookup in [("min", "price__gte"), ("max", "price__lte")]:
            raw = request.GET.get(parameter)
            if raw:
                value = Decimal(raw)
                if not value.is_finite() or value < 0 or value > Decimal("99999999"):
                    raise ValueError
                products = products.filter(**{lookup: value})
    except (InvalidOperation, ValueError):
        messages.error(request, "Please enter valid non-negative price filters.")
    variant_filters = {}
    if request.GET.get("size"):
        variant_filters["variants__size"] = request.GET["size"]
    if request.GET.get("colour"):
        variant_filters["variants__colour"] = request.GET["colour"]
    if request.GET.get("stock"):
        variant_filters["variants__stock__gt"] = 0
    if variant_filters:
        products = products.filter(**variant_filters)
    sorting = {"price": "price", "-price": "-price", "name": "name", "new": "-created"}
    products = products.order_by(
        sorting.get(request.GET.get("sort"), "-created")
    ).distinct()
    from django.core.paginator import Paginator

    page = Paginator(products, 12).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "shop/shop.html",
        {
            "page_obj": page,
            "query_string": params.urlencode(),
            "fabrics": Product.objects.filter(active=True)
            .order_by("fabric")
            .values_list("fabric", flat=True)
            .distinct(),
            "occasions": Product.objects.filter(active=True)
            .order_by("occasion")
            .values_list("occasion", flat=True)
            .distinct(),
            "colours": Variant.objects.values_list("colour", flat=True).distinct(),
            "sizes": Variant.objects.values_list("size", flat=True).distinct(),
        },
    )


def product(request, slug):
    item = get_object_or_404(Product, slug=slug, active=True, category__active=True)
    return render(
        request,
        "shop/product.html",
        {
            "product": item,
            "variants": item.variants.all(),
            "related": Product.objects.filter(
                category=item.category, active=True
            ).exclude(pk=item.pk)[:4],
            "review_form": ReviewForm(),
            "rating": item.reviews.aggregate(value=Avg("rating"))["value"],
        },
    )


@require_POST
def add_cart(request, variant_id):
    variant = get_object_or_404(
        Variant, pk=variant_id, product__active=True, product__category__active=True
    )
    cart = request.session.get("cart", {}).copy()
    try:
        quantity = int(request.POST.get("quantity", "1"))
        total = int(cart.get(str(variant.pk), 0)) + quantity
        if quantity < 1 or total > 10 or total > variant.stock:
            raise ValueError
    except (ValueError, TypeError):
        messages.error(
            request, "Choose a valid quantity within available stock (maximum 10)."
        )
        return redirect("product", slug=variant.product.slug)
    cart[str(variant.pk)] = total
    request.session["cart"] = cart
    messages.success(request, "Added to your bag.")
    if request.POST.get("buy_now"):
        return redirect("checkout")
    return redirect("cart")


@require_POST
def add_product(request):
    try:
        variant_id = int(request.POST.get("variant", ""))
    except ValueError:
        messages.error(request, "Please choose a size and colour.")
        return redirect("shop")
    return add_cart(request, variant_id)


def cart(request):
    cart_data = request.session.get("cart", {}).copy()
    if request.method == "POST":
        if request.POST.get("action") == "coupon":
            request.session["coupon"] = request.POST.get("code", "").strip().upper()
        else:
            key = request.POST.get("variant")
            try:
                quantity = int(request.POST.get("quantity", "0"))
                if quantity <= 0:
                    cart_data.pop(key, None)
                elif key in cart_data and 1 <= quantity <= 10:
                    variant = get_object_or_404(Variant, pk=key)
                    if quantity > variant.stock:
                        raise ValueError
                    cart_data[key] = quantity
                else:
                    raise ValueError
                request.session["cart"] = cart_data
            except (ValueError, TypeError):
                messages.error(
                    request, "Quantity exceeds the limit or available stock."
                )
        return redirect("cart")
    try:
        data = quote(cart_data, request.session.get("coupon", ""))
    except ShopError as exc:
        messages.error(request, str(exc))
        request.session.pop("coupon", None)
        try:
            data = quote(cart_data)
        except ShopError:
            request.session["cart"] = {}
            data = quote({})
    return render(request, "shop/cart.html", data)


@login_required
def checkout(request):
    initial = {"email": request.user.email, "name": request.user.get_full_name()}
    address = Address.objects.filter(user=request.user).first()
    if address:
        initial.update(
            {
                field: getattr(address, field)
                for field in [
                    "name",
                    "phone",
                    "line1",
                    "line2",
                    "city",
                    "state",
                    "postcode",
                ]
            }
        )
    form = CheckoutForm(request.POST or None, initial=initial)
    try:
        data = quote(request.session.get("cart", {}), request.session.get("coupon", ""))
        if not data["lines"]:
            return redirect("cart")
        if request.method == "POST" and form.is_valid():
            order = create_order(
                request.user,
                request.session.get("cart", {}),
                request.session.get("coupon", ""),
                form.cleaned_data,
            )
            request.session["cart"] = {}
            request.session.pop("coupon", None)
            if order.total == 0:
                mark_paid(order.pk, "no-charge-" + str(order.pk))
                return redirect("order", pk=order.pk)
            return redirect("payment", pk=order.pk)
    except ShopError as exc:
        messages.error(request, str(exc))
        return redirect("cart")
    return render(request, "shop/checkout.html", {**data, "form": form})


@login_required
def payment(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if order.payment_status != "pending" or order.status == "cancelled":
        return redirect("order", pk=order.pk)
    if order.expires_at <= timezone.now():
        cancel_order(order.pk)
        messages.error(
            request, "Payment reservation expired. Please place a new order."
        )
        return redirect("order", pk=order.pk)
    gateway_id = ""
    cashfree_session = ""
    if order.payment_mode == "cashfree":
        from .cashfree import CashfreeError, ensure_cashfree_order

        try:
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=pk)
                cashfree_session = ensure_cashfree_order(locked, request)
        except CashfreeError as exc:
            messages.error(request, str(exc))
    if order.payment_mode == "razorpay":
        try:
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=pk)
                gateway_id = ensure_gateway_order(locked)
        except ShopError as exc:
            messages.error(request, str(exc))
    return render(
        request,
        "shop/payment.html",
        {
            "order": order,
            "gateway_id": gateway_id,
            "key_id": settings.RAZORPAY_KEY_ID,
            "cashfree_session": cashfree_session,
            "cashfree_mode": "production" if settings.CASHFREE_ENV == "production" else "sandbox",
        },
    )


@login_required
@require_POST
def demo_payment(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if (
        settings.PAYMENT_MODE != "demo"
        or order.payment_mode != "demo"
        or not settings.DEBUG
    ):
        return HttpResponse("Demo payments are disabled.", status=403)
    if order.expires_at <= timezone.now() or order.status == "cancelled":
        messages.error(request, "Order expired or cancelled.")
    elif request.POST.get("result") == "success":
        mark_paid(order.pk, "demo-" + str(order.pk))
    else:
        messages.error(request, "Demo payment failed. Retry from the order page.")
    return redirect("order", pk=pk)


@login_required
@require_POST
def payment_verify(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user, payment_mode="razorpay")
    try:
        if request.POST.get("razorpay_order_id") != order.provider_order:
            raise ShopError("Payment order mismatch.")
        verify_payment(
            order,
            request.POST.get("razorpay_payment_id", ""),
            request.POST.get("razorpay_signature", ""),
        )
    except ShopError as exc:
        messages.error(request, str(exc))
    return redirect("order", pk=pk)


@login_required
def cashfree_return(request, pk):
    """Customer lands here after Cashfree checkout; verify server-to-server."""
    order = get_object_or_404(Order, pk=pk, user=request.user, payment_mode="cashfree")
    if order.payment_status == "pending":
        from .cashfree import verify_cashfree

        try:
            verify_cashfree(order)
            messages.success(request, "Payment successful! Your order is confirmed.")
        except ShopError as exc:
            messages.error(request, str(exc))
    return redirect("order", pk=pk)


@csrf_exempt
@require_POST
def cashfree_webhook(request):
    from .cashfree import valid_webhook, verify_cashfree

    if not valid_webhook(
        request.body,
        request.headers.get("x-webhook-timestamp", ""),
        request.headers.get("x-webhook-signature", ""),
    ):
        return HttpResponse(status=400)
    try:
        data = json.loads(request.body)
        if data.get("type") == "PAYMENT_SUCCESS_WEBHOOK":
            oid = data["data"]["order"]["order_id"]
            order = Order.objects.filter(provider_order=oid, payment_mode="cashfree").first()
            if order and order.payment_status == "pending":
                verify_cashfree(order)  # re-checks amount/status with Cashfree API
    except (KeyError, ValueError, ShopError):
        return HttpResponse(status=400)
    return HttpResponse("ok")


@csrf_exempt
@require_POST
def webhook(request):
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    signature = request.headers.get("X-Razorpay-Signature", "")
    expected = (
        hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        if secret
        else ""
    )
    if not secret or not hmac.compare_digest(expected, signature):
        return HttpResponse(status=400)
    try:
        data = json.loads(request.body)
        if data.get("event") == "payment.captured":
            entity = data["payload"]["payment"]["entity"]
            order = Order.objects.get(
                provider_order=entity["order_id"], payment_mode="razorpay"
            )
            if (
                entity.get("status") != "captured"
                or entity.get("currency") != "INR"
                or entity.get("amount") != int(order.total * 100)
            ):
                return HttpResponse(status=400)
            mark_paid(order.pk, entity["id"])
        elif data.get("event") == "refund.processed":
            refund = data["payload"]["refund"]["entity"]
            with transaction.atomic():
                result = (
                    ReturnRequest.objects.filter(refund_id=refund["id"])
                    .select_related("item__order")
                    .first()
                )
                if (
                    result
                    and refund.get("payment_id") == result.item.order.provider_payment
                    and refund.get("amount") == int(result.refund_amount * 100)
                ):
                    result.refund_status = "refunded"
                    result.save(update_fields=["refund_status"])
                    update_return_refund_state(result.item.order)
                elif not result:
                    order = Order.objects.select_for_update().get(
                        provider_payment=refund["payment_id"],
                        provider_refund=refund["id"],
                    )
                    if refund.get("amount") == int(order.total * 100):
                        order.payment_status = Order.Payment.REFUNDED
                        order.save(update_fields=["payment_status"])
    except Order.DoesNotExist:
        return HttpResponse(status=200)
    except (ValueError, KeyError, TypeError):
        return HttpResponse(status=400)
    return HttpResponse(status=200)


@login_required
def account(request):
    if request.method == "POST":
        request.user.first_name = request.POST.get("first_name", "")[:150]
        request.user.last_name = request.POST.get("last_name", "")[:150]
        request.user.save(update_fields=["first_name", "last_name"])
        messages.success(request, "Profile updated.")
        return redirect("account")
    return render(
        request,
        "shop/account.html",
        {
            "orders": Order.objects.filter(user=request.user),
            "returns": ReturnRequest.objects.filter(
                item__order__user=request.user
            ).select_related("item"),
        },
    )


@login_required
def order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if order.status == "pending" and order.expires_at <= timezone.now():
        cancel_order(order.pk)
        order.refresh_from_db()
    return render(
        request,
        "shop/order.html",
        {
            "order": order,
            "steps": [
                "Confirmed",
                "Packed",
                "Shipped",
                "Out for delivery",
                "Delivered",
            ],
        },
    )


@login_required
@require_POST
def cancel(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    try:
        cancel_order(order.pk)
        messages.success(
            request,
            "Order cancelled. If paid, the refund will be processed by the store.",
        )
    except ShopError as exc:
        messages.error(request, str(exc))
    return redirect("order", pk=pk)


@login_required
def invoice(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if order.payment_status not in ("paid", "partial_refund", "refunded"):
        return HttpResponse("Invoice available after verified payment.", status=403)
    if request.GET.get("format") == "pdf":
        from .invoices import issue_invoice, invoice_pdf
        try:
            content = invoice_pdf(issue_invoice(order))
        except ShopError as exc:
            return HttpResponse(str(exc), status=403)
        response = HttpResponse(content, content_type="application/pdf")
        response['Content-Disposition'] = 'attachment; filename="' + order.number + '.pdf"'
        return response
    return render(request, "shop/invoice.html", {"order": order})


@login_required
def wishlist(request):
    return render(
        request,
        "shop/wishlist.html",
        {
            "products": Product.objects.filter(
                wishlist__user=request.user, active=True, category__active=True
            ),
        },
    )


@login_required
@require_POST
def wishlist_toggle(request, pk):
    item = get_object_or_404(Product, pk=pk, active=True)
    saved, created = Wishlist.objects.get_or_create(user=request.user, product=item)
    if not created:
        saved.delete()
    return redirect("wishlist")


@login_required
@require_POST
def review(request, pk):
    item = get_object_or_404(Product, pk=pk, active=True)
    purchased = OrderItem.objects.filter(
        order__user=request.user, order__status="delivered", variant__product=item
    ).exists()
    form = ReviewForm(request.POST)
    if purchased and form.is_valid():
        Review.objects.update_or_create(
            user=request.user, product=item, defaults=form.cleaned_data
        )
        messages.success(request, "Thank you. Your review was saved.")
    else:
        messages.error(
            request, "Reviews require a delivered purchase and a valid 1–5 rating."
        )
    return redirect("product", slug=item.slug)


@login_required
def addresses(request):
    selected = None
    if request.GET.get("edit"):
        selected = get_object_or_404(Address, pk=request.GET["edit"], user=request.user)
    form = AddressForm(request.POST or None, instance=selected)
    if request.method == "POST" and form.is_valid():
        address = form.save(commit=False)
        address.user = request.user
        address.save()
        return redirect("addresses")
    return render(
        request,
        "shop/addresses.html",
        {"form": form, "addresses": Address.objects.filter(user=request.user)},
    )


@login_required
@require_POST
def delete_address(request, pk):
    get_object_or_404(Address, pk=pk, user=request.user).delete()
    return redirect("addresses")


@login_required
def return_request(request, pk):
    item = get_object_or_404(OrderItem, pk=pk, order__user=request.user)
    store = StoreSettings.objects.first()
    days = store.return_days if store else 7
    if (
        item.order.status != "delivered"
        or not item.order.delivered_at
        or timezone.now() > item.order.delivered_at + timedelta(days=days)
    ):
        messages.error(
            request, f"Returns are available within {days} days of delivery."
        )
        return redirect("order", pk=item.order_id)
    if ReturnRequest.objects.filter(item=item).exists():
        messages.info(
            request,
            "A request for this item already exists. View its status in My Account.",
        )
        return redirect("account")
    form = ReturnForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if (
            form.cleaned_data["kind"] == "exchange"
            and not Variant.objects.filter(
                product=item.variant.product,
                colour=item.colour,
                size=form.cleaned_data["requested_size"],
            ).exists()
        ):
            form.add_error(
                "requested_size", "This size is not offered for this product."
            )
        else:
            result = form.save(commit=False)
            result.item = item
            result.save()
            messages.success(
                request, "Request submitted. The store will review and arrange pickup."
            )
            return redirect("account")
    return render(request, "shop/return.html", {"form": form, "item": item})


def register(request):
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("account")
    return render(request, "shop/register.html", {"form": form})


def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your enquiry has been sent to the store.")
        return redirect("contact")
    return render(request, "shop/contact.html", {"form": form})


def information(request, page="about"):
    allowed = ["about", "shipping", "returns", "privacy", "terms", "faq"]
    if page not in allowed:
        from django.http import Http404

        raise Http404
    return render(request, "shop/" + page + ".html")


def delivery_check(request):
    postcode = request.GET.get("postcode", "")
    valid = len(postcode) == 6 and postcode.isdigit() and not postcode.startswith("0")
    return JsonResponse(
        {
            "valid": valid,
            "message": (
                "Valid Indian PIN format. Delivery normally takes 4–7 business days; serviceability is confirmed by the store."
                if valid
                else "Enter a valid six-digit Indian PIN code."
            ),
        }
    )


def product_preview(request, slug):
    from .agents import serialize_product

    item = get_object_or_404(Product, slug=slug, active=True, category__active=True)
    return JsonResponse(serialize_product(item))

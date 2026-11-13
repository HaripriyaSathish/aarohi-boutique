"""Execute unambiguous storefront requests through the existing commerce tools."""
import re

from django.db import transaction
from django.db.models import Q

from .agents import execute_tool, serialize_product
from .autonomous import prepare_purchase
from .models import Variant
from .services import ShopError

ITEM_REQUEST = re.compile(
    r"(add|buy|order)\s+(\d+)\s+(.+?)(?:\s+to (?:my |the )?(?:bag|cart))?"
    r"(\s+and\s+(?:prepare\s+)?checkout)?[.!]?",
    re.IGNORECASE,
)


def customer_workflow(request, message, agent_id, tools, authorized):
    if agent_id not in {"cart", "stylist", "voice"}:
        return None

    match = ITEM_REQUEST.fullmatch(message)
    if not authorized or not match:
        return None
    verb, quantity, name, checkout_requested = match.groups()
    quantity = int(quantity)
    wants_checkout = bool(checkout_requested) or verb.lower() in {"buy", "order"}
    name = name.strip()
    names = Q(product__name__iexact=name)
    if name.casefold().endswith("s"):
        names |= Q(product__name__iexact=name[:-1])
    variants = list(
        Variant.objects.filter(
            names,
            product__active=True,
            product__category__active=True,
        ).select_related("product__category")
    )
    if not variants:
        return None

    result = {"products": [], "orders": [], "actions": [], "journeys": [],
              "tools": [], "engine": "Verified shopping workflow"}
    if not 1 <= quantity <= 10:
        return {**result, "answer": "Choose a quantity between 1 and 10."}

    cart = request.session.get("cart", {}).copy()
    available = [
        variant for variant in variants
        if int(cart.get(str(variant.pk), 0)) + quantity <= min(10, variant.stock)
    ]
    if not available:
        return {**result, "answer": "There is insufficient stock for that quantity in your bag."}
    if len(available) > 1:
        options = ", ".join(f"{item.size} / {item.colour}" for item in available)
        return {
            **result,
            "answer": f"Which option would you like for {name}? {options}.",
            "products": [serialize_product(available[0].product)],
        }

    variant = available[0]
    try:
        with transaction.atomic():
            if wants_checkout:
                journey = prepare_purchase(request, [
                    {"variant_id": variant.pk, "quantity": quantity}
                ])
            action = execute_tool("execute_authorized_action", {
                "action": "add_to_cart", "target": str(variant.pk),
                "quantity": quantity, "value": "", "extra": "",
            }, request.user, tools)
            if not action.get("executed"):
                raise ShopError(action.get("error", "The item could not be added."))
    except ShopError as error:
        if request.session.get("cart", {}) != cart:
            request.session["cart"] = cart
        return {**result, "answer": str(error)}

    answer = f"Stock checked. I added {quantity} × {variant.product.name} to your bag."
    result["tools"].append("execute_authorized_action")
    if wants_checkout:
        result["journeys"].append(journey)
        result["tools"].append("prepare_purchase")
        answer += " Checkout is ready with your saved delivery address."
    return {**result, "answer": answer}

"""Specialist agent registry and request-scoped, permission-checked tools."""

from contextvars import ContextVar
from decimal import Decimal
import json
import re
import requests
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import F
from .models import (
    AgentJob,
    WorkTask,
    RestockPlan,
    Product,
    Enquiry,
    Order,
    ReturnRequest,
    Shipment,
)
from .services import quote, ShopError
from .actions import propose

REQUEST_CONTEXT = ContextVar("commerce_agent_request", default=None)
ACTION_SCOPES = {
    "stylist": {"add_to_cart"},
    "cart": {"add_to_cart", "remove_from_cart", "apply_coupon"},
    "support": {"cancel_order", "request_return", "request_exchange"},
    "payments": {"cancel_order"},
    "returns": {"request_return", "request_exchange"},
    "admin": {
        "mark_packed",
        "approve_return",
        "complete_return",
        "refund_order",
        "refund_return",
        "receive_stock",
        "refresh_tracking",
        "retry_job",
        "resolve_enquiry",
    },
    "inventory": {"receive_stock"},
    "shipping": {"mark_packed", "refresh_tracking", "retry_job"},
    "content": {"update_description", "update_price"},
}
STAFF_AGENTS = {"admin", "inventory", "shipping", "content"}


def install_registry(module):
    Spec = module.AgentSpec
    schema = module.function_schema
    module.TOOL_SCHEMAS.update(
        {
            "cart_status": schema(
                "cart_status",
                "Read this signed-in user’s current bag, variant IDs and server totals. No address information.",
                {},
            ),
            "best_coupon": schema(
                "best_coupon",
                "Find the best currently eligible discount for this bag. Does not apply it.",
                {},
            ),
            "operations_status": schema(
                "operations_status",
                "Staff-only queues, physical tasks, open restock plans and exception summaries.",
                {},
            ),
            "propose_action": schema(
                "propose_action",
                "Create a permitted action proposal. It is NOT executed until the user explicitly confirms its server-written action card. target is a variant/item/request/product/plan/shipment ID or order reference, depending on action. value is coupon, reason, description or price; extra is requested exchange size. quantity is integer, usually 1.",
                {
                    "action": {
                        "type": "string",
                        "enum": sorted(set().union(*ACTION_SCOPES.values())),
                    },
                    "target": {"type": "string"},
                    "value": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "extra": {"type": "string"},
                },
            ),
        }
    )
    module.AGENTS.update(
        {
            "stylist": Spec(
                "Shopping & Styling Agent",
                "Recommend actual in-stock products. Get exact variant IDs, then propose add_to_cart when explicitly requested. Never assume body measurements.",
                (
                    "search_products",
                    "get_product_details",
                    "store_policies",
                    "propose_action",
                ),
            ),
            "cart": Spec(
                "Cart & Checkout Agent",
                "Read the current bag, choose an eligible coupon, and propose requested cart edits. Checkout and payment require the customer to use the secure checkout page. Never auto-purchase.",
                (
                    "cart_status",
                    "best_coupon",
                    "search_products",
                    "get_product_details",
                    "propose_action",
                ),
            ),
            "support": Spec(
                "Order & Customer Support Agent",
                "Read only the signed-in customer’s orders. Propose a cancellation or return/exchange only when requested. Read server state before every order claim.",
                ("my_orders", "order_status", "store_policies", "propose_action"),
            ),
            "payments": Spec(
                "Payment Assistance Agent",
                "Explain verified payment status, help locate a pending checkout, and propose cancellation if requested. You cannot mark paid or collect card/UPI credentials.",
                ("my_orders", "order_status", "store_policies", "propose_action"),
            ),
            "returns": Spec(
                "Returns & Exchange Agent",
                "Read the customer’s order item IDs and policy. Propose a return or size exchange with a reason; physical inspection is required before refund/replacement.",
                ("my_orders", "order_status", "store_policies", "propose_action"),
            ),
            "admin": Spec(
                "Admin Operations Agent",
                "Review queues and measured metrics; propose requested staff actions. Packing, inspection and received-stock actions require the human to confirm physical completion.",
                (
                    "operations_status",
                    "sales_summary",
                    "stock_alerts",
                    "propose_action",
                ),
            ),
            "inventory": Spec(
                "Inventory & Restock Agent",
                "Inspect measured stock and open restock plans. Propose receive_stock only when staff says goods physically arrived. Never invent stock or place supplier purchases.",
                (
                    "stock_alerts",
                    "operations_status",
                    "search_products",
                    "propose_action",
                ),
            ),
            "shipping": Spec(
                "Shipping & Tracking Agent",
                "Inspect fulfillment queues. Propose requested packing confirmation, tracking refresh or reviewed-job retry. Carrier APIs and the worker handle booking/status updates.",
                ("operations_status", "propose_action"),
            ),
            "content": Spec(
                "Catalogue & Merchandising Agent",
                "Use existing product facts to draft clear product descriptions. Do not invent fabric, certification, material or discount claims. Propose description/price changes only with explicit staff approval.",
                ("search_products", "get_product_details", "propose_action"),
            ),
        }
    )


def extended_tool(name, arguments, user, allowed, legacy):
    from .agents import has_staff_access

    if name not in allowed:
        return {"error": "Tool is not allowed for this agent."}
    if not isinstance(arguments, dict):
        return {"error": "Tool arguments must be an object."}
    from .business_agents import REPORTS

    if name in REPORTS:
        if not has_staff_access(user):
            return {"error": "Authorized staff permission required."}
        return REPORTS[name]()
    context = REQUEST_CONTEXT.get() or {}
    request = context.get("request")
    try:
        if name in ["cart_status", "best_coupon"]:
            if request is None or not user.is_authenticated:
                return {"error": "A signed-in request is required."}
            data = quote(
                request.session.get("cart", {}), request.session.get("coupon", "")
            )
            if name == "cart_status":
                return {
                    "lines": [
                        {
                            "variant_id": line["variant"].pk,
                            "product": line["variant"].product.name,
                            "size": line["variant"].size,
                            "colour": line["variant"].colour,
                            "quantity": line["quantity"],
                            "line_total_inr": str(line["total"]),
                        }
                        for line in data["lines"]
                    ],
                    "total_inr": str(data["total"]),
                    "coupon": request.session.get("coupon", ""),
                    "checkout_url": "/checkout/",
                }
            from .models import Coupon
            from django.utils import timezone

            best = None
            for coupon in Coupon.objects.filter(
                active=True,
                starts__lte=timezone.now(),
                expires__gt=timezone.now(),
                minimum__lte=data["subtotal"],
                uses__lt=F("max_uses"),
            ):
                candidate = quote(request.session.get("cart", {}), coupon.code)
                if best is None or candidate["total"] < best["total"]:
                    best = {
                        "code": coupon.code,
                        "discount_inr": str(candidate["discount"]),
                        "total_inr": str(candidate["total"]),
                        "total": candidate["total"],
                    }
            if best:
                best.pop("total")
            return {"best_coupon": best}
        if name == "operations_status":
            if not has_staff_access(user):
                return {"error": "Authorized staff required."}
            return {
                "jobs": [
                    {
                        "id": str(job.pk),
                        "kind": job.kind,
                        "state": job.state,
                        "error": job.error[:250],
                    }
                    for job in AgentJob.objects.exclude(state="succeeded")[:15]
                ],
                "physical_tasks": list(
                    WorkTask.objects.filter(status="open").values(
                        "id", "kind", "title"
                    )[:20]
                ),
                "restock_plans": list(
                    RestockPlan.objects.filter(status="proposed").values(
                        "id", "variant__sku", "quantity"
                    )[:15]
                ),
                "confirmed_orders": [
                    {"reference": order.number, "status": order.status}
                    for order in Order.objects.filter(status="confirmed")[:10]
                ],
                "returns": list(
                    ReturnRequest.objects.exclude(
                        status__in=["completed", "rejected"]
                    ).values("id", "kind", "status")[:15]
                ),
                "shipments": list(Shipment.objects.values("id", "state", "awb")[:15]),
                "operations_url": "/operations/",
            }
        if name == "propose_action":
            agent_id = context.get("agent_id")
            action = arguments.get("action", "")
            if request is None or action not in ACTION_SCOPES.get(agent_id, set()):
                return {"error": "Action is outside this agent’s permitted scope."}
            payload = {
                key: arguments.get(key, "") for key in ["target", "value", "extra"]
            }
            payload["quantity"] = arguments.get("quantity", 1)
            proposal = propose(request, action, payload, agent=agent_id)
            return {"actions": [proposal], "executed": False}
    except PermissionDenied:
        return {"error": "You do not have permission for this operation."}
    except (ShopError, ValueError, TypeError):
        return {
            "error": "Action could not be proposed. Check ownership, policy, target ID and stock."
        }
    return legacy(name, arguments, user, allowed)


def local_specialist(message, agent_id, user):
    from .agents import execute_tool
    from .agents import AGENTS

    allowed = AGENTS[agent_id].tools
    result = {"answer": "", "products": [], "orders": [], "actions": [], "tools": []}
    # Local action syntax is explicit and intentionally narrow. No LLM is claimed.
    syntax = [
        (r"^add\s+(\d+)(?:\s+(\d+))?$", "add_to_cart"),
        (r"^remove\s+(\d+)$", "remove_from_cart"),
        (r"^coupon\s+(\S+)$", "apply_coupon"),
        (r"^cancel\s+(AB-[A-Fa-f0-9]{8})$", "cancel_order"),
        (r"^pack\s+(AB-[A-Fa-f0-9]{8})$", "mark_packed"),
        (r"^return\s+(\d+)\s+(.+)$", "request_return"),
        (r"^exchange\s+(\d+)\s+(\S+)\s+(.+)$", "request_exchange"),
        (r"^receive\s+(\d+)\s+(\d+)$", "receive_stock"),
        (r"^approve-return\s+(\d+)$", "approve_return"),
        (r"^inspect-return\s+(\d+)$", "complete_return"),
        (r"^refund-return\s+(\d+)$", "refund_return"),
        (r"^refund\s+(AB-[A-Fa-f0-9]{8})$", "refund_order"),
        (r"^track-shipment\s+(\d+)$", "refresh_tracking"),
        (r"^retry\s+([A-Fa-f0-9-]{36})$", "retry_job"),
        (r"^description\s+(\d+)\s+(.+)$", "update_description"),
        (r"^price\s+(\d+)\s+([0-9.]+)$", "update_price"),
    ]
    for pattern, action in syntax:
        match = re.match(pattern, message.strip(), re.I | re.S)
        if not match:
            continue
        groups = match.groups()
        args = {
            "action": action,
            "target": groups[0],
            "value": "",
            "extra": "",
            "quantity": 1,
        }
        if action == "apply_coupon":
            args["value"] = groups[0]
        elif action in ["add_to_cart", "receive_stock"]:
            args["quantity"] = int(groups[1] or 1)
        elif action in ["request_return", "update_description", "update_price"]:
            args["value"] = groups[1]
        elif action == "request_exchange":
            args["extra"], args["value"] = groups[1], groups[2]
        response = execute_tool("propose_action", args, user, allowed)
        result["actions"] = response.get("actions", [])
        result["tools"] = ["propose_action"]
        result["answer"] = response.get(
            "error",
            "I prepared an action. Review the exact details and confirm below. Nothing has been changed yet.",
        )
        return result
    from .business_agents import REPORTS

    if agent_id in STAFF_AGENTS and allowed[0] in REPORTS:
        data = execute_tool(allowed[0], {}, user, allowed)
        result["tools"] = [allowed[0]]
        result["answer"] = (
            "Measured report / labelled estimates:\n"
            + json.dumps(data, ensure_ascii=False, indent=2)[:5500]
            + "\nUse the Intelligence dashboard for tables, evidence and data setup. Conversational interpretation uses the configured LLM."
        )
        return result
    if agent_id in ["cart", "voice", "recovery"]:
        data = execute_tool("cart_status", {}, user, allowed)
        coupon = execute_tool("best_coupon", {}, user, allowed)
        result["tools"] = ["cart_status", "best_coupon"]
        result["answer"] = (
            "Current bag:\n"
            + "\n".join(
                f"Variant {line['variant_id']}: {line['product']} / {line['size']} / {line['colour']} × {line['quantity']}"
                for line in data.get("lines", [])
            )
            + "\nTotal INR "
            + data.get("total_inr", "0")
        )
        if coupon.get("best_coupon"):
            result["answer"] += (
                "\nBest eligible coupon: "
                + coupon["best_coupon"]["code"]
                + ". Type “coupon CODE” to prepare it."
            )
    elif agent_id in STAFF_AGENTS:
        allowed_ops = ("operations_status",)
        data = execute_tool("operations_status", {}, user, allowed_ops)
        result["tools"] = ["operations_status"]
        result["answer"] = (
            "Operations snapshot:\n"
            + json.dumps(data, ensure_ascii=False, indent=2)[:4500]
        )
        result[
            "answer"
        ] += "\nLocal commands: pack AB-XXXXXXXX; receive PLAN_ID QUANTITY; approve-return REQUEST_ID; inspect-return REQUEST_ID; track-shipment SHIPMENT_ID. Choose the corresponding specialist. Every action requires confirmation."
    elif agent_id in ["returns", "payments"]:
        data = execute_tool("my_orders", {}, user, allowed)
        result["orders"] = data.get("orders", [])
        result["tools"] = ["my_orders"]
        result["answer"] = (
            "Your orders:\n"
            + json.dumps(data, ensure_ascii=False, indent=2)[:4500]
            + "\nReturn command: return ITEM_ID reason. Exchange: exchange ITEM_ID SIZE reason. Cancel: cancel AB-XXXXXXXX."
        )
    return result



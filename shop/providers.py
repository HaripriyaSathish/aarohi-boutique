"""Explicit demo adapter and a staged Shiprocket adapter.

A timed-out remote create/booking is ambiguous. It enters review instead of
blindly repeating a potentially chargeable operation.
"""

from urllib.parse import quote
import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from .models import OrderEvent, WorkTask


class ProviderError(Exception):
    pass


class NeedsReview(ProviderError):
    pass


def shiprocket_request(method, path, payload=None):
    token = settings.SHIPROCKET_TOKEN or cache.get("shiprocket_token")
    try:
        if not token:
            if not settings.SHIPROCKET_EMAIL or not settings.SHIPROCKET_PASSWORD:
                raise NeedsReview("Configure Shiprocket API-user credentials or token.")
            response = requests.post(
                "https://apiv2.shiprocket.in/v1/external/auth/login",
                json={
                    "email": settings.SHIPROCKET_EMAIL,
                    "password": settings.SHIPROCKET_PASSWORD,
                },
                timeout=(5, 20),
            )
            response.raise_for_status()
            token = response.json()["token"]
            cache.set("shiprocket_token", token, 3600)
        response = requests.request(
            method,
            "https://apiv2.shiprocket.in/v1/external/" + path,
            json=payload,
            headers={"Authorization": "Bearer " + token},
            timeout=(5, 25),
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except NeedsReview:
        raise
    except (requests.RequestException, ValueError, KeyError) as exc:
        raise ProviderError(
            "Courier request failed. Check account configuration and provider dashboard."
        ) from exc


def shipment_payload(shipment):
    if shipment.direction == "reverse":
        return reverse_payload(shipment)
    order = shipment.order
    address = order.delivery_data
    required = ["name", "line1", "city", "state", "postcode"]
    if not all(address.get(field) for field in required):
        raise NeedsReview(
            "Structured shipping address missing. Update delivery_data before booking."
        )
    names = address["name"].split(" ", 1)
    items = order.items.all()
    if shipment.return_request_id:
        items = [shipment.return_request.item]
    reference = (
        ("EX-" + str(shipment.return_request_id) + "-")
        if shipment.return_request_id
        else ""
    )
    return {
        "order_id": reference + str(order.pk),
        "order_date": order.created.strftime("%Y-%m-%d %H:%M"),
        "pickup_location": settings.SHIPROCKET_PICKUP_LOCATION,
        "billing_customer_name": names[0],
        "billing_last_name": names[1] if len(names) > 1 else "",
        "billing_address": address["line1"],
        "billing_address_2": address.get("line2", ""),
        "billing_city": address["city"],
        "billing_pincode": address["postcode"],
        "billing_state": address["state"],
        "billing_country": "India",
        "billing_email": order.email,
        "billing_phone": order.phone,
        "shipping_is_billing": True,
        "order_items": [
            {
                "name": item.name,
                "sku": item.variant.sku
                + (
                    "-EX-" + shipment.return_request.requested_size
                    if shipment.return_request_id
                    else ""
                ),
                "units": item.quantity,
                "selling_price": str(item.price),
                "discount": "0",
                "tax": "0",
            }
            for item in items
        ],
        "payment_method": "Prepaid",
        "shipping_charges": str(
            order.shipping if not shipment.return_request_id else 0
        ),
        "total_discount": str(order.discount if not shipment.return_request_id else 0),
        "sub_total": str(
            order.subtotal
            if not shipment.return_request_id
            else shipment.return_request.item.total
        ),
        "length": settings.PARCEL_LENGTH_CM,
        "breadth": settings.PARCEL_BREADTH_CM,
        "height": settings.PARCEL_HEIGHT_CM,
        "weight": settings.PARCEL_WEIGHT_KG,
    }


def book_shipment(shipment):
    if shipment.pickup_booked:
        return shipment
    if shipment.provider == "demo":
        shipment.awb = "DEMO" + str(shipment.pk).zfill(8)
        shipment.courier = "Demo courier — no real booking"
        shipment.tracking_url = ""
        shipment.provider_shipment_id = "demo-" + str(shipment.pk)
        shipment.pickup_booked = True
        shipment.state = "pickup_requested"
        shipment.save()
        return shipment
    if shipment.state in ["creating", "awb_requesting", "pickup_requesting", "review"]:
        raise NeedsReview(
            "Courier request outcome is uncertain. Reconcile with Shiprocket, fill existing references, and reset state to new before retry. Never create a duplicate booking blindly."
        )
    if not shipment.provider_shipment_id:
        payload = shipment_payload(shipment)
        shipment.state = "creating"
        shipment.save(update_fields=["state", "updated"])
        try:
            result = shiprocket_request(
                "POST",
                (
                    "orders/create/return"
                    if shipment.direction == "reverse"
                    else "orders/create/adhoc"
                ),
                payload,
            )
            if not result.get("shipment_id") or not result.get("order_id"):
                raise ProviderError("Missing provider references.")
            shipment.provider_order_id = str(result["order_id"])
            shipment.provider_shipment_id = str(result["shipment_id"])
            shipment.state = "created"
            shipment.save()
        except ProviderError as exc:
            shipment.state = "review"
            shipment.save(update_fields=["state", "updated"])
            raise NeedsReview(
                "Order creation needs reconciliation in the courier dashboard; possible partial success."
            ) from exc
    if not shipment.awb:
        shipment.state = "awb_requesting"
        shipment.save(update_fields=["state", "updated"])
        try:
            response = shiprocket_request(
                "POST",
                "courier/assign/awb",
                {
                    "shipment_id": int(shipment.provider_shipment_id),
                    **({"is_return": 1} if shipment.direction == "reverse" else {}),
                },
            )
            details = response.get("response", {}).get("data", {})
            if not details.get("awb_code"):
                raise ProviderError("No AWB assigned.")
            shipment.awb = str(details["awb_code"])
            shipment.courier = str(details.get("courier_name", "Shiprocket courier"))
            shipment.tracking_url = "https://shiprocket.co/tracking/" + quote(
                shipment.awb, safe=""
            )
            shipment.state = "awb_assigned"
            shipment.save()
        except ProviderError as exc:
            shipment.state = "review"
            shipment.save(update_fields=["state", "updated"])
            raise NeedsReview(
                "AWB assignment needs courier-dashboard reconciliation."
            ) from exc
    shipment.state = "pickup_requesting"
    shipment.save(update_fields=["state", "updated"])
    try:
        result = shiprocket_request(
            "POST",
            "courier/generate/pickup",
            {"shipment_id": [int(shipment.provider_shipment_id)]},
        )
        if not result.get("pickup_status"):
            raise ProviderError("Pickup was not accepted.")
        shipment.pickup_booked = True
        shipment.state = "pickup_requested"
        shipment.save()
    except ProviderError as exc:
        shipment.state = "review"
        shipment.save(update_fields=["state", "updated"])
        raise NeedsReview(
            "Pickup request needs courier-dashboard reconciliation before retry."
        ) from exc
    return shipment


def generate_label(shipment):
    if shipment.provider == "demo":
        return
    data = shiprocket_request(
        "POST",
        "courier/generate/label",
        {"shipment_id": [int(shipment.provider_shipment_id)]},
    )
    if data.get("label_url"):
        shipment.label_url = data["label_url"]
        shipment.save(update_fields=["label_url", "updated"])
    else:
        raise ProviderError("Courier label is not ready. Retry label job later.")


def track_shipment(shipment):
    if shipment.provider == "demo":
        shipment.demo_step = min(shipment.demo_step + 1, 3)
        status = ["pickup_requested", "shipped", "out", "delivered"][shipment.demo_step]
    else:
        data = shiprocket_request(
            "GET", "courier/track/awb/" + quote(shipment.awb, safe="")
        )
        tracking = data.get("tracking_data", {})
        records = tracking.get("shipment_track", [])
        text = (
            str(records[0].get("current_status", "") if records else "").strip().lower()
        )
        mapping = {
            "delivered": "delivered",
            "out for delivery": "out",
            "shipped": "shipped",
            "in transit": "shipped",
            "picked up": "shipped",
        }
        text = " ".join(text.replace("_", " ").replace("-", " ").split())
        status = mapping.get(text)
        if not status:
            status = {
                6: "shipped",
                7: "delivered",
                17: "out",
                18: "shipped",
                42: "shipped",
            }.get(tracking.get("shipment_status"))
        if not status:
            if any(
                value in text
                for value in ["rto", "lost", "damaged", "undelivered", "cancel"]
            ):
                WorkTask.objects.get_or_create(
                    key=f"shipping-exception:{shipment.pk}:{text[:30]}",
                    defaults={
                        "kind": "shipping_exception",
                        "title": f"Courier exception: {text[:120]}",
                        "order": shipment.order,
                    },
                )
            shipment.last_checked = timezone.now()
            shipment.save(update_fields=["last_checked", "updated"])
            return shipment
    shipment.state = status
    shipment.last_checked = timezone.now()
    shipment.save()
    order = shipment.order
    if shipment.direction == "reverse":
        result = shipment.return_request
        if status == "delivered" and result.status == "approved":
            result.status = "received"
            result.staff_note = "Courier reports return delivered to the store. Staff must inspect before completing the return."
            result.save()
            WorkTask.objects.get_or_create(
                key=f"inspect:{result.pk}",
                defaults={
                    "kind": "return_inspection",
                    "title": f"Inspect returned item for {order.number}",
                    "order": order,
                    "return_request": result,
                },
            )
        return shipment
    if shipment.return_request_id:
        note = f"Exchange shipment {shipment.awb}: {status}."
        if not OrderEvent.objects.filter(order=order, message=note).exists():
            OrderEvent.objects.create(order=order, message=note)
        shipment.return_request.staff_note = note
        shipment.return_request.save(update_fields=["staff_note"])
    else:
        rank = {"confirmed": 0, "packed": 1, "shipped": 2, "out": 3, "delivered": 4}
        if order.status in rank and rank[status] > rank[order.status]:
            order.status = status
            if status == "delivered":
                order.delivered_at = timezone.now()
            order.save()
            OrderEvent.objects.create(
                order=order,
                message=(
                    "Demo tracking: "
                    if shipment.provider == "demo"
                    else "Courier tracking: "
                )
                + order.get_status_display(),
            )
    return shipment


def reverse_payload(shipment):
    order = shipment.order
    address = order.delivery_data
    destination = settings.RETURN_WAREHOUSE
    required = ["name", "address", "city", "state", "postcode", "phone", "email"]
    if not all(destination.get(key) for key in required):
        raise NeedsReview(
            "Configure the return warehouse address, phone and email before reverse booking."
        )
    if not all(
        address.get(key) for key in ["name", "line1", "city", "state", "postcode"]
    ):
        raise NeedsReview("Customer structured address is missing.")
    item = shipment.return_request.item
    return {
        "order_id": "9" + str(shipment.return_request_id).zfill(12),
        "order_date": timezone.now().strftime("%Y-%m-%d %H:%M"),
        "pickup_customer_name": address["name"],
        "pickup_address": address["line1"],
        "pickup_address_2": address.get("line2", ""),
        "pickup_city": address["city"],
        "pickup_state": address["state"],
        "pickup_country": "India",
        "pickup_pincode": address["postcode"],
        "pickup_email": order.email,
        "pickup_phone": order.phone,
        "shipping_customer_name": destination["name"],
        "shipping_address": destination["address"],
        "shipping_city": destination["city"],
        "shipping_state": destination["state"],
        "shipping_country": "India",
        "shipping_pincode": destination["postcode"],
        "shipping_email": destination["email"],
        "shipping_phone": destination["phone"],
        "order_items": [
            {
                "name": item.name,
                "sku": item.variant.sku,
                "units": item.quantity,
                "selling_price": str(item.price),
            }
        ],
        "payment_method": "Prepaid",
        "sub_total": str(item.total),
        "length": settings.PARCEL_LENGTH_CM,
        "breadth": settings.PARCEL_BREADTH_CM,
        "height": settings.PARCEL_HEIGHT_CM,
        "weight": settings.PARCEL_WEIGHT_KG,
    }


def generate_manifest(shipment):
    if shipment.provider == "demo" or shipment.direction == "reverse":
        return
    data = shiprocket_request(
        "POST",
        "manifests/generate",
        {"shipment_id": [int(shipment.provider_shipment_id)]},
    )
    url = data.get("manifest_url", "")
    if not url:
        data = shiprocket_request(
            "POST", "manifests/print", {"order_ids": [int(shipment.provider_order_id)]}
        )
        url = data.get("manifest_url", "")
    if not url:
        raise ProviderError("Manifest is not ready. Retry the document job.")
    shipment.manifest_url = url
    shipment.save(update_fields=["manifest_url", "updated"])

"""Branded HTML + plain-text emails sent through the configured SMTP backend.

Order confirmations get a rich design with inline product photos (CID images,
so they show even when the site runs on localhost) and the colour invoice PDF
attached.
"""

import logging
from email.mime.image import MIMEImage
from io import BytesIO

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


def _absolute(path):
    return settings.SITE_URL.rstrip("/") + path


def _store():
    from .models import StoreSettings

    return StoreSettings.objects.first()


def _thumbnail(field):
    """Return small JPEG bytes for an ImageField, or None."""
    if not field:
        return None
    try:
        from PIL import Image

        with field.open("rb") as fh:
            image = Image.open(fh)
            image = image.convert("RGB")
            image.thumbnail((240, 300))
            out = BytesIO()
            image.save(out, "JPEG", quality=82)
            return out.getvalue()
    except Exception:  # missing file / unreadable image must not block email
        logger.warning("Could not build email thumbnail for %s", field)
        return None


def send_branded(to, subject, body, user=None, order=None, cta_label=None, cta_path=None):
    """Send one HTML email with a plain-text fallback. Raises on SMTP failure."""
    store = _store()
    store_name = store.name if store else "Aarohi Boutique"
    if order is not None:
        cta_label = cta_label or "Track your order"
        cta_path = cta_path or reverse("order", args=[order.pk])
    context = {
        "subject": subject,
        "body": body,
        "name": (user.first_name or user.username) if user else "there",
        "order": order,
        "items": list(order.items.all()) if order is not None else [],
        "store_name": store_name,
        "cta_label": cta_label or "Visit the boutique",
        "cta_url": _absolute(cta_path or "/"),
    }
    html = render_to_string("emails/notification.html", context)
    text = f"{subject}\n\nHi {context['name']},\n\n{body}\n\n{context['cta_label']}: {context['cta_url']}\n\n— {store_name}"
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
    message.attach_alternative(html, "text/html")
    message.send(fail_silently=False)


def send_order_confirmation(order, to=None):
    """Rich 'order confirmed' email with product photos + colour invoice PDF."""
    from .invoices import invoice_pdf, issue_invoice

    store = _store()
    store_name = store.name if store else "Aarohi Boutique"
    user = order.user
    items, images = [], []
    for n, item in enumerate(order.items.select_related("variant__product"), 1):
        data = _thumbnail(item.variant.product.image)
        cid = f"item{n}@aarohi" if data else ""
        if data:
            images.append((cid, data))
        items.append({"name": item.name, "size": item.size, "colour": item.colour,
                      "quantity": item.quantity, "total": item.total, "cid": cid})
    payment_label = {"cashfree": "Cashfree", "razorpay": "Razorpay"}.get(order.payment_mode, "Demo payment")
    status_rank = {"confirmed": 1, "packed": 2, "shipped": 3, "out": 3, "delivered": 4}.get(order.status, 1)
    steps = [{"label": label, "done": i < status_rank}
             for i, label in enumerate(["Confirmed", "Packed", "Shipped", "Delivered"])]
    context = {
        "order": order, "items": items, "steps": steps, "store_name": store_name,
        "name": user.first_name or user.username, "payment_label": payment_label,
        "return_days": store.return_days if store else 7,
        "track_url": _absolute(reverse("order", args=[order.pk])),
        "shop_url": _absolute(reverse("shop")),
    }
    subject = f"🎉 Order confirmed! {order.number} is on its way to being packed"
    html = render_to_string("emails/order_confirmed.html", context)
    lines = "\n".join(f"- {i['name']} ({i['size']}, {i['colour']}) x{i['quantity']}: Rs.{i['total']}" for i in items)
    text = (f"Thank you, {context['name']}! Your order {order.number} is confirmed.\n\n{lines}\n\n"
            f"Total paid: Rs.{order.total}\nTrack your order: {context['track_url']}\n\n"
            f"Your invoice is attached.\n— {store_name}")
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to or user.email or order.email])
    message.mixed_subtype = "related"
    message.attach_alternative(html, "text/html")
    for cid, data in images:
        part = MIMEImage(data, "jpeg")
        part.add_header("Content-ID", f"<{cid}>")
        part.add_header("Content-Disposition", "inline", filename=cid.split("@")[0] + ".jpg")
        message.attach(part)
    try:
        message.attach(f"{order.number}.pdf", invoice_pdf(issue_invoice(order)), "application/pdf")
    except Exception:
        logger.exception("Invoice attachment failed for %s", order.number)
    message.send(fail_silently=False)


def send_notification(notification):
    order = notification.order
    if (
        order is not None
        and notification.key.startswith("confirmed:")
        and order.payment_status in ("paid", "partial_refund", "refunded")
    ):
        return send_order_confirmation(order, to=notification.user.email)
    send_branded(
        notification.user.email,
        notification.subject,
        notification.body,
        user=notification.user,
        order=order,
    )

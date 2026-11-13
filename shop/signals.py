from datetime import timedelta
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from .models import Order, ReturnRequest, Enquiry


@receiver(pre_save, sender=Order)
def remember_order(sender, instance, **kwargs):
    if kwargs.get("raw"):  # loaddata / fixture import: never trigger side effects
        return
    instance._previous = (
        sender.objects.filter(pk=instance.pk).values("status", "payment_status").first()
        or {}
    )


@receiver(post_save, sender=Order)
def order_events(sender, instance, created, **kwargs):
    if kwargs.get("raw"):  # loaddata / fixture import: never trigger side effects
        return
    from .automation import enqueue

    old = getattr(instance, "_previous", {})
    from .models import PurchaseJourney
    if instance.payment_status == 'paid':
        PurchaseJourney.objects.filter(order=instance).update(state='paid')
    elif instance.payment_status == 'refunded':
        PurchaseJourney.objects.filter(order=instance).update(state='refunded')
    elif instance.status == 'cancelled':
        PurchaseJourney.objects.filter(order=instance).update(state='cancelled')
    if created:
        enqueue(
            "payment_check",
            f"payment-check:{instance.pk}",
            order=instance,
            due=instance.created + timedelta(minutes=10),
        )
    if instance.payment_status == "paid" and old.get("payment_status") != "paid":
        enqueue("order_paid", f"paid:{instance.pk}", order=instance)
    if (
        instance.payment_status == "refund_due"
        and old.get("payment_status") != "refund_due"
    ):
        enqueue("refund_order", f"refund-order:{instance.pk}", order=instance)
    if instance.status == "cancelled" and old.get("status") != "cancelled":
        enqueue("order_cancelled", f"cancelled:{instance.pk}", order=instance)
    if instance.status == "packed" and old.get("status") != "packed":
        enqueue("ship_order", f"ship:{instance.pk}", order=instance)
    if instance.status != old.get("status") and not created:
        enqueue(
            "status_notice", f"status:{instance.pk}:{instance.status}", order=instance
        )
    if instance.status == "delivered" and old.get("status") != "delivered":
        from .automation import policy

        enqueue(
            "review_request",
            f"review:{instance.pk}",
            order=instance,
            due=(
                instance.delivered_at + timedelta(days=policy().review_delay_days)
                if instance.delivered_at
                else None
            ),
        )


@receiver(post_save, sender=ReturnRequest)
def return_events(sender, instance, created, **kwargs):
    if kwargs.get("raw"):  # loaddata / fixture import: never trigger side effects
        return
    from .automation import enqueue

    if instance.status == "requested":
        enqueue(
            "review_return",
            f"return-review:{instance.pk}",
            order=instance.item.order,
            payload={"return_id": instance.pk},
        )
    if instance.status == "approved":
        enqueue(
            "reverse_pickup",
            f"reverse-pickup:{instance.pk}",
            order=instance.item.order,
            payload={"return_id": instance.pk},
        )
    if instance.status == "completed":
        enqueue(
            "return_completed",
            f"return-completed:{instance.pk}",
            order=instance.item.order,
            payload={"return_id": instance.pk},
        )


@receiver(post_save, sender=Enquiry)
def enquiry_event(sender, instance, created, **kwargs):
    if kwargs.get("raw"):  # loaddata / fixture import: never trigger side effects
        return
    if created:
        from .automation import enqueue

        enqueue(
            "triage_enquiry",
            f"enquiry:{instance.pk}",
            payload={"enquiry_id": instance.pk},
        )

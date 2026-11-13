from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from shop.models import Enquiry, Order, OrderEvent, OrderItem, ReturnRequest, Variant


class Command(BaseCommand):
    help = (
        "Create sample orders/returns/enquiries for the test customer so the order history, "
        "tracking and Admin Pending-Work board have data. Safe to run more than once."
    )

    def handle(self, *args, **options):
        try:
            customer = User.objects.get(username="customer")
        except User.DoesNotExist as exc:
            raise CommandError("Run `python manage.py seed_users` first.") from exc
        if Order.objects.filter(user=customer, delivery_data__seed=True).exists():
            self.stdout.write("Sample orders already exist.")
            return
        variants = list(Variant.objects.filter(stock__gt=2).select_related("product")[:6])
        if len(variants) < 4:
            raise CommandError("Run `python manage.py seed_demo` first.")
        now = timezone.now()
        address = "Test Customer, 12 Lotus Street, Chennai, Tamil Nadu 600001"

        def make(variant, status, payment, days_ago, qty=1, **extra):
            total = variant.product.price * qty
            order = Order.objects.create(
                user=customer, email=customer.email, phone="9876543210",
                shipping_address=address, subtotal=total, total=total,
                status=status, payment_status=payment, payment_mode="demo",
                expires_at=now + timedelta(hours=1), delivery_data={"seed": True}, **extra,
            )
            Order.objects.filter(pk=order.pk).update(created=now - timedelta(days=days_ago, hours=3))
            item = OrderItem.objects.create(
                order=order, variant=variant, name=variant.product.name, size=variant.size,
                colour=variant.colour, price=variant.product.price, quantity=qty,
            )
            OrderEvent.objects.create(order=order, message=f"Order {order.get_status_display().lower()}.")
            return order, item

        make(variants[0], "confirmed", "paid", 2)            # paid, waiting > 24h to pack (HIGH)
        make(variants[1], "confirmed", "paid", 0, qty=2)     # paid today
        make(variants[2], "shipped", "paid", 4,
             courier="Demo Express", tracking_number="DEMO123456")
        _, delivered_item = make(variants[3], "delivered", "paid", 9, delivered_at=now - timedelta(days=3))
        ReturnRequest.objects.create(
            item=delivered_item, kind="exchange", requested_size="L",
            reason="The fit is a little tight around the shoulders; please exchange for size L.",
        )
        Enquiry.objects.create(
            name="Meena", email="meena@example.com",
            message="Do you have the lavender kurta set in XXL? Need it before Friday.",
        )
        low = variants[4]
        low.stock = 2
        low.save(update_fields=["stock"])
        self.stdout.write(self.style.SUCCESS(
            "Created 4 sample orders, 1 exchange request, 1 enquiry and a low-stock variant."
        ))

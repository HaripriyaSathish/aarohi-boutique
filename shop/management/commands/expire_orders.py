from django.core.management.base import BaseCommand
from django.utils import timezone
from shop.models import Order
from shop.services import cancel_order


class Command(BaseCommand):
    help = "Release stock on unpaid orders older than their 30-minute reservation."

    def handle(self, *args, **options):
        orders = Order.objects.filter(
            status="pending", payment_status="pending", expires_at__lte=timezone.now()
        )
        count = 0
        for order in orders:
            cancel_order(order.pk)
            count += 1
        self.stdout.write(f"Expired {count} orders.")

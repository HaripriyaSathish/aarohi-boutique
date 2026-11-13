from django.core.management.base import BaseCommand
from shop.models import Order
from shop.services import ShopError, gateway, mark_paid, sync_refund, sync_return_refund
from shop.models import ReturnRequest


class Command(BaseCommand):
    help = "Recover captured payments and reconcile refunds after missed webhooks."

    def handle(self, *args, **options):
        for order in Order.objects.filter(
            payment_mode="razorpay", payment_status="pending"
        ).exclude(provider_order=None):
            try:
                response = gateway(
                    "GET", "orders/" + order.provider_order + "/payments"
                )
                for payment in response.get("items", []):
                    if (
                        payment.get("status") == "captured"
                        and payment.get("amount") == int(order.total * 100)
                        and payment.get("currency") == "INR"
                        and payment.get("order_id") == order.provider_order
                    ):
                        mark_paid(order.pk, payment["id"])
                        self.stdout.write("Reconciled " + order.number)
                        break
            except ShopError as exc:
                self.stderr.write(str(exc))
        for order in Order.objects.filter(payment_status="refund_pending"):
            try:
                sync_refund(order)
            except ShopError as exc:
                self.stderr.write(str(exc))
        for result in ReturnRequest.objects.filter(refund_status="processing"):
            try:
                sync_return_refund(result)
            except ShopError as exc:
                self.stderr.write(str(exc))

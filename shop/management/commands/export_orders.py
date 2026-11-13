import csv
from django.core.management.base import BaseCommand
from shop.models import Order


def safe_cell(value):
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


class Command(BaseCommand):
    help = "Export orders to a CSV file for reports. Example: export_orders orders.csv"

    def add_arguments(self, parser):
        parser.add_argument("output")

    def handle(self, *args, **options):
        with open(options["output"], "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "Order",
                    "Date",
                    "Customer",
                    "Email",
                    "Subtotal INR",
                    "Discount INR",
                    "Shipping INR",
                    "Total INR",
                    "Payment",
                    "Fulfillment",
                    "Courier",
                    "Tracking",
                ]
            )
            for order in Order.objects.select_related("user").iterator():
                writer.writerow(
                    [
                        safe_cell(value)
                        for value in [
                            order.number,
                            order.created.isoformat(),
                            order.user.username,
                            order.email,
                            order.subtotal,
                            order.discount,
                            order.shipping,
                            order.total,
                            order.payment_status,
                            order.status,
                            order.courier,
                            order.tracking_number,
                        ]
                    ]
                )
        self.stdout.write(self.style.SUCCESS("Exported orders."))

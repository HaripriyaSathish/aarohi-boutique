import csv
from decimal import Decimal
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from shop.models import Supplier, SupplierQuote, Variant, Product, CompetitorPrice


class Command(BaseCommand):
    help = "Import structured supplier quotes or recorded competitor observations. See data_examples CSV headers."

    def add_arguments(self, parser):
        parser.add_argument("kind", choices=["suppliers", "competitors"])
        parser.add_argument("file")

    @transaction.atomic
    def handle(self, *args, **options):
        count = 0
        try:
            with open(options["file"], newline="", encoding="utf-8-sig") as source:
                for row in csv.DictReader(source):
                    price = Decimal(
                        row["unit_price" if options["kind"] == "suppliers" else "price"]
                    )
                    if not price.is_finite() or price <= 0:
                        raise ValueError("Prices must be positive finite numbers.")
                    if options["kind"] == "suppliers":
                        variant = Variant.objects.get(sku=row["sku"])
                        supplier, _ = Supplier.objects.get_or_create(
                            name=row["supplier"],
                            defaults={"lead_days": int(row.get("lead_days") or 7)},
                        )
                        quote = SupplierQuote(
                            supplier=supplier,
                            variant=variant,
                            unit_price=price,
                            minimum_quantity=int(row.get("minimum_quantity") or 1),
                            freight=Decimal(row.get("freight") or 0),
                            valid_until=date.fromisoformat(row["valid_until"]),
                        )
                        quote.full_clean()
                        quote.save()
                    else:
                        product = Product.objects.get(slug=row["product_slug"])
                        observation = CompetitorPrice(
                            product=product,
                            seller=row["seller"],
                            source_url=row.get("source_url", ""),
                            price=price,
                        )
                        observation.full_clean()
                        observation.save()
                    count += 1
        except Exception as exc:
            raise CommandError(
                f"Import rolled back. Check CSV headers, numbers, dates and matching SKUs/slugs. Error type: {type(exc).__name__}"
            ) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {count} records. These are user-supplied observations, not automatically scraped competitor data."
            )
        )

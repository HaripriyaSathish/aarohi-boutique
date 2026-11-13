from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal
from django.utils import timezone


class Supplier(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    lead_days = models.PositiveIntegerField(default=7)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class SupplierQuote(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    variant = models.ForeignKey("shop.Variant", on_delete=models.PROTECT)
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    minimum_quantity = models.PositiveIntegerField(default=1)
    freight = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    valid_until = models.DateField()


class PurchaseOrder(models.Model):
    quote = models.ForeignKey(SupplierQuote, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    freight = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20,
        default="draft",
        choices=[
            ("draft", "Draft"),
            ("approved", "Approved"),
            ("received", "Received"),
            ("cancelled", "Cancelled"),
        ],
    )
    expected_date = models.DateField(null=True, blank=True)
    received_date = models.DateField(null=True, blank=True)
    defective_units = models.PositiveIntegerField(default=0)
    stock_applied = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    @property
    def total(self):
        return self.quantity * self.unit_price + self.freight


class CompetitorPrice(models.Model):
    product = models.ForeignKey("shop.Product", on_delete=models.CASCADE)
    seller = models.CharField(max_length=120)
    source_url = models.URLField(blank=True)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    observed = models.DateTimeField(default=timezone.now)


class Campaign(models.Model):
    name = models.CharField(max_length=150)
    body = models.TextField(max_length=3000)
    segment = models.CharField(
        max_length=20,
        default="all_opted_in",
        choices=[
            ("all_opted_in", "All opted-in customers"),
            ("inactive", "Inactive customers"),
            ("repeat", "Repeat customers"),
        ],
    )
    status = models.CharField(
        max_length=20,
        default="draft",
        choices=[
            ("draft", "Draft"),
            ("approved", "Approved"),
            ("sent", "Sent / queued to outbox"),
        ],
    )
    approved_audience = models.JSONField(default=list, blank=True)
    created = models.DateTimeField(auto_now_add=True)


class RiskReport(models.Model):
    order = models.OneToOneField("shop.Order", on_delete=models.CASCADE)
    score = models.PositiveIntegerField(default=0)
    signals = models.JSONField(default=list)
    reviewed = models.BooleanField(default=False)
    updated = models.DateTimeField(auto_now=True)


class BusinessReport(models.Model):
    day = models.DateField(unique=True)
    metrics = models.JSONField(default=dict)
    plan = models.JSONField(default=list)
    created = models.DateTimeField(auto_now_add=True)


class Warehouse(models.Model):
    name = models.CharField(max_length=120)
    city = models.CharField(max_length=120)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    active = models.BooleanField(default=True)


class RoutePlan(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    stops = models.JSONField(default=list)
    distance_km = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(
        max_length=100, default="Nearest-neighbour straight-line heuristic"
    )
    created = models.DateTimeField(auto_now_add=True)


class TryOnRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    product = models.ForeignKey("shop.Product", on_delete=models.PROTECT)
    provider_id = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=25, default="queued")
    result_path = models.CharField(max_length=220, blank=True)
    error = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

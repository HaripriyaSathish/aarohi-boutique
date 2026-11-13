import uuid
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    image = models.ImageField(upload_to="categories/", blank=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class Product(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    name = models.CharField(max_length=180)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    fabric = models.CharField(max_length=100, default="Cotton")
    occasion = models.CharField(max_length=100, default="Everyday")
    care = models.TextField(default="Gentle hand wash. Dry in shade.")
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    seo_title = models.CharField(max_length=160, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)
    tags = models.CharField(max_length=300, blank=True)
    original_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    image = models.ImageField(upload_to="products/", blank=True)
    active = models.BooleanField(default=True)
    featured = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.name


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product, related_name="gallery", on_delete=models.CASCADE
    )
    image = models.ImageField(upload_to="products/gallery/")
    alt = models.CharField(max_length=150, blank=True)


class Variant(models.Model):
    product = models.ForeignKey(
        Product, related_name="variants", on_delete=models.CASCADE
    )
    size = models.CharField(max_length=20)
    colour = models.CharField(max_length=50)
    sku = models.CharField(max_length=80, unique=True)
    stock = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "size", "colour"], name="unique_product_variant"
            )
        ]

    def __str__(self):
        return f"{self.product.name} / {self.size} / {self.colour}"


class StoreSettings(models.Model):
    name = models.CharField(max_length=100, default="Aarohi Boutique")
    tagline = models.CharField(
        max_length=180, default="Everyday elegance. Beautifully yours."
    )
    hero_title = models.CharField(
        max_length=180, default="Elegance for every version of you."
    )
    hero_image = models.ImageField(upload_to="banners/", blank=True)
    email = models.EmailField(default="hello@example.com")
    phone = models.CharField(max_length=25, blank=True)
    whatsapp = models.CharField(
        max_length=20, blank=True, help_text="Country code and digits only"
    )
    address = models.TextField(blank=True)
    instagram_url = models.URLField(blank=True, help_text="Full profile link, e.g. https://instagram.com/yourshop")
    facebook_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)
    pinterest_url = models.URLField(blank=True)
    x_url = models.URLField(blank=True, verbose_name="X (Twitter) URL")
    linkedin_url = models.URLField(blank=True)
    shipping_fee = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=79,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    free_shipping_over = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1999,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    return_days = models.PositiveIntegerField(default=7)
    announcement = models.CharField(
        max_length=200,
        default="Thoughtfully curated styles • Free shipping over ₹1,999",
    )

    def __str__(self):
        return self.name


class Banner(models.Model):
    title = models.CharField(max_length=100)
    subtitle = models.CharField(max_length=180, blank=True)
    image = models.ImageField(upload_to="banners/", blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True
    )
    active = models.BooleanField(default=True)


class Address(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15)
    line1 = models.CharField(max_length=180)
    line2 = models.CharField(max_length=180, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    postcode = models.CharField(max_length=6)

    def __str__(self):
        return f"{self.name}, {self.line1}, {self.city}, {self.state} {self.postcode}"


class Coupon(models.Model):
    code = models.CharField(max_length=30, unique=True)
    percent = models.PositiveIntegerField(
        default=10, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    minimum = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    active = models.BooleanField(default=True)
    starts = models.DateTimeField(default=timezone.now)
    expires = models.DateTimeField()
    max_uses = models.PositiveIntegerField(default=100)
    uses = models.PositiveIntegerField(default=0, editable=False)

    def __str__(self):
        return self.code


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting payment"
        CONFIRMED = "confirmed", "Confirmed"
        PACKED = "packed", "Packed"
        SHIPPED = "shipped", "Shipped"
        OUT = "out", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    class Payment(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        PARTIAL = "partial_refund", "Partially refunded"
        REFUND_DUE = "refund_due", "Refund required"
        REFUND_PENDING = "refund_pending", "Refund processing"
        REFUNDED = "refunded", "Refunded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    email = models.EmailField()
    shipping_address = models.TextField()
    delivery_data = models.JSONField(default=dict, blank=True)
    delivery_latitude = models.FloatField(null=True, blank=True)
    delivery_longitude = models.FloatField(null=True, blank=True)
    risk_hold = models.BooleanField(default=False)
    phone = models.CharField(max_length=15)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    coupon = models.ForeignKey(Coupon, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    payment_status = models.CharField(
        max_length=20, choices=Payment.choices, default=Payment.PENDING
    )
    payment_mode = models.CharField(max_length=15, default="demo")
    provider_order = models.CharField(
        max_length=100, blank=True, unique=True, null=True
    )
    provider_payment = models.CharField(max_length=100, blank=True)
    provider_refund = models.CharField(max_length=100, blank=True)
    courier = models.CharField(max_length=100, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    tracking_url = models.URLField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    stock_released = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created"]

    @property
    def number(self):
        return "AB-" + str(self.id).split("-")[0].upper()

    def __str__(self):
        return self.number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT)
    name = models.CharField(max_length=180)
    size = models.CharField(max_length=20)
    colour = models.CharField(max_length=50)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    @property
    def total(self):
        return self.price * self.quantity


class OrderEvent(models.Model):
    order = models.ForeignKey(Order, related_name="events", on_delete=models.CASCADE)
    message = models.CharField(max_length=250)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created"]


class Wishlist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "product"], name="unique_wishlist")
        ]


class Review(models.Model):
    product = models.ForeignKey(
        Product, related_name="reviews", on_delete=models.CASCADE
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    rating = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    text = models.TextField(max_length=1000)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "product"], name="unique_review")
        ]


class ReturnRequest(models.Model):
    item = models.OneToOneField(OrderItem, on_delete=models.PROTECT)
    kind = models.CharField(
        max_length=15,
        choices=[("return", "Return / refund"), ("exchange", "Size exchange")],
    )
    reason = models.TextField(max_length=1000)
    requested_size = models.CharField(max_length=20, blank=True)
    status = models.CharField(
        max_length=30,
        default="requested",
        choices=[
            ("requested", "Requested"),
            ("approved", "Approved — arrange pickup"),
            ("received", "Received — inspect item"),
            ("completed", "Completed"),
            ("rejected", "Rejected"),
        ],
    )
    staff_note = models.TextField(blank=True)
    stock_processed = models.BooleanField(default=False, editable=False)
    refund_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, editable=False
    )
    refund_id = models.CharField(max_length=100, blank=True, editable=False)
    refund_status = models.CharField(max_length=20, default="none", editable=False)
    created = models.DateTimeField(auto_now_add=True)


class Enquiry(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    message = models.TextField(max_length=3000)
    resolved = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)


class AgentUsage(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    hour = models.DateTimeField()
    requests = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "hour"], name="unique_agent_hour"),
        ]


# Imported here so Django discovers the automation models in the same app.
from .automation_models import (
    AutomationPolicy,
    AgentJob,
    AgentAudit,
    WorkTask,
    Shipment,
    RestockPlan,
    CustomerPreference,
    Notification,
    CartSnapshot,
    AgentAction,
)
from .intelligence_models import (
    Supplier,
    SupplierQuote,
    PurchaseOrder,
    CompetitorPrice,
    Campaign,
    RiskReport,
    BusinessReport,
    Warehouse,
    RoutePlan,
    TryOnRequest,
)

# Additive V4 checkout state; existing tables and primary keys remain unchanged.
from .autonomous import PurchaseJourney
from .autonomous import InvoiceDocument
from .autonomous import AgentRun

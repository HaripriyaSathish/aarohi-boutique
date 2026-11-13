"""Durable automation state. Secrets belong in environment variables, not rows."""

import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class AutomationPolicy(models.Model):
    enabled = models.BooleanField(default=True)
    auto_shipping = models.BooleanField(default=True)
    auto_return_approval = models.BooleanField(default=True)
    auto_refunds = models.BooleanField(default=True)
    fraud_review_threshold = models.PositiveIntegerField(default=70)
    allow_live_refunds = models.BooleanField(default=False)
    low_stock_threshold = models.PositiveIntegerField(default=5)
    restock_target = models.PositiveIntegerField(default=20)
    review_delay_days = models.PositiveIntegerField(default=2)
    abandoned_cart_hours = models.PositiveIntegerField(default=24)
    auto_cart_reminders = models.BooleanField(default=True)

    def __str__(self):
        return "Store automation policy"


class AgentJob(models.Model):
    STATES = [
        (value, value.replace("_", " ").title())
        for value in ["queued", "running", "review", "succeeded", "failed"]
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=220, unique=True)
    agent = models.CharField(max_length=80)
    kind = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    order = models.ForeignKey(
        "shop.Order", null=True, blank=True, on_delete=models.PROTECT
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    state = models.CharField(max_length=15, choices=STATES, default="queued")
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]
        indexes = [models.Index(fields=["state", "available_at"])]

    def __str__(self):
        return f"{self.agent}: {self.kind} ({self.state})"


class AgentAudit(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    agent = models.CharField(max_length=80)
    event = models.CharField(max_length=60)
    summary = models.CharField(max_length=400)
    job = models.ForeignKey(AgentJob, null=True, blank=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]


class WorkTask(models.Model):
    key = models.CharField(max_length=200, unique=True)
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=220)
    order = models.ForeignKey(
        "shop.Order", null=True, blank=True, on_delete=models.PROTECT
    )
    return_request = models.ForeignKey(
        "shop.ReturnRequest", null=True, blank=True, on_delete=models.PROTECT
    )
    status = models.CharField(
        max_length=15, default="open", choices=[("open", "Open"), ("done", "Done")]
    )
    note = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]


class Shipment(models.Model):
    key = models.CharField(max_length=150, unique=True)
    order = models.ForeignKey(
        "shop.Order", related_name="shipments", on_delete=models.PROTECT
    )
    return_request = models.ForeignKey(
        "shop.ReturnRequest", null=True, blank=True, on_delete=models.PROTECT
    )
    direction = models.CharField(
        max_length=15,
        default="outbound",
        choices=[
            ("outbound", "Outbound"),
            ("reverse", "Return pickup"),
            ("exchange", "Exchange replacement"),
        ],
    )
    provider = models.CharField(max_length=20, default="demo")
    state = models.CharField(max_length=30, default="new")
    provider_order_id = models.CharField(max_length=100, blank=True)
    provider_shipment_id = models.CharField(max_length=100, blank=True)
    awb = models.CharField(max_length=100, blank=True)
    courier = models.CharField(max_length=150, blank=True)
    tracking_url = models.URLField(blank=True)
    label_url = models.URLField(blank=True)
    manifest_url = models.URLField(blank=True)
    pickup_booked = models.BooleanField(default=False)
    demo_step = models.PositiveIntegerField(default=0)
    last_checked = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)


class RestockPlan(models.Model):
    variant = models.ForeignKey("shop.Variant", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        default="proposed",
        choices=[
            ("proposed", "Proposed"),
            ("received", "Received"),
            ("dismissed", "Dismissed"),
        ],
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant"],
                condition=models.Q(status="proposed"),
                name="one_open_restock_plan",
            )
        ]


class CustomerPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    email_updates = models.BooleanField(default=True)
    marketing_opt_in = models.BooleanField(default=False)
    review_requests = models.BooleanField(default=True)


class Notification(models.Model):
    key = models.CharField(max_length=220, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="store_notifications",
        on_delete=models.CASCADE,
    )
    order = models.ForeignKey(
        "shop.Order", null=True, blank=True, on_delete=models.PROTECT
    )
    subject = models.CharField(max_length=220)
    body = models.TextField()
    read = models.BooleanField(default=False)
    kind = models.CharField(max_length=20, default="transactional")
    email_state = models.CharField(max_length=20, default="pending")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]


class CartSnapshot(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    cart = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=64, blank=True)
    updated = models.DateTimeField(default=timezone.now)
    converted = models.BooleanField(default=False)


class AgentAction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    agent = models.CharField(max_length=80)
    kind = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    summary = models.TextField()
    state = models.CharField(
        max_length=20,
        default="pending",
        choices=[
            ("pending", "Pending"),
            ("applied", "Applied"),
            ("rejected", "Rejected"),
        ],
    )
    result = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    created = models.DateTimeField(auto_now_add=True)

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django import forms
from django.utils import timezone
from .models import (
    Address,
    Banner,
    Category,
    Coupon,
    Enquiry,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    ProductImage,
    ReturnRequest,
    Review,
    StoreSettings,
    Variant,
    Wishlist,
)
from .services import (
    ShopError,
    cancel_order,
    request_refund,
    sync_refund,
    refund_return,
    sync_return_refund,
)

admin.site.site_header = "Aarohi Boutique | Store Management"
admin.site.site_title = "Aarohi Admin"
admin.site.index_template = "admin/boutique_index.html"
admin.site.index_title = "Products, orders & customer care"


class VariantInline(admin.TabularInline):
    model = Variant
    extra = 1


class ImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "price", "active", "featured"]
    list_filter = ["category", "active", "featured", "fabric"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [VariantInline, ImageInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "sort_order", "active"]
    prepopulated_fields = {"slug": ["name"]}


class ItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ["variant", "name", "size", "colour", "price", "quantity"]

    def has_add_permission(self, request, obj=None):
        return False


class EventInline(admin.TabularInline):
    model = OrderEvent
    extra = 0
    can_delete = False
    readonly_fields = ["message", "created"]

    def has_add_permission(self, request, obj=None):
        return False


class OrderAdminForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = "__all__"

    def clean(self):
        data = super().clean()
        if not self.instance.pk:
            return data
        original = Order.objects.get(pk=self.instance.pk)
        status = data.get("status", original.status)
        allowed = {
            "pending": ["pending"],
            "confirmed": ["confirmed", "packed"],
            "packed": ["packed", "shipped"],
            "shipped": ["shipped", "out"],
            "out": ["out", "delivered"],
            "delivered": ["delivered"],
            "cancelled": ["cancelled"],
        }
        if status not in allowed[original.status]:
            raise ValidationError(
                "Advance fulfillment one step at a time. Use Cancel action for cancellation."
            )
        if status != original.status and original.risk_hold:
            raise ValidationError(
                "Release the verification hold through the guarded operations workflow first."
            )
        if status != original.status and original.payment_status != "paid":
            raise ValidationError("Only verified paid orders can be fulfilled.")
        if status in ["shipped", "out", "delivered"] and not (
            data.get("courier") and data.get("tracking_number")
        ):
            raise ValidationError(
                "Courier and tracking number are required before shipping."
            )
        return data


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
    list_display = ["number", "user", "total", "payment_status", "status", "created"]
    list_filter = ["status", "payment_status", "payment_mode", "created"]
    search_fields = ["id", "user__username", "email", "tracking_number"]
    inlines = [ItemInline, EventInline]
    readonly_fields = [
        field.name
        for field in Order._meta.fields
        if field.name
        not in [
            "status",
            "courier",
            "tracking_number",
            "tracking_url",
            "delivery_latitude",
            "delivery_longitude",
        ]
    ]
    actions = [
        "cancel_selected",
        "refund_selected",
        "sync_selected_refunds",
        "export_csv",
    ]
    change_list_template = "admin/shop/order/change_list.html"

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["sales_total"] = (
            Order.objects.filter(payment_status="paid").aggregate(total=Sum("total"))[
                "total"
            ]
            or 0
        )
        extra_context["low_stock"] = Variant.objects.filter(stock__lt=5).count()
        extra_context["pending_orders"] = Order.objects.filter(
            status="confirmed"
        ).count()
        return super().changelist_view(request, extra_context=extra_context)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if "status" in form.changed_data:
            if obj.status == "delivered":
                obj.delivered_at = timezone.now()
            super().save_model(request, obj, form, change)
            OrderEvent.objects.create(order=obj, message=obj.get_status_display())
        else:
            super().save_model(request, obj, form, change)

    @admin.action(description="Download selected orders as CSV report")
    def export_csv(self, request, queryset):
        import csv
        from django.http import HttpResponse
        from .management.commands.export_orders import safe_cell

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="orders.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(
            [
                "Order",
                "Date",
                "Customer",
                "Email",
                "Total INR",
                "Payment",
                "Fulfillment",
            ]
        )
        for order in queryset.select_related("user"):
            writer.writerow(
                [
                    safe_cell(value)
                    for value in [
                        order.number,
                        order.created.isoformat(),
                        order.user.username,
                        order.email,
                        order.total,
                        order.payment_status,
                        order.status,
                    ]
                ]
            )
        return response

    @admin.action(description="Cancel selected eligible orders and release stock")
    def cancel_selected(self, request, queryset):
        for order in queryset:
            try:
                cancel_order(order.pk)
            except ShopError as exc:
                self.message_user(request, f"{order}: {exc}", messages.ERROR)

    @admin.action(
        description="Submit full refund for cancelled / fully returned orders"
    )
    def refund_selected(self, request, queryset):
        for order in queryset:
            try:
                request_refund(order)
            except ShopError as exc:
                self.message_user(request, f"{order}: {exc}", messages.ERROR)

    @admin.action(description="Refresh gateway refund status")
    def sync_selected_refunds(self, request, queryset):
        for order in queryset:
            try:
                sync_refund(order)
            except ShopError as exc:
                self.message_user(request, str(exc), messages.ERROR)


@admin.register(ReturnRequest)
class ReturnAdmin(admin.ModelAdmin):
    list_display = ["item", "kind", "status", "created"]
    list_filter = ["status", "kind"]
    readonly_fields = [
        "item",
        "kind",
        "reason",
        "requested_size",
        "created",
        "stock_processed",
        "refund_amount",
        "refund_id",
        "refund_status",
    ]
    actions = ["refund_items", "refresh_refunds"]

    @admin.action(description="Refund completed returns to original payment method")
    def refund_items(self, request, queryset):
        for result in queryset:
            try:
                refund_return(result.pk)
            except ShopError as exc:
                self.message_user(request, str(exc), messages.ERROR)

    @admin.action(description="Refresh item refund status")
    def refresh_refunds(self, request, queryset):
        for result in queryset:
            try:
                sync_return_refund(result)
            except ShopError as exc:
                self.message_user(request, str(exc), messages.ERROR)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        # Completing a return restocks it once. Completing an exchange swaps stock atomically.
        with transaction.atomic():
            old = ReturnRequest.objects.select_for_update().get(pk=obj.pk)
            transitions = {
                "requested": ["requested", "approved", "rejected"],
                "approved": ["approved", "received", "rejected"],
                "received": ["received", "completed", "rejected"],
                "completed": ["completed"],
                "rejected": ["rejected"],
            }
            if obj.status not in transitions[old.status]:
                self.message_user(
                    request,
                    "Advance request status one step at a time.",
                    messages.ERROR,
                )
                return
            if obj.status == "completed" and not old.stock_processed:
                item = obj.item
                if obj.kind == "exchange":
                    target = Variant.objects.filter(
                        product=item.variant.product,
                        colour=item.colour,
                        size=obj.requested_size,
                    ).first()
                    if not target or not Variant.objects.filter(
                        pk=target.pk, stock__gte=item.quantity
                    ).update(stock=F("stock") - item.quantity):
                        self.message_user(
                            request,
                            "Exchange size has insufficient stock. No stock was changed.",
                            messages.ERROR,
                        )
                        return
                Variant.objects.filter(pk=item.variant_id).update(
                    stock=F("stock") + item.quantity
                )
                obj.stock_processed = True
            super().save_model(request, obj, form, change)
            order = obj.item.order
            OrderEvent.objects.create(
                order=order,
                message=f"{obj.kind.title()} request: {obj.get_status_display()}",
            )


@admin.register(StoreSettings)
class SettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not StoreSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    list_display = ["sku", "product", "size", "colour", "stock"]
    list_filter = ["size", "colour"]
    search_fields = ["sku", "product__name"]


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = [
        "code",
        "percent",
        "minimum",
        "uses",
        "max_uses",
        "expires",
        "active",
    ]


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "resolved", "created"]
    readonly_fields = ["name", "email", "message", "created"]
    list_filter = ["resolved"]


admin.site.register([Banner, Address, Review])

# Automation records are inspectable; execution is routed through the guarded
# operations UI instead of editable job payloads or arbitrary status changes.
from .models import (
    AutomationPolicy,
    AgentJob,
    AgentAudit,
    AgentAction,
    WorkTask,
    RestockPlan,
    Shipment,
    Notification,
    CustomerPreference,
)


class ReadOnlyRecordAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AgentJob)
class AgentJobAdmin(ReadOnlyRecordAdmin):
    list_display = ["agent", "kind", "state", "attempts", "available_at"]
    list_filter = ["agent", "state", "kind"]
    search_fields = ["key", "error"]


@admin.register(AgentAudit)
class AuditAdmin(ReadOnlyRecordAdmin):
    list_display = ["created", "agent", "event", "summary"]
    list_filter = ["agent", "event"]


@admin.register(AgentAction)
class ActionAdmin(ReadOnlyRecordAdmin):
    list_display = ["agent", "kind", "user", "state", "created"]
    list_filter = ["kind", "state"]


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["order", "provider", "direction", "awb", "state", "pickup_booked"]
    readonly_fields = [
        "key",
        "order",
        "return_request",
        "provider",
        "demo_step",
        "last_checked",
        "created",
        "updated",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        from .automation import audit

        super().save_model(request, obj, form, change)
        audit(
            "Shipping",
            "manual_reconciliation",
            f'Staff reconciled shipment #{obj.pk}. Changed fields: {", ".join(form.changed_data)}.',
            actor=request.user,
        )


@admin.register(AutomationPolicy)
class PolicyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not AutomationPolicy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["subject", "user", "email_state", "read", "created"]
    readonly_fields = ["key", "user", "order", "subject", "body", "created"]
    list_filter = ["email_state", "read"]

    def has_add_permission(self, request):
        return False


admin.site.register(WorkTask, ReadOnlyRecordAdmin)
admin.site.register(RestockPlan, ReadOnlyRecordAdmin)
admin.site.register(CustomerPreference)

from .models import (
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

admin.site.register([Supplier, SupplierQuote, CompetitorPrice, Warehouse])


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(ReadOnlyRecordAdmin):
    list_display = ["id", "quote", "quantity", "status", "created"]
    list_filter = ["status"]


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ["name", "segment", "status", "created"]
    readonly_fields = ["status", "approved_audience", "created"]


admin.site.register(
    [RiskReport, BusinessReport, RoutePlan, TryOnRequest], ReadOnlyRecordAdmin
)


from .models import PurchaseJourney, InvoiceDocument, AgentRun

class ImmutableAgentRecordAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

admin.site.register(PurchaseJourney, ImmutableAgentRecordAdmin)
admin.site.register(InvoiceDocument, ImmutableAgentRecordAdmin)
admin.site.register(AgentRun, ImmutableAgentRecordAdmin)

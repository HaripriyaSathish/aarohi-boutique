from datetime import timedelta

from django.db.models import Avg, Count
from django.utils import timezone
from rest_framework import serializers

from shop.models import (
    Address,
    Banner,
    Category,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    ReturnRequest,
    Review,
    StoreSettings,
    Variant,
)


def media_url(request, field):
    """Absolute URL for an ImageField (empty string when no file)."""
    if not field:
        return ""
    try:
        url = field.url
    except ValueError:
        return ""
    return request.build_absolute_uri(url) if request else url


def product_image(request, product):
    if product.image:
        return media_url(request, product.image)
    first = next(iter(product.gallery.all()), None)
    return media_url(request, first.image) if first else ""


def money(value):
    return str(value) if value is not None else None


class StoreSerializer(serializers.ModelSerializer):
    hero_image = serializers.SerializerMethodField()

    class Meta:
        model = StoreSettings
        fields = [
            "name", "tagline", "hero_title", "hero_image", "email", "phone",
            "whatsapp", "address", "instagram_url", "facebook_url", "youtube_url",
            "shipping_fee", "free_shipping_over", "return_days", "announcement",
        ]

    def get_hero_image(self, obj):
        return media_url(self.context.get("request"), obj.hero_image)


class CategorySerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "image"]

    def get_image(self, obj):
        return media_url(self.context.get("request"), obj.image)


class BannerSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    category = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Banner
        fields = ["id", "title", "subtitle", "image", "category"]

    def get_image(self, obj):
        return media_url(self.context.get("request"), obj.image)


class ProductCardSerializer(serializers.ModelSerializer):
    """Light-weight shape for product grids (fast lists)."""

    image = serializers.SerializerMethodField()
    category = serializers.CharField(source="category.name")
    in_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "slug", "name", "price", "original_price", "image",
            "category", "fabric", "featured", "in_stock",
        ]

    def get_image(self, obj):
        return product_image(self.context.get("request"), obj)

    def get_in_stock(self, obj):
        return any(v.stock > 0 for v in obj.variants.all())


class VariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Variant
        fields = ["id", "size", "colour", "sku", "stock"]


class ReviewSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ["id", "rating", "text", "created", "user"]

    def get_user(self, obj):
        return obj.user.first_name or obj.user.username


class ProductDetailSerializer(ProductCardSerializer):
    images = serializers.SerializerMethodField()
    variants = VariantSerializer(many=True)
    rating = serializers.SerializerMethodField()
    reviews = serializers.SerializerMethodField()
    in_wishlist = serializers.SerializerMethodField()
    can_review = serializers.SerializerMethodField()

    class Meta(ProductCardSerializer.Meta):
        fields = ProductCardSerializer.Meta.fields + [
            "description", "occasion", "care", "images", "variants",
            "rating", "reviews", "in_wishlist", "can_review",
        ]

    def get_images(self, obj):
        request = self.context.get("request")
        urls = [media_url(request, obj.image)] if obj.image else []
        urls += [media_url(request, g.image) for g in obj.gallery.all()]
        return [u for u in urls if u]

    def get_rating(self, obj):
        data = obj.reviews.aggregate(avg=Avg("rating"), count=Count("id"))
        return {"average": round(data["avg"], 1) if data["avg"] else None, "count": data["count"]}

    def get_reviews(self, obj):
        items = obj.reviews.select_related("user").order_by("-created")[:10]
        return ReviewSerializer(items, many=True).data

    def _user(self):
        request = self.context.get("request")
        return request.user if request and request.user.is_authenticated else None

    def get_in_wishlist(self, obj):
        user = self._user()
        return bool(user and obj.wishlist_set.filter(user=user).exists())

    def get_can_review(self, obj):
        user = self._user()
        return bool(
            user
            and OrderItem.objects.filter(
                order__user=user, order__status="delivered", variant__product=obj
            ).exists()
        )


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = ["id", "name", "phone", "line1", "line2", "city", "state", "postcode"]


class OrderItemSerializer(serializers.ModelSerializer):
    total = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    slug = serializers.CharField(source="variant.product.slug")
    can_return = serializers.SerializerMethodField()
    return_status = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id", "name", "slug", "size", "colour", "price", "quantity", "total",
            "image", "can_return", "return_status",
        ]

    def get_total(self, obj):
        return money(obj.total)

    def get_image(self, obj):
        return product_image(self.context.get("request"), obj.variant.product)

    def get_return_status(self, obj):
        result = ReturnRequest.objects.filter(item=obj).first()
        return result.get_status_display() if result else None

    def get_can_return(self, obj):
        order = obj.order
        store = StoreSettings.objects.first()
        days = store.return_days if store else 7
        return (
            order.status == "delivered"
            and order.delivered_at is not None
            and timezone.now() <= order.delivered_at + timedelta(days=days)
            and not ReturnRequest.objects.filter(item=obj).exists()
        )


class OrderEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderEvent
        fields = ["message", "created"]


class OrderListSerializer(serializers.ModelSerializer):
    number = serializers.CharField(read_only=True)
    status_label = serializers.CharField(source="get_status_display")
    payment_label = serializers.CharField(source="get_payment_status_display")
    item_count = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "number", "created", "total", "status", "status_label",
            "payment_status", "payment_label", "item_count", "image",
        ]

    def get_item_count(self, obj):
        return sum(i.quantity for i in obj.items.all())

    def get_image(self, obj):
        first = next(iter(obj.items.all()), None)
        return product_image(self.context.get("request"), first.variant.product) if first else ""


class OrderDetailSerializer(OrderListSerializer):
    items = OrderItemSerializer(many=True)
    events = OrderEventSerializer(many=True)
    can_cancel = serializers.SerializerMethodField()
    can_pay = serializers.SerializerMethodField()
    has_invoice = serializers.SerializerMethodField()

    class Meta(OrderListSerializer.Meta):
        fields = OrderListSerializer.Meta.fields + [
            "email", "phone", "shipping_address", "subtotal", "discount", "shipping",
            "payment_mode", "courier", "tracking_number", "tracking_url",
            "expires_at", "delivered_at", "items", "events",
            "can_cancel", "can_pay", "has_invoice",
        ]

    def get_can_cancel(self, obj):
        return obj.status in ("pending", "confirmed")

    def get_can_pay(self, obj):
        return (
            obj.payment_status == "pending"
            and obj.status == "pending"
            and obj.expires_at > timezone.now()
        )

    def get_has_invoice(self, obj):
        return obj.payment_status in ("paid", "partial_refund", "refunded")


class ReturnSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name")
    order_number = serializers.CharField(source="item.order.number")
    status_label = serializers.CharField(source="get_status_display")
    kind_label = serializers.CharField(source="get_kind_display")

    class Meta:
        model = ReturnRequest
        fields = [
            "id", "item_name", "order_number", "kind", "kind_label", "reason",
            "requested_size", "status", "status_label", "staff_note",
            "refund_amount", "refund_status", "created",
        ]

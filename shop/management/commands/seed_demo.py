from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from shop.models import Banner, Category, Coupon, Product, StoreSettings, Variant


class Command(BaseCommand):
    help = "Create or update a demo catalogue. Does not reset existing stock or prices."

    def handle(self, *args, **options):
        store, _ = StoreSettings.objects.get_or_create(pk=1)
        if store.hero_title == "Elegance for every version of you.":
            store.hero_title = "A little elegance. Every single day."
            store.save(update_fields=["hero_title"])
        if not store.hero_image or str(store.hero_image) == "demo/product-1.jpg":
            store.hero_image = "demo/editorial-hero.jpg"
            store.save()
        catalog = [
            ("Sarees", "Rose Silk Saree", "Silk", "Festive", 3499, 2),
            (
                "Kurtas & Kurta Sets",
                "Lavender Embroidered Kurta Set",
                "Cotton",
                "Everyday",
                2499,
                1,
            ),
            ("Salwar Suits", "Dusty Rose Salwar Suit", "Cotton", "Everyday", 2199, 11),
            ("Lehengas", "Burgundy Celebration Lehenga", "Silk", "Festive", 6999, 4),
            ("Dresses", "Ivory Floral Midi Dress", "Cotton", "Everyday", 1899, 5),
            ("Tops & Tunics", "Peach Easy-fit Tunic", "Linen", "Everyday", 1299, 6),
            ("Co-ord Sets", "Lilac Weekend Co-ord", "Linen", "Everyday", 2599, 7),
            ("Bottomwear", "Sand Palazzo Trousers", "Cotton", "Everyday", 999, 8),
            ("Dupattas", "Pastel Printed Dupatta", "Chiffon", "Festive", 799, 9),
            ("Blouses", "Purple Embroidered Blouse", "Silk", "Festive", 1499, 10),
            ("Anarkalis", "Sage Embroidered Anarkali", "Chanderi", "Festive", 3299, 3),
            ("Casual Wear", "Indigo Cotton Kurti", "Cotton", "Everyday", 1199, 12),
        ]
        for index, (category_name, name, fabric, occasion, price, image) in enumerate(
            catalog
        ):
            category, _ = Category.objects.get_or_create(
                slug=slugify(category_name),
                defaults={
                    "name": category_name,
                    "sort_order": index,
                    "image": f"demo/product-{image}.jpg",
                },
            )
            product, created = Product.objects.get_or_create(
                slug=slugify(name),
                defaults={
                    "category": category,
                    "name": name,
                    "price": Decimal(price),
                    "original_price": Decimal(price + 500),
                    "fabric": fabric,
                    "occasion": occasion,
                    "featured": index < 8,
                    "description": f"A beautifully curated {fabric.lower()} style for your {occasion.lower()} wardrobe. Refined details and a comfortable silhouette make it a lovely addition to your collection.",
                    "image": f"demo/product-{image}.jpg",
                },
            )
            sizes = (
                ["Free size"]
                if category_name in ["Sarees", "Dupattas"]
                else ["XS", "S", "M", "L", "XL", "XXL"]
            )
            colours = ["Lavender", "Ivory"] if image == 1 else ["As pictured"]
            for size in sizes:
                for colour in colours:
                    Variant.objects.get_or_create(
                        product=product,
                        size=size,
                        colour=colour,
                        defaults={
                            "sku": f"AR-{index + 1}-{slugify(size)}-{slugify(colour)}",
                            "stock": 12,
                        },
                    )
        Coupon.objects.get_or_create(
            code="WELCOME10",
            defaults={
                "percent": 10,
                "minimum": 999,
                "expires": timezone.now() + timedelta(days=365),
            },
        )
        Banner.objects.get_or_create(
            title="Celebration-ready",
            defaults={
                "subtitle": "Beautiful details for moments that matter.",
                "category": Category.objects.get(slug="lehengas"),
                "image": "demo/product-4.jpg",
            },
        )
        Banner.objects.get_or_create(
            title="Everyday, elevated",
            defaults={
                "subtitle": "Soft cottons. Effortless silhouettes.",
                "category": Category.objects.get(slug="kurtas-kurta-sets"),
                "image": "demo/product-1.jpg",
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Demo catalogue ready. Run createsuperuser to set your own admin login. Coupon: WELCOME10."
            )
        )

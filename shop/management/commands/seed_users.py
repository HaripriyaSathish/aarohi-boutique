import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from shop.models import Address


class Command(BaseCommand):
    help = (
        "Create a local test admin and a test customer (with a saved address). "
        "Passwords are generated and written to LOCAL_TEST_LOGINS.txt (git-ignored)."
    )

    def handle(self, *args, **options):
        out = Path(settings.BASE_DIR) / "LOCAL_TEST_LOGINS.txt"
        lines = []

        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@example.com", "is_staff": True, "is_superuser": True},
        )
        if created:
            pwd = secrets.token_urlsafe(9)
            admin.set_password(pwd)
            admin.save()
            lines.append(f"ADMIN     username: admin      password: {pwd}   (/admin/ and /operations/)")

        customer, created = User.objects.get_or_create(
            username="customer",
            defaults={"email": "customer@example.com", "first_name": "Test"},
        )
        if created:
            pwd = secrets.token_urlsafe(9)
            customer.set_password(pwd)
            customer.save()
            lines.append(f"CUSTOMER  username: customer   password: {pwd}   (shop, AI checkout)")
        if not Address.objects.filter(user=customer).exists():
            Address.objects.create(
                user=customer,
                name="Test Customer",
                phone="9876543210",
                line1="12 Lotus Street",
                city="Chennai",
                state="Tamil Nadu",
                postcode="600001",
            )

        if lines:
            with out.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            self.stdout.write(self.style.SUCCESS(f"Logins written to {out.name}"))
        else:
            self.stdout.write("Test users already exist; see LOCAL_TEST_LOGINS.txt.")
        self.stdout.write(
            "Tip: change the customer email in /admin/ to your own inbox to receive order emails."
        )

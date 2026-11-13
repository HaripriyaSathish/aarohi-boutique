import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Category, Enquiry, Order, Product, Variant
from .pending_work import collect, local_briefing


def make_staff():
    user = User.objects.create_user("boss", "boss@example.com", "pw-123456", is_staff=True)
    user.user_permissions.add(
        *Permission.objects.filter(codename__in=["view_order", "view_variant"])
    )
    return user


class PendingWorkTests(TestCase):
    def setUp(self):
        cat = Category.objects.create(name="Sarees", slug="sarees")
        self.product = Product.objects.create(
            category=cat, name="Silk", slug="silk", price=Decimal("1000")
        )
        self.variant = Variant.objects.create(
            product=self.product, size="M", colour="Red", sku="S-M-R", stock=0
        )
        self.customer = User.objects.create_user("c", "c@example.com", "pw-123456")

    def paid_order(self, hours_ago):
        order = Order.objects.create(
            user=self.customer, email="c@example.com", phone="9876543210",
            shipping_address="x", subtotal=1000, total=1000,
            status="confirmed", payment_status="paid",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        Order.objects.filter(pk=order.pk).update(created=timezone.now() - timedelta(hours=hours_ago))
        return order

    def test_empty_store_is_all_clear(self):
        Variant.objects.update(stock=10)
        data = collect()
        self.assertEqual(data["total_items"], 0)
        self.assertIn("All clear", local_briefing(data))

    def test_collects_and_prioritises_queues(self):
        self.paid_order(30)
        Enquiry.objects.create(name="A", email="a@example.com", message="hi")
        data = collect()
        keys = {q["key"]: q for q in data["queues"]}
        self.assertEqual(keys["orders_to_pack"]["count"], 1)
        self.assertEqual(keys["orders_to_pack"]["priority"], "high")  # waiting > 24h
        self.assertIn("out_of_stock", keys)
        self.assertIn("enquiries", keys)
        priorities = [q["priority"] for q in data["queues"]]
        self.assertEqual(priorities, sorted(priorities, key=["high", "medium", "low"].index))

    def test_board_requires_staff(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse("pending_board")).status_code, 403)

    @override_settings(AI_MODE="local")
    def test_staff_board_briefing_and_chat_agent(self):
        self.paid_order(2)
        self.client.force_login(make_staff())
        page = self.client.get(reverse("pending_board"))
        self.assertContains(page, "Paid orders waiting to be packed")
        brief = self.client.get(reverse("pending_briefing")).json()
        self.assertEqual(brief["engine"], "Rules-based")
        self.assertIn("Paid orders waiting to be packed", brief["briefing"])
        chat = self.client.post(
            reverse("assistant_chat"),
            data=json.dumps({"message": "what is pending?", "agent": "pending"}),
            content_type="application/json",
        )
        self.assertEqual(chat.status_code, 200)
        self.assertIn("Paid orders waiting to be packed", chat.json()["answer"])

    def test_customer_cannot_use_pending_agent(self):
        self.client.force_login(self.customer)
        chat = self.client.post(
            reverse("assistant_chat"),
            data=json.dumps({"message": "what is pending?", "agent": "pending"}),
            content_type="application/json",
        )
        self.assertNotIn("Paid orders waiting", json.dumps(chat.json()))

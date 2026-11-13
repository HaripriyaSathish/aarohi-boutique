import base64
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .cashfree import valid_webhook, verify_cashfree
from .models import Category, Order, OrderItem, Product, Variant
from .services import ShopError

CF = dict(CASHFREE_APP_ID="TESTID", CASHFREE_SECRET_KEY="secret", CASHFREE_ENV="sandbox")


@override_settings(**CF)
class CashfreeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("c", "c@example.com", "pw-123456")
        cat = Category.objects.create(name="Sarees", slug="sarees")
        product = Product.objects.create(category=cat, name="Silk", slug="silk", price=Decimal("1000"))
        self.variant = Variant.objects.create(product=product, size="M", colour="Red", sku="S1", stock=3)
        self.order = Order.objects.create(
            user=self.user, email="c@example.com", phone="9876543210", shipping_address="x",
            subtotal=1000, total=Decimal("1000.00"), payment_mode="cashfree",
            provider_order="AB_x", expires_at=timezone.now() + timedelta(minutes=30),
        )
        OrderItem.objects.create(order=self.order, variant=self.variant, name="Silk", size="M",
                                 colour="Red", price=1000, quantity=1)

    def fake(self, status="PAID", amount=1000.0):
        def request(method, path, data=None, idempotency=None):
            if path.endswith("/payments"):
                return [{"payment_status": "SUCCESS", "cf_payment_id": 42}]
            return {"order_status": status, "order_amount": amount, "order_currency": "INR"}
        return mock.patch("shop.cashfree.request", side_effect=request)

    def test_verify_marks_paid_only_when_server_confirms(self):
        with self.fake():
            verify_cashfree(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(self.order.provider_payment, "cf_42")

    def test_unpaid_or_wrong_amount_is_rejected(self):
        with self.fake(status="ACTIVE"), self.assertRaises(ShopError):
            verify_cashfree(self.order)
        with self.fake(amount=1.0), self.assertRaises(ShopError):
            verify_cashfree(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "pending")

    def test_webhook_signature(self):
        body, ts = b'{"a":1}', "1700000000"
        sig = base64.b64encode(hmac.new(b"secret", ts.encode() + body, hashlib.sha256).digest()).decode()
        self.assertTrue(valid_webhook(body, ts, sig))
        self.assertFalse(valid_webhook(body, ts, "bad"))
        response = self.client.post(reverse("cashfree_webhook"), data=body, content_type="application/json",
                                    HTTP_X_WEBHOOK_TIMESTAMP=ts, HTTP_X_WEBHOOK_SIGNATURE="bad")
        self.assertEqual(response.status_code, 400)

    def test_return_url_verifies_and_confirms(self):
        self.client.force_login(self.user)
        with self.fake():
            response = self.client.get(reverse("cashfree_return", args=[self.order.pk]))
        self.assertRedirects(response, reverse("order", args=[self.order.pk]), fetch_redirect_response=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "confirmed")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="http://shop.test")
class BrandedEmailTests(TestCase):
    def test_html_order_email(self):
        from .emails import send_branded

        user = User.objects.create_user("c", "c@example.com", "pw-123456", first_name="Priya")
        cat = Category.objects.create(name="S", slug="s")
        p = Product.objects.create(category=cat, name="Silk Saree", slug="silk", price=Decimal("1000"))
        v = Variant.objects.create(product=p, size="M", colour="Red", sku="S1", stock=3)
        order = Order.objects.create(user=user, email="c@example.com", phone="9", shipping_address="x",
                                     subtotal=1000, total=1000, expires_at=timezone.now())
        OrderItem.objects.create(order=order, variant=v, name="Silk Saree", size="M", colour="Red", price=1000, quantity=1)
        send_branded("c@example.com", "Order confirmed", "Thanks!", user=user, order=order)
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("Silk Saree", html)
        self.assertIn("Hi Priya", html)
        self.assertIn("http://shop.test/orders/", html)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="http://shop.test")
class OrderConfirmationEmailTests(TestCase):
    def test_rich_confirmation_with_invoice_pdf(self):
        from .emails import send_order_confirmation

        user = User.objects.create_user("c", "c@example.com", "pw-123456", first_name="Priya")
        cat = Category.objects.create(name="S", slug="s")
        p = Product.objects.create(category=cat, name="Silk Saree", slug="silk", price=Decimal("1000"))
        v = Variant.objects.create(product=p, size="M", colour="Red", sku="S1", stock=3)
        order = Order.objects.create(user=user, email="c@example.com", phone="9", shipping_address="x",
                                     subtotal=1000, total=1000, status="confirmed", payment_status="paid",
                                     payment_mode="cashfree", expires_at=timezone.now())
        OrderItem.objects.create(order=order, variant=v, name="Silk Saree", size="M", colour="Red", price=1000, quantity=1)
        send_order_confirmation(order)
        msg = mail.outbox[0]
        self.assertIn("Order confirmed", msg.subject)
        self.assertIn("Thank you, Priya!", msg.alternatives[0][0])
        pdfs = [a for a in msg.attachments if isinstance(a, tuple) and a[2] == "application/pdf"]
        self.assertEqual(len(pdfs), 1)
        self.assertTrue(pdfs[0][1].startswith(b"%PDF"))

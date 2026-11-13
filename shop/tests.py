from datetime import timedelta
from decimal import Decimal
import hashlib
import hmac
import json
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import (
    Category,
    Coupon,
    Order,
    Product,
    ReturnRequest,
    Review,
    StoreSettings,
    Variant,
)
from .services import (
    ShopError,
    cancel_order,
    create_order,
    mark_paid,
    quote,
    refund_return,
    request_refund,
    verify_payment,
)


class CommerceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "buyer", "buyer@example.com", "A-strong-pass-123"
        )
        self.other = User.objects.create_user(
            "other", "other@example.com", "A-strong-pass-456"
        )
        self.category = Category.objects.create(name="Kurtas", slug="kurtas")
        self.product = Product.objects.create(
            category=self.category,
            name="Lavender Kurta",
            slug="lavender-kurta",
            price=Decimal("1000.00"),
            description="Soft cotton kurta",
            featured=True,
        )
        self.variant = Variant.objects.create(
            product=self.product, size="M", colour="Lilac", stock=3, sku="K1"
        )
        self.store = StoreSettings.objects.create(
            shipping_fee=50, free_shipping_over=1999
        )
        self.coupon = Coupon.objects.create(
            code="HELLO10",
            percent=10,
            minimum=999,
            expires=timezone.now() + timedelta(days=1),
            max_uses=2,
        )
        self.cart = {str(self.variant.pk): 1}
        self.address = {
            "name": "Test Buyer",
            "email": "buyer@example.com",
            "phone": "9876543210",
            "line1": "10 Test Road",
            "line2": "",
            "city": "Tirunelveli",
            "state": "Tamil Nadu",
            "postcode": "627002",
        }

    def make_order(self, coupon=""):
        return create_order(self.user, self.cart, coupon, self.address)

    def test_public_pages_render(self):
        urls = [
            "/",
            "/shop/",
            "/product/lavender-kurta/",
            "/cart/",
            "/login/",
            "/register/",
            "/contact/",
            "/password/reset/",
        ]
        urls += [
            "/info/" + page + "/"
            for page in ["about", "shipping", "returns", "privacy", "terms", "faq"]
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_filters_and_search(self):
        response = self.client.get(
            "/shop/?q=Lavender&size=M&colour=Lilac&min=900&max=1100&stock=1"
        )
        self.assertContains(response, "Lavender Kurta")
        self.assertContains(self.client.get("/shop/?q=missing"), "No styles found")

    def test_coupon_and_shipping_calculated_on_server(self):
        data = quote(self.cart, "hello10")
        self.assertEqual(data["discount"], Decimal("100.00"))
        self.assertEqual(data["shipping"], Decimal("50.00"))
        self.assertEqual(data["total"], Decimal("950.00"))

    def test_expired_coupon_rejected(self):
        self.coupon.expires = timezone.now() - timedelta(seconds=1)
        self.coupon.save()
        with self.assertRaises(ShopError):
            quote(self.cart, "HELLO10")

    def test_coupon_limit_and_cancellation_release(self):
        order = self.make_order("HELLO10")
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.uses, 1)
        cancel_order(order.pk)
        cancel_order(order.pk)
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.uses, 0)

    def test_reservation_decrements_stock(self):
        self.make_order()
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 2)

    def test_no_overselling_and_atomic_rollback(self):
        self.make_order()
        self.make_order()
        self.make_order()
        with self.assertRaises(ShopError):
            self.make_order()
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)
        self.assertEqual(Order.objects.count(), 3)

    def test_multiline_stock_failure_rolls_back(self):
        variant2 = Variant.objects.create(
            product=self.product, size="L", colour="Lilac", stock=0, sku="K2"
        )
        with self.assertRaises(ShopError):
            create_order(
                self.user,
                {str(self.variant.pk): 1, str(variant2.pk): 1},
                "",
                self.address,
            )
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 3)
        self.assertEqual(Order.objects.count(), 0)

    def test_repeated_payment_callback_is_idempotent(self):
        order = self.make_order()
        mark_paid(order.pk, "payment_test")
        mark_paid(order.pk, "payment_test")
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")
        self.assertEqual(order.events.count(), 2)

    def test_cancel_paid_order_requires_refund(self):
        order = self.make_order()
        mark_paid(order.pk, "payment_test")
        cancel_order(order.pk)
        cancel_order(order.pk)
        self.variant.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.variant.stock, 3)
        self.assertEqual(order.payment_status, "refund_due")
        request_refund(order)
        self.assertEqual(order.payment_status, "refunded")

    def test_late_payment_does_not_fulfill_cancelled_order(self):
        order = self.make_order()
        cancel_order(order.pk)
        mark_paid(order.pk, "late_payment")
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(order.payment_status, "refund_due")

    def test_shipped_order_cannot_be_cancelled(self):
        order = self.make_order()
        order.status = "shipped"
        order.save()
        with self.assertRaises(ShopError):
            cancel_order(order.pk)

    def test_order_ownership_enforced(self):
        order = self.make_order()
        self.client.force_login(self.other)
        for name in ["order", "payment", "invoice"]:
            self.assertEqual(
                self.client.get(reverse(name, args=[order.pk])).status_code, 404
            )
        self.assertEqual(
            self.client.post(reverse("cancel", args=[order.pk])).status_code, 404
        )

    def test_csrf_and_post_required(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            client.post("/cart/add/", {"variant": self.variant.pk}).status_code, 403
        )
        self.assertEqual(client.get("/cart/add/").status_code, 405)

    @override_settings(DEBUG=True, PAYMENT_MODE="demo")
    def test_complete_browser_shopping_flow(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/cart/add/", {"variant": self.variant.pk, "quantity": 1}
        )
        self.assertRedirects(response, "/cart/")
        self.client.post("/cart/", {"action": "coupon", "code": "HELLO10"})
        response = self.client.post("/checkout/", {**self.address, "total": "1"})
        order = Order.objects.get()
        self.assertEqual(order.total, Decimal("950.00"))
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            reverse("demo_payment", args=[order.pk]), {"result": "success"}
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")
        self.assertContains(
            self.client.get(reverse("order", args=[order.pk])), "payment is confirmed"
        )
        self.assertEqual(
            self.client.get(reverse("invoice", args=[order.pk])).status_code, 200
        )

    @override_settings(DEBUG=False)
    def test_demo_disabled_outside_development(self):
        order = self.make_order()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("demo_payment", args=[order.pk]), {"result": "success"}
        )
        self.assertEqual(response.status_code, 403)

    def test_failed_demo_leaves_order_unpaid(self):
        order = self.make_order()
        self.client.force_login(self.user)
        self.client.post(reverse("demo_payment", args=[order.pk]), {"result": "failed"})
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "pending")

    def test_expiry_releases_stock_once(self):
        order = self.make_order()
        order.expires_at = timezone.now() - timedelta(minutes=1)
        order.save()
        call_command("expire_orders", verbosity=0)
        call_command("expire_orders", verbosity=0)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 3)

    @override_settings(RAZORPAY_KEY_SECRET="secret")
    def test_forged_payment_signature_rejected(self):
        order = self.make_order()
        order.provider_order = "order_test"
        order.save()
        with self.assertRaises(ShopError):
            verify_payment(order, "pay_fake", "forged")

    @override_settings(RAZORPAY_KEY_SECRET="secret")
    @patch("shop.services.gateway")
    def test_captured_amount_verified(self, mock):
        order = self.make_order()
        order.provider_order = "order_test"
        order.save()
        signature = hmac.new(
            b"secret", b"order_test|pay_test", hashlib.sha256
        ).hexdigest()
        mock.return_value = {
            "order_id": "order_test",
            "amount": 1,
            "currency": "INR",
            "status": "captured",
        }
        with self.assertRaises(ShopError):
            verify_payment(order, "pay_test", signature)
        mock.return_value["amount"] = int(order.total * 100)
        result = verify_payment(order, "pay_test", signature)
        self.assertEqual(result.payment_status, "paid")

    @override_settings(RAZORPAY_WEBHOOK_SECRET="websecret")
    def test_signed_webhook_and_duplicate_delivery(self):
        order = self.make_order()
        order.payment_mode = "razorpay"
        order.provider_order = "order_hook"
        order.save()
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_hook",
                            "order_id": "order_hook",
                            "amount": int(order.total * 100),
                            "currency": "INR",
                            "status": "captured",
                        }
                    }
                },
            }
        ).encode()
        signature = hmac.new(b"websecret", body, hashlib.sha256).hexdigest()
        for unused in range(2):
            response = self.client.post(
                "/payments/webhook/",
                body,
                content_type="application/json",
                HTTP_X_RAZORPAY_SIGNATURE=signature,
            )
            self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.events.count(), 2)
        self.assertEqual(
            self.client.post(
                "/payments/webhook/",
                body,
                content_type="application/json",
                HTTP_X_RAZORPAY_SIGNATURE="bad",
            ).status_code,
            400,
        )

    def test_review_requires_delivered_purchase(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse("review", args=[self.product.pk]), {"rating": 5, "text": "Lovely"}
        )
        self.assertEqual(Review.objects.count(), 0)
        order = self.make_order()
        order.status = "delivered"
        order.delivered_at = timezone.now()
        order.save()
        self.client.post(
            reverse("review", args=[self.product.pk]), {"rating": 5, "text": "Lovely"}
        )
        self.assertEqual(Review.objects.count(), 1)

    def test_return_window_and_duplicate_request(self):
        order = self.make_order()
        order.status = "delivered"
        order.delivered_at = timezone.now()
        order.save()
        self.client.force_login(self.user)
        item = order.items.first()
        self.client.post(
            reverse("return", args=[item.pk]), {"kind": "return", "reason": "Wrong fit"}
        )
        self.assertEqual(ReturnRequest.objects.count(), 1)
        self.client.post(
            reverse("return", args=[item.pk]), {"kind": "return", "reason": "Again"}
        )
        self.assertEqual(ReturnRequest.objects.count(), 1)
        order.delivered_at = timezone.now() - timedelta(days=20)
        order.save()
        response = self.client.get(reverse("return", args=[item.pk]))
        self.assertEqual(response.status_code, 302)

    def test_return_refund_is_proportional_and_idempotent(self):
        order = self.make_order("HELLO10")
        mark_paid(order.pk, "demo-pay")
        order.refresh_from_db()
        order.status = "delivered"
        order.delivered_at = timezone.now()
        order.save()
        item = order.items.first()
        result = ReturnRequest.objects.create(
            item=item,
            kind="return",
            reason="Fit",
            status="completed",
            stock_processed=True,
        )
        refund_return(result.pk)
        refund_return(result.pk)
        result.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(result.refund_amount, Decimal("900.00"))
        self.assertEqual(result.refund_status, "refunded")
        self.assertEqual(order.payment_status, "partial_refund")

    def test_wishlist_toggle(self):
        self.client.force_login(self.user)
        self.client.post(reverse("wishlist_toggle", args=[self.product.pk]))
        self.assertContains(self.client.get("/wishlist/"), "Lavender Kurta")
        self.client.post(reverse("wishlist_toggle", args=[self.product.pk]))
        self.assertContains(self.client.get("/wishlist/"), "Something caught your eye?")

    def test_address_validation(self):
        self.client.force_login(self.user)
        response = self.client.post("/addresses/", {**self.address, "postcode": "123"})
        self.assertContains(response, "valid six-digit")

    def test_account_pages_and_admin_metrics_render(self):
        self.client.force_login(self.user)
        for url in ["/account/", "/addresses/", "/wishlist/", "/password/change/"]:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        self.assertContains(self.client.get("/admin/shop/order/"), "Verified sales")

    def test_contact_saves_enquiry(self):
        from .models import Enquiry

        self.client.post(
            "/contact/",
            {
                "name": "Buyer",
                "email": "buyer@example.com",
                "message": "Need size advice",
            },
        )
        self.assertEqual(Enquiry.objects.count(), 1)

    def test_invalid_price_filters_do_not_crash(self):
        for value in ["NaN", "Infinity", "-3", "xyz"]:
            response = self.client.get("/shop/", {"min": value})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "valid non-negative price")

    @patch("shop.services.gateway")
    def test_real_item_refund_uses_stable_idempotency_key(self, mock):
        order = self.make_order()
        mark_paid(order.pk, "pay_real")
        order.refresh_from_db()
        order.status = "delivered"
        order.payment_mode = "razorpay"
        order.save()
        result = ReturnRequest.objects.create(
            item=order.items.first(),
            kind="return",
            reason="Fit",
            status="completed",
            stock_processed=True,
        )
        mock.return_value = {"id": "rfnd_1", "status": "processed"}
        refund_return(result.pk)
        refund_return(result.pk)
        self.assertEqual(mock.call_count, 1)
        self.assertGreaterEqual(len(mock.call_args.kwargs["idempotency"]), 10)
        self.assertEqual(mock.call_args.args[2]["amount"], 100000)

    def test_admin_fulfillment_requires_verified_payment(self):
        from .admin import OrderAdminForm

        order = self.make_order()
        form = OrderAdminForm(instance=order, data={"status": "packed"})
        self.assertFalse(form.is_valid())
        self.assertIn("Advance fulfillment", str(form.errors))

    def test_admin_exchange_processes_stock_once(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from .admin import ReturnAdmin

        order = self.make_order()
        mark_paid(order.pk, "demo-pay")
        order.refresh_from_db()
        order.status = "delivered"
        order.delivered_at = timezone.now()
        order.save()
        target = Variant.objects.create(
            product=self.product, size="L", colour="Lilac", stock=2, sku="EXCHANGE-L"
        )
        result = ReturnRequest.objects.create(
            item=order.items.first(),
            kind="exchange",
            reason="Fit",
            requested_size="L",
            status="received",
        )
        result.status = "completed"
        admin = ReturnAdmin(ReturnRequest, AdminSite())
        request = RequestFactory().post("/admin/")
        request.user = self.user
        admin.save_model(request, result, None, True)
        admin.save_model(request, result, None, True)
        self.variant.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(self.variant.stock, 3)
        self.assertEqual(target.stock, 1)

    def test_saved_address_cannot_be_edited_by_another_customer(self):
        from .models import Address

        address = Address.objects.create(
            user=self.user,
            **{key: value for key, value in self.address.items() if key != "email"}
        )
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get("/addresses/", {"edit": address.pk}).status_code, 404
        )

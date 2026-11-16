from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from shop.models import Address, Category, Order, Product, StoreSettings, Variant


class MobileApiTests(TestCase):
    def setUp(self):
        StoreSettings.objects.create(shipping_fee=Decimal("79"), free_shipping_over=Decimal("1999"))
        cat = Category.objects.create(name="Kurtis", slug="kurtis")
        self.product = Product.objects.create(
            category=cat, name="Rose Kurti", slug="rose-kurti", description="Soft cotton",
            price=Decimal("999.00"), featured=True,
        )
        self.variant = Variant.objects.create(product=self.product, size="M", colour="Rose", sku="RK-M", stock=5)
        self.client = APIClient()
        self.user = User.objects.create_user("asha", "asha@example.com", "Str0ng-pass-123")

    def auth(self):
        res = self.client.post("/api/auth/login/", {"username": "asha@example.com", "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + res.json()["access"])

    def address(self):
        return {"name": "Asha", "phone": "9876543210", "line1": "12 Main Rd", "line2": "",
                "city": "Chennai", "state": "Tamil Nadu", "postcode": "600001"}

    def test_home_and_catalog(self):
        home = self.client.get("/api/home/").json()
        self.assertEqual(home["featured"][0]["slug"], "rose-kurti")
        self.assertTrue(home["featured"][0]["in_stock"])
        page = self.client.get("/api/products/?q=rose&sort=price").json()
        self.assertEqual(page["count"], 1)
        detail = self.client.get("/api/products/rose-kurti/").json()
        self.assertEqual(detail["variants"][0]["size"], "M")
        self.assertFalse(detail["in_wishlist"])
        self.assertEqual(self.client.get("/api/products/missing/").status_code, 404)
        filters = self.client.get("/api/products/filters/").json()
        self.assertIn("M", filters["sizes"])

    def test_register_and_login(self):
        res = self.client.post("/api/auth/register/", {
            "username": "meena", "email": "meena@example.com", "first_name": "Meena",
            "password": "Another-pass-456", "password2": "Another-pass-456",
        }, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertIn("access", res.json())
        dup = self.client.post("/api/auth/register/", {
            "username": "meena2", "email": "MEENA@example.com",
            "password": "Another-pass-456", "password2": "Another-pass-456",
        }, format="json")
        self.assertEqual(dup.status_code, 400)
        bad = self.client.post("/api/auth/login/", {"username": "asha", "password": "nope"}, format="json")
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_quote_shipping_and_unavailable(self):
        res = self.client.post("/api/cart/quote/", {"items": [
            {"variant": self.variant.pk, "quantity": 1}, {"variant": 99999, "quantity": 1}]}, format="json").json()
        self.assertEqual(res["subtotal"], "999.00")
        self.assertEqual(res["shipping"], "79.00")
        self.assertEqual(res["total"], "1078.00")
        self.assertEqual(res["unavailable"], [99999])
        res = self.client.post("/api/cart/quote/", {"items": [{"variant": self.variant.pk, "quantity": 1}],
                                                    "coupon": "BOGUS"}, format="json").json()
        self.assertTrue(res["coupon_error"])

    @override_settings(PAYMENT_MODE="demo", DEBUG=True)
    def test_checkout_demo_pay_and_cancel(self):
        items = [{"variant": self.variant.pk, "quantity": 2}]
        self.assertEqual(self.client.post("/api/checkout/", {"items": items}, format="json").status_code, 401)
        self.auth()
        res = self.client.post("/api/checkout/", {"items": items, "address": self.address(), "save_address": True}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        order = res.json()
        self.assertEqual(order["total"], "2077.00")  # 1998 + 79 shipping (free over 1999)
        self.assertTrue(order["can_pay"])
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 3)
        self.assertEqual(Address.objects.filter(user=self.user).count(), 1)
        pay = self.client.post(f"/api/orders/{order['id']}/pay/", format="json").json()
        self.assertEqual(pay["mode"], "demo")
        paid = self.client.post(f"/api/orders/{order['id']}/demo-pay/", {"result": "success"}, format="json").json()
        self.assertTrue(paid["paid"])
        self.assertEqual(self.client.get("/api/orders/").json()["count"], 1)
        self.assertEqual(self.client.get(f"/api/orders/{order['id']}/invoice/").status_code, 200)
        cancelled = self.client.post(f"/api/orders/{order['id']}/cancel/").json()
        self.assertEqual(cancelled["order"]["status"], "cancelled")
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 5)

    def test_bad_address_and_stock(self):
        self.auth()
        bad = dict(self.address(), postcode="12")
        res = self.client.post("/api/checkout/", {"items": [{"variant": self.variant.pk, "quantity": 1}], "address": bad}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("postcode", res.json()["errors"])
        res = self.client.post("/api/checkout/", {"items": [{"variant": self.variant.pk, "quantity": 9}], "address": self.address()}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("stock", res.json()["detail"].lower())

    @override_settings(PAYMENT_MODE="cashfree", CASHFREE_ENV="sandbox")
    def test_cashfree_pay_flow(self):
        self.auth()
        order = self.client.post("/api/checkout/", {"items": [{"variant": self.variant.pk, "quantity": 1}],
                                                    "address": self.address()}, format="json").json()

        def fake(method, path, data=None, idempotency=None):
            if method == "POST":
                self.assertIn("/api/pay/return/", data["order_meta"]["return_url"])
                return {"order_id": data["order_id"], "payment_session_id": "session_abc"}
            return {"order_status": "ACTIVE", "payment_session_id": "session_abc"}

        with mock.patch("api.payments.cf_request", side_effect=fake):
            res = self.client.post(f"/api/orders/{order['id']}/pay/",
                                   {"return_url": "exp://192.168.1.4:8081/--/payment-result"}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["payment_session_id"], "session_abc")
        page = self.client.get(body["checkout_url"])
        self.assertContains(page, "session_abc")
        self.assertIn("aarohi_app_return", page.cookies)

        with mock.patch("shop.cashfree.request", side_effect=lambda m, p, d=None, idempotency=None: (
            {"order_status": "PAID", "order_amount": float(Decimal(order["total"])), "order_currency": "INR"}
            if p.endswith(Order.objects.get().provider_order) else [{"payment_status": "SUCCESS", "cf_payment_id": 77}]
        )):
            ret = self.client.get(f"/api/pay/return/{order['id']}/?order_id=x")
        self.assertContains(ret, "exp://192.168.1.4:8081/--/payment-result?order=")
        verify = self.client.post(f"/api/orders/{order['id']}/verify/").json()
        self.assertTrue(verify["paid"])

    def test_open_redirect_blocked_and_bad_token(self):
        from api.payments import safe_app_redirect

        self.assertEqual(safe_app_redirect("https://evil.example.com"), "")
        self.assertEqual(safe_app_redirect("aarohi://payment-result"), "aarohi://payment-result")
        self.assertEqual(self.client.get("/api/pay/start/?t=bad").status_code, 400)

    def test_wishlist_and_addresses(self):
        self.auth()
        self.assertTrue(self.client.post(f"/api/wishlist/{self.product.pk}/toggle/").json()["in_wishlist"])
        self.assertEqual(len(self.client.get("/api/wishlist/").json()), 1)
        self.assertTrue(self.client.get("/api/products/rose-kurti/").json()["in_wishlist"])
        created = self.client.post("/api/addresses/", self.address(), format="json")
        self.assertEqual(created.status_code, 201)
        aid = created.json()["id"]
        updated = self.client.put(f"/api/addresses/{aid}/", dict(self.address(), city="Madurai"), format="json")
        self.assertEqual(updated.json()["city"], "Madurai")
        self.assertEqual(self.client.delete(f"/api/addresses/{aid}/").status_code, 204)

    def test_expired_order_cannot_pay(self):
        self.auth()
        order = self.client.post("/api/checkout/", {"items": [{"variant": self.variant.pk, "quantity": 1}],
                                                    "address": self.address()}, format="json").json()
        Order.objects.filter(pk=order["id"]).update(expires_at=timezone.now() - timedelta(minutes=1))
        res = self.client.post(f"/api/orders/{order['id']}/pay/")
        self.assertEqual(res.status_code, 400)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 5)

    def test_website_still_works(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/shop/").status_code, 200)

    def test_android_return_link_uses_intent(self):
        from api.payments import android_intent_url

        self.assertEqual(
            android_intent_url("exp://192.168.1.4:8081/--/payment-result?order=abc"),
            "intent://192.168.1.4:8081/--/payment-result?order=abc#Intent;scheme=exp;end",
        )
        self.assertEqual(android_intent_url("vetri://payment-result?order=1"), "intent://payment-result?order=1#Intent;scheme=vetri;end")
        self.assertEqual(android_intent_url("https://evil.example.com/x"), "")

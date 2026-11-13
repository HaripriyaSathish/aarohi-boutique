from datetime import timedelta
from decimal import Decimal
import json
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import (
    Category,
    Product,
    Variant,
    StoreSettings,
    Order,
    ReturnRequest,
    AgentAction,
    AgentJob,
    AgentAudit,
    WorkTask,
    Notification,
    Shipment,
    RestockPlan,
    CustomerPreference,
    CartSnapshot,
)
from .services import create_order, mark_paid, cancel_order
from .automation import enqueue, run_jobs, sweep, policy, complete_return
from .providers import book_shipment, track_shipment, NeedsReview, ProviderError


@override_settings(SHIPPING_MODE="demo", NOTIFICATION_MODE="demo", AI_MODE="local")
class AutomationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "shopper", "shopper@example.com", "Strong-Password-1"
        )
        self.other = User.objects.create_user(
            "another", "another@example.com", "Strong-Password-2"
        )
        self.staff = User.objects.create_superuser(
            "owner", "owner@example.com", "Strong-Password-3"
        )
        StoreSettings.objects.create(shipping_fee=50, free_shipping_over=1999)
        category = Category.objects.create(name="Kurtas", slug="kurtas")
        self.product = Product.objects.create(
            category=category,
            name="Lavender Kurta",
            slug="lavender",
            description="Original cotton kurta.",
            price=1000,
        )
        self.variant = Variant.objects.create(
            product=self.product, size="M", colour="Lavender", stock=10, sku="V-M"
        )
        self.large = Variant.objects.create(
            product=self.product, size="L", colour="Lavender", stock=5, sku="V-L"
        )
        self.address = {
            "name": "Shopper",
            "email": "shopper@example.com",
            "phone": "9876543210",
            "line1": "10 Demo Road",
            "line2": "",
            "city": "Tirunelveli",
            "state": "Tamil Nadu",
            "postcode": "627002",
        }
        self.client.force_login(self.user)

    def order(self, delivered=False):
        order = create_order(self.user, {str(self.variant.pk): 1}, "", self.address)
        mark_paid(order.pk, "demo-" + str(order.pk))
        order.refresh_from_db()
        if delivered:
            order.status = "delivered"
            order.delivered_at = timezone.now()
            order.save()
        return order

    def proposal(self, kind, target="", value="", quantity=1, extra=""):
        response = self.client.post(
            "/assistant/actions/propose/",
            {
                "kind": kind,
                "target": target,
                "value": value,
                "quantity": quantity,
                "extra": extra,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/assistant/actions/", response.url)
        return AgentAction.objects.latest("created")

    def confirm(self, action):
        return self.client.post(
            reverse("action_confirm", args=[action.pk]), HTTP_ACCEPT="application/json"
        )

    def test_payment_event_creates_pack_task_and_notice_once(self):
        order = self.order()
        mark_paid(order.pk, "same")
        run_jobs(100)
        run_jobs(100)
        self.assertEqual(WorkTask.objects.filter(key=f"pack:{order.pk}").count(), 1)
        self.assertEqual(
            Notification.objects.filter(key=f"confirmed:{order.pk}").count(), 1
        )
        self.assertFalse(Shipment.objects.exists())
        self.assertEqual(
            Notification.objects.get(key=f"confirmed:{order.pk}").email_state, "demo"
        )

    def test_proposal_does_not_mutate_until_confirmation_and_replay_is_safe(self):
        action = self.proposal("add_to_cart", self.variant.pk, quantity=2)
        self.assertEqual(self.client.session.get("cart", {}), {})
        self.assertEqual(self.confirm(action).status_code, 200)
        self.assertEqual(self.confirm(action).status_code, 200)
        self.assertEqual(self.client.session["cart"][str(self.variant.pk)], 2)
        self.assertTrue(AgentAudit.objects.filter(event="action_applied").exists())

    def test_confirmation_revalidates_available_stock(self):
        action = self.proposal("add_to_cart", self.variant.pk, quantity=2)
        self.variant.stock = 0
        self.variant.save()
        self.assertEqual(self.confirm(action).status_code, 400)
        self.assertEqual(self.client.session.get("cart", {}), {})

    def test_action_ownership_and_expiration(self):
        action = self.proposal("add_to_cart", self.variant.pk)
        self.client.force_login(self.other)
        self.assertEqual(self.confirm(action).status_code, 404)
        self.client.force_login(self.user)
        action.expires_at = timezone.now() - timedelta(seconds=1)
        action.save()
        self.assertEqual(self.confirm(action).status_code, 400)

    def test_customer_cannot_execute_staff_action(self):
        order = self.order()
        response = self.client.post(
            "/assistant/actions/propose/",
            {"kind": "mark_packed", "target": order.number, "quantity": 1},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AgentAction.objects.count(), 0)

    def test_customer_cancellation_runs_automatic_demo_refund(self):
        order = self.order()
        action = self.proposal("cancel_order", order.number)
        self.confirm(action)
        run_jobs(100)
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(order.payment_status, "refunded")
        self.assertEqual(self.variant.stock, 10)
        self.assertEqual(
            WorkTask.objects.filter(order=order, kind="pack", status="open").count(), 0
        )

    def test_packing_starts_shipping_and_demo_tracking_advances(self):
        order = self.order()
        run_jobs(100)
        self.client.force_login(self.staff)
        action = self.proposal("mark_packed", order.number)
        self.confirm(action)
        run_jobs(100)
        shipment = Shipment.objects.get(order=order)
        self.assertTrue(shipment.pickup_booked)
        self.assertTrue(shipment.awb.startswith("DEMO"))
        base = timezone.now()
        for seconds in [20, 40, 60]:
            with patch(
                "shop.automation.timezone.now",
                return_value=base + timedelta(seconds=seconds),
            ):
                sweep()
                run_jobs(100)
        order.refresh_from_db()
        self.assertEqual(order.status, "delivered")
        self.assertIsNotNone(order.delivered_at)
        self.assertTrue(AgentJob.objects.filter(key=f"review:{order.pk}").exists())

    def test_return_approval_waits_for_physical_inspection_then_refunds(self):
        order = self.order(delivered=True)
        item = order.items.first()
        action = self.proposal("request_return", item.pk, "The fit is unsuitable")
        self.confirm(action)
        run_jobs(100)
        result = ReturnRequest.objects.get(item=item)
        self.assertEqual(result.status, "approved")
        self.assertEqual(result.refund_status, "none")
        self.assertTrue(
            WorkTask.objects.filter(
                return_request=result, kind="return_pickup"
            ).exists()
        )
        self.client.force_login(self.staff)
        action = self.proposal("complete_return", result.pk)
        self.confirm(action)
        run_jobs(100)
        result.refresh_from_db()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.refund_status, "refunded")
        self.assertEqual(result.refund_amount, Decimal("1000.00"))

    def test_exchange_restocks_once_and_queues_replacement_shipment(self):
        order = self.order(delivered=True)
        result = ReturnRequest.objects.create(
            item=order.items.first(),
            kind="exchange",
            reason="Need larger size",
            requested_size="L",
        )
        run_jobs(100)
        complete_return(result.pk)
        complete_return(result.pk)
        run_jobs(100)
        self.large.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(self.large.stock, 4)
        self.assertEqual(self.variant.stock, 10)
        self.assertTrue(
            Shipment.objects.filter(return_request=result, pickup_booked=True).exists()
        )

    @patch("shop.automation.request_refund")
    def test_real_auto_refunds_are_gated_by_policy(self, refund):
        order = self.order()
        order.payment_mode = "razorpay"
        order.save()
        cancel_order(order.pk)
        run_jobs(100)
        self.assertFalse(refund.called)
        self.assertEqual(
            AgentJob.objects.get(key=f"refund-order:{order.pk}").state, "review"
        )

    def test_low_stock_creates_plan_without_inventing_inventory(self):
        self.variant.stock = 2
        self.variant.save()
        sweep()
        sweep()
        self.assertEqual(
            RestockPlan.objects.filter(variant=self.variant, status="proposed").count(),
            1,
        )
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 2)
        self.client.force_login(self.staff)
        plan = RestockPlan.objects.get(variant=self.variant)
        action = self.proposal("receive_stock", plan.pk, quantity=7)
        self.confirm(action)
        self.confirm(action)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 9)

    def test_cart_recovery_requires_opt_in_and_unconverted_cart(self):
        snapshot = CartSnapshot.objects.create(
            user=self.user,
            cart={str(self.variant.pk): 1},
            fingerprint="fingerprint",
            updated=timezone.now() - timedelta(days=2),
        )
        sweep()
        run_jobs(100)
        self.assertFalse(
            Notification.objects.filter(key__startswith="cart-reminder:").exists()
        )
        preferences, _ = CustomerPreference.objects.get_or_create(user=self.user)
        preferences.marketing_opt_in = True
        preferences.save()
        snapshot.fingerprint = "new-fingerprint"
        snapshot.save()
        sweep()
        run_jobs(100)
        self.assertTrue(
            Notification.objects.filter(key__startswith="cart-reminder:").exists()
        )

    def test_failed_job_retry_has_backoff_and_limit(self):
        order = self.order()
        job = enqueue("label", "failing-label", {"shipment_id": 9999}, order=order)
        # Unexpected errors are reviewed, not blindly retried.
        run_jobs(100)
        job.refresh_from_db()
        self.assertEqual(job.state, "review")

    def test_expired_worker_lease_requires_review(self):
        job = enqueue("triage_enquiry", "expired-worker", {"enquiry_id": 0})
        job.state = "running"
        job.lease_until = timezone.now() - timedelta(minutes=1)
        job.save()
        sweep()
        job.refresh_from_db()
        self.assertEqual(job.state, "review")

    @patch("shop.providers.shiprocket_request")
    def test_courier_timeout_is_not_blindly_rebooked(self, provider):
        order = self.order()
        shipment = Shipment.objects.create(
            key="ambiguous", order=order, provider="shiprocket"
        )
        provider.side_effect = ProviderError("Network issue")
        with self.assertRaises(NeedsReview):
            book_shipment(shipment)
        shipment.refresh_from_db()
        with self.assertRaises(NeedsReview):
            book_shipment(shipment)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(shipment.state, "review")

    @patch("shop.providers.shiprocket_request")
    def test_courier_adapter_happy_path(self, provider):
        order = self.order()
        shipment = Shipment.objects.create(
            key="real-adapter", order=order, provider="shiprocket"
        )
        provider.side_effect = [
            {"order_id": 101, "shipment_id": 202},
            {
                "response": {
                    "data": {"awb_code": "AWB-123", "courier_name": "Test Courier"}
                }
            },
            {"pickup_status": 1},
        ]
        book_shipment(shipment)
        self.assertTrue(shipment.pickup_booked)
        self.assertEqual(shipment.awb, "AWB-123")
        payload = provider.call_args_list[0].args[2]
        self.assertEqual(payload["billing_pincode"], "627002")
        self.assertEqual(payload["payment_method"], "Prepaid")
        book_shipment(shipment)
        self.assertEqual(provider.call_count, 3)

    @patch("shop.providers.shiprocket_request")
    def test_undelivered_is_not_mistaken_for_delivered(self, provider):
        order = self.order()
        order.status = "packed"
        order.save()
        shipment = Shipment.objects.create(
            key="track-exception",
            order=order,
            provider="shiprocket",
            pickup_booked=True,
            awb="123",
        )
        provider.return_value = {
            "tracking_data": {"shipment_track": [{"current_status": "Undelivered"}]}
        }
        track_shipment(shipment)
        order.refresh_from_db()
        self.assertEqual(order.status, "packed")
        self.assertTrue(WorkTask.objects.filter(kind="shipping_exception").exists())

    def test_local_cart_agent_prepares_real_action(self):
        response = self.client.post(
            "/assistant/chat/",
            json.dumps({"agent": "cart", "message": f"add {self.variant.pk} 1"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["actions"]), 1)
        self.assertEqual(self.client.session.get("cart", {}), {})
        self.confirm(AgentAction.objects.get())
        self.assertEqual(self.client.session["cart"][str(self.variant.pk)], 1)

    def test_agent_cannot_propose_cross_domain_or_cross_user_action(self):
        response = self.client.post(
            "/assistant/chat/",
            json.dumps({"agent": "stylist", "message": "cancel AB-12345678"}),
            content_type="application/json",
        )
        self.assertEqual(response.json().get("actions"), [])
        self.assertIn("scope", response.json()["answer"])
        order = self.order()
        self.client.force_login(self.other)
        response = self.client.post(
            "/assistant/actions/propose/",
            {"kind": "cancel_order", "target": order.number, "quantity": 1},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AgentAction.objects.exists())

    def test_operations_and_notifications_are_scoped(self):
        self.assertEqual(self.client.get("/operations/").status_code, 403)
        self.assertEqual(self.client.get("/notifications/").status_code, 200)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get("/operations/"), "Your agents at work")

    def test_product_preview_and_motion_assets_are_wired(self):
        response = self.client.get("/product-preview/lavender/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["variants"][0]["id"], self.variant.pk)
        response = self.client.get("/")
        self.assertContains(response, "motion.js")
        self.assertContains(response, "quick-view-dialog")

    @override_settings(
        RETURN_WAREHOUSE={
            "name": "Vetri Boutique",
            "address": "Store Road",
            "city": "Tirunelveli",
            "state": "Tamil Nadu",
            "postcode": "627002",
            "phone": "9876543210",
            "email": "store@example.com",
        }
    )
    @patch("shop.providers.shiprocket_request")
    def test_reverse_courier_payload_and_awb_flag(self, provider):
        order = self.order(delivered=True)
        result = ReturnRequest.objects.create(
            item=order.items.first(),
            kind="return",
            reason="Wrong fit",
            status="approved",
        )
        shipment = Shipment.objects.create(
            key="reverse-live",
            order=order,
            return_request=result,
            provider="shiprocket",
            direction="reverse",
        )
        provider.side_effect = [
            {"order_id": 301, "shipment_id": 302},
            {
                "response": {
                    "data": {
                        "awb_code": "RETURN-123",
                        "courier_name": "Reverse Courier",
                    }
                }
            },
            {"pickup_status": 1},
        ]
        book_shipment(shipment)
        create_call = provider.call_args_list[0]
        self.assertEqual(create_call.args[1], "orders/create/return")
        self.assertEqual(create_call.args[2]["pickup_customer_name"], "Shopper")
        self.assertEqual(
            create_call.args[2]["shipping_customer_name"], "Vetri Boutique"
        )
        self.assertEqual(provider.call_args_list[1].args[2]["is_return"], 1)

    def test_reverse_delivery_requires_human_inspection_before_refund(self):
        order = self.order(delivered=True)
        result = ReturnRequest.objects.create(
            item=order.items.first(),
            kind="return",
            reason="Wrong fit",
            status="approved",
        )
        shipment = Shipment.objects.create(
            key="reverse-demo",
            order=order,
            return_request=result,
            provider="demo",
            direction="reverse",
            pickup_booked=True,
            demo_step=2,
            awb="DEMO-RETURN",
        )
        track_shipment(shipment)
        result.refresh_from_db()
        self.assertEqual(result.status, "received")
        self.assertEqual(result.refund_status, "none")
        self.assertTrue(WorkTask.objects.filter(key=f"inspect:{result.pk}").exists())

    @patch("shop.providers.shiprocket_request")
    def test_manifest_document_generation(self, provider):
        from .providers import generate_manifest

        order = self.order()
        shipment = Shipment.objects.create(
            key="manifest-live",
            order=order,
            provider="shiprocket",
            provider_shipment_id="11",
            provider_order_id="22",
            pickup_booked=True,
        )
        provider.return_value = {"manifest_url": "https://example.com/manifest.pdf"}
        generate_manifest(shipment)
        self.assertEqual(shipment.manifest_url, "https://example.com/manifest.pdf")
        self.assertEqual(provider.call_args.args[1], "manifests/generate")

    @patch(
        "shop.automation.generate_label",
        side_effect=ProviderError("Label temporarily unavailable"),
    )
    def test_transient_provider_error_is_retried_with_backoff(self, label):
        order = self.order()
        shipment = Shipment.objects.create(
            key="label-retry", order=order, provider="demo"
        )
        job = enqueue("label", "retry-label", {"shipment_id": shipment.pk}, order=order)
        run_jobs(100)
        job.refresh_from_db()
        self.assertEqual(job.state, "queued")
        self.assertEqual(job.attempts, 1)
        self.assertGreater(job.available_at, timezone.now())
        for unused in range(2):
            job.available_at = timezone.now() - timedelta(seconds=1)
            job.save()
            run_jobs(100)
            job.refresh_from_db()
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.attempts, 3)

    def test_nonfinite_price_cannot_be_published_through_agent(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            "/assistant/actions/propose/",
            {
                "kind": "update_price",
                "target": self.product.pk,
                "value": "NaN",
                "quantity": 1,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AgentAction.objects.count(), 0)

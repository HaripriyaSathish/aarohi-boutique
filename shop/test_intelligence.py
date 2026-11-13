from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
import json
import tempfile
from pathlib import Path
from PIL import Image
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from langchain_core.messages import AIMessage
from .models import (
    Category,
    Product,
    Variant,
    StoreSettings,
    Supplier,
    SupplierQuote,
    PurchaseOrder,
    CompetitorPrice,
    Campaign,
    CustomerPreference,
    AgentAction,
    Order,
    OrderItem,
    ReturnRequest,
    RiskReport,
    Warehouse,
    Notification,
)
from .services import create_order, mark_paid
from .automation import run_jobs, sweep
from .intelligence import (
    business_metrics,
    forecast_demand,
    supplier_comparison,
    pricing_intelligence,
    inventory_optimization,
    customer_segments,
    campaign_audience,
    assess_order,
    logistics_analysis,
    management_plan,
)


class IntelligenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "real-buyer", "buyer@example.com", "Strong-Password-1"
        )
        self.staff = User.objects.create_superuser(
            "store-owner", "owner@example.com", "Strong-Password-2"
        )
        StoreSettings.objects.create(shipping_fee=0)
        category = Category.objects.create(name="Kurtas", slug="kurtas")
        self.product = Product.objects.create(
            name="Lavender Cotton Kurta",
            slug="cotton-kurta",
            category=category,
            description="Cotton kurta.",
            price=1000,
            cost_price=400,
        )
        self.variant = Variant.objects.create(
            product=self.product, size="M", colour="Lavender", stock=20, sku="BIZ-M"
        )
        self.address = {
            "name": "Real Buyer",
            "email": "buyer@example.com",
            "phone": "9876543210",
            "line1": "Sample Road",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "postcode": "600001",
        }

    def order(self):
        order = create_order(self.user, {str(self.variant.pk): 1}, "", self.address)
        mark_paid(order.pk, "demo-" + str(order.pk))
        order.refresh_from_db()
        return order

    def action(self, kind, target="", value="", quantity=1, extra=""):
        self.client.force_login(self.staff)
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
        action = AgentAction.objects.latest("created")
        return self.client.post(
            f"/assistant/actions/{action.pk}/confirm/", HTTP_ACCEPT="application/json"
        )

    def test_cost_snapshot_and_measured_cash(self):
        order = self.order()
        self.assertEqual(order.items.first().unit_cost, Decimal("400"))
        order.status = "delivered"
        order.delivered_at = timezone.now()
        order.save()
        metrics = business_metrics()
        self.assertEqual(Decimal(metrics["net_collected_inr"]), Decimal("1000"))
        self.assertEqual(Decimal(metrics["margin_estimate_inr"]), Decimal("600"))
        self.assertIn("Not accounting profit", metrics["margin_note"])

    def test_insufficient_history_does_not_invent_forecast(self):
        self.assertEqual(
            forecast_demand()["forecasts"][0]["status"], "insufficient_data"
        )

    def test_forecast_has_observed_basis_and_backtest(self):
        for days in [1, 5, 10, 15, 20, 30, 35, 40]:
            order = self.order()
            Order.objects.filter(pk=order.pk).update(
                created=timezone.now() - timedelta(days=days)
            )
        row = forecast_demand()["forecasts"][0]
        self.assertEqual(row["status"], "estimate")
        self.assertIsNotNone(row["next_14_days_estimate"])
        self.assertIn("backtest_daily_mae", row)

    def test_supplier_comparison_and_moq(self):
        supplier = Supplier.objects.create(name="Sample Supplier", lead_days=5)
        SupplierQuote.objects.create(
            supplier=supplier,
            variant=self.variant,
            unit_price=300,
            minimum_quantity=10,
            freight=100,
            valid_until=timezone.localdate() + timedelta(days=5),
        )
        row = supplier_comparison(5)["quotes"][0]
        self.assertEqual(row["quantity"], 10)
        self.assertEqual(row["landed_unit_inr"], "310.00")
        self.assertIsNone(row["on_time_percent"])

    def test_pricing_requires_recorded_cost_and_competitor_data(self):
        self.assertIsNone(pricing_intelligence()["pricing"][0]["suggested_price_inr"])
        CompetitorPrice.objects.create(
            product=self.product, seller="Comparable Seller", price=900
        )
        row = pricing_intelligence()["pricing"][0]
        self.assertEqual(row["suggested_price_inr"], "891.00")
        self.assertGreaterEqual(
            Decimal(row["suggested_price_inr"]),
            Decimal(row["margin_floor_35_percent_inr"]),
        )

    def test_purchase_receipt_updates_stock_once_after_approval(self):
        supplier = Supplier.objects.create(name="Supplier")
        quote = SupplierQuote.objects.create(
            supplier=supplier,
            variant=self.variant,
            unit_price=300,
            minimum_quantity=5,
            valid_until=timezone.localdate() + timedelta(days=5),
        )
        self.assertEqual(
            self.action("create_purchase_order", quote.pk, quantity=5).status_code, 200
        )
        purchase = PurchaseOrder.objects.get()
        self.assertEqual(purchase.status, "draft")
        self.assertEqual(
            self.action("approve_purchase_order", purchase.pk).status_code, 200
        )
        self.assertEqual(
            self.action(
                "receive_purchase_order", purchase.pk, value="1", quantity=5
            ).status_code,
            200,
        )
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 24)
        action = AgentAction.objects.latest("created")
        self.client.post(
            f"/assistant/actions/{action.pk}/confirm/", HTTP_ACCEPT="application/json"
        )
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 24)

    def test_marketing_approval_rechecks_opt_in(self):
        CustomerPreference.objects.create(user=self.user, marketing_opt_in=True)
        self.assertEqual(
            self.action(
                "draft_campaign",
                "The Weekend Edit",
                "Discover the latest cotton styles in your boutique.",
                extra="all_opted_in",
            ).status_code,
            200,
        )
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.status, "draft")
        self.assertEqual(self.action("approve_campaign", campaign.pk).status_code, 200)
        CustomerPreference.objects.filter(user=self.user).update(marketing_opt_in=False)
        run_jobs(100)
        self.assertFalse(
            Notification.objects.filter(key__startswith="campaign:").exists()
        )

    def test_changed_campaign_cannot_use_old_approval(self):
        self.client.force_login(self.staff)
        campaign = Campaign.objects.create(
            name="Campaign", body="Original approved draft content."
        )
        self.client.post(
            "/assistant/actions/propose/",
            {"kind": "approve_campaign", "target": campaign.pk, "quantity": 1},
        )
        action = AgentAction.objects.get()
        campaign.body = "Changed content that was not reviewed."
        campaign.save()
        self.assertEqual(
            self.client.post(
                f"/assistant/actions/{action.pk}/confirm/",
                HTTP_ACCEPT="application/json",
            ).status_code,
            400,
        )

    def test_risk_screening_is_explainable_and_reviewable(self):
        self.product.price = 30000
        self.product.save()
        order = self.order()
        for unused in range(3):
            self.order()
        report = assess_order(order)
        self.assertGreater(report.score, 0)
        self.assertTrue(report.signals)
        order.risk_hold = True
        order.save()
        self.assertEqual(self.action("release_hold", order.number).status_code, 200)
        order.refresh_from_db()
        report.refresh_from_db()
        self.assertFalse(order.risk_hold)
        self.assertTrue(report.reviewed)

    def test_route_requires_actual_coordinates(self):
        self.assertEqual(
            logistics_analysis()["route_status"],
            "needs_warehouse_and_order_coordinates",
        )
        Warehouse.objects.create(
            name="Store", city="Chennai", latitude=13.08, longitude=80.27
        )
        order = self.order()
        order.status = "packed"
        order.delivery_latitude = 13.1
        order.delivery_longitude = 80.3
        order.save()
        report = logistics_analysis()
        self.assertEqual(report["local_delivery_route"], [order.number])
        self.assertGreater(report["estimated_straight_line_km"], 0)

    def test_business_reports_and_intelligence_pages(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/intelligence/").status_code, 403)
        self.assertContains(self.client.get("/agents/"), "Virtual Try-On")
        self.client.force_login(self.staff)
        for report in [
            "business_metrics",
            "demand_forecast",
            "supplier_comparison",
            "pricing_intelligence",
            "business_manager",
        ]:
            self.assertEqual(
                self.client.get("/intelligence/", {"report": report}).status_code, 200
            )
        self.assertTrue(management_plan()["action_plan"])

    def test_seo_changes_are_reviewed_and_published(self):
        response = self.action(
            "save_seo",
            self.product.pk,
            json.dumps(
                {
                    "title": "Cotton Kurta",
                    "meta": "Discover the lavender cotton kurta.",
                    "tags": "cotton,kurta",
                }
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.seo_title, "Cotton Kurta")

    def test_visual_search_validates_image_and_does_not_fake_tryon(self):
        self.client.force_login(self.user)
        self.assertContains(
            self.client.get("/ai-studio/"), "Try-on provider setup required"
        )
        response = self.client.post(
            "/ai-studio/",
            {
                "image": SimpleUploadedFile(
                    "bad.jpg", b"not-an-image", content_type="image/jpeg"
                )
            },
        )
        self.assertContains(response, "valid JPG")
        self.assertEqual(
            self.client.post(
                "/ai-studio/tryon/", {"product": self.product.pk, "photo_consent": "1"}
            ).status_code,
            302,
        )

    def test_local_colour_search_is_honestly_labelled(self):
        self.client.force_login(self.user)
        image = Image.new("RGB", (100, 100), "#ab8bc4")
        content = BytesIO()
        image.save(content, "JPEG")
        response = self.client.post(
            "/ai-studio/",
            {
                "mode": "colour",
                "image": SimpleUploadedFile(
                    "garment.jpg", content.getvalue(), content_type="image/jpeg"
                ),
            },
        )
        self.assertContains(response, "not semantic AI")


class GraphTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "graph-user", "graph@example.com", "Strong-Password"
        )
        StoreSettings.objects.create()
        category = Category.objects.create(name="Kurtas", slug="kurtas")
        self.product = Product.objects.create(
            category=category,
            name="Cotton Kurta",
            slug="graph-kurta",
            description="Cotton kurta.",
            price=1000,
        )
        self.variant = Variant.objects.create(
            product=self.product, size="M", colour="Lavender", stock=5, sku="GRAPH-1"
        )
        self.client.force_login(self.user)

    @override_settings(AI_MODE="gemini", AGENT_ENGINE="langgraph")
    @patch("shop.llm_graph.make_model")
    def test_langgraph_runs_actual_tools_and_returns_proposal(self, model):
        bound = model.return_value.bind_tools.return_value
        bound.invoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "propose_action",
                        "id": "graph-action",
                        "type": "tool_call",
                        "args": {
                            "action": "add_to_cart",
                            "target": str(self.variant.pk),
                            "value": "",
                            "quantity": 1,
                            "extra": "",
                        },
                    }
                ],
            ),
            AIMessage(
                content="I prepared the selected kurta. Please review and confirm."
            ),
        ]
        response = self.client.post(
            "/assistant/chat/",
            json.dumps({"agent": "cart", "message": "Add this kurta"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["engine"], "LangGraph + LangChain")
        self.assertEqual(len(response.json()["actions"]), 1)
        self.assertEqual(self.client.session.get("cart", {}), {})
        action = AgentAction.objects.get()
        self.assertEqual(
            self.client.post(
                f"/assistant/actions/{action.pk}/confirm/",
                HTTP_ACCEPT="application/json",
            ).status_code,
            200,
        )
        self.assertEqual(self.client.session["cart"][str(self.variant.pk)], 1)

    @override_settings(AI_MODE="gemini", AGENT_ENGINE="langgraph")
    @patch("shop.llm_graph.make_model")
    def test_langgraph_blocks_unallowed_tools(self, model):
        bound = model.return_value.bind_tools.return_value
        bound.invoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "sales_summary",
                        "args": {},
                        "id": "forbidden",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="I cannot access store-wide sales in this customer session."
            ),
        ]
        response = self.client.post(
            "/assistant/chat/",
            json.dumps({"agent": "stylist", "message": "Show other customers"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("cannot access", response.json()["answer"])

    @override_settings(AI_MODE="gemini", AGENT_ENGINE="langgraph")
    @patch("shop.llm_graph.make_model", side_effect=RuntimeError("provider problem"))
    def test_graph_initialization_error_is_visible(self, model):
        response = self.client.post(
            "/assistant/chat/",
            json.dumps({"agent": "stylist", "message": "Show kurtas"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)

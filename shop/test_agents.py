import json
from unittest.mock import patch
from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase, override_settings
from django.urls import reverse
from .agents import AGENTS, AgentError, execute_tool, route_agent, run_openai
from .models import Category, Product, StoreSettings, Variant


class AgentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "ai-buyer", "buyer@example.com", "StrongPass-21"
        )
        self.staff = User.objects.create_superuser(
            "ai-admin", "admin@example.com", "StrongPass-22"
        )
        StoreSettings.objects.create(return_days=10)
        category = Category.objects.create(name="Kurtas", slug="kurtas")
        self.product = Product.objects.create(
            name="Lavender Cotton Kurta",
            slug="lavender-cotton-kurta",
            category=category,
            description="Soft cotton",
            fabric="Cotton",
            price=1500,
        )
        Variant.objects.create(
            product=self.product, size="M", colour="Lavender", stock=3, sku="AI-1"
        )
        self.client.force_login(self.user)

    def send(self, message, agent="auto"):
        return self.client.post(
            reverse("assistant_chat"),
            data=json.dumps({"message": message, "agent": agent}),
            content_type="application/json",
        )

    def test_assistant_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/assistant/").status_code, 302)
        self.assertEqual(self.send("Show kurtas").status_code, 302)

    def test_page_explains_local_mode(self):
        response = self.client.get("/assistant/")
        self.assertContains(response, "Rules-based, not generative AI")
        self.assertNotContains(response, 'value="admin"')

    @override_settings(AI_MODE="local")
    def test_local_stylist_returns_live_catalogue(self):
        data = self.send("cotton kurta under 2000 size M").json()
        self.assertEqual(data["mode"], "local")
        self.assertEqual(data["products"][0]["id"], self.product.pk)
        self.assertEqual(data["products"][0]["price_inr"], "1500.00")
        self.assertIn("search_products", data["tools"])
        self.assertIn("local demo", data["answer"].lower())

    def test_local_budget_filters_out_expensive_products(self):
        data = self.send("cotton under 1000").json()
        self.assertEqual(data["products"], [])

    def test_local_size_filter_excludes_unavailable_size(self):
        data = self.send("cotton size XL").json()
        self.assertEqual(data["products"], [])

    def test_admin_agent_denied_for_customer(self):
        self.assertEqual(self.send("Show all sales", "admin").status_code, 403)
        result = execute_tool("sales_summary", {}, self.user, AGENTS["admin"].tools)
        self.assertEqual(result, {"error": "Staff permission required."})

    def test_disallowed_tool_cannot_be_called_by_model(self):
        result = execute_tool("sales_summary", {}, self.user, AGENTS["stylist"].tools)
        self.assertEqual(result, {"error": "Tool is not allowed for this agent."})
        result = execute_tool(
            "cancel_order", {"order": "anything"}, self.user, AGENTS["support"].tools
        )
        self.assertIn("error", result)

    def test_admin_insights_for_authorized_staff(self):
        self.client.force_login(self.staff)
        response = self.send("What needs restocking?", "admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn("stock_alerts", response.json()["tools"])
        self.assertIn("AI-1", response.json()["answer"])
        self.assertContains(self.client.get("/admin/"), "Verified sales")

    @override_settings(AI_MAX_REQUESTS_PER_HOUR=1)
    def test_database_quota_enforced(self):
        self.assertEqual(self.send("Show kurtas").status_code, 200)
        self.assertEqual(self.send("Show kurtas").status_code, 429)

    def test_bad_payload_rejected(self):
        self.assertEqual(self.send("").status_code, 400)
        self.assertEqual(self.send("x" * 1201).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/assistant/chat/", data="[]", content_type="application/json"
            ).status_code,
            400,
        )

    def test_private_orders_are_scoped_to_authenticated_user(self):
        from .services import create_order

        other = User.objects.create_user(
            "second", "second@example.com", "StrongPass-24"
        )
        variant = Variant.objects.first()
        order = create_order(
            other,
            {str(variant.pk): 1},
            "",
            {
                "name": "Private Customer",
                "email": "second@example.com",
                "phone": "9876543210",
                "line1": "Secret address",
                "city": "Chennai",
                "state": "Tamil Nadu",
                "postcode": "600001",
            },
        )
        data = execute_tool(
            "order_status",
            {"reference": order.number},
            self.user,
            AGENTS["support"].tools,
        )
        self.assertIn("error", data)
        own = execute_tool(
            "order_status", {"reference": order.number}, other, AGENTS["support"].tools
        )
        self.assertEqual(own["orders"][0]["reference"], order.number)
        self.assertNotIn("Secret address", json.dumps(own))
        self.assertNotIn("9876543210", json.dumps(own))

    @override_settings(AI_MODE="openai", AGENT_ENGINE="direct")
    @patch("shop.agents.call_model")
    def test_real_agent_function_loop_is_grounded(self, model):
        model.side_effect = [
            {
                "output": [
                    {
                        "type": "function_call",
                        "name": "search_products",
                        "call_id": "call_1",
                        "arguments": json.dumps(
                            {
                                "query": "cotton",
                                "max_price": 2000,
                                "size": "M",
                                "colour": "",
                            }
                        ),
                    }
                ]
            },
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Try the Lavender Cotton Kurta at INR 1500.",
                            }
                        ],
                    }
                ]
            },
        ]
        response = self.send("Suggest a cotton kurta under 2000")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["mode"], "openai")
        self.assertEqual(result["products"][0]["id"], self.product.pk)
        second_request = model.call_args_list[1].args[0]
        self.assertTrue(
            any(
                item.get("type") == "function_call_output"
                for item in second_request["input"]
            )
        )
        self.assertFalse(second_request["store"])

    @override_settings(AI_MODE="openai", AGENT_ENGINE="direct")
    @patch(
        "shop.agents.call_model", side_effect=AgentError("AI service is unavailable.")
    )
    def test_provider_failure_is_visible_not_fake_ai(self, model):
        response = self.send("Suggest an outfit")
        self.assertEqual(response.status_code, 503)
        self.assertIn("unavailable", response.json()["error"])

    def test_clear_chat_removes_server_history(self):
        self.send("Show cotton kurtas")
        self.assertIn("agent_history", self.client.session)
        self.client.post("/assistant/clear/")
        self.assertNotIn("agent_history", self.client.session)

    def test_invalid_tool_arguments_do_not_crash(self):
        self.assertIn(
            "error",
            execute_tool(
                "search_products",
                {"max_price": "NaN"},
                self.user,
                AGENTS["stylist"].tools,
            ),
        )
        self.assertIn(
            "error",
            execute_tool(
                "get_product_details",
                {"product_id": "not-an-id"},
                self.user,
                AGENTS["stylist"].tools,
            ),
        )
        self.assertIn(
            "error",
            execute_tool("get_product_details", [], self.user, AGENTS["stylist"].tools),
        )

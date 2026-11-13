import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from langchain_core.messages import AIMessage

from .models import Category, Product, StoreSettings, Variant


@override_settings(AI_MODE="gemini", GEMINI_API_KEY="test", PAYMENT_MODE="demo", NOTIFICATION_MODE="demo")
class AgentCardTests(TestCase):
    """Product cards shown in chat must match what the agent actually recommends."""

    def setUp(self):
        StoreSettings.objects.create()
        cat = Category.objects.create(name="Sarees", slug="sarees")
        self.cheap = Product.objects.create(category=cat, name="Cotton Dupatta", slug="dupatta", price=500)
        self.saree = Product.objects.create(category=cat, name="Rose Silk Saree", slug="rose", price=3499)
        for p in (self.cheap, self.saree):
            Variant.objects.create(product=p, size="Free", colour="Rose", sku=f"SKU-{p.pk}", stock=3)
        self.user = User.objects.create_user("buyer", "b@example.com", "Secret-12345")
        self.client.force_login(self.user)

    def chat(self, model_reply, language="en-IN"):
        with patch("shop.llm_graph.make_model") as make_model:
            model = Mock()
            make_model.return_value.bind_tools.return_value = model
            model.invoke.side_effect = [AIMessage(content=model_reply)]
            response = self.client.post(
                "/assistant/chat/",
                json.dumps({"agent": "stylist", "message": "silk saree please", "language": language}),
                content_type="application/json",
            )
            prompt = model.invoke.call_args[0][0][0].content
        self.assertEqual(response.status_code, 200, response.content)
        return response.json(), prompt

    def test_cards_only_show_products_named_in_reply(self):
        data, _ = self.chat("Try the **Rose Silk Saree** for ₹3499.")
        self.assertEqual([p["name"] for p in data["products"]], ["Rose Silk Saree"])

    def test_background_listing_is_not_shown_when_nothing_named(self):
        data, _ = self.chat("Could you tell me your budget?")
        self.assertEqual(data["products"], [])

    def test_language_rule_is_explicit(self):
        _, prompt = self.chat("Hello", language="en-IN")
        self.assertIn("Reply ONLY in clear, simple English", prompt)
        _, prompt = self.chat("Vanakkam", language="tanglish")
        self.assertIn("Tanglish", prompt)


class SmartSearchTests(TestCase):
    def setUp(self):
        cat = Category.objects.create(name="Kurtas & Kurta Sets", slug="kurtas")
        other = Category.objects.create(name="Sarees", slug="sarees")
        self.lav = Product.objects.create(category=cat, name="Lavender Embroidered Kurta Set", slug="lav", price=2499)
        self.purple = Product.objects.create(category=cat, name="Elegant Kurta set", slug="elegant", price=1499,
                                             description="Elegant daily wear kurta set")
        self.saree = Product.objects.create(category=other, name="Rose Silk Saree", slug="rose", price=3499)
        Variant.objects.create(product=self.lav, size="M", colour="Lavender", sku="L1", stock=5)
        self.purple_variant = Variant.objects.create(product=self.purple, size="L", colour="Purple", sku="P1", stock=4)
        Variant.objects.create(product=self.saree, size="Free", colour="Rose", sku="S1", stock=3)

    def names(self, query):
        from .agents import search_products
        return [p["name"] for p in search_products(query)["products"]]

    def test_colour_family_finds_purple_kurta_for_lavender(self):
        names = self.names("show me a lavender kurta")
        self.assertIn("Elegant Kurta set", names)
        self.assertIn("Lavender Embroidered Kurta Set", names)
        self.assertNotIn("Rose Silk Saree", names)
        self.assertEqual(names[0], "Lavender Embroidered Kurta Set")  # name + colour match ranks first

    def test_out_of_stock_products_are_hidden(self):
        self.purple_variant.stock = 0
        self.purple_variant.save()
        self.assertNotIn("Elegant Kurta set", self.names("lavender kurta"))

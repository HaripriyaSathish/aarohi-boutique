import base64
from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
from PIL import Image
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from .models import Category, Product, StoreSettings, TryOnRequest


class VisionTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(
            MEDIA_ROOT=Path(self.directory.name) / "media",
            PRIVATE_MEDIA_ROOT=Path(self.directory.name) / "private",
            FASHN_API_KEY="test-provider-key",
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create_user(
            "photo-owner", "photo@example.com", "Strong-Password"
        )
        self.other = User.objects.create_user(
            "other-photo", "other@example.com", "Strong-Password"
        )
        StoreSettings.objects.create()
        category = Category.objects.create(name="Kurtas", slug="kurtas")
        self.product = Product.objects.create(
            category=category,
            name="Lilac Kurta",
            slug="lilac-kurta",
            description="Kurta.",
            price=1000,
        )
        output = BytesIO()
        Image.new("RGB", (100, 150), "#bca2d5").save(output, "JPEG")
        self.image = output.getvalue()
        self.product.image.save("garment.jpg", ContentFile(self.image))
        self.client.force_login(self.user)

    def photo(self):
        return SimpleUploadedFile("person.jpg", self.image, content_type="image/jpeg")

    @patch("shop.vision.requests.post")
    def test_explicit_consent_is_required_before_provider_submission(self, post):
        response = self.client.post(
            "/ai-studio/tryon/", {"product": self.product.pk, "person": self.photo()}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(post.called)
        self.assertEqual(TryOnRequest.objects.count(), 0)

    @patch("shop.vision.requests.get")
    @patch("shop.vision.requests.post")
    def test_real_adapter_private_output_and_owner_access(self, post, get):
        post.return_value = Mock()
        post.return_value.json.return_value = {"id": "provider-test-id", "error": None}
        response = self.client.post(
            "/ai-studio/tryon/",
            {"product": self.product.pk, "person": self.photo(), "photo_consent": "1"},
        )
        self.assertEqual(response.status_code, 302)
        record = TryOnRequest.objects.get()
        self.assertEqual(record.status, "processing")
        payload = post.call_args.kwargs["json"]
        self.assertTrue(
            payload["inputs"]["model_image"].startswith("data:image/jpeg;base64,")
        )
        self.assertTrue(payload["inputs"]["return_base64"])
        get.return_value = Mock()
        get.return_value.json.return_value = {
            "status": "completed",
            "output": [
                "data:image/jpeg;base64," + base64.b64encode(self.image).decode()
            ],
        }
        self.client.post(f"/ai-studio/tryon/{record.pk}/refresh/")
        record.refresh_from_db()
        self.assertEqual(record.status, "completed")
        response = self.client.get(f"/ai-studio/tryon/{record.pk}/image/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        response.close()
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(f"/ai-studio/tryon/{record.pk}/image/").status_code, 404
        )
        self.assertEqual(
            self.client.post(f"/ai-studio/tryon/{record.pk}/delete/").status_code, 404
        )
        self.client.force_login(self.user)
        self.client.post(f"/ai-studio/tryon/{record.pk}/delete/")
        self.assertFalse(TryOnRequest.objects.exists())
        self.assertFalse(
            (Path(self.directory.name) / "private" / f"tryon-{record.pk}.jpg").exists()
        )

    @patch("shop.vision.requests.post", side_effect=__import__("requests").Timeout())
    def test_uncertain_tryon_submission_is_not_faked_or_retried(self, post):
        self.client.post(
            "/ai-studio/tryon/",
            {"product": self.product.pk, "person": self.photo(), "photo_consent": "1"},
        )
        record = TryOnRequest.objects.get()
        self.assertEqual(record.status, "review")
        self.assertFalse(record.result_path)
        self.client.post(f"/ai-studio/tryon/{record.pk}/refresh/")
        self.assertEqual(post.call_count, 1)

    @patch("shop.llm_graph.make_model")
    def test_vision_model_search_request_uses_images_and_returns_query(self, factory):
        model = factory.return_value.model_copy.return_value
        model.invoke.return_value.content = "lavender kurta"
        response = self.client.post(
            "/ai-studio/", {"image": self.photo(), "mode": "vision", "vision_consent": "1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "lavender kurta")
        content = model.invoke.call_args.args[0][0].content
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    @patch("shop.llm_graph.make_model")
    def test_vision_requires_consent_before_provider_call(self, factory):
        response = self.client.post("/ai-studio/", {"image": self.photo(), "mode": "vision"})
        self.assertContains(response, "Consent to Google Gemini")
        factory.assert_not_called()

    def test_invalid_product_cannot_crash_tryon_endpoint(self):
        response = self.client.post(
            "/ai-studio/tryon/",
            {"product": "bad-id", "person": self.photo(), "photo_consent": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TryOnRequest.objects.count(), 0)

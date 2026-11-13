"""Image similarity, Gemini vision understanding and real try-on API.

Colour matching is explicitly distinguished from semantic vision. Person photos
are never stored in publicly served MEDIA_ROOT or silently sent to a provider.
"""

import base64
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import quote
import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from .models import Product
from .agents import serialize_product, search_products


class VisionError(Exception):
    pass


def clean_image(upload):
    if upload.size > 5 * 1024 * 1024:
        raise VisionError("Choose an image smaller than 5 MB.")
    try:
        image = Image.open(upload)
        if image.width * image.height > 20_000_000:
            raise VisionError("Choose an image below 20 megapixels.")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1400, 1600))
        output = BytesIO()
        image.save(output, format="JPEG", quality=90)
        return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise VisionError("Upload a valid JPG, PNG or WebP photo.") from exc


def data_url(content):
    return "data:image/jpeg;base64," + base64.b64encode(content).decode()


def colour_vector(content):
    image = Image.open(BytesIO(content)).convert("RGB")
    # Focus on the central garment area to reduce background influence.
    width, height = image.size
    image = (
        image.crop((width * 0.25, height * 0.22, width * 0.75, height * 0.85))
        .resize((80, 80))
        .convert("HSV")
    )
    histogram = image.histogram()
    features = []
    for channel in range(3):
        values = histogram[channel * 256 : (channel + 1) * 256]
        features += [
            sum(values[index : index + 32]) / 6400 for index in range(0, 256, 32)
        ]
    return features


def describe_with_gemini(content):
    from langchain_core.messages import HumanMessage
    from .llm_graph import make_model, provider_error
    from .agents import AgentError
    try:
        model = make_model("gemini")
        model = model.model_copy(update={"model": settings.GEMINI_VISION_MODEL})
        result = model.invoke([HumanMessage(content=[
            {"type": "text", "text": "Describe only the garment for catalogue search in 12 words maximum: clothing type, visible colour and pattern. No person identification or sensitive inferences. Return plain text only."},
            {"type": "image_url", "image_url": {"url": data_url(content)}},
        ])])
        answer = result.content
        if isinstance(answer, list):
            answer = " ".join(item.get("text", "") for item in answer if isinstance(item, dict))
        if not isinstance(answer, str) or not answer.strip():
            raise VisionError("No garment description returned. Try a clearer garment photo.")
        return answer.strip()[:200]
    except VisionError:
        raise
    except AgentError as exc:
        raise VisionError(str(exc)) from exc
    except Exception as exc:
        raise VisionError(provider_error(exc)) from exc


def visual_search(content, semantic=False):
    query = describe_with_gemini(content) if semantic else ""
    candidates = (
        Product.objects.filter(active=True, category__active=True)
        .exclude(image="")
        .select_related("category")
        .prefetch_related("variants")[:300]
    )
    matched = search_products(query).get("products", []) if query else []
    semantic_ids = {product["id"] for product in matched}
    target = colour_vector(content)
    scored = []
    for product in candidates:
        try:
            with product.image.open("rb") as file:
                vector = colour_vector(file.read())
        except (OSError, ValueError, UnidentifiedImageError):
            continue
        distance = sum((first - second) ** 2 for first, second in zip(target, vector))
        if semantic_ids and product.pk not in semantic_ids:
            distance += 2
        scored.append((distance, product))
    scored.sort(key=lambda item: item[0])
    return {
        "products": [serialize_product(product) for _, product in scored[:6]],
        "query": query,
        "method": (
            "Gemini garment description + catalogue/color ranking"
            if semantic
            else "Local colour similarity — not semantic AI and not an exact garment match"
        ),
    }


def submit_tryon(record, person):
    if not settings.FASHN_API_KEY:
        raise VisionError(
            "Virtual try-on requires the configured FASHN provider key. No fake try-on preview is generated."
        )
    if not record.product.image:
        raise VisionError("This product needs a garment image.")
    with record.product.image.open("rb") as file:
        garment_image = Image.open(file).convert("RGB")
        output = BytesIO()
        garment_image.thumbnail((1400, 1600))
        garment_image.save(output, format="JPEG", quality=90)
    record.status = "submitting"
    record.save(update_fields=["status"])
    try:
        response = requests.post(
            "https://api.fashn.ai/v1/run",
            headers={"Authorization": "Bearer " + settings.FASHN_API_KEY},
            json={
                "model_name": settings.TRYON_MODEL,
                "inputs": {
                    "model_image": data_url(person),
                    "garment_image": data_url(output.getvalue()),
                    "return_base64": True,
                    "output_format": "jpeg",
                    "moderation_level": "conservative",
                    "num_samples": 1,
                },
            },
            timeout=(5, 45),
        )
        response.raise_for_status()
        provider_id = response.json().get("id")
        if not provider_id:
            raise ValueError
        record.provider_id = provider_id
        record.status = "processing"
        record.save(update_fields=["provider_id", "status"])
    except (requests.RequestException, ValueError) as exc:
        record.status = "review"
        record.error = "Provider submission outcome is uncertain. Do not retry a potentially billed request blindly; check the provider dashboard."
        record.save(update_fields=["status", "error"])
        raise VisionError(record.error) from exc


def refresh_tryon(record):
    if record.status != "processing" or not record.provider_id:
        return
    try:
        response = requests.get(
            "https://api.fashn.ai/v1/status/" + quote(record.provider_id, safe=""),
            headers={"Authorization": "Bearer " + settings.FASHN_API_KEY},
            timeout=(5, 25),
        )
        response.raise_for_status()
        result = response.json()
        status = result.get("status")
        if status == "completed":
            output = result.get("output", [])
            if (
                not output
                or not isinstance(output[0], str)
                or not output[0].startswith("data:image/")
            ):
                raise VisionError(
                    "Provider did not return the requested private base64 output. Check provider configuration."
                )
            decoded = base64.b64decode(output[0].split(",", 1)[1], validate=True)
            if len(decoded) > 15 * 1024 * 1024:
                raise VisionError("Provider image exceeds the allowed size.")
            image = Image.open(BytesIO(decoded))
            if image.width * image.height > 20_000_000:
                raise VisionError("Provider output exceeds the image-dimension limit.")
            image = image.convert("RGB")
            destination = Path(settings.PRIVATE_MEDIA_ROOT) / f"tryon-{record.pk}.jpg"
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, format="JPEG", quality=92)
            record.result_path = destination.name
            record.status = "completed"
        elif status in ["failed", "cancelled"]:
            record.status = "failed"
            record.error = "The try-on provider could not generate this preview. Check your provider dashboard."
        record.save()
    except (requests.RequestException, ValueError, OSError) as exc:
        raise VisionError(
            "Unable to refresh try-on right now. Try again later."
        ) from exc

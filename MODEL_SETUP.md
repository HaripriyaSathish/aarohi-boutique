# Gemini model setup

Follow README.md. No local model installation is required.

```dotenv
AI_MODE=gemini
AGENT_ENGINE=langgraph
GEMINI_API_KEY=YOUR_PRIVATE_KEY
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_VISION_MODEL=gemini-3.5-flash-lite
AI_TIMEOUT_SECONDS=60
```

Use a model your account can access; check free quota in Google AI Studio. Restart server AND worker after edits. Process environment variables override `.env`; remove old PowerShell AI_MODE values or set AI_MODE=gemini explicitly.

Run `python manage.py check_agents --live` to check the provider. It consumes quota. 429 = quota/rate limit; key/access error = verify key/project; model unavailable = choose an accessible model. Quota failures do not mean the cart/order backend failed.

FASHN is a separate optional realistic try-on integration. Gemini text/vision does not implement clothing fitting by itself.

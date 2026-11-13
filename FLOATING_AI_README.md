# Aarohi Floating AI Assistant Update

## Implemented
- Floating launcher in `templates/shop/base.html` on customer pages.
- Chat panel on desktop, fullscreen panel on mobile, close/Escape support.
- Existing AI Stylist links open the widget instead of navigating away.
- Existing assistant page embedded in a compact widget view (`?widget=1`), preserving the original agent cards, chat form, voice input, speech playback, permissions and backend endpoints.
- Customer-facing widget CSS/JS: `static/css/ai_widget.css`, `static/css/ai_widget_inner.css`, `static/js/ai_widget.js`.

## Start
1. Create your virtual environment and install requirements as described in the existing README.
2. Configure `.env` using `.env.example`. Do not put credentials in frontend JavaScript.
3. `python manage.py migrate`
4. `python manage.py runserver`
5. Visit the storefront and click **Ask Aarohi AI**.
6. In a separate terminal, start the existing worker: `python manage.py run_agents --watch` (check supported flags with `--help`). Use a supervised background service in production.

## Validation
- Python compilation for `shop/` succeeded.
- JavaScript syntax check for `ai_widget.js` succeeded.
- Django runtime/browser, Gemini live calls, payment gateway and courier services were NOT verified in this environment.

## Not yet delivered
- 18 fully autonomous, production-tested agents.
- Verified end-to-end voice order placement and automatic payment reconciliation.
- Real courier integration and external email/WhatsApp delivery.
- Fully automated AI decision-making across all background jobs.

Existing worker actions and existing tool permissions remain unchanged; the UI must not be interpreted as evidence of new autonomous backend execution.

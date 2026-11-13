# Aarohi Boutique Autonomous V4

Complete updated Django project, upgraded from the supplied V3 source. Existing store data, routes, checkout, returns, catalogue, wishlist, admin and image studio are retained. New state uses additive migrations 0010–0013. Start with `SETUP_V4.md`.

## What executes

- Gemini + LangGraph runs a bounded model → permitted tool → model loop, with real database reads and writes. No model-generated SQL or Python is executed.
- **12 public admin agents + 6 public customer agents**, listed in `AGENTS_V4.md`. Earlier specialist IDs and intelligence report URLs remain for compatibility.
- Requested bag edits, eligible coupon application, catalogue description/SEO edits and campaign **draft** creation can execute during a turn when the visible routine-update authorization is enabled. Object permissions and agent scopes still apply.
- Checkout uses exact variants, current stock, a saved customer address and server-written totals. A durable review page requires explicit authenticated customer approval, creates one pending order, and opens existing secure payment checkout. Repeated approval returns the same order.
- Gemini cannot authorize a charge or mark an order paid. Verified gateway callbacks/webhooks and the clearly labelled local demo flow control payment status.
- Paid-order jobs issue an immutable invoice snapshot, generate a downloadable PDF, queue notifications and create packing tasks. Courier booking starts only after packing confirmation and risk checks.
- Existing durable workers cover order expiry/reconciliation, stock alerts/restock plans, shipping, return pickup/exchanges/refunds under policy, consent-based recovery/marketing and daily management reports.
- Text, editable browser speech input and browser speech output support English/Tamil; Tanglish response mode explicitly requests romanized Tamil mixed with English. Speech quality, available voices and mixed-language recognition depend on browser/OS services. No audio is stored by Django.
- Shared plum/champagne/porcelain styling covers customer pages, checkout review, agent studio, operations and all Django admin pages, with mobile layouts and reduced-motion handling.

## Verification performed

114 automated tests passed, including the original 101 regression tests and 13 new authorization, ownership, checkout, retry, stock, graph execution and invoice tests. Actual Gemini calls succeeded against isolated synthetic records: a multi-step customer cart/checkout journey and an authorized admin product-description update. Demo payment completed separately after customer approval.

Live Razorpay transactions/webhooks, Shiprocket booking, SMTP delivery, external try-on and real microphone/speaker behavior were **not verified**. Browser screenshot QA was blocked by runtime/browser access restrictions; template/API tests passed. See `TEST_REPORT.md` and `INTEGRATIONS_V4.md` for exact evidence and limits.

No real API key is included in the updated package. Keep your existing private `.env` separately, then restore only the settings you intend to use. The supplied `.env.example` starts in an explicitly labelled local sandbox; set `AI_MODE=gemini` and your key for real model execution. The included database preserves your uploaded records and login credentials; no shared/default admin password is added.

This is implemented source with tests and runnable sandbox services. Live service onboarding, tax/legal invoice configuration, production infrastructure and real-world acceptance testing remain deployment work; the package does not claim those are completed.

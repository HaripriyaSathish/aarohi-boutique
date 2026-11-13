# Upgrade verification — 10 November 2026

## Automated checks

- Original source baseline: **101 tests passed**.
- Upgraded full suite: **114 tests passed** in 40.215 seconds; Django reported no system-check issues.
- New coverage: public role roster/access, customer ownership, no reservation before checkout approval, invalid/overstock quantities, repeated checkout approval, stock/price changes with rollback, expired quotes, CSRF/approval requirements, routine turn consent/scope, protected financial/physical boundaries, authorized staff description write, real compiled LangGraph loop with mocked model messages, paid-order invoice/ownership and client retry protection.
- Python 3.12; Django 5.2.19; LangGraph 1.2.20; LangChain Google GenAI 4.4.3; ReportLab 4.5.1. Exact installed versions: `requirements-tested.txt`.
- `manage.py check`: passed. `makemigrations --check --dry-run`: no missing migrations. `node --check static/js/assistant.js`: passed.
- Additive migrations 0010–0013 applied successfully to a copy of the uploaded database. Original products (12), variants (68), orders (1), users (2) and addresses (0) retained. Final preservation comparison confirmed all 162 original rows in 37 shop/user tables exactly match the uploaded archive.

## Real Gemini execution

A private key present in the uploaded configuration was used only server-side. The key is excluded from the output archive. A separate synthetic SQLite database was used for all live workflow writes; no uploaded customer/order records were sent for those tests.

1. Minimal Gemini request returned successfully.
2. Customer request explicitly selected a synthetic product/variant, quantity two and saved address. Actual Gemini/LangGraph execution used `cart_status → get_product_details → execute_authorized_action → saved_checkout_details → prepare_purchase`. HTTP 200, bag quantity 2, one durable checkout journey; **no order before customer approval**.
3. Authenticated customer review POST created one pending order. The existing explicitly labelled demo payment endpoint changed payment state to paid. This is not evidence of a live gateway transaction.
4. Admin content request used `search_products → execute_authorized_action`. HTTP 200; synthetic product description matched the requested text in the database.

The SDK reported that this Gemini model ignores the supplied sampling temperature and some strict JSON-schema fields. Server-side validation/permissions are enforced independently and the live tool calls succeeded. Model responses remain nondeterministic and subject to provider quota/availability.

## Unverified / deployment limits

- Live Razorpay charge/capture/webhook/refund, Shiprocket bookings, SMTP inbox delivery, live FASHN and Gemini image calls were not exercised. Their mocked/sandbox regression tests are separate from live verification.
- Real microphone, Tamil/English/Tanglish recognition accuracy, installed speech voices and actual speaker output were not exercised.
- Screenshot/layout QA was attempted: the local Chromium process was blocked by runtime socket restrictions, and the separate browser could not access the development server. Responsive CSS and templates are implemented, but desktop/mobile rendered appearance needs acceptance testing on your device.
- No load, penetration, recovery-under-process-crash, tax compliance or production deployment certification is claimed.
- Background operation requires the worker process. Physical packing/inspection and customer/provider payment authorization remain human/provider actions.

Final focused reruns after language, error-reporting and tool-deduplication changes: 45 agent/intelligence tests passed, then 17 autonomous/Gemini tests passed.

# Integration verification

| Component | Implementation | Verified in this upgrade | Configuration / limit |
|---|---|---|---|
| Gemini + LangGraph | Real bounded tool execution | Live minimal response, customer multi-step bag + checkout preparation, admin description write, isolated synthetic database | Supply your private key; selected model must support tools |
| Razorpay | Existing orders, signature verification, capture/status/amount checks, webhooks, refunds/reconciliation | Automated regression tests; no real money sent | `PAYMENT_MODE=razorpay`, three Razorpay settings; use provider test keys first; live payments unverified |
| Demo payment | Explicit DEBUG-only simulation | Customer-approved synthetic order became paid | `.env.example` uses `demo`; not a payment service |
| Shiprocket | Outbound/reverse/exchange booking, labels/pickup/tracking through adapter | Automated mocked/sandbox tests; live API unverified | Set `SHIPPING_MODE=shiprocket`, API user credentials/token, pickup and return warehouse |
| SMTP email | Existing durable notification outbox and consent-aware marketing | Automated tests, in-app demo; actual inbox delivery unverified | `NOTIFICATION_MODE=email`, SMTP backend/credentials; console backend does not deliver |
| PDF invoice | Immutable paid-order snapshot + ReportLab PDF | Payment worker and PDF bytes/ownership tested | Paid-order receipt; GST/statutory tax fields need business implementation |
| Browser voice | Editable Web Speech recognition; SpeechSynthesis output | UI/code integration only; real mic, Tamil/Tanglish accuracy and speaker behavior unverified | Browser/OS/permissions dependent, HTTPS or localhost |
| FASHN try-on | Existing private image submission/polling | Existing validation/ownership tests; live provider unverified | Separate paid provider key; no key configured during check |
| Visual search | Existing catalogue/Gemini image search | Existing tests; live image-model call unverified | Separate vision quota; fallback is labelled colour matching |
| Marketing | Internal draft → staff approval → consent-filtered outbox | Existing tests + authorized draft tool path | No new external ad platform, WhatsApp or SMS adapter is claimed |
| Supplier purchase orders | Internal draft/approval/physical-receipt records | Existing tests | Does not place real supplier orders or transfer supplier payments |

No unconfigured adapter is represented as live. The package does not silently turn on external spending, SMTP delivery, advertising, real refunds or courier bookings. Restore your own secrets from your existing `.env`; secrets used for verification are excluded from the ZIP.

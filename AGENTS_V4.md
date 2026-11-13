# Public agent roster

The public picker and studio expose exactly 12 admin specialists and 6 customer specialists. Existing `manager`, `logistics`, `inventory_optimizer` and `recovery` specialist IDs remain internally available for backward-compatible links; visual search/try-on remain separate existing features.

| Admin ID | Specialist | Implemented work / execution boundary |
|---|---|---|
| admin | Operations | Measured queues, packing confirmation, approved returns/refunds, exceptions |
| inventory | Inventory | Stock alerts, restock plans; physical receipt confirmed by staff |
| shipping | Shipping | Packing confirmation, carrier tracking refresh, reviewed retries |
| content | Catalogue | Authorized description and SEO writes; prices require confirmation |
| business | Business intelligence | Measured sales/margins/customer activity |
| forecast | Demand forecasting | Historical estimates with missing-data/uncertainty flags |
| suppliers | Supplier intelligence | Recorded quotes and reviewed purchase-order/receipt operations |
| pricing | Pricing intelligence | Recorded competitor/cost analysis; confirmed price changes |
| marketing | Marketing | Authorized campaign draft writes; confirmed opt-in sends |
| retention | Retention | Customer segments and authorized retention campaign drafts |
| fraud | Fraud investigation | Explainable risk reports and confirmed hold release |
| return_investigator | Return investigation | Evidence-based eligibility and reviewed return approval |

| Customer ID | Specialist | Implemented work / execution boundary |
|---|---|---|
| stylist | Shopping & styling | Real products/variants, authorized bag edits, prepared checkout |
| cart | Cart & checkout | Bag, offers, authorized edits, saved-address checkout preparation |
| support | Order support | Own order lookup, confirmed cancellation/return requests |
| payments | Payment assistance | Verified order/payment status and secure checkout guidance |
| returns | Returns & exchanges | Own eligible delivered items and confirmed requests |
| voice | Voice shopping | Same product/bag/checkout tools with editable voice transcripts |

Not every specialist needs a write: forecasting and intelligence produce evidence-grounded reads; financial and physical specialists require confirmation where appropriate. All use the real Gemini/LangGraph loop when Gemini is enabled, with tool allowlists checked again on the server.

## Authorization and retry semantics

`execute_authorized_action` only accepts add/remove bag, eligible coupon, product description/SEO and campaign draft actions, when authorized for that turn and permitted by the selected agent and Django object/model permissions. Campaign draft execution never sends a message. It creates and applies an audited action record; repeated identical routine tool arguments within the same turn are deduplicated.

`propose_action` remains for protected operations and for routine changes when turn authorization is off. The authenticated confirmation endpoint rechecks access/eligibility. Checkout authorization uses its separate review POST, not model-generated confirmation text. Carrier booking does not claim physical picking/packing/delivery occurred.

Browser chat sends a UUID `Idempotency-Key`. The server saves completed responses per user/key and rejects key reuse with a different request. An uncertain running request is not replayed. API clients should also provide this header; clients omitting it retain legacy behavior and do not receive turn-level retry protection. No tool can run arbitrary SQL, shell commands or read another customer's address/order.

Read-only request/action/journey/invoice records are available in Django admin to appropriately permissioned staff. Existing detailed `AGENTS_GUIDE.md`, `AUTOMATION_GUIDE.md` and intelligence reports describe V3 foundations; V4 behavior and setup in this document and `SETUP_V4.md` take precedence.

# Event-driven operations

Run the website and `python manage.py run_agents --watch` at the same time. The Windows launcher starts a separate worker window. Production should use a maintained service, not an unattended development terminal.

| Trigger | Automatic work | Human/configuration boundary |
| --- | --- | --- |
| Verified captured payment | Confirm order, screen explainable risk signals, create packing task, create notification | High-risk hold requires review; physical packing requires staff confirmation |
| Confirmed packing | Create shipment, assign AWB, request pickup, generate documents | Live Shiprocket configuration/account required; uncertain calls enter review |
| Booked shipment | Poll carrier tracking; update customer timeline/notifications | Carrier scans are authoritative; no fake live delivery state |
| Demo shipment | Advance clearly labelled simulated tracking | No real parcel is booked |
| Cancel before packing | Release stock/coupon reservation, queue eligible refund | Real autonomous refunds default OFF until owner enables them; staff may approve a specific refund |
| Eligible return request | Policy review, approve under configured policy, schedule reverse pickup | Real warehouse destination and carrier support required |
| Reverse delivery | Record received state and create inspection task | Staff must inspect goods before refund/replacement |
| Accepted inspection | Restock once; issue permitted refund or reserve/send exchange size | Insufficient exchange stock becomes a review problem rather than negative stock |
| Low stock | Create one open restock plan and task | Stock does not increase until staff confirms physical receipt |
| Purchase receipt | Record receipt/defects and apply saleable stock once | Internal purchase approval is not supplier payment/order transmission |
| Abandoned bag | Recheck snapshot/conversion/consent; queue personalized reminder | Marketing opt-in required, and consent rechecked before email |
| Approved campaign | Queue unique notifications for the reviewed audience | Audience capped at 1,000; consent rechecked; SMTP required for external email |
| Delivered order | Schedule review request | Customer can disable review requests |
| Enquiry | Categorize and create staff task | No fabricated email answer is sent automatically |
| Daily business sweep | Persist report and create priority review tasks | Pricing/campaign/spend/catalogue changes need explicit authority |

## Owner policy

`/operations/` exposes automation toggles, live-refund authority, low-stock settings and review timing. Only authorized policy editors can change them. Staff actions still require the corresponding Django permission.

Defaults allow demo workflows and routine automation. `allow_live_refunds` is false by default. Enabling it explicitly authorizes eligible automatic real-money refund submissions. Physical inspection still cannot be bypassed by chat.

## Durable queue and retries

Jobs are stored in SQLite with unique keys, attempts, availability time, lease, status, result and error. Eligible queued jobs are claimed with a conditional database update. Expected transient provider errors back off (30, 60… seconds) and stop after three attempts. Unexpected errors and expired worker leases require review.

External exactly-once delivery cannot be guaranteed by a database transaction alone. This implementation handles that explicitly:

- Razorpay refund requests use stable provider idempotency keys.
- Courier creation/AWB/pickup stages persist references and state.
- Uncertain courier outcomes are not blindly re-created. Reconcile in the provider dashboard, fill the existing shipment references in Django admin, reset the reconciled stage appropriately, then retry the job.
- Uncertain SMTP outcomes remain `sending` and require log review/reset rather than silently resending.
- Notifications, campaign recipients, stock receipts and action confirmations have application-level deduplication.

Read the task/error before retrying. Clicking retry is an acknowledgement that any uncertain external effect was reconciled; it does not magically repair a provider account.

## Continuous run / scheduler

```bash
python manage.py run_agents --watch --interval 5
```

Or schedule a bounded cycle:

```bash
python manage.py run_agents --once
```

SQLite is suitable for this local/small-store package. For sustained parallel writes, use a production database/queue and load-test provider timeouts and concurrency. Back up the database/media. The worker service example in `deploy/` needs actual server paths and a least-privilege runtime user.

## Delivery integrations

Configure the Shiprocket API-user credentials or token, a valid pickup location, realistic package dimensions/weight and return warehouse details. These example parcel dimensions are not automatic weighing measurements. Carrier serviceability, balance, permissions and pickup acceptance must be tested against the merchant account.

Tracking uses authenticated provider polling; unsupported/exception statuses remain visible and create review tasks where appropriate. For local fleet route suggestions, enter real coordinates; the heuristic uses straight-line distances and does not operate a carrier's private routing system.

WhatsApp click-to-chat is available in store settings. A WhatsApp Business notification provider was not supplied and automated WhatsApp sending is not claimed.

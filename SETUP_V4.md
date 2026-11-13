# Setup, upgrade and operation

## 1. Preserve your current installation

Stop the web process and automation worker. Back up `db.sqlite3`, `media/`, any `private_media/` and your private `.env`. Extract this archive into a **new directory**, not over a running virtual environment. The included database is the original uploaded data with additive migrations applied. If your working database has newer records, copy your own database into the new project instead, then run migrations. Do not replace a newer database with the bundled snapshot.

Python 3.10+ is required (verified here on Python 3.12). Create a fresh environment; never copy a Windows virtual environment to Linux or another computer.

Windows PowerShell, from `aarohi_boutique`:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-tested.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py check
python manage.py runserver 8001
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-tested.txt
cp .env.example .env
python manage.py migrate
python manage.py check
python manage.py runserver 8001
```

Open http://127.0.0.1:8001/. Retain your original admin login. If you do not know its password, use `python manage.py changepassword YOUR_USERNAME` locally. For an entirely empty database, run `python manage.py createsuperuser`; optional `python manage.py seed_demo` adds sample catalogue data. Do not seed an existing business database unless you intend to add samples.

`START_WINDOWS.bat` and `./start.sh` also install, migrate, ensure an admin exists and start the server/worker. They respect `.env` and do not automatically seed or force Gemini. `RUN_AGENTS_WINDOWS.bat` starts the worker alone. Avoid starting a duplicate worker accidentally.

## 2. Enable real Gemini execution

Edit your private `.env`:

```dotenv
AI_MODE=gemini
AGENT_ENGINE=langgraph
GEMINI_API_KEY=YOUR_PRIVATE_GOOGLE_AI_STUDIO_KEY
GEMINI_MODEL=gemini-3.5-flash-lite
AI_TIMEOUT_SECONDS=60
```

Choose a text/tool-calling model accessible to your own account; availability and quota are provider-specific. The model above was successfully used in this upgrade's live checks. Restart Django after editing environment settings.

```bash
python manage.py check_agents
python manage.py check_agents --live
```

`python scripts/verify_live_workflow.py` additionally exercises a live Gemini shopping/content workflow against disposable synthetic data and a demo payment. It creates a temporary database and does not use your customer database. It makes several billable model calls; run it intentionally with your own key.

`--live` makes one model request and can consume quota. A multi-step agent turn consumes several calls; an hourly turn quota does not equal a provider token budget. Keys remain server-side. Optional OpenAI mode remains compatible; this upgrade's autonomous workflows are primarily verified through Gemini/LangGraph.

## 3. Run the background worker

In a second terminal with the same environment activated:

```bash
python manage.py run_agents --watch --interval 5
```

Or use a scheduler:

```bash
python manage.py run_agents --once --limit 100
```

On a VPS, adapt `deploy/aarohi-agents.service` and run the worker as an unprivileged service user. The worker must have access to the same database and environment as Django. A worker process is necessary for continuous operation; opening a dashboard does not run a scheduler. Jobs persist in the database, use claims/leases and backoff. Uncertain expired leases require review rather than blindly replaying external effects. `/operations/` shows jobs, physical tasks, review exceptions and policy. Live automatic refunds are disabled unless the store owner explicitly enables the relevant policy; reviewed per-refund actions are available.

## 4. Exercise an order

1. Sign in as a customer and save a valid Indian delivery address in `/addresses/`. The account needs a valid email.
2. Open `/assistant/`, choose Shopping or Cart, and request “Order 2 silk sarees”. The agent searches real catalogue data. Choose the exact product/colour/size if more than one choice fits; it must not invent a selection or saved address.
3. Keep “Allow requested bag updates and routine staff edits” enabled if you want requested routine edits executed during the message. Disable it for proposals requiring confirmation.
4. The agent prepares a checkout and shows **Review items & authorize checkout**. Review exact variants, quantity, totals and address. No order or charge exists yet.
5. Approve the server-written summary. Stock/totals are rechecked, one pending order is created, purchased items leave the bag and payment opens. If prices changed, prepare a new quote. If stock ran out, no order is committed.
6. In demo mode use the clearly marked demo payment form. In Razorpay mode the customer completes provider checkout. Chat never receives card/UPI secrets. Callback signatures, order/payment identity, amount/currency and webhook checks remain in the existing payment service.
7. The worker issues the paid-order invoice snapshot and notices and creates a pack task. Download the PDF from the order invoice page. An authorized staff member confirms actual packing before configured courier booking; returns require appropriate policy/inspection steps.
8. Check customer notifications, `/operations/`, order tracking and agent/action audit records in admin.

Local sandbox without Gemini: use exact commands `add VARIANT_ID 2`, `remove VARIANT_ID`, `coupon CODE`, then `prepare checkout` with the Cart Agent. These execute through the same validation tools and are explicitly rules-based. Free-form Tamil/Tanglish understanding requires Gemini; local rules are not represented as an LLM.

## 5. Voice

Use Chrome/Edge with HTTPS (localhost is generally treated as a secure context). Select English, Tamil or Tanglish, click **Use voice**, allow microphone access yourself, review the editable transcript and send. Tanglish uses Tamil recognition plus a mixed-language response preference; recognition quality varies. **Listen** reads one response; **Read replies aloud** is optional. Browser/OS speech services may process audio externally. If the browser lacks recognition or a Tamil voice, use text; the UI reports the limitation. No unattended voice authorization of payment is provided.

## 6. Production configuration

Set a strong unique `SECRET_KEY`, `DEBUG=false`, exact `ALLOWED_HOSTS`, TLS, live payment/courier modes and appropriate notification settings. Configure Nginx/Gunicorn, restricted media/private-media access, backups, monitoring, database permissions and worker service. Install Gunicorn separately for a Linux deployment. Serve static files after `python manage.py collectstatic --noinput`. Do not serve `.env`, SQLite files or private media as static content. Private try-on images remain behind authenticated views.

This release retains SQLite for data compatibility. For higher write concurrency, validate a PostgreSQL migration separately; SQLite cannot provide PostgreSQL-style row locks. Conditional stock/claim updates and atomic transactions guard supported paths, but a high-volume multiworker deployment still needs load testing.

The invoice PDF is a paid-order receipt. GST/tax calculation, statutory invoice numbering/required business fields and credit notes are not implemented as a tax compliance system. Configure those for the business before using it as a statutory tax invoice.

## 7. Checks and rollback

```bash
python manage.py test --noinput
python manage.py makemigrations --check --dry-run
python manage.py check
```

Tests use a separate test database and mocked external providers, except explicitly requested `check_agents --live`. For rollback, stop both processes and restore the previous source **and matching database/media backup**. Do not simply remove new model code while keeping incompatible migration state. Do not delete migration history.

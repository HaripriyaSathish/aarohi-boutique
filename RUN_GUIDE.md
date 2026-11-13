# Aarohi Boutique – How to run (Windows, MySQL)

## One-time setup (already done on this PC)
```powershell
cd C:\Users\Haripriya\Downloads\Aarohi_Boutique_Agent_Checkout\Aarohi_Boutique_Autonomous_V4\aarohi_boutique
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py seed_users
python manage.py seed_orders   # sample orders/returns/enquiry
```
If Activate.ps1 is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Every day
Easiest: double-click **RUN_SERVER.bat** and **RUN_WORKER.bat** in this folder.

Or in PowerShell (you MUST be inside the `aarohi_boutique` folder, and activate .venv first, otherwise `python`/`pip` are "not found"):
Terminal 1 (website):
```powershell
cd C:\Users\Haripriya\Downloads\Aarohi_Boutique_Agent_Checkout\Aarohi_Boutique_Autonomous_V4\aarohi_boutique
.\.venv\Scripts\Activate.ps1
python manage.py runserver 8001
```
Terminal 2 (background agents: emails, invoices, order jobs):
```powershell
cd C:\Users\Haripriya\Downloads\Aarohi_Boutique_Agent_Checkout\Aarohi_Boutique_Autonomous_V4\aarohi_boutique
.\.venv\Scripts\Activate.ps1
python manage.py run_agents --watch --interval 5
```
Open http://127.0.0.1:8001/ — admin at /admin/. Logins: LOCAL_TEST_LOGINS.txt

## Keys (put in .env, then restart runserver)
| Need | Where to get | .env lines |
|---|---|---|
| Gemini AI | https://aistudio.google.com/apikey → Create API key (free) | `AI_MODE=gemini`, `GEMINI_API_KEY=...`, `GEMINI_MODEL=gemini-2.5-flash` |
| Razorpay test | https://dashboard.razorpay.com → switch to **Test Mode** → Settings → API Keys | `PAYMENT_MODE=razorpay`, `RAZORPAY_KEY_ID=rzp_test_...`, `RAZORPAY_KEY_SECRET=...` |
| Gmail SMTP | Google Account → Security → 2-Step Verification ON → App passwords → create | see below |

Gmail SMTP:
```
NOTIFICATION_MODE=email
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=yourgmail@gmail.com
EMAIL_HOST_PASSWORD=16-char-app-password-no-spaces
DEFAULT_FROM_EMAIL=Aarohi Boutique <yourgmail@gmail.com>
```
Check Gemini works: `python manage.py check_agents --live`

## Database (MySQL)
Configured in .env (`DB_ENGINE=mysql`, database `aarohi_boutique`). View tables in MySQL Workbench → Local instance → Schemas → aarohi_boutique (click refresh).

## Admin Pending-Work Agent
- Board: http://127.0.0.1:8001/admin-tasks/ (staff only; link also on the /admin/ home page)
- Chat: open "Ask Aarohi AI" while logged in as admin -> choose "Admin: Pending Work"
- Uses Gemini for the briefing when AI_MODE=gemini, otherwise a rules-based plan.

## Cashfree payments (test mode)
`.env`: `PAYMENT_MODE=cashfree`, `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY`, `CASHFREE_ENV=sandbox`.
On the Cashfree page choose **Test Wallet** → simulator → OTP shown on page → SUCCESS. No real money.
For `demo` (no gateway) set `PAYMENT_MODE=demo`. Restart the server after changing `.env`.

## Order emails (Gmail SMTP)
`.env`: `NOTIFICATION_MODE=email`, `EMAIL_HOST_USER=<your gmail>`, `EMAIL_HOST_PASSWORD=<app password>`,
`DEFAULT_FROM_EMAIL=Aarohi Boutique <your gmail>`. Emails are sent by the worker (RUN_WORKER.bat).
Set the customer's email (admin → Users) to an inbox you can check.

### Why Cashfree shows a "Simulator" page
Test (sandbox) keys never touch real banks, so Cashfree replaces the bank/UPI step with its simulator
(pick SUCCESS / FAILED, OTP shown on the page). This is expected. For the real flow:
1. Complete KYC/activation in the Cashfree merchant dashboard and generate **Production** API keys.
2. In `.env`: `CASHFREE_ENV=production`, production `CASHFREE_APP_ID` / `CASHFREE_SECRET_KEY`.
3. Host the site on a public **HTTPS** domain, whitelist that domain in Cashfree, set `SITE_URL=https://yourdomain`,
   and add the webhook URL `https://yourdomain/payments/cashfree/webhook/` in the dashboard.
No code change is needed — the same checkout then opens real UPI apps / bank OTP pages.

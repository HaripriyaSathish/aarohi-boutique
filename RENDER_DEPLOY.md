# Deploy Aarohi Boutique on Render (one Web Service + Neon PostgreSQL)

**Local** keeps using MySQL (from `.env`). **Render** uses Neon PostgreSQL through `DATABASE_URL`.
Neon already contains a full copy of the local MySQL data (all 40 tables / 278 rows matched).

## 1. Put the code on GitHub (one time)
Run inside the `aarohi_boutique` folder (the one with `manage.py`):
```powershell
git init
git add .
git commit -m "Aarohi Boutique: purple UI, Gemini agents, Cashfree, Neon-ready"
git branch -M main
git remote add origin https://github.com/<your-username>/aarohi-boutique.git
git push -u origin main
```
`.env`, `.venv/`, `LOCAL_TEST_LOGINS.txt` and `staticfiles/` are git-ignored, so secrets are NOT uploaded.
The `media/demo/` product photos ARE uploaded (the site needs them).

## 2. Create the Web Service
Render dashboard → **New + → Web Service** → connect the GitHub repo.

| Setting | Value |
|---|---|
| Name | `aarohi-boutique` (gives `https://aarohi-boutique.onrender.com`) |
| Region | Singapore (closest to India) or Ohio (closest to the Neon DB in us-east-2) |
| Branch | `main` |
| Root Directory | leave empty (or `aarohi_boutique` if you pushed the parent folder) |
| Runtime | **Python 3** |
| Build Command | `bash build.sh` |
| Start Command | `bash render_start.sh` |
| Instance type | Free (or Starter for always-on) |

`build.sh` installs packages, collects static files and runs migrations on Neon.
`render_start.sh` starts the background agent worker (emails, invoices, order jobs) **and** Gunicorn in the same web service.

## 3. Environment variables (Render → your service → Environment)
Copy the values from your local `.env`. Never commit them.

| Key | Value |
|---|---|
| `PYTHON_VERSION` | `3.12.8` |
| `DEBUG` | `false` |
| `SECRET_KEY` | a new long random string (generate: `python -c "import secrets;print(secrets.token_urlsafe(50))"`) |
| `DATABASE_URL` | the Neon URL (same as `NEON_DATABASE_URL` in your `.env`) |
| `SITE_URL` | `https://aarohi-boutique.onrender.com` (your Render URL) |
| `AI_MODE` | `gemini` |
| `AGENT_ENGINE` | `langgraph` |
| `GEMINI_API_KEY` | your Gemini key |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` |
| `PAYMENT_MODE` | `cashfree` |
| `CASHFREE_APP_ID` / `CASHFREE_SECRET_KEY` | your Cashfree keys |
| `CASHFREE_ENV` | `sandbox` (test) — `production` only with live keys |
| `NOTIFICATION_MODE` | `email` |
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend` |
| `EMAIL_HOST` | `smtp.gmail.com` |
| `EMAIL_PORT` | `587` |
| `EMAIL_HOST_USER` | your Gmail address |
| `EMAIL_HOST_PASSWORD` | your Gmail app password |
| `DEFAULT_FROM_EMAIL` | `Aarohi Boutique <your Gmail address>` |
| `SHIPPING_MODE` | `demo` |
| `ALLOW_DEMO_SHIPPING` | `true` (no courier account yet) |

`ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` are set automatically from Render's hostname. If you add a custom
domain, add it to both, e.g. `ALLOWED_HOSTS=shop.example.com` and `CSRF_TRUSTED_ORIGINS=https://shop.example.com`.

## 4. Deploy
Click **Create Web Service**. First build takes ~3–5 minutes. Open the URL when the log shows
`Listening at: http://0.0.0.0:10000`.
- Admin: `https://<your-app>.onrender.com/admin/` — same users as local (they were copied to Neon).
  **Change the `admin123` password immediately** (Admin → Users → admin → change password).
- Test a purchase with Cashfree sandbox, check the confirmation email.

## 5. Cashfree on Render
In the Cashfree dashboard (sandbox): Developers → Webhooks → add
`https://<your-app>.onrender.com/payments/cashfree/webhook/` (Payment success). Whitelist your Render domain if asked.

## Good to know
- **Free plan sleeps** after ~15 min idle; the first visit then takes ~30–50 s. The background worker sleeps too,
  so emails are sent when the service wakes. Use the Starter plan for always-on.
- **Uploaded images** (new products added in admin on Render) are stored on Render's temporary disk and are lost
  on redeploy. Add new product images to `media/` in git, or later connect Cloudinary/S3.
- Re-copy local data to Neon later (overwrites matching rows):
  ```powershell
  .\.venv\Scripts\python -X utf8 manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission -e sessions -o data.json
  $env:DATABASE_URL = "<Neon URL>"; .\.venv\Scripts\python -X utf8 manage.py loaddata data.json; Remove-Item Env:DATABASE_URL
  ```

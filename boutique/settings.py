import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
import sys

# The project .env wins over stale shell/IDE variables (e.g. a VS Code terminal
# that captured an old AI_MODE). Tests keep explicit env overrides instead.
load_dotenv(
    BASE_DIR / ".env",
    override="test" not in sys.argv and os.getenv("DOTENV_OVERRIDE", "true").lower() == "true",
)
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
SECRET_KEY = os.getenv("SECRET_KEY", "local-development-only-change-before-deployment")
if not DEBUG and SECRET_KEY in [
    "local-development-only-change-before-deployment",
    "replace-with-a-long-random-secret-for-production",
]:
    raise ImproperlyConfigured("Set SECRET_KEY before production deployment.")
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",") if h.strip()]
# Render sets RENDER_EXTERNAL_HOSTNAME (e.g. aarohi-boutique.onrender.com) automatically.
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append("https://" + RENDER_EXTERNAL_HOSTNAME)
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "shop.apps.ShopConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "shop.middleware.CartSnapshotMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "boutique.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "shop.context_processors.store_context",
            ],
        },
    }
]
WSGI_APPLICATION = "boutique.wsgi.application"
DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").lower()
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL:
    # Production / Render: PostgreSQL (Neon) via one connection URL.
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=60, conn_health_checks=True)}
elif DB_ENGINE == "mysql":
    # PyMySQL is pure Python (no C build tools needed on Windows).
    import pymysql

    pymysql.version_info = (2, 2, 1, "final", 0)
    pymysql.install_as_MySQLdb()
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.getenv("DB_NAME", "aarohi_boutique"),
            "USER": os.getenv("DB_USER", "root"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
            "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
        },
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("DATABASE_PATH", str(BASE_DIR / "db.sqlite3")),
            "OPTIONS": {"timeout": 20},
        },
    }
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# Serve /media/ from Django when no CDN/object storage is configured (Render demo).
SERVE_MEDIA = os.getenv("SERVE_MEDIA", "true").lower() == "true"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "account"
LOGOUT_REDIRECT_URL = "home"
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_TIMEOUT = 20
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8001")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "orders@example.com")
PAYMENT_MODE = os.getenv("PAYMENT_MODE", "demo")
if PAYMENT_MODE not in ["demo", "razorpay", "cashfree"]:
    raise ImproperlyConfigured("PAYMENT_MODE must be demo, razorpay or cashfree.")
if not DEBUG and PAYMENT_MODE == "demo":
    raise ImproperlyConfigured(
        "Demo payments require DEBUG=true. Use cashfree or razorpay in production."
    )
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
CASHFREE_APP_ID = os.getenv("CASHFREE_APP_ID", "")
CASHFREE_SECRET_KEY = os.getenv("CASHFREE_SECRET_KEY", "")
CASHFREE_ENV = os.getenv("CASHFREE_ENV", "sandbox").lower()
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    # Render terminates HTTPS at its proxy and forwards X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "true").lower() == "true"
    SECURE_HSTS_SECONDS = 31536000

# API keys remain server-side and are never rendered into templates.
AI_MODE = os.getenv("AI_MODE", "gemini").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", GEMINI_MODEL)
AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "60"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
AI_MAX_REQUESTS_PER_HOUR = int(os.getenv("AI_MAX_REQUESTS_PER_HOUR", "30"))
if AI_MODE not in ["local", "openai", "gemini"]:
    raise ImproperlyConfigured("AI_MODE must be local, openai or gemini. This edition does not require Ollama.")

SHIPPING_MODE = os.getenv("SHIPPING_MODE", "demo")
SHIPROCKET_TOKEN = os.getenv("SHIPROCKET_TOKEN", "")
SHIPROCKET_EMAIL = os.getenv("SHIPROCKET_EMAIL", "")
SHIPROCKET_PASSWORD = os.getenv("SHIPROCKET_PASSWORD", "")
SHIPROCKET_PICKUP_LOCATION = os.getenv("SHIPROCKET_PICKUP_LOCATION", "Primary")
PARCEL_WEIGHT_KG = float(os.getenv("PARCEL_WEIGHT_KG", "0.5"))
PARCEL_LENGTH_CM = float(os.getenv("PARCEL_LENGTH_CM", "25"))
PARCEL_BREADTH_CM = float(os.getenv("PARCEL_BREADTH_CM", "20"))
PARCEL_HEIGHT_CM = float(os.getenv("PARCEL_HEIGHT_CM", "5"))
NOTIFICATION_MODE = os.getenv("NOTIFICATION_MODE", "demo")
DEMO_TRACK_SECONDS = int(os.getenv("DEMO_TRACK_SECONDS", "15"))
if SHIPPING_MODE not in ["demo", "shiprocket"]:
    raise ImproperlyConfigured("SHIPPING_MODE must be demo or shiprocket.")
if NOTIFICATION_MODE not in ["demo", "email"]:
    raise ImproperlyConfigured("NOTIFICATION_MODE must be demo or email.")
# A hosted demo / college project may run without a courier account: set
# ALLOW_DEMO_SHIPPING=true explicitly. Real stores should use SHIPPING_MODE=shiprocket.
ALLOW_DEMO_SHIPPING = os.getenv("ALLOW_DEMO_SHIPPING", "false").lower() == "true"
if not DEBUG and SHIPPING_MODE == "demo" and not ALLOW_DEMO_SHIPPING:
    raise ImproperlyConfigured(
        "Production requires SHIPPING_MODE=shiprocket (or ALLOW_DEMO_SHIPPING=true for a demo deployment)."
    )

RETURN_WAREHOUSE = {
    "name": os.getenv("RETURN_WAREHOUSE_NAME", ""),
    "address": os.getenv("RETURN_WAREHOUSE_ADDRESS", ""),
    "city": os.getenv("RETURN_WAREHOUSE_CITY", ""),
    "state": os.getenv("RETURN_WAREHOUSE_STATE", ""),
    "postcode": os.getenv("RETURN_WAREHOUSE_PIN", ""),
    "phone": os.getenv("RETURN_WAREHOUSE_PHONE", ""),
    "email": os.getenv("RETURN_WAREHOUSE_EMAIL", ""),
}

AGENT_ENGINE = os.getenv("AGENT_ENGINE", "langgraph")
if AGENT_ENGINE not in ["langgraph", "direct"]:
    raise ImproperlyConfigured("AGENT_ENGINE must be langgraph or direct.")

PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"
FASHN_API_KEY = os.getenv("FASHN_API_KEY", "")
TRYON_MODEL = os.getenv("TRYON_MODEL", "tryon-v1.6")
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

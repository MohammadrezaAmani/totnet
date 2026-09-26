"""
Django settings for Multi-Tenant VPN Platform
"""

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from decouple import AutoConfig, Config, RepositoryEmpty

BASE_DIR = Path(__file__).resolve().parent.parent

# Compose injects values from its env_file. Avoid having python-decouple reopen
# the host-mounted .env, which is commonly mode 0600 and unreadable by the
# non-root container user. Outside Docker, retain its normal .env discovery.
config = (
    Config(RepositoryEmpty())
    if os.environ.get("RUNNING_IN_DOCKER") == "1"
    else AutoConfig()
)


def parse_debug(value):
    """Accept deployment labels as well as boolean DEBUG values."""
    normalized = str(value).strip().lower()
    if normalized in {"release", "production", "prod", "false", "0", "no", "off"}:
        return False
    if normalized in {"debug", "development", "dev", "true", "1", "yes", "on"}:
        return True
    raise ValueError("DEBUG must be a boolean or an environment label such as release")


SECRET_KEY = config("SECRET_KEY", default="django-insecure-change-this-in-production")
DEBUG = config("DEBUG", default=True, cast=parse_debug)
ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1",
    cast=lambda v: [s.strip() for s in v.split(",")],
)
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="",
    cast=lambda v: [origin.strip() for origin in v.split(",") if origin.strip()],
)


DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "django_extensions",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.brands",
    "apps.vpn_providers",
    "apps.subscriptions",
    "apps.orders",
    "apps.referrals",
    "apps.support",
    "apps.broadcasts",
    "apps.bot",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


DATABASE_URL = config("DATABASE_URL", default="sqlite:///db.sqlite3")

if DATABASE_URL.startswith("sqlite"):
    database_path = unquote(DATABASE_URL[len("sqlite:///") :].split("?", 1)[0])
    database_name = (
        ":memory:"
        if database_path == ":memory:"
        else Path(database_path or "db.sqlite3")
    )
    if isinstance(database_name, Path) and not database_name.is_absolute():
        database_name = BASE_DIR / database_name
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": database_name,
        }
    }
elif DATABASE_URL.startswith("postgresql"):
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


AUTH_USER_MODEL = "accounts.User"


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATIC_ROOT = Path(config("STATIC_ROOT", default=str(BASE_DIR / "staticfiles")))
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").is_dir() else []


MEDIA_URL = "media/"
MEDIA_ROOT = Path(config("MEDIA_ROOT", default=str(BASE_DIR / "media")))


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")
CONNECTIX_API_BASE_URL = (
    config("CONNECTIX_BASE_URL", default="https://api.connectix.vip")
    or "https://api.connectix.vip"
).rstrip("/")
CONNECTIX_USERNAME = config("CONNECTIX_USERNAME", default="")
CONNECTIX_PASSWORD = config("CONNECTIX_PASSWORD", default="")
CONNECTIX_TIMEOUT_SECONDS = config("CONNECTIX_TIMEOUT_SECONDS", default=20, cast=int)
BOT_RELOAD_INTERVAL_SECONDS = config(
    "BOT_RELOAD_INTERVAL_SECONDS", default=5, cast=int
)


CELERY_BROKER_URL = config("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE


CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}


SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_AGE = 86400


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}


CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

CORS_ALLOW_CREDENTIALS = True


SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="localhost")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="noreply@example.com")


FILE_UPLOAD_MAX_MEMORY_SIZE = config("MAX_UPLOAD_SIZE", default=10485760, cast=int)
DATA_UPLOAD_MAX_MEMORY_SIZE = FILE_UPLOAD_MAX_MEMORY_SIZE


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "file": {
            "level": "INFO",
            "class": "logging.FileHandler",
            "filename": Path(
                config("LOG_DIR", default=str(BASE_DIR / "logs"))
            )
            / "django.log",
            "formatter": "verbose",
        },
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}


LOG_DIR = Path(config("LOG_DIR", default=str(BASE_DIR / "logs")))
os.makedirs(LOG_DIR, exist_ok=True)


USE_WEBHOOK = config("USE_WEBHOOK", default=False, cast=bool)
WEBHOOK_DOMAIN = config("WEBHOOK_DOMAIN", default="")
WEBHOOK_PATH = config("WEBHOOK_PATH", default="/webhook")

SOCKS5_PROXY = config("SOCKS5_PROXY", default=None)
if SOCKS5_PROXY and os.environ.get("RUNNING_IN_DOCKER") == "1":
    proxy_value = SOCKS5_PROXY.strip()
    proxy_url = (
        proxy_value if "://" in proxy_value else f"socks5://{proxy_value}"
    )
    proxy_parts = urlsplit(proxy_url)
    if proxy_parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        userinfo = (
            f"{proxy_parts.netloc.rsplit('@', 1)[0]}@"
            if "@" in proxy_parts.netloc
            else ""
        )
        proxy_port = f":{proxy_parts.port}" if proxy_parts.port else ""
        proxy_parts = proxy_parts._replace(
            netloc=f"{userinfo}host.docker.internal{proxy_port}"
        )
    SOCKS5_PROXY = urlunsplit(proxy_parts)
VPN_PROVIDER_TIMEOUT = config("VPN_PROVIDER_TIMEOUT", default=30, cast=int)
VPN_HEALTH_CHECK_INTERVAL = config("VPN_HEALTH_CHECK_INTERVAL", default=300, cast=int)


ENCRYPTION_KEY = config("ENCRYPTION_KEY", default="").encode()[:32]
if len(ENCRYPTION_KEY) < 32:
    ENCRYPTION_KEY = ENCRYPTION_KEY.ljust(32, b"0")


PAYMENT_GATEWAY_TIMEOUT = config("PAYMENT_GATEWAY_TIMEOUT", default=30, cast=int)


ANALYTICS_RETENTION_DAYS = config("ANALYTICS_RETENTION_DAYS", default=365, cast=int)


MAX_BRANDS_PER_PLATFORM = config("MAX_BRANDS_PER_PLATFORM", default=100, cast=int)
DEFAULT_BRAND_CURRENCY = config("DEFAULT_BRAND_CURRENCY", default="USD")


RATE_LIMIT_ENABLED = config("RATE_LIMIT_ENABLED", default=True, cast=bool)
RATE_LIMIT_PER_MINUTE = config("RATE_LIMIT_PER_MINUTE", default=60, cast=int)


ENABLE_REFERRAL_SYSTEM = config("ENABLE_REFERRAL_SYSTEM", default=True, cast=bool)
ENABLE_LOYALTY_PROGRAM = config("ENABLE_LOYALTY_PROGRAM", default=True, cast=bool)
ENABLE_BROADCAST_SYSTEM = config("ENABLE_BROADCAST_SYSTEM", default=True, cast=bool)
ENABLE_SUPPORT_SYSTEM = config("ENABLE_SUPPORT_SYSTEM", default=True, cast=bool)

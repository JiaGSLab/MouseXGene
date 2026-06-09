import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def get_env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean from the environment. Unset uses default; empty string uses default."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


# 🔐 基础配置
SECRET_KEY = get_env("DJANGO_SECRET_KEY", "change-me-in-production")

DEBUG = get_env("DJANGO_DEBUG", "1").lower() in {"1", "true", "yes", "on"}

# Bumped on each deploy; used for static cache-busting and support checks.
APP_RELEASE = get_env("APP_RELEASE", "20260529e")

ALLOWED_HOSTS = [
    host.strip()
    for host in get_env("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in get_env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]


# 🧩 应用
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "users",
    "colony",
    "breeding",
    "genotypes",
]


# ⚙️ 中间件
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.CurrentActorMiddleware",
    "core.middleware.NoCacheHtmlForAuthenticatedMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# 🌐 路由
ROOT_URLCONF = "config.urls"


# 🎨 模板
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
                "users.context_processors.role_permissions",
                "core.context_processors.app_release",
            ],
        },
    },
]


# 🚀 WSGI / ASGI
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# 🗄️ 数据库（PostgreSQL）
_db_host = os.getenv("DB_HOST", "").strip()
_db_port = os.getenv("DB_PORT", "").strip()
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": get_env("POSTGRES_DB", "mousexgene"),
        "USER": get_env("POSTGRES_USER", "mousexgene"),
        "PASSWORD": get_env("POSTGRES_PASSWORD", "mousexgene_dev_password"),
        "HOST": _db_host if _db_host else get_env("POSTGRES_HOST", "db"),
        "PORT": _db_port if _db_port else get_env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": get_env_int("DB_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": get_env_bool("DB_CONN_HEALTH_CHECKS", True),
        "OPTIONS": {
            "options": get_env("POSTGRES_OPTIONS", "-c jit=off"),
        },
    }
}

# `manage.py test` without a live Postgres instance (local / CI).
if len(sys.argv) > 1 and sys.argv[1] == "test":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }


# 🔐 密码校验
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# 🌍 国际化
LANGUAGE_CODE = "en-us"
TIME_ZONE = get_env("DJANGO_TIME_ZONE", "UTC")

USE_I18N = True
USE_TZ = True


# 📦 静态文件（关键：修复 admin 样式）
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

# Strain line PDF uploads (max 10 MB each in app validation; allow headroom per request)
FILE_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

WHITENOISE_USE_FINDERS = DEBUG

if not DEBUG:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    if get_env_bool("DJANGO_SECURE_PROXY_SSL_HEADER", default=False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = get_env_bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
    SESSION_COOKIE_SECURE = get_env_bool("DJANGO_SESSION_COOKIE_SECURE", default=False)
    CSRF_COOKIE_SECURE = get_env_bool("DJANGO_CSRF_COOKIE_SECURE", default=False)
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"


# 🧱 默认主键类型
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 🔑 App authentication (not Django admin)
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

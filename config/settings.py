import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


# 🔐 基础配置
SECRET_KEY = get_env("DJANGO_SECRET_KEY", "change-me-in-production")

DEBUG = get_env("DJANGO_DEBUG", "1").lower() in {"1", "true", "yes", "on"}

ALLOWED_HOSTS = [
    host.strip()
    for host in get_env("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
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
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
            ],
        },
    },
]


# 🚀 WSGI / ASGI
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# 🗄️ 数据库（PostgreSQL）
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": get_env("POSTGRES_DB", "mousexgene"),
        "USER": get_env("POSTGRES_USER", "mousexgene"),
        "PASSWORD": get_env("POSTGRES_PASSWORD", "mousexgene_dev_password"),
        "HOST": get_env("POSTGRES_HOST", "db"),
        "PORT": get_env("POSTGRES_PORT", "5432"),
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


# 🧱 默认主键类型
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

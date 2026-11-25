"""Django project settings, including database, caching, storage, API, and security configuration."""

from pathlib import Path
import environ
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = 'django-insecure-x=f))b*r&c!-@@-agco23d^otgp3l*(^)8)c59%-0qnk(4t!%*'
DEBUG = True
ALLOWED_HOSTS = []
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

API_CACHE_ENABLED = env.bool("API_CACHE_ENABLED", default=True)
API_CACHE_TTL = env.int("API_CACHE_TTL", default=300)
API_CACHE_INVALIDATE_ON_COMMIT = env.bool(
    "API_CACHE_INVALIDATE_ON_COMMIT", default=True
)

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STATIC_URL = 'static/'

UPLOAD_POOL_SIZE = 4     
UPLOAD_QUEUE_SIZE = 32
UPLOAD_QUEUE_WAIT = 30

EXPORT_ROOT = BASE_DIR / "exports"          
EXPORT_VERSION = "beta"
EXPORT_SUBDOMAIN = "123presto"
EXPORT_CDN_BASE_URL = "https://cdn.123presto.com"
EXPORT_JSON_INDENT = 4

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'drf_spectacular',
    'sites',
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'sites.ratelimit.middleware.RateLimitMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
    },

    "sqlite": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    },
}

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://127.0.0.1:6379/1"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_CONNECT_TIMEOUT": env("REDIS_CONNECT_TIMEOUT", default=2),
            "SOCKET_TIMEOUT": env("REDIS_SOCKET_TIMEOUT", default=2),
        },
        "KEY_PREFIX": env("REDIS_KEY_PREFIX", default="presto-cache"),
        "TIMEOUT": env("REDIS_CACHE_TIMEOUT", default=300),
    }
}

RATE_LIMIT = {
    "ENABLED": env.bool("RATE_LIMIT_ENABLED", default=True),
    "REDIS_URL": env("RATE_LIMIT_REDIS_URL", default="redis://127.0.0.1:6379/2"),
    "SOCKET_TIMEOUT": env.float("RATE_LIMIT_SOCKET_TIMEOUT", default=0.08),
    "EXEMPT_PATHS": ["/health/", "/admin/", "/api/docs/", "/api/schema/", "/api/redoc/"],
    "MAX_UPLOAD_BYTES": 100 * 1024 * 1024,
    "POLICIES": {
        "read":   {"capacity": 120, "rate": 2},     
        "write":  {"capacity": 30,  "rate": 0.5},
        "upload": {"capacity": 250, "rate": 2},
    },
}

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": MEDIA_ROOT,
            "base_url": MEDIA_URL,
        },
    },

    "s3": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": env("AWS_ACCESS_KEY_ID"),
            "secret_key": env("AWS_SECRET_ACCESS_KEY"),
            "bucket_name": env("AWS_STORAGE_BUCKET_NAME"),
            "region_name": env("AWS_S3_REGION_NAME"),
            "file_overwrite": False,
        },
    },

    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "sites.exceptions.custom_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_SCHEMA_CLASS": "sites.utils.docs.PrestoAutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Presto API",
    "DESCRIPTION": "API for authentication, themes, sites, pages, media, preview and publishing.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,   
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
]

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}

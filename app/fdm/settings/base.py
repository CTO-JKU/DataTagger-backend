import ast
import datetime
import os
import sys

import environ
from dateutil.relativedelta import relativedelta
from django.utils.translation import gettext_lazy as _

########################################################################################################################
# Django Settings
# Please note that those settings can and should be overwritten by other files in this directory, e.g.
# - development.py
# - production.py
# - test.py
########################################################################################################################

# Load .env file

project_root = environ.Path(__file__) - 4  # four folders back

# load env file name
env_file_name = os.environ.get("ENV_FILE", ".env.development")

# load django env from django-environ
env = environ.Env(
    # set casting, default value
    DEBUG=(bool, False),
)
environ.Env.read_env(env_file=project_root(env_file_name))

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG", default=False)  # False if not in os.environ

# https://docs.djangoproject.com/en/5.0/ref/settings/#admins
# Read the ADMINS value from the environment
admins_str = env("ADMINS", default="[]")
try:
    # Use ast.literal_eval to safely evaluate the string as a Python literal
    admins_list = ast.literal_eval(admins_str)
    # Convert the list of dictionaries to a list of tuples
    ADMINS = [(admin["name"], admin["email"]) for admin in admins_list]
except (ValueError, SyntaxError):
    # If parsing fails, set ADMINS to an empty list
    ADMINS = []

# Allowed Hosts
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", [])

# Django Secret Key
SECRET_KEY = env("SECRET_KEY")  # Raises ImproperlyConfigured exception if SECRET_KEY not in os.environ

# Database
# See https://django-environ.readthedocs.io/en/latest/#supported-types (e.g., postgres://)
DATABASES = {
    "default": env.db(),  # Raises ImproperlyConfigured exception if DATABASE_URL not in os.environ
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": env("CHANNEL_LAYER_BACKEND"),
        "CONFIG": {
            "hosts": [(env("CHANNEL_LAYER_URL"))],
        },
    },
}

ASGI_APPLICATION = "fdm.asgi.application"

# EMail Settings
# https://docs.djangoproject.com/en/5.2/ref/settings/#email-backend
EMAIL_CONFIG = env.email_url("EMAIL_URL", default="smtp://user@:password@localhost:25")
vars().update(EMAIL_CONFIG)

EMAIL_SENDER = env("EMAIL_SENDER", default="noreply@example.com")

# Celery and RabbitMQ Config
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_MAX_FILE_PARSER_TASKS_PER_INTERVAL = env.int("CELERY_MAX_FILE_PARSER_TASKS_PER_INTERVAL", default=250)
UPLOADED_FILE_PROCESSING_LEASE_SECONDS = env.int("UPLOADED_FILE_PROCESSING_LEASE_SECONDS", default=3600)
UPLOADED_FILE_PROCESSING_LEGACY_GRACE_SECONDS = env.int(
    "UPLOADED_FILE_PROCESSING_LEGACY_GRACE_SECONDS",
    default=300,
)
METADATA_TEMPLATE_PROCESSING_LEASE_SECONDS = env.int(
    "METADATA_TEMPLATE_PROCESSING_LEASE_SECONDS",
    default=3600,
)
METADATA_TEMPLATE_PROCESSING_RECONCILIATION_BATCH_SIZE = env.int(
    "METADATA_TEMPLATE_PROCESSING_RECONCILIATION_BATCH_SIZE",
    default=250,
)
METADATA_TEMPLATE_PROCESSING_LEGACY_GRACE_SECONDS = env.int(
    "METADATA_TEMPLATE_PROCESSING_LEGACY_GRACE_SECONDS",
    default=300,
)
METADATA_TEMPLATE_PROCESSING_RETRY_BACKOFF_SECONDS = env.int(
    "METADATA_TEMPLATE_PROCESSING_RETRY_BACKOFF_SECONDS",
    default=5,
)
METADATA_TEMPLATE_PROCESSING_AVAILABILITY_MAX_RETRIES = env.int(
    "METADATA_TEMPLATE_PROCESSING_AVAILABILITY_MAX_RETRIES",
    default=2,
)
CELERY_RECOMPUTE_STORAGE_SIZE_DEBOUNCE_SECONDS = env.int(
    "CELERY_RECOMPUTE_STORAGE_SIZE_DEBOUNCE_SECONDS",
    default=30,
)

# Application definition
INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Django channels, used for websockets
    "channels",
    # Django REST framework
    "corsheaders",
    "django_filters",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_rest_passwordreset",
    # Django UserForeignKey
    "django_userforeignkey",
    # OpenAPI
    "drf_spectacular",
    "drf_spectacular_sidecar",
    # Miscellaneous
    "adminsortable2",
    "waffle",
    # Encrypted model fields
    "django_fernet",
    # App
    "fdm.core",
    "fdm.dashboard",
    "fdm.dbsettings",
    "fdm.faq",
    "fdm.exports",
    "fdm.file_parser",
    "fdm.folders",
    "fdm.metadata",
    "fdm.projects",
    "fdm.rest_framework_tus",
    "fdm.search",
    "fdm.storages",
    "fdm.uploads",
    "fdm.users",
    "fdm.websockets",
    "fdm.shibboleth",
    "fdm.cms",
    "fdm.approval_queue",
]

MIDDLEWARE = [
    # CORS
    "corsheaders.middleware.CorsMiddleware",
    # Django
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Django UserForeignKey
    "django_userforeignkey.middleware.UserForeignKeyMiddleware",
    # Waffle (feature flipper)
    "waffle.middleware.WaffleMiddleware",
    "fdm.rest_framework_tus.middleware.TusMiddleware",
    # Request time logging middleware (must be after debug toolbar middleware, if debug middleware is enabled)
    "fdm.core.middleware.request_time_logging.RequestTimeLoggingMiddleware",
]

ROOT_URLCONF = "fdm.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "wsgi.application"


# Password validation
# https://docs.djangoproject.com/en/5.2/topics/auth/passwords/#password-validation

AUTH_PASSWORD_VALIDATORS = [
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

# Custom user app defaults
# Select the correct user model
AUTH_USER_MODEL = "users.User"

AUTHENTICATION_BACKENDS = [
    "fdm.users.backends.AuthenticateByEmailPasswordBackend",
]

# Global permissions that will be configured on each model
GLOBAL_MODEL_PERMISSIONS = [
    "view",
    "add",
    "change",
    "delete",
]

# Waffle (feature flipper)
# https://waffle.readthedocs.io/en/stable/starting/configuring.html
# Currently only switches are used, so settings for flags and samples would need to be added here
WAFFLE_SWITCH_DEFAULT = True
WAFFLE_CREATE_MISSING_SWITCHES = True

# Internationalization
# https://docs.djangoproject.com/en/1.10/topics/i18n/
LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", _("English")),
]

LOCALE_PATHS = (os.path.join(BASE_DIR, "locale/"),)

TIME_ZONE = "Europe/Vienna"

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images) and other paths
# https://docs.djangoproject.com/en/5.2/howto/static-files/

# Static Root: by default this is in the public directory in a sub-directory called static
STATIC_ROOT = project_root(
    env("STATIC_ROOT", default="./data/public/static"),
)  # os.path.join(os.path.dirname(BASE_DIR), 'public', 'static')
STATIC_URL = env("STATIC_URL", default="/static/")

# Media Root (uploaded files): by default this is in the public directory in a sub-directory called media
MEDIA_ROOT = project_root(
    env("MEDIA_ROOT", default="./data/public/media"),
)  # os.path.join(os.path.dirname(BASE_DIR), 'public', 'media')
MEDIA_URL = env("MEDIA_URL", default="/media/")

# todo: replace default with production URL
FRONTEND_WEB_URL = env("FRONTEND_WEB_URL", default="http://0.0.0.0:3000")


# Where Django should look for fixtures
# https://docs.djangoproject.com/en/5.2/howto/initial-data/
FIXTURE_DIRS = (os.path.join(BASE_DIR, "fixtures/"),)


# Logging
# https://docs.djangoproject.com/en/5.2/topics/logging/

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(levelname)s %(message)s"},
        "standard": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: '%(funcName)s' in '%(pathname)s' line '%(lineno)d': %(message)s",
        },
    },
    "filters": {},
    "handlers": {
        "mail_admins": {
            "level": "ERROR",
            "class": "django.utils.log.AdminEmailHandler",
            "formatter": "verbose",
            "include_html": True,
        },
    },
    "loggers": {
        "": {
            "handlers": [
                "default",
                "mail_admins",
            ],
            "level": "DEBUG",
            "propagate": True,
            "formatter": "verbose",
        },
        "django.request": {
            "handlers": [
                "default",
                "mail_admins",
            ],
            "level": "DEBUG",
            "propagate": False,
            "formatter": "verbose",
        },
        "django.middleware.request_time_logging": {
            "handlers": [
                "default",
            ],
            "level": "DEBUG",
            "propagate": False,
            "formatter": "verbose",
        },
        "rest_framework": {
            "handlers": [
                "default",
            ],
            "level": "DEBUG",
            "propagate": True,
        },
        "fdm.core.query": {
            "handlers": [
                "default",
            ],
            "level": "DEBUG",
            "propagate": False,
            "formatter": "verbose",
        },
    },
}


if not env("LOG_TO_CONSOLE", default=False):
    # log everything into an application.log file with a rotating file handler
    LOGGING["handlers"]["default"] = {
        "level": "DEBUG",
        # logs are stored in projects root directory by default
        "filename": env("LOG_FILE", default=project_root(os.path.join("logs", "application.log"))),
        # rotate logs
        "class": "logging.handlers.RotatingFileHandler",
        "maxBytes": 1024 * 1024 * 5,  # 5 MB
        "backupCount": 50,
        "formatter": "verbose",
        "filters": [],
    }
else:
    # log everything to console (e.g., for cloud native deployments)
    LOGGING["handlers"]["default"] = {
        "class": "logging.StreamHandler",
    }


# REST configuration
# https://www.django-rest-framework.org/api-guide/settings/

REST_FRAMEWORK = {
    "PAGE_SIZE": sys.maxsize,
    "MAX_PAGE_SIZE": sys.maxsize,
    "EXCEPTION_HANDLER": "fdm.core.rest.exceptions.error_handler",
    "COERCE_DECIMAL_TO_STRING": False,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "fdm.throttles.UltraHighRateThrottle",
        "fdm.throttles.HighRateThrottle",
        "fdm.throttles.StandardRateThrottle",
        "fdm.throttles.LowRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "ultrahigh": "480/min",
        "high": "240/min",
        "standard": "120/min",
        "low": "60/min",
    },
}


SPECTACULAR_SETTINGS = {
    "TITLE": "TUM DataTagger",
    "DESCRIPTION": "Overview of the available API endpoints for TUM DataTagger",
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.1.0",
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "COMPONENT_SPLIT_REQUEST": True,
}

# JWT Auth
# https://django-rest-framework-simplejwt.readthedocs.io/

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": datetime.timedelta(days=7),
    "REFRESH_TOKEN_LIFETIME": datetime.timedelta(days=365),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": False,
    "AUTH_COOKIE": "token",
    "AUTH_COOKIE_SECURE": env.bool("JWT_AUTH_COOKIE_SECURE", default=False),
    "AUTH_COOKIE_HTTP_ONLY": True,
    "AUTH_COOKIE_SAMESITE": "Lax",
}


# CORS Setup
# https://github.com/ottoyiu/django-cors-headers

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=True)
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", [], default=[])
CORS_ALLOW_METHODS = (
    "GET",
    "POST",
    "HEAD",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
)
CORS_ALLOW_HEADERS = (
    "Accept",
    "Accept-Encoding",
    "Authorization",
    "Content-Type",
    "Cookie",
    "DNT",
    "Origin",
    "User-Agent",
    "X-CSRF-TOKEN",
    "X-Requested-With",
    "Tus-Resumable",
    "Location",
    "Upload-Concat",
    "Upload-Defer-Length",
    "Upload-Length",
    "Upload-Metadata",
    "Upload-Offset",
    "Original-File-Path",
    # Shibboleth Headers
    "HTTP_SHIB_CN",
    "HTTP_SHIB_EDU_PERSON_AFFILIATION",
    "HTTP_SHIB_GIVEN_NAME",
    "HTTP_SHIB_IM_AKADEMISCHER_GRAD",
    "HTTP_SHIB_IM_ORG_ZUG_GAST",
    "HTTP_SHIB_IM_ORG_ZUG_MITARBEITER",
    "HTTP_SHIB_IM_ORG_ZUG_STUDENT",
    "HTTP_SHIB_IM_TITEL_ANREDE",
    "HTTP_SHIB_IM_TITEL_POST",
    "HTTP_SHIB_IM_TITEL_PRE",
    "HTTP_SHIB_MAIL",
    "HTTP_SHIB_SN",
    "HTTP_SHIB_PERSISTENT_ID",
    "HTTP_SHIB_REMOTE_USER",
    "HTTP_SHIB_AUTH_TYPE",
    "HTTP_SHIB_APPLICATION_ID",
    "HTTP_SHIB_AUTHENTICATION_INSTANT",
    "HTTP_SHIB_AUTHENTICATION_METHOD",
    "HTTP_SHIB_AUTHNCONTEXT_CLASS",
    "HTTP_SHIB_IDENTITY_PROVIDER",
    "HTTP_SHIB_SESSION_ID",
    "HTTP_SHIB_SESSION_INDEX",
    "HTTP_SHIB_PERSISTENT_ID",
)
CORS_EXPOSE_HEADERS = (
    "Filename",
    "Content-Type",
    "Content-Disposition",
    "Location",
    "Upload-Concat",
    "Upload-Defer-Length",
    "Upload-Length",
    "Upload-Offset",
    "File-Size",
    "File-Last-Modified",
    "File-Accept-Ranges",
    "File-Mime-Type",
    "File-Checksum_SHA256",
    "File-Checksum_SHA512",
    "File-Checksum_MD5",
    "ETag",
)

FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB

REST_FRAMEWORK_TUS = {
    "UPLOAD_DIR": os.path.join(MEDIA_ROOT, "tus", "tmp", "uploads"),  # must always be in MEDIA_ROOT
    "TUS_UPLOAD_DESTINATION": "uploads",
    "MAX_FILE_SIZE": 1024 * 1024 * 1024 * 4000,  # 4000 GB
    "UPLOAD_EXPIRES": relativedelta(days=1),
}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

DJANGO_REST_PASSWORDRESET_USER_DETAILS_ON_VALIDATION = True

MAX_LOCK_TIME = 20  # minutes

DRAFT_FILES_MAX_LIFETIME = 30  # days

SHIBBOLETH_AUTH_CODE_MAX_LIFESPAN = env.int("SHIBBOLETH_AUTH_CODE_MAX_LIFESPAN", default=300)


# URL schemes that are allowed within links
ALLOWED_URL_SCHEMES = [
    "file",
    "ftp",
    "ftps",
    "http",
    "https",
    "irc",
    "mailto",
    "sftp",
    "ssh",
    "tel",
    "telnet",
    "tftp",
    "vnc",
    "xmpp",
]

# https://gist.github.com/mrmrs/7650266
ALLOWED_HTML_TAGS = [
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "cite",
    "code",
    "command",
    "dd",
    "del",
    "dl",
    "dt",
    "em",
    "fieldset",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "iframe",
    "img",
    "input",
    "ins",
    "kbd",
    "label",
    "legend",
    "li",
    "ol",
    "optgroup",
    "option",
    "p",
    "pre",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
]

# https://github.com/decal/werdlists/blob/master/html-words/html-attributes-list.txt
ALLOWED_HTML_ATTRIBUTES = [
    "alt",
    "class",
    "color",
    "colspan",
    "datetime",
    "height",
    "href",
    "id",
    "name",
    "reversed",
    "rowspan",
    "scope",
    "src",
    "style",
    "title",
    "type",
    "width",
]

# This is used for local development, but will be overwritten for production environments.
PRIVATE_DSS_MOUNT_PATH = MEDIA_ROOT

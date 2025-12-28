"""
Django settings for core project.
Enhanced for SpaceRes Analysis System.
"""
import os
from pathlib import Path
from datetime import timedelta
from decouple import config, Csv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# ==============================================================================
# 1. 核心安全配置 (Core Security)
# ==============================================================================

# 读取 .env 文件中的 SECRET_KEY
SECRET_KEY = config('SECRET_KEY')

# 读取 .env, 默认为 False
DEBUG = config('DEBUG', default=False, cast=bool)

# 读取 .env, 支持 CSV 格式
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='127.0.0.1,localhost', cast=Csv())

# ==============================================================================
# 2. 应用定义 (Applications)
# ==============================================================================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # --- 第三方库 ---
    'rest_framework',              # DRF 核心
    'rest_framework_simplejwt',    # JWT 认证
    'rest_framework_simplejwt.token_blacklist', # JWT 黑名单
    'corsheaders',                 # 跨域处理
    'django_filters',              # 高级过滤
    'django_celery_beat',          # 定时任务调度

    'drf_spectacular',
    # --- 自定义业务模块 ---
    'users.apps.UsersConfig',
    'spaces.apps.SpacesConfig',
    'bookings.apps.BookingsConfig',
    'notifications.apps.NotificationsConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware', # 必须要在 CommonMiddleware 之前
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'core', 'templates')], # <-- 添加这一行
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# ==============================================================================
# 3. 数据库配置 (Database) - MySQL
# ==============================================================================

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',

        # 强制从 .env 读取，如果这里报错说明 .env 没配好
        'NAME': config('DATABASE_NAME'),
        'USER': config('DATABASE_USER'),
        'PASSWORD': config('DATABASE_PASSWORD'),
        'HOST': config('DATABASE_HOST'),
        'PORT': config('DATABASE_PORT'),

        'CONN_MAX_AGE': config('DB_CONN_MAX_AGE', default=60, cast=int),

        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

# ==============================================================================
# 4. 认证与用户模型 (Authentication)
# ==============================================================================

AUTH_USER_MODEL = 'users.CustomUser'

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# ==============================================================================
# 5. 国际化与时区 (I18n & L10n)
# ==============================================================================

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ==============================================================================
# 6. 静态文件与媒体文件 (Static & Media)
# ==============================================================================

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [ BASE_DIR / 'static' ]

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ==============================================================================
# 7. Django REST Framework 配置 (DRF)
# ==============================================================================

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 10,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DATETIME_FORMAT': '%Y-%m-%d %H:%M:%S',
    'DATE_FORMAT': '%Y-%m-%d',
    'EXCEPTION_HANDLER': 'core.utils.error_handler.custom_exception_handler',

    # 添加 OpenAPI Schema 渲染器 (用于 drf-spectacular)
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# ==============================================================================
# 8. JWT 配置 (Simple JWT)
# ==============================================================================

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=config('JWT_ACCESS_TOKEN_LIFETIME', default=60, cast=int)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=config('JWT_REFRESH_TOKEN_LIFETIME_DAYS', default=1, cast=int)),

    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,

    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,

    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',

    # [重要修正] 默认改为 'id'，除非你在 CustomUser 模型里显式定义了 user_id 字段
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# ==============================================================================
# 9. 跨域资源共享 (CORS)
# ==============================================================================

CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL_ORIGINS', default=True, cast=bool)

if not CORS_ALLOW_ALL_ORIGINS:
    CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='http://localhost:3000', cast=Csv())

CORS_ALLOW_CREDENTIALS = True

# ==============================================================================
# 10. Celery & Redis 配置 (缺失部分已补全)
# ==============================================================================

# 从 .env 读取配置
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://127.0.0.1:6379/1')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://127.0.0.1:6379/2')

CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# ==============================================================================
# 11. 其他
# ==============================================================================

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==============================================================================
# 12. DRF Spectacular (OpenAPI/Swagger) 配置
# ==============================================================================

SPECTACULAR_SETTINGS = {
    'TITLE': '公共空间预订管理系统 API',
    'DESCRIPTION': '毕业设计项目：高性能、可扩展的公共空间预订管理系统后端 API 文档。',
    'VERSION': '1.0.0',
    # 其他设置，如认证方案
    'SERVE_INCLUDE_SCHEMA': False, # 不将schema文件本身添加到UI中
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
        'displayOperationId': False,
        'displayRequestDuration': True,
    },
    'CONTACT': {
        'name': '你的名字/团队名称',
        'email': '你的邮箱',
    },
    'LICENSE': {
        'name': '自定义许可证', # 或 'MIT', 'Apache-2.0'
    },
}

# ==============================================================================
# 13. 日志配置 (Logging)
# ==============================================================================

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO' if DEBUG else 'WARNING', # 调试模式显示INFO及以上，生产模式只显示WARNING及以上
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'logs/debug.log', # 日志文件路径
            'formatter': 'verbose',
        },
        'error_file': {
            'level': 'ERROR',
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'logs/error.log', # 错误日志文件路径
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True,
        },
        'core': { # 为你的 core 应用定义一个 logger
            'handlers': ['console', 'file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
        # 为其他应用如 users, spaces 等也定义类似的 logger
        # 'users': {
        #     'handlers': ['console', 'file', 'error_file'],
        #     'level': 'INFO',
        #     'propagate': False,
        # },
        # ...
        '': { # 根 logger，捕获所有未指定 logger 的消息
            'handlers': ['console', 'file', 'error_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# 确保日志目录存在
LOG_DIR = BASE_DIR / 'logs'
if not LOG_DIR.exists():
    LOG_DIR.mkdir()
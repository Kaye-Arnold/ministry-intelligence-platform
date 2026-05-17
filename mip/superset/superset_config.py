"""
Ministry Intelligence Platform (MIP) – Apache Superset Configuration
Version 1.0 | Apache 2.0 License
"""
import os
from datetime import timedelta
from celery.schedules import crontab
from flask_appbuilder.security.manager import AUTH_OAUTH

# ── Core Security ──────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY", "CHANGE_ME_MUST_BE_SET")
WTF_CSRF_ENABLED = True
WTF_CSRF_EXEMPT_LIST = ["superset.views.core.log"]
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

# ── Database ──────────────────────────────────────────────────────
SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://"
    f"{os.environ.get('POSTGRES_USER', 'ministry')}:"
    f"{os.environ.get('POSTGRES_PASSWORD', '')}@"
    f"{os.environ.get('DATABASE_HOST', 'db')}:5432/"
    f"{os.environ.get('POSTGRES_DB', 'ministry_db')}"
)

# Connection pool settings for Oracle A1 (24 GB RAM, ARM)
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_size": 10,
    "pool_timeout": 30,
    "pool_recycle": 3600,
    "max_overflow": 20,
}

# ── Redis / Celery ────────────────────────────────────────────────
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_URL": f"redis://{REDIS_HOST}:{REDIS_PORT}/1",
}

DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 600,
    "CACHE_KEY_PREFIX": "superset_data_",
    "CACHE_REDIS_URL": f"redis://{REDIS_HOST}:{REDIS_PORT}/2",
}

FILTER_STATE_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 3600,
    "CACHE_KEY_PREFIX": "superset_filter_",
    "CACHE_REDIS_URL": f"redis://{REDIS_HOST}:{REDIS_PORT}/3",
}

# Celery configuration for scheduled reports
class CeleryConfig:
    broker_url = f"redis://{REDIS_HOST}:{REDIS_PORT}/4"
    result_backend = f"redis://{REDIS_HOST}:{REDIS_PORT}/5"
    include = [
        "superset.tasks.scheduler",
        "superset.tasks.thumbnails",
        "superset.tasks.cache",
    ]
    task_annotations = {"tasks.add": {"rate_limit": "10/s"}}
    beat_schedule = {
        "weekly_report_monday_6am_eat": {
            "task": "superset.tasks.scheduler.schedule_email_reports",
            "schedule": crontab(hour=3, minute=0, day_of_week=1),  # Monday 03:00 UTC = 06:00 EAT
        },
    }
    worker_prefetch_multiplier = 10
    task_acks_late = True
    worker_max_tasks_per_child = 128


CELERY_CONFIG = CeleryConfig

# ── Thumbnail / PDF Export via headless Chrome ────────────────────
SCREENSHOT_LOCATE_WAIT = 100
SCREENSHOT_LOAD_WAIT = 600
WEBDRIVER_TYPE = "chrome"
WEBDRIVER_OPTION_ARGS = [
    "--force-device-scale-factor=2.0",
    "--high-dpi-support=2.0",
    "--headless",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-extensions",
]
WEBDRIVER_BASEURL = "http://superset:8088/"
WEBDRIVER_BASEURL_USER_FRIENDLY = f"https://{os.environ.get('DOMAIN', 'localhost')}/superset/"

# Point to remote chrome
SELENIUM_DRIVER_SERVICE = None
CHROME_URL = os.environ.get("CHROME_URL", "http://chrome:3000")

# ── Feature Flags ─────────────────────────────────────────────────
FEATURE_FLAGS = {
    "ALERT_REPORTS": True,
    "DASHBOARD_RBAC": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
    "EMBEDDED_SUPERSET": True,
    "DRILL_TO_DETAIL": True,
    "DRILL_BY": True,
    "ROW_LEVEL_SECURITY": True,
    "THUMBNAILS": True,
    "THUMBNAILS_SQLA_LISTENERS": True,
    "LISTVIEWS_DEFAULT_CARD_VIEW": True,
    "GLOBAL_ASYNC_QUERIES": True,
    "SUPERSET_EXPORT_BUTTON": True,
}

# ── Authentication: Google OAuth 2.0 ─────────────────────────────
AUTH_TYPE = AUTH_OAUTH
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = "Public"  # default; RLS and roles assigned manually

OAUTH_PROVIDERS = [
    {
        "name": "google",
        "icon": "fa-google",
        "token_key": "access_token",
        "remote_app": {
            "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "api_base_url": "https://www.googleapis.com/oauth2/v2/",
            "client_kwargs": {"scope": "email profile"},
            "access_token_url": "https://accounts.google.com/o/oauth2/token",
            "authorize_url": "https://accounts.google.com/o/oauth2/auth",
            "request_token_url": None,
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        },
    }
]

# Custom Security Manager for RLS
from superset.security import SupersetSecurityManager

class MIPSecurityManager(SupersetSecurityManager):
    """
    Custom security manager that assigns roles based on user_teams table.
    Team leaders logging in via Google OAuth get Alpha role with RLS applied.
    """

    def oauth_user_info(self, provider, response=None):
        info = super().oauth_user_info(provider, response)
        return info

    def auth_user_oauth(self, userinfo):
        user = super().auth_user_oauth(userinfo)
        if user:
            email = userinfo.get("email", "")
            # Assign appropriate role based on user_teams table
            try:
                from superset import db
                from sqlalchemy import text
                result = db.session.execute(
                    text("SELECT team_id, role FROM user_teams WHERE user_email = :email"),
                    {"email": email}
                ).fetchone()
                if result:
                    team_role = result[1]
                    if team_role == "admin":
                        superset_role = self.find_role("Admin")
                    elif team_role == "finance":
                        superset_role = self.find_role("Alpha")
                    else:
                        superset_role = self.find_role("Alpha")
                    if superset_role and superset_role not in user.roles:
                        user.roles.append(superset_role)
                        db.session.commit()
            except Exception as ex:
                import logging
                logging.getLogger(__name__).error(
                    "Failed to set role for user %s: %s", email, ex
                )
        return user


CUSTOM_SECURITY_MANAGER = MIPSecurityManager

# ── Row-Level Security ────────────────────────────────────────────
# RLS rules are configured in Superset UI after dashboard import.
# The following comment documents the intended filter clauses:
#
# For Attendance dataset:
#   team_owner IN (
#       SELECT team_id FROM user_teams
#       WHERE user_email = '{{ current_username() }}'
#   )
#
# For Outreaches dataset:
#   activity_code IN (
#       SELECT activity_code FROM activities
#       WHERE team_owner IN (
#           SELECT team_id FROM user_teams
#           WHERE user_email = '{{ current_username() }}'
#       )
#   )
#
# For Follow_Ups dataset:
#   assigned_to IN (
#       SELECT member_id FROM members
#       WHERE email = '{{ current_username() }}'
#   )

# ── Email (for Celery reports, optional SMTP) ─────────────────────
SMTP_HOST = "smtp.gmail.com"
SMTP_STARTTLS = True
SMTP_SSL = False
SMTP_PORT = 587
SMTP_MAIL_FROM = os.environ.get("SUPERSET_ADMIN_EMAIL", "admin@ministry.org")
# SMTP_PASSWORD set in environment as SMTP_PASSWORD if email alerts needed

# ── UI Settings ───────────────────────────────────────────────────
ROW_LIMIT = 5000
SQL_MAX_ROW = 100000
SUPERSET_WEBSERVER_PORT = 8088
ENABLE_PROXY_FIX = True
PROXY_FIX_CONFIG = {"x_for": 1, "x_proto": 1, "x_host": 1, "x_port": 1, "x_prefix": 1}

APP_NAME = "Ministry Intelligence Platform"
APP_ICON = "/static/assets/images/superset-logo-horiz.png"
FAVICONS = [{"href": "/static/assets/images/favicon.png"}]

# ── Superset Analytics DB Connection Name ─────────────────────────
# This is the database name that will be shown in Superset UI.
SQLALCHEMY_EXAMPLES_URI = SQLALCHEMY_DATABASE_URI

# ── Logging ──────────────────────────────────────────────────────
ENABLE_TIME_ROTATE = True
TIME_ROTATE_LOG_LEVEL = "INFO"
FILENAME = "/app/superset_home/superset.log"
ROLLOVER = "midnight"
INTERVAL = 1
BACKUP_COUNT = 30

# ── HTML Sanitisation (allow iframes in portal) ───────────────────
TALISMAN_ENABLED = False  # Managed by Nginx; disable Talisman to avoid CSP conflicts

# ── Async Query Config ────────────────────────────────────────────
GLOBAL_ASYNC_QUERIES_TRANSPORT = "polling"
GLOBAL_ASYNC_QUERIES_POLLING_DELAY = 500

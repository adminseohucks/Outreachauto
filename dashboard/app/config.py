"""LinkedPilot v2 — Configuration loader."""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _str(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


# --- Dashboard ---
DASHBOARD_HOST = _str("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = _int("DASHBOARD_PORT", 8080)
SECRET_KEY = _str("SECRET_KEY", "change-me")

# --- Database paths ---
CRM_DB_PATH = _str("CRM_DB_PATH", str(BASE_DIR / ".." / "openoutreach" / "assets" / "data" / "crm.db"))
LINKEDPILOT_DB_PATH = _str("LINKEDPILOT_DB_PATH", str(BASE_DIR / "data" / "linkedpilot.db"))

# --- VPS AI ---
VPS_AI_URL = _str("VPS_AI_URL", "")
VPS_API_KEY = _str("VPS_API_KEY", "")
VPS_HEALTH_URL = _str("VPS_HEALTH_URL", "")
VPS_SSL_VERIFY = _bool("VPS_SSL_VERIFY", False)

# --- Work hours ---
WORK_HOUR_START = _int("WORK_HOUR_START", 9)
WORK_HOUR_END = _int("WORK_HOUR_END", 18)
WORK_DAYS = [int(d) for d in _str("WORK_DAYS", "0,1,2,3,4").split(",")]

# --- Like limits ---
LIKE_DAILY_LIMIT = _int("LIKE_DAILY_LIMIT", 100)
LIKE_WEEKLY_LIMIT = _int("LIKE_WEEKLY_LIMIT", 300)
LIKE_MIN_DELAY = _int("LIKE_MIN_DELAY", 240)
LIKE_MAX_DELAY = _int("LIKE_MAX_DELAY", 840)

# --- Comment limits ---
COMMENT_DAILY_LIMIT = _int("COMMENT_DAILY_LIMIT", 50)
COMMENT_WEEKLY_LIMIT = _int("COMMENT_WEEKLY_LIMIT", 200)
COMMENT_MIN_DELAY = _int("COMMENT_MIN_DELAY", 480)
COMMENT_MAX_DELAY = _int("COMMENT_MAX_DELAY", 1320)

# --- Ramp-up ---
RAMP_UP_WEEKS = _int("RAMP_UP_WEEKS", 2)
RAMP_UP_PERCENTAGE = _int("RAMP_UP_PERCENTAGE", 30)

# --- Senders ---
MAX_SENDERS = _int("MAX_SENDERS", 4)

# --- Global cooldown (days) ---
CONTACT_COOLDOWN_DAYS = 3

# --- Templates / Static ---
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
STATIC_DIR = BASE_DIR / "app" / "static"
EXPORTS_DIR = BASE_DIR / "data" / "exports"

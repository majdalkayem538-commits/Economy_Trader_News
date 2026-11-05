"""Settings from .env — every secret lives there, never in the code."""
import os
from dotenv import load_dotenv

load_dotenv()


def _req(name):
    v = os.getenv(name, "").strip()
    if not v:
        raise SystemExit(f"ناقص {name} بملف .env")
    return v


def _list(name):
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


class Config:
    # Telegram
    API_ID = int(os.getenv("TG_API_ID", "0") or 0)
    API_HASH = os.getenv("TG_API_HASH", "")
    PHONE = os.getenv("TG_PHONE", "")
    SESSION = os.getenv("TG_SESSION", "").strip()        # StringSession من login.py (للاستضافة)
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    SOURCE = os.getenv("SOURCE_CHANNEL", "ForexBreakingNews").lstrip("@")
    SOURCE_MODE = os.getenv("SOURCE_MODE", "web").lower()          # web | userbot
    POLL_SECONDS = int(os.getenv("POLL_SECONDS", "20"))
    TARGET = os.getenv("TARGET_CHANNEL", "")           # @yourchannel or -100…
    ADMINS = [int(x) for x in _list("ADMIN_IDS")]       # your numeric Telegram id(s)

    # AI
    PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()   # gemini | deepseek
    GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODELS = _list("GEMINI_MODELS") or ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    DEEPSEEK_KEY = "" if os.getenv("DEEPSEEK_API_KEY", "").strip().lower() in ("", "none", "-", "0", "no") else os.getenv("DEEPSEEK_API_KEY").strip()
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    MIN_INTERVAL = float(os.getenv("AI_MIN_INTERVAL_SEC", "7"))   # free tier ≈ 10 req/min
    # Reviewer: a second AI checks every draft against the source
    REVIEW_MODE = os.getenv("REVIEW_MODE", "all").lower()          # all | high | off
    REVIEW_PROVIDER = os.getenv("REVIEW_PROVIDER", "deepseek").lower()   # deepseek | gemini
    REVIEW_MODEL = os.getenv("REVIEW_MODEL", "")                     # empty = deepseek-chat / gemini-3.5-flash-lite

    # Behaviour
    HASHTAGS = os.getenv("HASHTAGS", "#EconomyTrader #ايكونومي_تريدر")
    SKIP_LOW = os.getenv("SKIP_LOW_IMPACT", "false").lower() == "true"
    SCENARIOS = os.getenv("SHOW_SCENARIOS", "true").lower() != "false"
    CATCHUP_MINUTES = int(os.getenv("CATCHUP_MINUTES", "120"))
    DATA_DIR = os.getenv("DATA_DIR", "data")             # على Render: /var/data (القرص الدائم)
    DB_PATH = os.getenv("DB_PATH", "") or os.path.join(DATA_DIR, "bot.db")
    STYLE_PATH = os.getenv("STYLE_PATH", "style.md")
    TZ = os.getenv("TZ_NAME", "Asia/Damascus")

    @classmethod
    def validate(cls):
        _req("BOT_TOKEN"); _req("TARGET_CHANNEL")
        if not cls.ADMINS:
            print("⚠ ADMIN_IDS فاضي: البوت رح يشتغل بس ما رح يبعت مسودات. ابعتله /start وبيقلك رقمك، حطه وأعد التشغيل.")
        if cls.SOURCE_MODE == "userbot" and not (cls.API_ID and cls.API_HASH and cls.SESSION):
            raise SystemExit("SOURCE_MODE=userbot بدو TG_API_ID و TG_API_HASH و TG_SESSION")
        if cls.SOURCE_MODE not in ("web", "userbot"):
            raise SystemExit("SOURCE_MODE لازم web أو userbot")
        if cls.PROVIDER == "gemini":
            _req("GEMINI_API_KEY")
        elif cls.PROVIDER == "deepseek":
            _req("DEEPSEEK_API_KEY")
        else:
            raise SystemExit("AI_PROVIDER لازم gemini أو deepseek")

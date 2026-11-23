"""Settings, read from environment variables with sensible defaults."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Read KEY=value lines from a .env file. Values already set in the terminal win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------- AI provider
# Both providers are free and speak the same "OpenAI-compatible" API, so the code is identical.
#   gemini: Google's Gemini API free tier. Needs a free key from https://aistudio.google.com/apikey
#           Note: on the free tier Google may use your messages to improve its products.
#   ollama: runs a model on your own computer. Free, private and works offline, but needs a
#           reasonably powerful laptop. Install from https://ollama.com, then `ollama pull <model>`.
PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.5-flash-lite",
        "key_env": "GEMINI_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3-vl:8b",
        "key_env": None,  # Ollama doesn't need a key
    },
}

PROVIDER = os.environ.get("MONEY_AGENT_PROVIDER", "gemini").strip().lower()
_preset = PROVIDERS.get(PROVIDER, {})
BASE_URL = os.environ.get("MONEY_AGENT_BASE_URL") or _preset.get("base_url")
MODEL = os.environ.get("MONEY_AGENT_MODEL") or _preset.get("model")
KEY_ENV = _preset.get("key_env")


def api_key() -> str | None:
    """The key for the chosen provider, or None if it is missing."""
    if os.environ.get("MONEY_AGENT_API_KEY"):
        return os.environ["MONEY_AGENT_API_KEY"]
    if PROVIDER == "ollama":
        return "ollama"  # any non-empty value works
    return os.environ.get(KEY_ENV) if KEY_ENV else None


# ---------------------------------------------------------------- app settings
DB_PATH = Path(os.environ.get("MONEY_AGENT_DB", PROJECT_ROOT / "data" / "money.db"))

# Public demo mode, for the deployed web app: every visitor gets a private copy of the sample
# data that disappears when they close the tab, and a cap on AI requests (your key pays for them).
DEMO_MODE = os.environ.get("MONEY_AGENT_DEMO", "").strip().lower() in ("1", "true", "yes")
DEMO_MAX_AI_REQUESTS = int(os.environ.get("MONEY_AGENT_DEMO_MAX_REQUESTS", "30"))

CURRENCY = "birr"

# A category is flagged once it has used this share of its monthly limit.
ALERT_THRESHOLD = 0.8

# Safety net for the agent loop: the most tool rounds allowed for one user message.
MAX_AGENT_STEPS = 12

CATEGORIES = [
    "Food & Drinks",
    "Groceries",
    "Transport",
    "Rent & Utilities",
    "Airtime & Internet",
    "Health",
    "Shopping",
    "Entertainment",
    "Education",
    "Business",
    "Family & Gifts",
    "Other",
]

# Bills paid once or twice a month. Their month-end estimate is what has been spent so far,
# and they are only flagged when they go over the limit.
FIXED_CATEGORIES = {"Rent & Utilities", "Airtime & Internet", "Education"}

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _proxy_list() -> list[str]:
    raw = os.getenv("PROXIES", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    concurrency: int = int(os.getenv("CONCURRENCY", "3"))
    headless: bool = _bool(os.getenv("HEADLESS"), True)
    proxies: list[str] = field(default_factory=_proxy_list)
    min_delay: float = float(os.getenv("MIN_DELAY", "1.5"))
    max_delay: float = float(os.getenv("MAX_DELAY", "4.0"))
    default_limit: int = int(os.getenv("DEFAULT_LIMIT", "60"))
    db_path: Path = ROOT_DIR / os.getenv("DB_PATH", "data/gmaps.db")
    screenshot_dir: Path = ROOT_DIR / os.getenv("SCREENSHOT_DIR", "logs/screenshots")


settings = Settings()
settings.db_path.parent.mkdir(parents=True, exist_ok=True)
settings.screenshot_dir.mkdir(parents=True, exist_ok=True)

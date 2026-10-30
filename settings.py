from __future__ import annotations

import os
import sys
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else SOURCE_DIR


def _load_dotenv() -> None:
    """Минимальная загрузка .env без дополнительной зависимости."""
    env_file = RUNTIME_DIR / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()
DATA_DIR = RUNTIME_DIR / "data"


def _path_from_env(name: str, default: Path) -> Path:
    value = Path(os.getenv(name, str(default)))
    return value if value.is_absolute() else RUNTIME_DIR / value


MEDIA_DIR = _path_from_env("MEDIA_PATH", DATA_DIR / "media")
DATABASE_PATH = _path_from_env("DATABASE_PATH", DATA_DIR / "queue.db")

APP_NAME = os.getenv("APP_NAME", "Электронная очередь ЦЗН Тульской области")
ORGANIZATION_NAME = os.getenv(
    "ORGANIZATION_NAME", "Центр занятости населения Тульской области"
)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
TICKET_PREFIX = os.getenv("TICKET_PREFIX", "A")
TICKET_WAITING_DIGITS = int(os.getenv("TICKET_WAITING_DIGITS", "3"))
PRINTER_NAME = os.getenv("PRINTER_NAME", "").strip()
AUTO_PRINT = os.getenv("AUTO_PRINT", "false").lower() in {"1", "true", "yes", "on"}
BROWSER_AUTO_PRINT = os.getenv("BROWSER_AUTO_PRINT", "true").lower() in {"1", "true", "yes", "on"}


def ensure_directories() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

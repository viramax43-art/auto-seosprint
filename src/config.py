from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "google/gemini-2.5-flash"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str
    openrouter_model: str
    executor_telegram: str
    executor_email: str
    executor_account_id: str
    executor_registration_date: str
    headless: bool
    browser_slow_mo: int
    data_dir: Path
    seosprint_base_url: str
    seosprint_cookies_file: Path
    browser_profile_dir: Path
    browser_debug: bool
    browser_channel: str

    @classmethod
    def load(cls, *, require_openrouter: bool = True) -> Settings:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if require_openrouter and not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY не задан. Скопируйте .env.example в .env и укажите ключ."
            )

        data_dir = Path(os.getenv("DATA_DIR", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)

        cookies_file = Path(os.getenv("SEOSPRINT_COOKIES_FILE", str(data_dir / "cookies.json")))
        profile_dir = Path(os.getenv("BROWSER_PROFILE_DIR", str(data_dir / "browser_profile")))

        return cls(
            openrouter_api_key=api_key,
            openrouter_model=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL).strip(),
            executor_telegram=os.getenv("EXECUTOR_TELEGRAM", "").strip(),
            executor_email=os.getenv("EXECUTOR_EMAIL", "").strip(),
            executor_account_id=os.getenv("EXECUTOR_ACCOUNT_ID", "").strip(),
            executor_registration_date=os.getenv("EXECUTOR_REGISTRATION_DATE", "").strip(),
            headless=os.getenv("HEADLESS", "false").lower() in {"1", "true", "yes"},
            browser_slow_mo=int(os.getenv("BROWSER_SLOW_MO", "500")),
            data_dir=data_dir,
            seosprint_base_url=os.getenv("SEOSPRINT_BASE_URL", "https://seosprint.net").strip(),
            seosprint_cookies_file=cookies_file,
            browser_profile_dir=profile_dir,
            browser_debug=os.getenv("BROWSER_DEBUG", "true").lower() in {"1", "true", "yes"},
            browser_channel=os.getenv("BROWSER_CHANNEL", "").strip(),
        )

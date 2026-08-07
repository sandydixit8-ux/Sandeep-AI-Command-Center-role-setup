"""Configuration for the Agents suite. Values come from environment / .env file."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR") or (BASE_DIR / "data"))
OUTPUTS_DIR = Path(os.environ.get("AGENT_OUTPUTS_DIR") or (BASE_DIR / "outputs"))
for _d in (DATA_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SUPPORTED_PROVIDERS = ("anthropic", "openai", "mock")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Settings:
    def __init__(self) -> None:
        self.provider: str = (_env("AGENT_LLM_PROVIDER") or "anthropic").lower()
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"AGENT_LLM_PROVIDER must be one of {SUPPORTED_PROVIDERS}, got {self.provider!r}"
            )
        self.anthropic_api_key = _env("AGENT_ANTHROPIC_API_KEY")
        self.anthropic_model = _env("AGENT_ANTHROPIC_MODEL") or "claude-sonnet-4-20250514"
        self.openai_api_key = _env("AGENT_OPENAI_API_KEY")
        self.openai_base_url = _env("AGENT_OPENAI_BASE_URL") or "https://api.openai.com/v1"
        self.openai_model = _env("AGENT_OPENAI_MODEL") or "gpt-4o-mini"
        self.search_brave_api_key = _env("AGENT_SEARCH_BRAVE_API_KEY")
        self.gmail_user = _env("AGENT_GMAIL_USER")
        self.gmail_app_password = _env("AGENT_GMAIL_APP_PASSWORD")
        self.google_service_account_file = _env("AGENT_GOOGLE_SERVICE_ACCOUNT_FILE")
        self.sheet_id = _env("AGENT_SHEET_ID")
        self.sheet_range = _env("AGENT_SHEET_RANGE", "A1:E1000")
        self.timeout = float(_env("AGENT_TIMEOUT", "90") or 90)
        self.max_tool_steps = int(_env("AGENT_MAX_TOOL_STEPS", "10") or 10)

    def has_provider_key(self) -> bool:
        if self.provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.provider == "openai":
            return bool(self.openai_api_key)
        return True  # mock


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

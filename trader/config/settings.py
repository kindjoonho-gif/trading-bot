from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KISEnv = Literal["mock", "real"]

_MOCK_BASE = "https://openapivts.koreainvestment.com:29443"
_REAL_BASE = "https://openapi.koreainvestment.com:9443"
_DEFAULT_PRDT_CD = "01"


def parse_account_no(raw: str) -> tuple[str, str]:
    """Split KIS_*_ACCOUNT_NO into (CANO, ACNT_PRDT_CD).

    Accepts "12345678-01" or bare "12345678" (defaults product code to 01).
    """
    if "-" in raw:
        cano, prdt = raw.split("-", 1)
    else:
        cano, prdt = raw, _DEFAULT_PRDT_CD
    if not cano.isdigit() or len(cano) != 8:
        raise ValueError(f"CANO must be 8 digits, got {cano!r}")
    if not prdt.isdigit() or len(prdt) != 2:
        raise ValueError(f"ACNT_PRDT_CD must be 2 digits, got {prdt!r}")
    return cano, prdt


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    KIS_ENV: KISEnv = "mock"

    KIS_MOCK_APP_KEY: str = ""
    KIS_MOCK_APP_SECRET: str = ""
    KIS_MOCK_ACCOUNT_NO: str = ""
    KIS_MOCK_BASE_URL: str = _MOCK_BASE

    KIS_REAL_APP_KEY: str = ""
    KIS_REAL_APP_SECRET: str = ""
    KIS_REAL_ACCOUNT_NO: str = ""
    KIS_REAL_BASE_URL: str = _REAL_BASE

    HISTORY_DB_DIR: Path = Path("data")

    @field_validator("KIS_MOCK_ACCOUNT_NO", "KIS_REAL_ACCOUNT_NO")
    @classmethod
    def _validate_account_shape(cls, v: str) -> str:
        if v:
            parse_account_no(v)
        return v

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def app_key(self) -> str:
        return self.KIS_MOCK_APP_KEY if self.KIS_ENV == "mock" else self.KIS_REAL_APP_KEY

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def app_secret(self) -> str:
        return self.KIS_MOCK_APP_SECRET if self.KIS_ENV == "mock" else self.KIS_REAL_APP_SECRET

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def base_url(self) -> str:
        return self.KIS_MOCK_BASE_URL if self.KIS_ENV == "mock" else self.KIS_REAL_BASE_URL

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def account(self) -> tuple[str, str]:
        raw = self.KIS_MOCK_ACCOUNT_NO if self.KIS_ENV == "mock" else self.KIS_REAL_ACCOUNT_NO
        if not raw:
            raise ValueError(f"KIS_{self.KIS_ENV.upper()}_ACCOUNT_NO is not set")
        return parse_account_no(raw)

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def history_db_path(self) -> Path:
        return self.HISTORY_DB_DIR / f"history_{self.KIS_ENV}.sqlite"

    def require_credentials(self) -> None:
        missing = []
        if not self.app_key:
            missing.append(f"KIS_{self.KIS_ENV.upper()}_APP_KEY")
        if not self.app_secret:
            missing.append(f"KIS_{self.KIS_ENV.upper()}_APP_SECRET")
        if missing:
            raise ValueError(f"Missing required env vars for KIS_ENV={self.KIS_ENV}: {missing}")
        _ = self.account


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def reset_settings_cache() -> None:
    """Test helper — drop the module-level Settings cache."""
    global _settings_instance
    _settings_instance = None

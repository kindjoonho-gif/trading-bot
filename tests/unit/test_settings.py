from __future__ import annotations

import pytest

from trader.config.settings import Settings, parse_account_no


class TestParseAccountNo:
    def test_dashed_format(self) -> None:
        assert parse_account_no("12345678-01") == ("12345678", "01")

    def test_bare_8_digits_defaults_to_01(self) -> None:
        assert parse_account_no("12345678") == ("12345678", "01")

    def test_dashed_with_alt_product_code(self) -> None:
        assert parse_account_no("12345678-22") == ("12345678", "22")

    @pytest.mark.parametrize("bad", ["1234567", "123456789", "abcdefgh", ""])
    def test_invalid_cano(self, bad: str) -> None:
        with pytest.raises(ValueError, match="CANO"):
            parse_account_no(bad)

    def test_invalid_product_code(self) -> None:
        with pytest.raises(ValueError, match="ACNT_PRDT_CD"):
            parse_account_no("12345678-1")


class TestSettings:
    def _build(self, **overrides: str) -> Settings:
        base = {
            "KIS_ENV": "mock",
            "KIS_MOCK_APP_KEY": "mock_key",
            "KIS_MOCK_APP_SECRET": "mock_secret",
            "KIS_MOCK_ACCOUNT_NO": "12345678-01",
            "KIS_REAL_APP_KEY": "real_key",
            "KIS_REAL_APP_SECRET": "real_secret",
            "KIS_REAL_ACCOUNT_NO": "87654321-01",
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)  # type: ignore[call-arg]

    def test_mock_active_picks_mock_credentials(self) -> None:
        s = self._build(KIS_ENV="mock")
        assert s.app_key == "mock_key"
        assert s.app_secret == "mock_secret"
        assert s.account == ("12345678", "01")
        assert "openapivts" in s.base_url

    def test_real_active_picks_real_credentials(self) -> None:
        s = self._build(KIS_ENV="real")
        assert s.app_key == "real_key"
        assert s.app_secret == "real_secret"
        assert s.account == ("87654321", "01")
        assert "openapivts" not in s.base_url

    def test_bare_8_digit_account_accepted(self) -> None:
        s = self._build(KIS_MOCK_ACCOUNT_NO="50190536")
        assert s.account == ("50190536", "01")

    def test_require_credentials_raises_on_missing(self) -> None:
        s = self._build(KIS_MOCK_APP_KEY="")
        with pytest.raises(ValueError, match="KIS_MOCK_APP_KEY"):
            s.require_credentials()

    def test_require_credentials_raises_on_missing_account(self) -> None:
        s = self._build(KIS_MOCK_ACCOUNT_NO="")
        with pytest.raises(ValueError, match="KIS_MOCK_ACCOUNT_NO"):
            s.require_credentials()

    def test_history_db_path_default_per_env(self) -> None:
        from pathlib import Path

        s = self._build(KIS_ENV="mock")
        assert s.history_db_path == Path("data") / "history_mock.sqlite"
        s_real = self._build(KIS_ENV="real")
        assert s_real.history_db_path == Path("data") / "history_real.sqlite"

    def test_history_db_path_honors_override(self) -> None:
        from pathlib import Path

        s = self._build(KIS_ENV="mock", HISTORY_DB_DIR="/tmp/custom")
        assert s.history_db_path == Path("/tmp/custom") / "history_mock.sqlite"

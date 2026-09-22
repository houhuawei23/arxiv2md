"""audit5 S5 settings-loader regression tests (G4-2, T-8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from arxiv2md_beta.settings import loader


@pytest.fixture(autouse=True)
def _fresh_settings_cache():
    loader.reset_settings_cache()
    yield
    loader.reset_settings_cache()


class TestCacheKeySensitivity:
    """audit5 G4-2: cached settings must track what actually feeds them.

    The key only held (user_path, environment): env-var changes and YAML
    edits inside one process returned the stale object forever.
    """

    def test_env_change_invalidates_cache(self) -> None:
        s1 = loader.load_settings()
        assert s1.http.fetch_timeout_s != 99.0
        import os

        os.environ["ARXIV2MD_BETA_HTTP__FETCH_TIMEOUT_S"] = "99.0"
        try:
            s2 = loader.load_settings()
            assert s2.http.fetch_timeout_s == 99.0
        finally:
            import os

            os.environ.pop("ARXIV2MD_BETA_HTTP__FETCH_TIMEOUT_S", None)

    def test_user_file_mtime_invalidates_cache(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yml"
        cfg.write_text("http:\n  fetch_max_retries: 1\n", encoding="utf-8")
        s1 = loader.load_settings(config_path=cfg)
        assert s1.http.fetch_max_retries == 1
        cfg.write_text("http:\n  fetch_max_retries: 7\n", encoding="utf-8")
        s2 = loader.load_settings(config_path=cfg)
        assert s2.http.fetch_max_retries == 7


class TestEnvStringEscape:
    """audit5 T-8: quoted env values stay strings."""

    def test_quoted_value_not_coerced(self) -> None:
        import os

        os.environ["ARXIV2MD_BETA_HTTP__USER_AGENT"] = '"true"'
        try:
            overlay = loader.env_overlay_from_os()
            assert overlay["http"]["user_agent"] == "true"
        finally:
            import os

            os.environ.pop("ARXIV2MD_BETA_HTTP__USER_AGENT", None)

    def test_leading_zero_not_octal_int(self) -> None:
        import os

        os.environ["ARXIV2MD_BETA_HTTP__LOG_PREFIX"] = '"007"'
        try:
            overlay = loader.env_overlay_from_os()
            assert overlay["http"]["log_prefix"] == "007"
        finally:
            import os

            os.environ.pop("ARXIV2MD_BETA_HTTP__LOG_PREFIX", None)


class TestEnvUnknownKeyWarning:
    """audit5 T-8: unknown env overlay keys must warn like YAML ones do."""

    def test_unknown_env_key_warns(self, monkeypatch) -> None:
        import os

        os.environ["ARXIV2MD_BETA_OUTPUTS__TYPO"] = "1"
        warnings: list[str] = []
        monkeypatch.setattr(loader.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
        try:
            loader.load_settings(force_reload=True)
            assert any("'outputs'" in msg for msg in warnings)
        finally:
            import os

            os.environ.pop("ARXIV2MD_BETA_OUTPUTS__TYPO", None)

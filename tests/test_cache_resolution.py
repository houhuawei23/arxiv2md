"""Tests for cache path resolution (not cwd-relative)."""

from __future__ import annotations

from pathlib import Path

import pytest

from arxiv2md_beta.settings import load_settings, reset_settings_cache


def test_resolved_cache_absolute_uses_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "my_abs_cache"
    monkeypatch.setenv("ARXIV2MD_BETA_CACHE__DIR", str(target))
    reset_settings_cache()
    s = load_settings(environment="test", force_reload=True)
    assert s.resolved_cache_path() == target.resolve()


def test_resolved_cache_relative_under_xdg_not_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    xdg = tmp_path / "xdg_cache"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    monkeypatch.setenv("ARXIV2MD_BETA_CACHE__DIR", "subdir_only")
    reset_settings_cache()
    s = load_settings(environment="test", force_reload=True)
    assert s.resolved_cache_path() == (xdg / "arxiv2md-beta" / "subdir_only").resolve()


def test_default_bundle_is_user_cache_not_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARXIV2MD_BETA_CACHE__DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    reset_settings_cache()
    s = load_settings(environment="test", force_reload=True)
    p = s.resolved_cache_path()
    assert p.is_absolute()
    assert p.name == "arxiv2md-beta"
    assert p.parent.name == ".cache"
    assert not str(p).startswith(str(tmp_path))


def test_suite_cache_never_resolves_into_real_home_cache() -> None:
    """audit5 T-3: under the test env, the cache root must be redirected away.

    The user's real ``~/.cache/arxiv2md-beta`` must never be touched by the
    suite (conftest _test_settings overrides the cache dir; real-paper runs
    must not pollute or silently reuse user cache).
    """
    from arxiv2md_beta.settings import get_settings

    p = get_settings().resolved_cache_path()
    assert not p.is_relative_to(Path.home() / ".cache" / "arxiv2md-beta"), p

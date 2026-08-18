"""Regression tests for the 2026-08 architecture-review fixes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from arxiv2md_beta.settings import apply_cli_overrides, load_settings
from arxiv2md_beta.settings.schema import AppSettings
from arxiv2md_beta.utils.arxiv_ids import strip_version


@pytest.fixture()
def settings() -> AppSettings:
    return load_settings(force_reload=True)


class TestStripVersion:
    def test_new_style_id(self) -> None:
        assert strip_version("2501.11120v3") == "2501.11120"

    def test_no_version(self) -> None:
        assert strip_version("2501.11120") == "2501.11120"

    def test_old_style_id(self) -> None:
        assert strip_version("math/0309136v2") == "math/0309136"


class TestApplyCliOverridesExplicitOnly:
    """CLI boolean flags must only override settings when explicitly passed."""

    def test_none_keeps_yaml_values(self, settings: AppSettings) -> None:
        s = settings.model_copy(
            update={"output": settings.output.model_copy(update={"include_anchors": True, "linked_citations": True})}
        )
        out = apply_cli_overrides(s, SimpleNamespace())
        assert out.output.include_anchors is True
        assert out.output.linked_citations is True

    def test_explicit_false_overrides(self, settings: AppSettings) -> None:
        s = settings.model_copy(
            update={"output": settings.output.model_copy(update={"include_anchors": True, "linked_citations": True})}
        )
        out = apply_cli_overrides(s, SimpleNamespace(include_anchors=False, linked_citations=False))
        assert out.output.include_anchors is False
        assert out.output.linked_citations is False

    def test_explicit_linked_citations_true(self, settings: AppSettings) -> None:
        out = apply_cli_overrides(settings, SimpleNamespace(linked_citations=True))
        assert out.output.linked_citations is True

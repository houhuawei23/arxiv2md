"""Degradation-path regression tests.

A failed TeX/images fetch must never abort an otherwise-successful HTML
conversion.
"""

from __future__ import annotations

import pytest

import arxiv2md_beta.ingestion.orchestrator as orch_module
from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.exceptions import NetworkError
from arxiv2md_beta.ingestion.orchestrator import IngestionOrchestrator
from arxiv2md_beta.latex.tex_source import TexSourceNotFoundError


def _params(tmp_path, *, no_images: bool = False) -> ConvertParams:
    return ConvertParams(
        input_text="2501.11120",
        parser="html",
        output=str(tmp_path),
        source="Arxiv",
        short=None,
        no_images=no_images,
        remove_refs=False,
        remove_inline_citations=False,
        section_filter_mode="exclude",
        sections=None,
        section=None,
        include_tree=False,
    )


def _orchestrator_with_output_dir(tmp_path) -> IngestionOrchestrator:
    orch = IngestionOrchestrator(_params(tmp_path))
    orch._parse_query()
    orch._paper_output_dir = tmp_path / "paper"
    orch._paper_output_dir.mkdir(parents=True, exist_ok=True)
    return orch


@pytest.mark.asyncio
async def test_tex_rate_limit_degrades_to_no_images(tmp_path, monkeypatch):
    """A TeX-download NetworkError must degrade, not kill the conversion.

    Covers the bare NetworkError the mirror fallback re-raises (e.g. 429 on
    both hosts): the HTML pipeline must finish without images.
    """

    async def raise_rate_limited(*args, **kwargs):
        raise NetworkError(
            "Failed to download TeX source after 3 attempts (HTTP 429)",
            status_code=429,
        )

    monkeypatch.setattr(orch_module, "fetch_and_extract_tex_source", raise_rate_limited)

    orch = _orchestrator_with_output_dir(tmp_path)
    await orch._fetch_tex_and_images()

    assert orch._tex_source_info is None
    assert orch._image_resolver is not None  # empty resolver: renders no images


@pytest.mark.asyncio
async def test_tex_not_found_still_degrades_quietly(tmp_path, monkeypatch):
    async def raise_not_found(*args, **kwargs):
        raise TexSourceNotFoundError("No TeX source available")

    monkeypatch.setattr(orch_module, "fetch_and_extract_tex_source", raise_not_found)

    orch = _orchestrator_with_output_dir(tmp_path)
    await orch._fetch_tex_and_images()

    assert orch._tex_source_info is None
    assert orch._image_resolver is not None


@pytest.mark.asyncio
async def test_affiliation_fetch_handles_timeout(tmp_path, monkeypatch):
    r"""The affiliation-only branch must swallow subprocess timeouts too."""
    import subprocess

    from arxiv2md_beta.settings import get_settings

    async def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pdftex", timeout=45)

    monkeypatch.setattr(orch_module, "fetch_and_extract_tex_source", raise_timeout)

    base = get_settings()
    ingestion = base.ingestion.model_copy(
        update={
            "enrich_affiliations_from_tex": True,
            "fetch_tex_for_affiliations_when_no_images": True,
        }
    )
    settings = base.model_copy(update={"ingestion": ingestion})
    params = _params(tmp_path, no_images=True)
    orch = IngestionOrchestrator(params, settings=settings)
    orch._parse_query()
    orch._paper_output_dir = tmp_path / "paper"
    orch._paper_output_dir.mkdir(parents=True, exist_ok=True)

    await orch._fetch_tex_and_images()  # must not raise
    assert orch._tex_source_info is None

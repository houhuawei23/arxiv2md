"""Direct unit tests for IngestionOrchestrator.run() and its untested steps.

The orchestrator is the heart of the HTML pipeline but historically only had
two lifecycle tests; these cover the main success path, the PDF-only
fallback, metadata degradation, save/structured-export failure propagation,
and the pure helpers (_merge_affiliations, _strip_abstract_heading).
Network boundaries are monkeypatched at the orchestrator module level.
"""

from __future__ import annotations

import pytest

import arxiv2md_beta.ingestion.orchestrator as orch_module
from arxiv2md_beta.exceptions import NetworkError
from arxiv2md_beta.ingestion.orchestrator import IngestionOrchestrator, _strip_abstract_heading
from arxiv2md_beta.ir.blocks import HeadingIR, ParagraphIR
from arxiv2md_beta.ir.document import AuthorIR, DocumentIR, PaperMetadata, SectionIR
from arxiv2md_beta.ir.inlines import TextIR
from tests.test_ingest_degrade import _params


def _orch(tmp_path, *, no_images: bool = True) -> IngestionOrchestrator:
    return IngestionOrchestrator(_params(tmp_path, no_images=no_images))


def _mock_html(monkeypatch, html: str) -> None:
    async def fake_fetch(*args, **kwargs) -> str:
        return html

    monkeypatch.setattr(orch_module, "fetch_arxiv_html", fake_fetch)


def _mock_no_tex(monkeypatch) -> None:
    async def no_tex(*args, **kwargs):
        raise NetworkError("no tex source", status_code=404)

    monkeypatch.setattr(orch_module, "fetch_and_extract_tex_source", no_tex)


SAMPLE = (
    "<html><head><title>Tiny Paper</title></head><body>"
    "<article class='ltx_document'>"
    "<h1 class='ltx_title'>Tiny Paper</h1>"
    "<div class='ltx_authors'><span class='ltx_personname'>Ada Lovelace</span></div>"
    "<section class='ltx_section' id='s1'><h2 class='ltx_title ltx_title_section'>Introduction</h2>"
    "<p class='ltx_p'>Hello world.</p></section>"
    "</article></body></html>"
)


@pytest.mark.asyncio
async def test_run_happy_path(tmp_path, monkeypatch, sample_html) -> None:
    _mock_html(monkeypatch, SAMPLE)
    _mock_no_tex(monkeypatch)

    orch = _orch(tmp_path)
    result, metadata = await orch.run()

    assert "Tiny Paper" in result.summary
    assert result.content.strip()
    assert metadata["arxiv_id"] == "2501.11120"
    assert metadata["paper_output_dir"] == orch._paper_output_dir
    assert metadata["paper_output_dir"].is_dir()
    assert not metadata.get("pdf_only")
    assert "performance" in metadata
    # paper.yml written by the concurrent gather branch
    assert (metadata["paper_output_dir"] / "paper.yml").exists()


@pytest.mark.asyncio
async def test_run_pdf_only_fallback(tmp_path, monkeypatch) -> None:
    async def no_html(*args, **kwargs) -> str:
        raise NetworkError("no HTML rendering", status_code=404)

    monkeypatch.setattr(orch_module, "fetch_arxiv_html", no_html)
    _mock_no_tex(monkeypatch)

    orch = _orch(tmp_path)
    result, metadata = await orch.run()

    assert metadata["pdf_only"] is True
    assert "PDF-only" in result.content
    assert metadata["paper_output_dir"].is_dir()
    assert (metadata["paper_output_dir"] / "paper.yml").exists()


@pytest.mark.asyncio
async def test_run_metadata_failure_degrades_to_html(tmp_path, monkeypatch) -> None:
    """A failing arXiv API metadata fetch must not abort the conversion."""
    _mock_html(monkeypatch, SAMPLE)
    _mock_no_tex(monkeypatch)

    async def fail_metadata(arxiv_id: str) -> dict:
        raise NetworkError("API down", status_code=500)

    orch = _orch(tmp_path)
    monkeypatch.setattr(orch, "_ingestion_cfg", orch._ingestion_cfg.model_copy(update={"fetch_arxiv_metadata": True}))
    monkeypatch.setattr(orch_module, "fetch_arxiv_metadata", fail_metadata)

    result, metadata = await orch.run()
    assert result.content.strip()
    assert metadata["title"] == "Tiny Paper"


@pytest.mark.asyncio
async def test_run_propagates_save_failure(tmp_path, monkeypatch) -> None:
    """An assembly error in paper.yml saving must fail the run (fail fast)."""
    _mock_html(monkeypatch, SAMPLE)
    _mock_no_tex(monkeypatch)

    def boom(meta, out_dir) -> None:
        raise ValueError("injected: paper.yml assembly bug")

    # finalize_ingestion_output resolves save_paper_metadata lazily from the
    # output.metadata module, so patch it there.
    monkeypatch.setattr("arxiv2md_beta.output.metadata.save_paper_metadata", boom)

    orch = _orch(tmp_path)
    with pytest.raises(ValueError, match="injected"):
        await orch.run()


@pytest.mark.asyncio
async def test_run_propagates_structured_export_failure(tmp_path, monkeypatch) -> None:
    _mock_html(monkeypatch, SAMPLE)
    _mock_no_tex(monkeypatch)

    def boom(*args, **kwargs) -> dict:
        raise ValueError("injected: structured export bug")

    monkeypatch.setattr("arxiv2md_beta.ingestion.ir_finalize.run_structured_export", boom)
    orch = _orch(tmp_path)
    monkeypatch.setattr(orch, "params", orch.params.__class__(**{**orch.params.__dict__, "structured_output": "full"}))

    with pytest.raises(ValueError, match="injected"):
        await orch.run()


# ── pure helpers ───────────────────────────────────────────────────────────


def _doc_with_authors(names: list[str]) -> DocumentIR:
    doc = DocumentIR(metadata=PaperMetadata(arxiv_id="2501.11120", title="T"))
    doc.metadata.authors = [AuthorIR(name=n) for n in names]
    doc.sections = [SectionIR(title="Intro", level=1, blocks=[])]
    return doc


def test_merge_affiliations_api_preferred_html_supplement(tmp_path) -> None:
    orch = _orch(tmp_path)
    from types import SimpleNamespace

    orch._parsed = SimpleNamespace(
        authors=[
            SimpleNamespace(name="Ada Lovelace", affiliations=["HTML Univ"]),
            SimpleNamespace(name="Bob", affiliations=["Local Coll"]),
        ],
        title="T",
        abstract="",
    )
    orch._api_metadata = {
        "authors": [
            {"name": "Ada Lovelace", "affiliations": ["API Lab"]},
        ]
    }
    orch._display_author_names = ["Ada Lovelace", "Bob"]
    doc = _doc_with_authors(["Ada Lovelace", "Bob"])
    orch._doc = doc

    orch._merge_affiliations()

    by_name = {a.name: a.affiliations for a in doc.metadata.authors}
    assert by_name["Ada Lovelace"] == ["API Lab"]  # API wins
    assert by_name["Bob"] == ["Local Coll"]  # HTML supplement


def test_strip_abstract_heading_removes_heading_block() -> None:
    doc = DocumentIR(metadata=PaperMetadata(arxiv_id="x", title="T"))
    doc.abstract = [
        HeadingIR(level=3, inlines=[TextIR(text="Abstract")]),
        ParagraphIR(inlines=[TextIR(text="Real content.")]),
    ]
    _strip_abstract_heading(doc)
    assert len(doc.abstract) == 1
    assert isinstance(doc.abstract[0], ParagraphIR)
    assert doc.abstract[0].inlines[0].text == "Real content."


def test_strip_abstract_heading_strips_baked_in_prefix() -> None:
    doc = DocumentIR(metadata=PaperMetadata(arxiv_id="x", title="T"))
    para = ParagraphIR(inlines=[TextIR(text="Abstract. This paper studies things.")])
    doc.abstract = [para]
    _strip_abstract_heading(doc)
    assert para.inlines[0].text == "This paper studies things."
    assert len(doc.abstract) == 1


def test_strip_abstract_heading_keeps_unrelated_heading() -> None:
    doc = DocumentIR(metadata=PaperMetadata(arxiv_id="x", title="T"))
    heading = HeadingIR(level=3, inlines=[TextIR(text="Highlights")])
    doc.abstract = [heading]
    _strip_abstract_heading(doc)
    assert doc.abstract == [heading]

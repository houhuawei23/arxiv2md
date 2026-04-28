"""Integration tests against real arXiv papers (requires network + proxy).

Set HTTP_PROXY / HTTPS_PROXY env vars before running, e.g.:
    HTTP_PROXY=http://127.0.0.1:7890 HTTPS_PROXY=http://127.0.0.1:7890 \
        pytest tests/test_integration_real_papers.py -v

These tests use the local cache (~/.cache/arxiv2md-beta/) so repeated runs
are fast. Use --no-cache or clear the cache dir to force re-download.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from arxiv2md_beta.ir.builders.html import HTMLBuilder
from arxiv2md_beta.html.parser import (
    _extract_authors_with_affiliations,
    _extract_sections,
    _extract_title,
    _find_document_root,
)

# ── skip decorator ─────────────────────────────────────────────────────────
# Run only when explicitly requested: pytest -m real_paper
pytestmark = pytest.mark.real_paper


# ── helpers ────────────────────────────────────────────────────────────────

def _load_cached_html(arxiv_id: str) -> str | None:
    """Load HTML from local cache if available."""
    cache_path = Path.home() / ".cache" / "arxiv2md-beta" / f"{arxiv_id}__latest" / "source.html"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    # Try versioned cache
    cache_dir = Path.home() / ".cache" / "arxiv2md-beta"
    if cache_dir.exists():
        for subdir in cache_dir.iterdir():
            if arxiv_id.replace("v", "_v") in subdir.name or arxiv_id in subdir.name:
                candidate = subdir / "source.html"
                if candidate.exists():
                    return candidate.read_text(encoding="utf-8")
    return None


def _fetch_and_cache_html(arxiv_id: str) -> str:
    """Fetch HTML via httpx (respects HTTP_PROXY env var)."""
    import httpx

    url = f"https://arxiv.org/html/{arxiv_id}"
    resp = httpx.get(url, follow_redirects=True, timeout=60)
    resp.raise_for_status()

    # Save to cache
    cache_dir = Path.home() / ".cache" / "arxiv2md-beta" / f"{arxiv_id}__latest"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "source.html").write_text(resp.text, encoding="utf-8")
    return resp.text


def _get_html(arxiv_id: str) -> str:
    """Return cached HTML or fetch and cache it."""
    cached = _load_cached_html(arxiv_id)
    if cached:
        return cached
    return _fetch_and_cache_html(arxiv_id)


# ── tests ──────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("HTTP_PROXY") and not os.environ.get("HTTPS_PROXY"),
    reason="No proxy configured (set HTTP_PROXY / HTTPS_PROXY env vars)",
)
class TestAttentionIsAllYouNeed:
    """End-to-end tests using arXiv 1706.03762."""

    ARXIV_ID = "1706.03762"

    @pytest.fixture(scope="class")
    def html(self) -> str:
        return _get_html(self.ARXIV_ID)

    def test_title_extracted(self, html: str) -> None:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        title = _extract_title(soup)
        assert title == "Attention Is All You Need"

    def test_authors_with_affiliations(self, html: str) -> None:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        authors = _extract_authors_with_affiliations(soup)
        names = [a.name for a in authors]
        assert "Ashish Vaswani" in names
        assert "Lukasz Kaiser" in names or "Łukasz Kaiser" in names
        assert "Illia Polosukhin" in names

        # Most authors should have affiliations.
        # (Illia Polosukhin in 1706.03762 HTML has no explicit affiliation line.)
        authors_with_affils = [a for a in authors if a.affiliations]
        assert len(authors_with_affils) >= len(authors) - 1, (
            f"Too many authors missing affiliations: "
            f"{[a.name for a in authors if not a.affiliations]}"
        )

    def test_html_builder_produces_clean_equations(self, html: str) -> None:
        """Equations must be pure LaTeX — no duplicated Unicode math symbols."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        doc_root = _find_document_root(soup)
        sections = _extract_sections(doc_root)

        builder = HTMLBuilder()
        doc = builder.build(html, arxiv_id=self.ARXIV_ID)

        # Collect all equation latex strings
        bad_chars = set("𝒙𝜽𝐖ℒ𝒟𝒫𝒚𝒩αη∇∼")  # Unicode math symbols that should not appear
        for sec in doc.sections:
            for blk in sec.blocks:
                if blk.type == "equation":
                    latex = blk.latex
                    # Check for duplicated rendering: if both Unicode and \text{...} appear
                    # that's the bug we fixed. Pure LaTeX should not contain these chars.
                    found_bad = [c for c in bad_chars if c in latex]
                    assert not found_bad, (
                        f"Equation contains Unicode math symbols {found_bad!r}: "
                        f"{latex[:100]}..."
                    )

    def test_html_builder_produces_clean_tables(self, html: str) -> None:
        """Tables must not contain whitespace-only TextIR nodes."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        doc_root = _find_document_root(soup)
        sections = _extract_sections(doc_root)

        builder = HTMLBuilder()
        doc = builder.build(html, arxiv_id=self.ARXIV_ID)

        for sec in doc.sections:
            for blk in sec.blocks:
                if blk.type == "table":
                    for row in blk.rows:
                        for cell in row:
                            for inline in cell:
                                if inline.type == "text":
                                    text = inline.text.strip()
                                    # Whitespace-only cells are the bug
                                    assert text, (
                                        "Table cell contains whitespace-only TextIR"
                                    )


@pytest.mark.skipif(
    not os.environ.get("HTTP_PROXY") and not os.environ.get("HTTPS_PROXY"),
    reason="No proxy configured (set HTTP_PROXY / HTTPS_PROXY env vars)",
)
class TestLearningMechanics:
    """End-to-end tests using arXiv 2604.21691v1."""

    ARXIV_ID = "2604.21691v1"

    @pytest.fixture(scope="class")
    def html(self) -> str:
        return _get_html(self.ARXIV_ID)

    def test_title_extracted(self, html: str) -> None:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        title = _extract_title(soup)
        assert "Scientific Theory of Deep Learning" in title

    def test_authors_with_affiliations(self, html: str) -> None:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        authors = _extract_authors_with_affiliations(soup)
        names = [a.name for a in authors]
        assert "Jamie Simon" in names
        assert "Daniel Kunin" in names

        for a in authors:
            assert a.affiliations, f"Author {a.name!r} missing affiliations"

    def test_equations_no_unicode_duplication(self, html: str) -> None:
        builder = HTMLBuilder()
        doc = builder.build(html, arxiv_id=self.ARXIV_ID)

        bad_chars = set("𝒙𝜽𝐖ℒ𝒟𝒫𝒚𝒩αη∇∼")
        for sec in doc.sections:
            for blk in sec.blocks:
                if blk.type == "equation":
                    found_bad = [c for c in bad_chars if c in blk.latex]
                    assert not found_bad, (
                        f"Equation has Unicode symbols {found_bad!r}: {blk.latex[:100]}"
                    )

    def test_tables_no_whitespace_cells(self, html: str) -> None:
        builder = HTMLBuilder()
        doc = builder.build(html, arxiv_id=self.ARXIV_ID)

        for sec in doc.sections:
            for blk in sec.blocks:
                if blk.type == "table":
                    for row in blk.rows:
                        for cell in row:
                            for inline in cell:
                                if inline.type == "text":
                                    assert inline.text.strip(), (
                                        "Whitespace-only table cell"
                                    )

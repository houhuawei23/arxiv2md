"""Dual-builder output contract tests (audit4 PR1.4).

Two halves:

1. *Shared invariants* — structural properties every builder must satisfy
   after the canonical pass pipeline (unique anchors, complete identity
   fields, emitter compatibility). Builders and transforms may evolve, these
   may not break.

2. *Known-divergence whitelist* — the HTML and LaTeX builders currently
   disagree on five IR conventions (see ``KNOWN_DIVERGENCES``). Each entry is
   pinned by a test asserting the CURRENT state of both sides. That makes any
   accidental convergence or further drift fail loudly here instead of
   surfacing as an emitter/golden surprise. S4 fixes flip an entry by
   updating both the test and the dict — the whitelist is the acceptance
   mechanism for builder-normalisation work, not a licence to keep the gaps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arxiv2md_beta.ir import DocumentIR, HTMLBuilder, LaTeXBuilder
from arxiv2md_beta.ir.emitters.markdown import _CITATION_NUM_RE, MarkdownEmitter
from arxiv2md_beta.ir.resolvers import ImageResolver
from arxiv2md_beta.ir.transforms import build_default_pipeline
from arxiv2md_beta.ir.visitor import IRVisitor, walk
from arxiv2md_beta.settings import get_settings

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# The five documented HTML/LaTeX convention gaps. Keyed by short id; tests
# below reference these ids in their docstrings so a failure points straight
# at the whitelist entry to update.
KNOWN_DIVERGENCES: dict[str, str] = {
    "figure_label": (
        "HTML figures carry label=ar5iv element id (e.g. 'S2.F1') so "
        "NumberingPass can repoint cross-references; LaTeX figures never set "
        "label, so LaTeX cross-reference repointing is a no-op."
    ),
    "figure_id_semantics": (
        "HTML figure_id is caption-derived ('figure-N'); LaTeX figure_id is "
        "the pandoc element id (e.g. 'fig:setup') or pass-assigned."
    ),
    "internal_link_target": (
        "HTML internal links resolve target_id to the mapped anchor with "
        "url=None; LaTeX keeps url='#pandoc-slug' and (when present) sets "
        "target_id to the LINK'S OWN anchor, not the target's — the target_id "
        "field is semantically wrong on this path (audit4 B4)."
    ),
    "struct_id": (
        "LaTeX sections get struct_id='sec_N' from SectionNumberingPass; HTML "
        "sections leave struct_id None at IR level (backfilled by the JSON "
        "emitter only)."
    ),
    "math_normalization": (
        "HTML math runs the full _normalize_math_latex rule set (strips "
        "\\displaystyle, color macros, empty [] args...); LaTeX applies only "
        "four rules, so the same formula yields different latex per parser."
    ),
}


# ── Fixtures / collection helpers ─────────────────────────────────────


class _Collector(IRVisitor):
    def __init__(self) -> None:
        self.figures = []
        self.tables = []
        self.links = []
        self.sections = []

    def visit_figure(self, node):
        self.figures.append(node)

    def visit_table(self, node):
        self.tables.append(node)

    def visit_link(self, node):
        self.links.append(node)

    def visit_section(self, node):
        self.sections.append(node)


def _collect(doc: DocumentIR) -> _Collector:
    c = _Collector()
    walk(doc, c)
    return c


@pytest.fixture(scope="module")
def html_doc() -> DocumentIR:
    doc = HTMLBuilder().build((FIXTURES / "sample_arxiv.html").read_text(encoding="utf-8"), arxiv_id="sample")
    build_default_pipeline(parser="html").run(doc)
    return doc


@pytest.fixture(scope="module")
def latex_doc() -> DocumentIR:
    pytest.importorskip("pypandoc")
    from arxiv2md_beta.latex.includes import resolve_latex_includes

    tex = resolve_latex_includes(FIXTURES / "sample_paper.tex", FIXTURES)
    doc = LaTeXBuilder(image_resolver=ImageResolver()).build(tex, arxiv_id="sample", title="A Sample Paper")
    build_default_pipeline(
        parser="latex",
        reference_section_titles=get_settings().ingestion.reference_section_titles,
    ).run(doc)
    return doc


# ── Shared invariants ─────────────────────────────────────────────────


@pytest.mark.parametrize("doc_fixture", ["html_doc", "latex_doc"])
def test_anchors_are_unique(doc_fixture: str, request: pytest.FixtureRequest) -> None:
    doc = request.getfixturevalue(doc_fixture)
    c = _collect(doc)
    anchors = [s.anchor for s in c.sections if s.anchor]
    anchors += [f.anchor for f in c.figures if f.anchor]
    anchors += [t.anchor for t in c.tables if t.anchor]
    assert len(anchors) == len(set(anchors)), f"duplicate anchors: {anchors}"


@pytest.mark.parametrize("doc_fixture", ["html_doc", "latex_doc"])
def test_every_figure_and_table_has_an_identity(doc_fixture: str, request: pytest.FixtureRequest) -> None:
    """NumberingPass must leave no anonymous figure/table behind."""
    doc = request.getfixturevalue(doc_fixture)
    c = _collect(doc)
    assert all(f.figure_id or f.anchor for f in c.figures)
    assert all(t.table_id or t.anchor for t in c.tables)


@pytest.mark.parametrize("doc_fixture", ["html_doc", "latex_doc"])
def test_section_titles_non_empty(doc_fixture: str, request: pytest.FixtureRequest) -> None:
    doc = request.getfixturevalue(doc_fixture)
    c = _collect(doc)
    assert c.sections, "fixture must produce sections"
    assert all(s.title.strip() for s in c.sections)


@pytest.mark.parametrize("doc_fixture", ["html_doc", "latex_doc"])
def test_emitter_accepts_whole_document(doc_fixture: str, request: pytest.FixtureRequest) -> None:
    """A full emit is the cheapest whole-IR compatibility contract."""
    doc = request.getfixturevalue(doc_fixture)
    out = MarkdownEmitter().emit(doc)
    assert out.strip()


@pytest.mark.parametrize("doc_fixture", ["html_doc", "latex_doc"])
def test_citation_targets_are_emitter_compatible(doc_fixture: str, request: pytest.FixtureRequest) -> None:
    """Numeric citation targets must round-trip through ``_CITATION_NUM_RE``."""
    doc = request.getfixturevalue(doc_fixture)
    for link in _collect(doc).links:
        if link.kind == "citation" and link.target_id and link.target_id[0].isdigit():
            assert _CITATION_NUM_RE.match(link.target_id), f"unrenderable citation target: {link.target_id!r}"


# ── Known-divergence whitelist pins ───────────────────────────────────


def test_w1_figure_label(html_doc: DocumentIR, latex_doc: DocumentIR) -> None:
    """Whitelist: figure_label."""
    html_labels = [f.label for f in _collect(html_doc).figures]
    latex_labels = [f.label for f in _collect(latex_doc).figures]
    assert html_labels and all(html_labels), "html figures must carry ar5iv element ids"
    assert latex_labels and all(lbl is None for lbl in latex_labels), "latex figures must not have grown labels"


def test_w2_figure_id_semantics(html_doc: DocumentIR, latex_doc: DocumentIR) -> None:
    """Whitelist: figure_id_semantics."""
    html_ids = [f.figure_id for f in _collect(html_doc).figures]
    latex_ids = [f.figure_id for f in _collect(latex_doc).figures]
    assert html_ids and all(i.startswith("figure-") for i in html_ids)
    assert latex_ids and all(i and not i.startswith("figure-") for i in latex_ids), (
        "latex figure ids are pandoc element ids; a 'figure-N' here means the "
        "builder started manufacturing html-style ids without a whitelist update"
    )


def test_w3_internal_link_target(html_doc: DocumentIR, latex_doc: DocumentIR) -> None:
    """Whitelist: internal_link_target (audit4 B4 marker — flip in PR2.2)."""
    for link in _collect(latex_doc).links:
        if link.kind == "internal":
            # Current (wrong) semantics: url keeps the pandoc slug; target_id
            # is either None or the link element's OWN anchor.
            assert link.url and link.url.startswith("#")
    # html_doc has no internal links in the shared fixture; the html-side
    # mechanism (_repoint_section_fragments) is covered by transforms tests.


def test_w4_struct_id(html_doc: DocumentIR, latex_doc: DocumentIR) -> None:
    """Whitelist: struct_id."""
    html_sids = [s.struct_id for s in _collect(html_doc).sections]
    latex_sids = [s.struct_id for s in _collect(latex_doc).sections if s.title.lower() != "references"]
    assert all(v is None for v in html_sids), "html struct_id is a JSON-emitter concern, not a builder one"
    assert any(v and v.startswith("sec_") for v in latex_sids), "latex sections must carry SectionNumberingPass ids"


def test_w5_math_normalization() -> None:
    r"""Whitelist: math_normalization.

    \displaystyle is stripped on the HTML path but survives on the LaTeX
    path (four-rule normalization only).
    """
    from arxiv2md_beta.ir.builders.html import _normalize_math_latex

    sample = r"\displaystyle x + y"
    assert "\\displaystyle" not in _normalize_math_latex(sample)

    pytest.importorskip("pypandoc")
    tex = r"""\documentclass{article}
\begin{document}
\section{A}
Inline $\displaystyle x + y$ here.
\end{document}"""
    doc = LaTeXBuilder().build(tex, arxiv_id="sample")
    maths = []
    for section in doc.sections:
        for block in section.blocks:
            if block.type == "paragraph":
                maths += [il for il in block.inlines if il.type == "math"]
    assert maths, "fixture must produce inline math"
    assert any("\\displaystyle" in m.latex for m in maths), (
        "latex builder started stripping \\displaystyle — update the math_normalization whitelist entry"
    )

"""Dual-builder output contract tests (audit4 PR1.4).

Two halves:

1. *Shared invariants* — structural properties every builder must satisfy
   after the canonical pass pipeline (unique anchors, complete identity
   fields, emitter compatibility). Builders and transforms may evolve, these
   may not break.

2. *Known-divergence whitelist* — the HTML and LaTeX builders currently
   disagree on the IR conventions listed in ``KNOWN_DIVERGENCES``. Each entry
   is pinned by a test asserting the CURRENT state of both sides. That makes any
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

# The documented HTML/LaTeX convention gaps that remain. Keyed by short id;
# tests below reference these ids in their docstrings so a failure points
# straight at the whitelist entry to update.
KNOWN_DIVERGENCES: dict[str, str] = {
    "figure_id_semantics": (
        "HTML figure_id is caption-derived ('figure-N'); LaTeX figure_id is "
        "the pandoc element id (e.g. 'fig:setup') or pass-assigned."
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


# ── Converged conventions (formerly divergences; keep guarding) ───────


@pytest.mark.parametrize("doc_fixture", ["html_doc", "latex_doc"])
def test_figures_carry_label_for_crossref_repoint(doc_fixture: str, request: pytest.FixtureRequest) -> None:
    """NumberingPass's label→anchor repointing needs figure.label populated.

    audit4 B4 point fix: the LaTeX builder used to leave label None, making
    cross-reference repointing a no-op on that path.
    """
    doc = request.getfixturevalue(doc_fixture)
    for fig in _collect(doc).figures:
        if fig.figure_id:
            assert fig.label, f"figure {fig.figure_id!r} lost its label"


# ── Known-divergence whitelist pins ───────────────────────────────────


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
    """audit4 B4: internal links carry the target fragment, never self-ids."""
    for doc in (html_doc, latex_doc):
        for link in _collect(doc).links:
            if link.kind == "internal" and link.url:
                # An internal link must not keep a raw '#...' url once it has
                # a resolvable target fragment.
                assert link.target_id == link.url.lstrip("#") or not link.target_id, (
                    f"internal link keeps stale url={link.url!r} with target_id={link.target_id!r}"
                )
    for link in _collect(latex_doc).links:
        if link.kind == "internal":
            assert link.target_id, "latex internal link lost its target fragment"


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


class TestVisitorWalkSingleSource:
    """audit5 根因 4: spec-driven iterators replace per-transform tree walks."""

    def test_child_block_lists_covers_all_block_containers(self):
        from arxiv2md_beta.ir.blocks import AlgorithmIR, BlockQuoteIR, ListIR, ParagraphIR
        from arxiv2md_beta.ir.inlines import TextIR
        from arxiv2md_beta.ir.visitor import child_block_lists

        inner = TextIR(text="x")
        bq = BlockQuoteIR(blocks=[ParagraphIR(inlines=[inner])])
        lst = ListIR(items=[[ParagraphIR(inlines=[inner])]])
        alg = AlgorithmIR(steps=[ParagraphIR(inlines=[inner])])
        assert child_block_lists(bq) == [bq.blocks]
        assert child_block_lists(lst) == lst.items
        assert child_block_lists(alg) == [alg.steps]
        assert child_block_lists(ParagraphIR(inlines=[inner])) == []

    def test_iter_inline_lists_reaches_nested_and_grid_inlines(self):
        from arxiv2md_beta.ir.blocks import FigureIR, ParagraphIR, TableIR
        from arxiv2md_beta.ir.inlines import EmphasisIR, LinkIR, TextIR
        from arxiv2md_beta.ir.visitor import iter_inline_lists

        nested = EmphasisIR(inlines=[TextIR(text="nested")])
        para = ParagraphIR(inlines=[nested])
        grid_cell = [LinkIR(kind="internal", target_id="S2.F1")]
        fig = FigureIR(caption=[TextIR(text="cap")], grid=[[grid_cell]])
        table = TableIR(
            headers=[[TextIR(text="h")]],
            rows=[[[TextIR(text="c")]]],
            caption=[TextIR(text="tcap")],
        )
        # paragraph: outer list + nested emphasis list
        lists = list(iter_inline_lists(para))
        assert lists[0] is para.inlines
        assert any(sub is nested.inlines for sub in lists)
        # figure: caption + grid cell
        fig_lists = list(iter_inline_lists(fig))
        assert fig_lists[0] is fig.caption
        assert grid_cell in fig_lists
        # table: headers + rows + caption all present
        t_lists = list(iter_inline_lists(table))
        assert table.headers[0] in t_lists
        assert table.rows[0][0] in t_lists
        assert table.caption in t_lists

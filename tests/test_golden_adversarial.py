r"""Golden snapshot over the adversarial corpus (audit5 根因 5).

Every P1 that escaped past goldens in past audits (C1-C6, G1-G4) shared one
root cause: no fixture exercised the trap. ``adversarial_paper.tex`` packs
those traps into one document — commented-out ``\\if0`` blocks, verbatim
regions, static-false conditionals with ``\\else`` branches, line-start
syntax, special characters in titles/captions, ``ol start`` (list counter),
rowspan/colspan tables, CJK paragraphs and an inline bibliography with
Unicode/underscore/ampersand entries — and this module locks the exact
pipeline output for it, the same way ``test_golden_snapshot.py`` locks the
benign fixture.

Regenerate with:

    GOLDEN_REGEN=1 python -m pytest tests/test_golden_adversarial.py

The corpus immediately paid for itself: it exposed two live bugs on the
LaTeX path — ``\\end{document}`` commented out by the orphan-end fix when a
phantom env entry leaked from an ``\\if0`` chunk (fixed in
``_fix_orphan_ends``), and every verbatim body vanishing into the fence
info-string because the builder read pandoc's CodeBlock ``[attr, text]`` as
``[attr, lang, text]`` (fixed in ``LaTeXBuilder._blocks_from_pandoc``).
One known limitation remains pinned: an *inline* ``thebibliography``
environment renders its bibitems merged into a single reference with the
widest-label ``{9}`` leaking through (the .bbl path is unaffected); see
REVIEW_2026-09-22b.md, finding N-1.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from arxiv2md_beta.ir import LaTeXBuilder
from arxiv2md_beta.ir.resolvers import ImageResolver
from arxiv2md_beta.ir.transforms import build_default_pipeline
from arxiv2md_beta.latex.includes import resolve_latex_includes
from arxiv2md_beta.settings import get_settings
from tests.test_golden_snapshot import _check, _check_json, _emit_json, _emit_parts

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "adversarial_paper.tex"
_REGEN = os.environ.get("GOLDEN_REGEN") == "1"


@pytest.fixture(scope="module")
def adv_doc():
    tex = resolve_latex_includes(FIXTURE, FIXTURE.parent)
    doc = LaTeXBuilder(image_resolver=ImageResolver()).build(
        tex, arxiv_id="adversarial", title="Adversarial Fixture: Under_scores, Stars* and Brackets [2026]"
    )
    pipeline = build_default_pipeline(
        parser="latex",
        reference_section_titles=get_settings().ingestion.reference_section_titles,
    )
    pipeline.run(doc)
    return doc


@pytest.fixture(scope="module")
def adv_parts(adv_doc):
    return _emit_parts(adv_doc)


@pytest.fixture(scope="module")
def adv_json(adv_doc):
    return _emit_json(adv_doc)


@pytest.mark.parametrize(
    "idx,name",
    [(0, "adversarial.main.md"), (1, "adversarial.refs.md"), (2, "adversarial.appendix.md")],
)
def test_adversarial_markdown_golden(adv_parts, idx, name):
    actual = adv_parts[idx]
    if actual is None:
        pytest.skip(f"{name} is empty (no content in this split)")
    _check(name, actual)


def test_adversarial_meta_json_golden(adv_json):
    _check_json("adversarial.meta.json", adv_json[0])


def test_adversarial_document_json_golden(adv_json):
    _check_json("adversarial.document.json", adv_json[1])


def test_adversarial_assets_json_golden(adv_json):
    _check_json("adversarial.assets.json", adv_json[2])

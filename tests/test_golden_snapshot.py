"""Golden snapshot of the LaTeX-fixture IR pipeline output.

Safety net for the 2026-07-19 refactor (see docs/REVIEW_2026-07-19.md).
Locks the exact markdown (main / refs / appendix) and structured JSON
(meta / document / assets) produced by running
``LaTeXBuilder -> PassPipeline -> MarkdownEmitter / JsonEmitter`` over
``tests/fixtures/sample_paper.tex``.

If the refactor intentionally changes output, regenerate the golden files:

    GOLDEN_REGEN=1 python -m pytest tests/test_golden_snapshot.py

Any *unintended* change fails the test.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from arxiv2md_beta.ir import (
    AnchorPass,
    FigureReorderPass,
    LaTeXBuilder,
    NumberingPass,
    PassPipeline,
    SectionFilterPass,
)
from arxiv2md_beta.ir.emitters.json_emitter import JsonEmitter
from arxiv2md_beta.ir.resolvers import ImageResolver
from arxiv2md_beta.latex.includes import resolve_latex_includes
from arxiv2md_beta.settings import get_settings

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_paper.tex"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
_REGEN = os.environ.get("GOLDEN_REGEN") == "1"


def _build_doc():
    tex = resolve_latex_includes(FIXTURE, FIXTURE.parent)
    doc = LaTeXBuilder(image_resolver=ImageResolver()).build(
        tex, arxiv_id="sample", title="A Sample Paper for Testing"
    )
    pipeline = PassPipeline()
    pipeline.add(SectionFilterPass(mode="exclude", selected=[]))
    pipeline.add(NumberingPass())
    pipeline.add(FigureReorderPass())
    pipeline.add(AnchorPass())
    pipeline.run(doc)
    return doc


def _emit_parts(doc):
    # Lazy import: arxiv2md_beta.ingestion package init pulls in cli, which
    # would cycle if loaded at test-module import time. By runtime the app
    # graph is already loaded.
    from arxiv2md_beta.ingestion.ir_finalize import emit_split_markdown

    # Use the shared finalization helper so the golden reflects real
    # production output (single format+clean pass, sidecars without abstract).
    main_md, refs_md, appx_md = emit_split_markdown(
        doc,
        reference_section_titles=get_settings().ingestion.reference_section_titles,
    )
    return main_md, refs_md, appx_md


def _emit_json(doc):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        JsonEmitter(mode="full").write_bundle(
            doc, tmp_path, images_subdir="images", emit_graph_csv=False
        )
        meta = json.loads((tmp_path / "paper.meta.json").read_text())
        document = json.loads((tmp_path / "paper.document.json").read_text())
        assets = json.loads((tmp_path / "paper.assets.json").read_text())
    return meta, document, assets


def _check(golden_name: str, actual: str) -> None:
    golden_path = GOLDEN_DIR / golden_name
    if _REGEN:
        golden_path.write_text(actual)
        pytest.skip(f"regenerated {golden_name}")
        return
    assert golden_path.exists(), (
        f"golden file missing: {golden_path}. Run with GOLDEN_REGEN=1 to create."
    )
    expected = golden_path.read_text()
    assert actual == expected, (
        f"{golden_name} drifted. If intended, regenerate with GOLDEN_REGEN=1.\n"
        f"--- expected (first 500 chars) ---\n{expected[:500]}\n"
        f"--- actual (first 500 chars) ---\n{actual[:500]}"
    )


def _check_json(golden_name: str, obj) -> None:
    _check(golden_name, json.dumps(obj, indent=2, sort_keys=True) + "\n")


@pytest.fixture(scope="module")
def doc():
    return _build_doc()


@pytest.fixture(scope="module")
def parts(doc):
    return _emit_parts(doc)


@pytest.fixture(scope="module")
def json_parts(doc):
    return _emit_json(doc)


@pytest.mark.parametrize("idx,name", [(0, "sample_paper.main.md"), (1, "sample_paper.refs.md"), (2, "sample_paper.appendix.md")])
def test_markdown_golden(parts, idx, name):
    actual = parts[idx]
    if actual is None:
        pytest.skip(f"{name} is empty (no references/appendix sections in fixture)")
    _check(name, actual)


def test_meta_json_golden(json_parts):
    _check_json("sample_paper.meta.json", json_parts[0])


def test_document_json_golden(json_parts):
    _check_json("sample_paper.document.json", json_parts[1])


def test_assets_json_golden(json_parts):
    _check_json("sample_paper.assets.json", json_parts[2])

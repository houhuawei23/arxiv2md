"""Tests for structured JSON export (paper.meta.json, paper.document.json, graph)."""

from __future__ import annotations

import json
from pathlib import Path

from arxiv2md_beta.output.structured_export import (
    build_graph,
    normalize_structured_mode,
    write_structured_bundle,
    write_minimal_structured,
)
from arxiv2md_beta.schemas.sections import SectionNode
from arxiv2md_beta.schemas.structured import PaperDocumentJson, PaperMetaJson, SCHEMA_VERSION


def test_normalize_structured_mode() -> None:
    assert normalize_structured_mode(None) == "none"
    assert normalize_structured_mode("") == "none"
    assert normalize_structured_mode("  META  ") == "meta"
    assert normalize_structured_mode("bogus") == "none"


def test_write_structured_bundle_document(tmp_path: Path) -> None:
    sections = [
        SectionNode(
            title="Introduction",
            level=1,
            anchor="intro",
            html="<p>Hello world</p>",
            markdown="Hello world",
            children=[],
        )
    ]
    out = write_structured_bundle(
        paper_output_dir=tmp_path,
        mode="document",
        emit_graph_csv=False,
        arxiv_id="1234.56789",
        arxiv_version="v1",
        title="Test",
        authors=["A"],
        submission_date="2025-01-01",
        html_url="https://arxiv.org/html/1234.56789",
        ar5iv_url=None,
        parser="html",
        sections=sections,
        abstract_md=None,
        abstract_html=None,
        front_matter_html=None,
        include_abstract_parts=False,
        image_map=None,
        stem_to_image_path=None,
        images_subdir="images",
    )
    assert out["schema_version"] == SCHEMA_VERSION
    assert "paper.meta.json" in out["paths"]
    assert "paper.document.json" in out["paths"]
    assert not (tmp_path / "paper.assets.json").exists()

    meta = json.loads((tmp_path / "paper.meta.json").read_text(encoding="utf-8"))
    assert meta["arxiv_id"] == "1234.56789"
    assert meta["parser"] == "html"

    doc = json.loads((tmp_path / "paper.document.json").read_text(encoding="utf-8"))
    assert doc["arxiv_id"] == "1234.56789"
    assert len(doc["sections"]) == 1
    assert len(doc["blocks"]) >= 1


def test_write_structured_bundle_all_graph_csv(tmp_path: Path) -> None:
    sections = [
        SectionNode(
            title="X",
            level=1,
            anchor="x",
            html="<p>a</p>",
            markdown="a",
            children=[],
        )
    ]
    write_structured_bundle(
        paper_output_dir=tmp_path,
        mode="all",
        emit_graph_csv=True,
        arxiv_id="1234.56789",
        arxiv_version=None,
        title=None,
        authors=[],
        submission_date=None,
        html_url=None,
        ar5iv_url=None,
        parser="html",
        sections=sections,
        abstract_md=None,
        abstract_html=None,
        front_matter_html=None,
        include_abstract_parts=False,
        image_map=None,
        stem_to_image_path=None,
        images_subdir="images",
    )
    assert (tmp_path / "paper.graph.json").exists()
    assert (tmp_path / "paper.graph.nodes.csv").exists()
    assert (tmp_path / "paper.graph.edges.csv").exists()


def test_write_minimal_structured_latex(tmp_path: Path) -> None:
    sections = [
        SectionNode(
            title="Body",
            level=1,
            anchor=None,
            html=None,
            markdown="# Hi\n",
            children=[],
        )
    ]
    out = write_minimal_structured(
        paper_output_dir=tmp_path,
        mode="document",
        emit_graph_csv=False,
        arxiv_id="1234.56789",
        arxiv_version=None,
        title="T",
        authors=[],
        submission_date=None,
        parser="latex",
        sections=sections,
        abstract_md=None,
    )
    assert out["paths"]["paper.document.json"]


def test_build_graph_block_order() -> None:
    from arxiv2md_beta.schemas.structured import BlockJson

    sec = SectionNode(
        title="S",
        level=1,
        anchor="s",
        html=None,
        markdown="m",
        children=[],
    )
    sec.struct_id = "sec_0"
    blocks = [
        BlockJson(
            id="sec_0:p0",
            type="paragraph",
            section_id="sec_0",
            order_index=0,
            text_plain="a",
        ),
        BlockJson(
            id="sec_0:p1",
            type="paragraph",
            section_id="sec_0",
            order_index=1,
            text_plain="b",
        ),
    ]
    g = build_graph(arxiv_id="x", section_nodes=[sec], blocks=blocks, asset_ids=[])
    next_edges = [e for e in g.edges if e.type == "next" and e.properties.get("scope") == "block"]
    assert len(next_edges) == 1
    assert next_edges[0].src == "sec_0:p0"
    assert next_edges[0].dst == "sec_0:p1"


def test_json_schema_files_match_models() -> None:
    """Bundled JSON Schema files stay in sync with Pydantic models."""
    root = Path(__file__).resolve().parents[1] / "src" / "arxiv2md_beta" / "schemas" / "json"
    meta_path = root / "paper.meta.schema.json"
    doc_path = root / "paper.document.schema.json"
    assert meta_path.is_file(), f"missing {meta_path}"
    assert doc_path.is_file(), f"missing {doc_path}"
    assert json.loads(meta_path.read_text(encoding="utf-8")) == PaperMetaJson.model_json_schema()
    assert json.loads(doc_path.read_text(encoding="utf-8")) == PaperDocumentJson.model_json_schema()

"""Tests for the bundled JSON Schema files vs the Pydantic structured-export models."""

from __future__ import annotations

import json
from pathlib import Path

from arxiv2md_beta.schemas.structured import PaperDocumentJson, PaperMetaJson


def test_json_schema_files_match_models() -> None:
    """Bundled JSON Schema files stay in sync with Pydantic models."""
    root = Path(__file__).resolve().parents[1] / "src" / "arxiv2md_beta" / "schemas" / "json"
    meta_path = root / "paper.meta.schema.json"
    doc_path = root / "paper.document.schema.json"
    assert meta_path.is_file(), f"missing {meta_path}"
    assert doc_path.is_file(), f"missing {doc_path}"
    assert json.loads(meta_path.read_text(encoding="utf-8")) == PaperMetaJson.model_json_schema()
    assert json.loads(doc_path.read_text(encoding="utf-8")) == PaperDocumentJson.model_json_schema()

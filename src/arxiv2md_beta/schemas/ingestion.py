"""Ingestion output models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class IngestionResult(BaseModel):
    """Final ingestion output.

    When ``split_for_reference`` is used in HTML ingestion, ``content`` is the
    main body (before References); ``content_references`` and ``content_appendix``
    hold the split parts. Otherwise ``content`` is the full document and the
    optional fields are None.
    """

    summary: str
    sections_tree: str
    content: str
    content_references: str | None = None
    content_appendix: str | None = None
    performance: dict | None = None


class IngestionMetadata(BaseModel):
    """Typed cross-layer contract carried alongside an :class:`IngestionResult`.

    Replaces the untyped ``dict[str, Any]`` that used to pass between the
    ingestion layer and the CLI persistence layer — ``paper_output_dir`` was
    sometimes a Path and sometimes a str, and ``pdf_only`` was a hidden
    magic key with no schema or type checking.

    Fields with cross-layer *behavior* (naming, gating, JSON-line emission)
    are typed above; ``extra`` carries source-provenance bits that merely
    flow into paper.yml (``archive_path``, ``html_path``) and therefore vary
    per input path.
    """

    arxiv_id: str = ""
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    submission_date: str | None = None
    paper_output_dir: Path | None = None
    structured_export: dict[str, Any] = Field(default_factory=dict)
    # PDF-only fallback: no Markdown can pass the quality gate; persist skips
    # the gate and writes only the PDF + manifest. Without this flag the
    # fallback would be unreachable (exit 5 with no artifacts).
    pdf_only: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)
    performance: dict[str, Any] | None = None

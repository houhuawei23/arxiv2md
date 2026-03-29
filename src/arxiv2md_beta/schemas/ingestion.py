"""Ingestion output model."""

from __future__ import annotations

from pydantic import BaseModel


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

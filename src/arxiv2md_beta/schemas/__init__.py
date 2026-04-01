"""Shared schemas for arxiv2md-beta."""

from arxiv2md_beta.schemas.ingestion import IngestionResult
from arxiv2md_beta.schemas.query import ArxivQuery, LocalArchiveQuery
from arxiv2md_beta.schemas.sections import SectionNode
from arxiv2md_beta.schemas.structured import (
    SCHEMA_VERSION,
    PaperDocumentJson,
    PaperMetaJson,
)

__all__ = [
    "ArxivQuery",
    "LocalArchiveQuery",
    "IngestionResult",
    "SectionNode",
    "SCHEMA_VERSION",
    "PaperMetaJson",
    "PaperDocumentJson",
]

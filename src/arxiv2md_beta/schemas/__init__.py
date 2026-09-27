"""Shared schemas for arxiv2md-beta."""

from arxiv2md_beta.schemas.ingestion import IngestionMetadata, IngestionResult
from arxiv2md_beta.schemas.query import ArxivQuery, LocalArchiveQuery, LocalHtmlQuery
from arxiv2md_beta.schemas.structured import SCHEMA_VERSION

__all__ = [
    "IngestionMetadata",
    "ArxivQuery",
    "LocalArchiveQuery",
    "LocalHtmlQuery",
    "IngestionResult",
    "SCHEMA_VERSION",
]

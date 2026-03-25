"""High-level ingestion orchestration (HTML / LaTeX / local archive)."""

from arxiv2md_beta.ingestion.pipeline import ingest_paper

__all__ = ["ingest_paper"]

"""Query model for arXiv ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ArxivQuery(BaseModel):
    """Parsed arXiv query details."""

    input_text: str
    arxiv_id: str
    version: str | None = None
    html_url: str
    ar5iv_url: str
    abs_url: str
    id: UUID
    cache_dir: Path
    remove_refs: bool = False
    remove_toc: bool = False
    remove_inline_citations: bool = False
    section_filter_mode: Literal["include", "exclude"] = "exclude"
    sections: list[str] = Field(default_factory=list)


class LocalArchiveQuery(BaseModel):
    """Parsed local archive (tar.gz/zip) query details."""

    input_text: str
    archive_path: Path
    archive_type: Literal["tar.gz", "tgz", "zip"]
    id: UUID
    cache_dir: Path
    # Optional metadata override
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    submission_date: str | None = None
    remove_refs: bool = False
    remove_toc: bool = False
    remove_inline_citations: bool = False
    section_filter_mode: Literal["include", "exclude"] = "exclude"
    sections: list[str] = Field(default_factory=list)

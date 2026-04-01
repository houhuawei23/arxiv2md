"""Versioned JSON models for structured paper export (paper.meta.json, paper.document.json, etc.)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class PaperMetaJson(BaseModel):
    """Machine-oriented metadata (``paper.meta.json``)."""

    schema_version: str = SCHEMA_VERSION
    arxiv_id: str
    arxiv_version: str | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    submission_date: str | None = None
    html_url: str | None = None
    ar5iv_url: str | None = None
    tool_name: str = "arxiv2md-beta"
    tool_version: str = "0.0.0"
    content_sha256: str | None = None
    parser: Literal["html", "latex", "local"] = "html"


class SectionTreeNodeJson(BaseModel):
    """One node in the section tree (no HTML bodies)."""

    struct_id: str
    title: str
    level: int = Field(..., ge=1, le=6)
    anchor: str | None = None
    html_sha256: str | None = None
    markdown_sha256: str | None = None
    children: list["SectionTreeNodeJson"] = Field(default_factory=list)


class BlockJson(BaseModel):
    """A coarse block inside a section (paragraph, figure, table, …)."""

    id: str
    type: Literal[
        "paragraph",
        "figure",
        "table",
        "equation",
        "list",
        "code",
        "heading",
        "blockquote",
        "other",
    ]
    section_id: str
    order_index: int = 0
    text_plain: str | None = None
    text_md: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class PaperDocumentJson(BaseModel):
    """Section tree + blocks (``paper.document.json``)."""

    schema_version: str = SCHEMA_VERSION
    arxiv_id: str
    abstract_blocks: list[BlockJson] = Field(default_factory=list)
    front_matter_blocks: list[BlockJson] = Field(default_factory=list)
    sections: list[SectionTreeNodeJson] = Field(default_factory=list)
    blocks: list[BlockJson] = Field(default_factory=list)


class AssetJson(BaseModel):
    """One image or other asset on disk."""

    id: str
    path: str
    kind: Literal["image", "svg", "other"] = "image"
    tex_stem: str | None = None
    figure_index: int | None = None


class PaperAssetsJson(BaseModel):
    """Resolved assets (``paper.assets.json``)."""

    schema_version: str = SCHEMA_VERSION
    arxiv_id: str
    images_subdir: str = "images"
    assets: list[AssetJson] = Field(default_factory=list)
    stem_to_path: dict[str, str] = Field(default_factory=dict)


class BibEntryJson(BaseModel):
    """Placeholder for parsed bibliography (Phase D)."""

    key: str | None = None
    raw: str | None = None


class PaperBibJson(BaseModel):
    """Bibliography stub (``paper.bib.json``)."""

    schema_version: str = SCHEMA_VERSION
    arxiv_id: str
    entries: list[BibEntryJson] = Field(default_factory=list)


class GraphNodeJson(BaseModel):
    """Node in ``paper.graph.json``."""

    id: str
    type: Literal["paper", "section", "block", "asset"]
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeJson(BaseModel):
    """Directed edge in ``paper.graph.json``."""

    src: str
    dst: str
    type: Literal[
        "contains",
        "next",
        "child_section",
        "uses_asset",
        "cites",
    ]
    properties: dict[str, Any] = Field(default_factory=dict)


class PaperGraphJson(BaseModel):
    """Heterogeneous graph export."""

    schema_version: str = SCHEMA_VERSION
    arxiv_id: str
    nodes: list[GraphNodeJson] = Field(default_factory=list)
    edges: list[GraphEdgeJson] = Field(default_factory=list)

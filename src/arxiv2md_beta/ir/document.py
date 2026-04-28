"""Document-level IR types: SectionIR, PaperMetadata, DocumentIR."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from arxiv2md_beta.ir.assets import AssetUnion
from arxiv2md_beta.ir.blocks import BlockUnion
from arxiv2md_beta.ir.core import IRNode


class AuthorIR(IRNode):
    """Structured author record with name and optional affiliations."""

    type: Literal["author"] = "author"
    name: str
    affiliations: list[str] = Field(default_factory=list)


class SectionIR(IRNode):
    """A hierarchical section node in the document tree.

    Each section contains an ordered list of ``blocks`` and zero or more
    child ``sections`` (sub-sections).
    """

    type: Literal["section"] = "section"
    title: str
    level: int = Field(ge=1, le=6)
    anchor: str | None = None
    struct_id: str | None = None  # e.g. "sec_0", "sec_0_1"
    blocks: list[BlockUnion] = Field(default_factory=list)
    children: list["SectionIR"] = Field(default_factory=list)


class PaperMetadata(IRNode):
    """Machine-readable paper metadata."""

    type: Literal["metadata"] = "metadata"
    arxiv_id: str
    arxiv_version: str | None = None
    title: str | None = None
    authors: list[AuthorIR] = Field(default_factory=list)
    submission_date: str | None = None
    abstract_text: str | None = None
    source_url: str | None = None
    parser: Literal["html", "latex", "local"] = "html"
    tool_name: str = "arxiv2md-beta"
    tool_version: str = "0.0.0"

    @property
    def author_names(self) -> list[str]:
        """Convenience accessor: ordered author name strings."""
        return [a.name for a in self.authors]


class DocumentIR(IRNode):
    """Complete intermediate representation of an academic paper.

    This is the top-level container produced by builders and consumed by
    transforms and emitters.
    """

    type: Literal["document"] = "document"
    schema_version: str = "2.0"
    metadata: PaperMetadata = Field(default_factory=lambda: PaperMetadata(arxiv_id="unknown"))
    abstract: list[BlockUnion] = Field(default_factory=list)
    front_matter: list[BlockUnion] = Field(default_factory=list)
    sections: list[SectionIR] = Field(default_factory=list)
    bibliography: list[BlockUnion] = Field(default_factory=list)
    assets: list[AssetUnion] = Field(default_factory=list)

"""Cross-layer data contracts.

The IR builders consume parse results produced two layers up (``html/parser``,
``latex/tex_source``); holding those input types here keeps the dependency
arrows pointing down (builders → contracts, parsers → contracts) instead of
bulding a cycle through the upper layers. Historically ``ParsedArxivHtml``
lived in ``html/parser.py`` and the IR builder had to import it lazily from
below; ``TexSourceInfo`` lived in ``latex/tex_source.py`` and dragged
``images/processor`` into depending on latex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, Field


class SectionNode(BaseModel):
    """A hierarchical section node produced by the HTML parser.

    This is the builder-input protocol — ``SectionIR`` is the canonical IR
    tree; HTMLBuilder converts these nodes into it.
    """

    title: str
    level: int = Field(..., ge=1, le=6)
    anchor: str | None = None
    struct_id: str | None = None
    html: str | None = None
    children: list[SectionNode] = Field(default_factory=list)


@dataclass
class ParsedAuthor:
    """An author record with optional affiliation(s)."""

    name: str
    affiliations: list[str] = field(default_factory=list)


@dataclass
class ParsedArxivHtml:
    """Parsed content extracted from arXiv HTML (the HTMLBuilder's input)."""

    title: str | None
    authors: list[ParsedAuthor]
    abstract: str | None
    abstract_html: str | None  # Inner HTML of abstract div for figure-aware conversion
    front_matter_html: str | None  # HTML between abstract and first section (e.g. title-block figures)
    sections: list[SectionNode]
    submission_date: str | None = None  # Format: YYYYMMDD


class TexSourceInfo(NamedTuple):
    """Information about an extracted TeX source tree (the images/ latex consumers' input)."""

    extracted_dir: Path
    main_tex_file: Path | None
    image_files: dict[str, Path]  # figure_label -> local_path
    all_images: list[Path]  # All image files found
    # \includegraphics inside \begin{figure} envs only, in float order. Drives
    # the figure-index map so ar5iv xN.png names resolve to the right file.
    figure_image_files: list[Path] = []

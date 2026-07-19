"""LaTeX source handling: include resolution, TeX source fetch, author affiliations."""

from arxiv2md_beta.exceptions import ParserNotAvailableError
from arxiv2md_beta.latex.includes import resolve_latex_includes
from arxiv2md_beta.latex.tex_source import (
    ImageExtractionError,
    TexSourceInfo,
    TexSourceNotFoundError,
    fetch_and_extract_tex_source,
)

__all__ = [
    "ImageExtractionError",
    "ParserNotAvailableError",
    "TexSourceInfo",
    "TexSourceNotFoundError",
    "fetch_and_extract_tex_source",
    "resolve_latex_includes",
]

"""CLI parameter dataclasses (shared by runner, output_finalize, batch)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from arxiv2md_beta.params import ConvertParams

__all__ = ["ConvertParams", "ImagesParams", "PaperYmlParams"]


@dataclass(frozen=True)
class ImagesParams:
    """Parameters for the ``images`` command."""

    arxiv_input: str
    output: str | None
    images_subdir: str
    no_tex_cache: bool


@dataclass(frozen=True)
class PaperYmlParams:
    """Parameters for the ``paper-yml`` command."""

    update_path: Path | None
    arxiv_input: str | None
    output: str | None
    force: bool

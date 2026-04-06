"""CLI parameter dataclasses (shared by runner, output_finalize, batch)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConvertParams:
    """Parameters for the ``convert`` and ``batch`` commands."""

    input_text: str
    parser: str
    output: str | None
    source: str
    short: str | None
    no_images: bool
    remove_refs: bool
    remove_toc: bool
    remove_inline_citations: bool
    section_filter_mode: str
    sections: str | None
    section: list[str] | None
    include_tree: bool
    emit_result_json: bool = False
    structured_output: str = "none"
    emit_graph_csv: bool = False
    use_cache: bool = True


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

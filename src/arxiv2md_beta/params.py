"""ConvertParams: shared by the CLI layer, runners and the ingestion orchestrator.

Lives at the package root (not under ``cli/``) so the ingestion layer does not
depend on the CLI layer for its parameter type.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    remove_inline_citations: bool
    section_filter_mode: str
    sections: str | None
    section: list[str] | None
    include_tree: bool
    emit_result_json: bool = False
    structured_output: str = "none"
    emit_graph_csv: bool = False
    no_cache: bool = False
    download_pdf: bool = True
    linked_citations: bool = False

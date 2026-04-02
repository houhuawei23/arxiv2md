"""Shared validation and ``ConvertParams`` construction for ``convert`` / ``batch``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import typer

from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.settings import apply_cli_overrides, get_settings, set_settings


def apply_convert_cli_settings(
    *,
    parser: Optional[str],
    source: Optional[str],
    section_filter_mode: Optional[str],
    structured_output: str,
    no_progress: bool,
) -> tuple[str, str, str, str]:
    """Validate parser/section/structured options and update global settings.

    Returns ``(parser_mode, source_v, section_mode, structured_output_normalized)``.
    """
    s = get_settings()
    d = s.cli_defaults
    parser_mode = parser if parser is not None else d.parser
    if parser_mode not in ("html", "latex"):
        typer.echo(
            f"Invalid --parser {parser_mode!r}; expected html or latex.", err=True
        )
        raise typer.Exit(code=2)
    source_v = source if source is not None else d.source
    mode = (
        section_filter_mode
        if section_filter_mode is not None
        else d.section_filter_mode
    )
    if mode not in ("include", "exclude"):
        typer.echo(
            f"Invalid --section-filter-mode {mode!r}; expected include or exclude.",
            err=True,
        )
        raise typer.Exit(code=2)
    so = structured_output.strip().lower()
    if so not in ("none", "meta", "document", "full", "all"):
        typer.echo(
            f"Invalid --structured-output {structured_output!r}; expected none, meta, document, full, or all.",
            err=True,
        )
        raise typer.Exit(code=2)

    merged = apply_cli_overrides(
        s,
        SimpleNamespace(parser=parser_mode, source=source_v, section_filter_mode=mode),
    )
    if no_progress:
        merged = merged.model_copy(
            update={
                "images": merged.images.model_copy(update={"disable_tqdm": True}),
            }
        )
    set_settings(merged)
    return parser_mode, source_v, mode, so


def make_convert_params(
    input_text: str,
    *,
    parser_mode: str,
    output: Optional[str],
    source_v: str,
    short: Optional[str],
    no_images: bool,
    remove_refs: bool,
    remove_toc: bool,
    remove_inline_citations: bool,
    mode: str,
    sections: Optional[str],
    section: list[str],
    include_tree: bool,
    emit_result_json: bool,
    so: str,
    emit_graph_csv: bool,
) -> ConvertParams:
    """Build ``ConvertParams`` after :func:`apply_convert_cli_settings`."""
    sec_list = section if section else None
    return ConvertParams(
        input_text=input_text.strip(),
        parser=parser_mode,
        output=output,
        source=source_v,
        short=short,
        no_images=no_images,
        remove_refs=remove_refs,
        remove_toc=remove_toc,
        remove_inline_citations=remove_inline_citations,
        section_filter_mode=mode,
        sections=sections,
        section=sec_list,
        include_tree=include_tree,
        emit_result_json=emit_result_json,
        structured_output=so,
        emit_graph_csv=emit_graph_csv,
    )

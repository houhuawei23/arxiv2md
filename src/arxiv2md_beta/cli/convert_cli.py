"""Shared validation and ``ConvertParams`` construction for ``convert`` / ``batch``."""

from __future__ import annotations

from dataclasses import dataclass

import typer

from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.settings import get_settings


@dataclass(frozen=True)
class ConvertCliSettings:
    """Resolved CLI flags from :func:`apply_convert_cli_settings`.

    Each flag is already merged against its settings default (CLI flag >
    config > default); consumers read these instead of re-reading mutated
    global settings.
    """

    parser_mode: str
    source_v: str
    mode: str
    so: str
    include_anchors: bool
    linked_citations: bool
    naming_scheme: str
    fetch_metadata: bool
    no_progress: bool
    concurrency: int | None


def apply_convert_cli_settings(
    *,
    parser: str | None,
    source: str | None,
    section_filter_mode: str | None,
    structured_output: str,
    no_progress: bool,
    include_anchors: bool | None = None,
    linked_citations: bool | None = None,
    naming_scheme: str | None = None,
    fetch_arxiv_metadata: bool = False,
    concurrency: int | None = None,
) -> ConvertCliSettings:
    """Validate parser/section/structured options and resolve CLI flags.

    Read-only: never mutates the process-global settings singleton. Returns a
    :class:`ConvertCliSettings` with every flag resolved against its settings
    default (CLI flag > config > default).
    """
    s = get_settings()
    d = s.cli_defaults
    parser_mode = parser if parser is not None else d.parser
    if parser_mode not in ("html", "latex"):
        typer.echo(f"Invalid --parser {parser_mode!r}; expected html or latex.", err=True)
        raise typer.Exit(code=2)
    source_v = source if source is not None else d.source
    mode = section_filter_mode if section_filter_mode is not None else d.section_filter_mode
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

    if naming_scheme is not None and naming_scheme not in ("classic", "paper-pipeline", "arxiv-ym"):
        typer.echo(
            f"Invalid --naming-scheme {naming_scheme!r}; expected classic, paper-pipeline, or arxiv-ym.",
            err=True,
        )
        raise typer.Exit(code=2)

    return ConvertCliSettings(
        parser_mode=parser_mode,
        source_v=source_v,
        mode=mode,
        so=so,
        include_anchors=include_anchors if include_anchors is not None else s.output.include_anchors,
        linked_citations=linked_citations if linked_citations is not None else s.output.linked_citations,
        naming_scheme=naming_scheme if naming_scheme is not None else s.output_naming.naming_scheme,
        fetch_metadata=fetch_arxiv_metadata,
        no_progress=no_progress,
        concurrency=concurrency,
    )


def make_convert_params(
    input_text: str,
    *,
    parser_mode: str,
    output: str | None,
    source_v: str,
    short: str | None,
    no_images: bool,
    remove_refs: bool,
    remove_inline_citations: bool,
    mode: str,
    sections: str | None,
    section: list[str],
    include_tree: bool,
    emit_result_json: bool,
    so: str,
    emit_graph_csv: bool,
    no_cache: bool = False,
    download_pdf: bool = True,
    linked_citations: bool = False,
    force: bool = False,
    allow_stub: bool = False,
    dry_run: bool = False,
    include_anchors: bool | None = None,
    naming_scheme: str | None = None,
    fetch_metadata: bool = False,
    no_progress: bool = False,
    concurrency: int | None = None,
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
        remove_inline_citations=remove_inline_citations,
        section_filter_mode=mode,
        sections=sections,
        section=sec_list,
        include_tree=include_tree,
        emit_result_json=emit_result_json,
        structured_output=so,
        emit_graph_csv=emit_graph_csv,
        no_cache=no_cache,
        download_pdf=download_pdf,
        linked_citations=linked_citations,
        force=force,
        allow_stub=allow_stub,
        dry_run=dry_run,
        include_anchors=include_anchors,
        naming_scheme=naming_scheme,
        fetch_metadata=fetch_metadata,
        no_progress=no_progress,
        concurrency=concurrency,
    )

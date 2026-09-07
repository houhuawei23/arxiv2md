"""Convert command runner.

The four input modes share one handler shape: parse input -> prepare output
dir -> ingest -> finalize. A per-mode spec table drives the shared handler so
the four paths cannot drift.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arxiv2md_beta.cli.helpers import collect_sections
from arxiv2md_beta.cli.output_finalize import finalize_convert_output
from arxiv2md_beta.exceptions import UserInputError
from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.ingestion.local import ingest_local_archive
from arxiv2md_beta.ingestion.local_html import ingest_local_html
from arxiv2md_beta.output.layout import determine_output_dir
from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.query.parser import (
    is_local_archive_path,
    is_local_html_path,
    parse_arxiv_input,
    parse_local_archive,
    parse_local_html,
)
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.utils.arxiv_ids import strip_version
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.timing import async_timed_operation

logger = get_logger()


@dataclass(frozen=True)
class _ModeSpec:
    """Per-input-mode wiring for the shared convert handler."""

    parse: Callable[[str], Any]
    ingest: Callable[..., Awaitable[tuple[IngestionResult, dict]]]
    fallback_stem: Callable[[Any], str]
    pdf_fetch: Callable[[Any], tuple[str, str | None] | None]
    log_local_success: bool
    log_extra: Callable[[Any], None]


def _ingest_arxiv_latex(query, params: ConvertParams, sections, base_output_dir: Path):
    return ingest_paper(
        arxiv_id=query.arxiv_id,
        version=query.version,
        html_url=query.html_url,
        ar5iv_url=query.ar5iv_url,
        parser=params.parser,
        remove_refs=params.remove_refs,
        remove_inline_citations=params.remove_inline_citations,
        linked_citations=params.linked_citations,
        section_filter_mode=params.section_filter_mode,
        sections=sections,
        base_output_dir=base_output_dir,
        no_images=params.no_images,
        source=params.source,
        short=params.short,
        structured_output=params.structured_output,
        emit_graph_csv=params.emit_graph_csv,
        use_cache=not params.no_cache,
    )


def _ingest_arxiv_html(query, params: ConvertParams, sections, base_output_dir: Path):
    from arxiv2md_beta.ingestion.orchestrator import IngestionOrchestrator

    return IngestionOrchestrator(params).run()


def _ingest_local_html(query, params: ConvertParams, sections, base_output_dir: Path):
    return ingest_local_html(
        query=query,
        base_output_dir=base_output_dir,
        source=params.source,
        short=params.short,
        no_images=params.no_images,
        remove_refs=params.remove_refs,
        remove_inline_citations=params.remove_inline_citations,
        linked_citations=params.linked_citations,
        section_filter_mode=params.section_filter_mode,
        sections=sections,
        structured_output=params.structured_output,
        emit_graph_csv=params.emit_graph_csv,
    )


def _ingest_local_archive(query, params: ConvertParams, sections, base_output_dir: Path):
    return ingest_local_archive(
        query=query,
        base_output_dir=base_output_dir,
        source=params.source,
        short=params.short,
        no_images=params.no_images,
        remove_refs=params.remove_refs,
        remove_inline_citations=params.remove_inline_citations,
        linked_citations=params.linked_citations,
        section_filter_mode=params.section_filter_mode,
        sections=sections,
        structured_output=params.structured_output,
        emit_graph_csv=params.emit_graph_csv,
        use_cache=not params.no_cache,
    )


async def _process_with(
    spec: _ModeSpec,
    input_text: str,
    params: ConvertParams,
    label: str,
) -> Path:
    query = spec.parse(input_text)
    spec.log_extra(query)
    logger.info(f"Processing {label}")

    sections = collect_sections(params.sections, params.section)
    base_output_dir = determine_output_dir(params.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    result, metadata = await spec.ingest(query, params, sections, base_output_dir)

    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        fallback_md_stem=spec.fallback_stem(query),
        pdf_fetch=spec.pdf_fetch(query),
        log_local_success=spec.log_local_success,
    )


def _arxiv_pdf_fetch(query) -> tuple[str, str | None]:
    return (query.arxiv_id, query.version)


def _no_pdf_fetch(query) -> None:
    return None


def _stem_of(path_like) -> str:
    return Path(path_like.html_path if hasattr(path_like, "html_path") else path_like.archive_path).stem


_SPECS: dict[str, _ModeSpec] = {
    "arxiv": _ModeSpec(
        parse=parse_arxiv_input,
        ingest=lambda q, p, s, b: _ingest_arxiv_html(q, p, s, b),
        fallback_stem=lambda q: strip_version(q.arxiv_id),
        pdf_fetch=_arxiv_pdf_fetch,
        log_local_success=False,
        log_extra=lambda q: None,
    ),
    "arxiv-latex": _ModeSpec(
        parse=parse_arxiv_input,
        ingest=lambda q, p, s, b: _ingest_arxiv_latex(q, p, s, b),
        fallback_stem=lambda q: strip_version(q.arxiv_id),
        pdf_fetch=_arxiv_pdf_fetch,
        log_local_success=False,
        log_extra=lambda q: logger.info("Parser mode: latex"),
    ),
    "local-html": _ModeSpec(
        parse=parse_local_html,
        ingest=_ingest_local_html,
        fallback_stem=_stem_of,
        pdf_fetch=_no_pdf_fetch,
        log_local_success=True,
        log_extra=lambda q: logger.info(f"Processing local HTML file: {q.html_path}"),
    ),
    "local-archive": _ModeSpec(
        parse=parse_local_archive,
        ingest=_ingest_local_archive,
        fallback_stem=_stem_of,
        pdf_fetch=_no_pdf_fetch,
        log_local_success=True,
        log_extra=lambda q: logger.info(f"Processing local archive: {q.archive_path}\nArchive type: {q.archive_type}"),
    ),
}


async def run_convert_flow(params: ConvertParams) -> Path:
    """Route to local HTML, local archive, or arXiv ingestion; returns paper output directory."""
    async with async_timed_operation("run_convert_flow"):
        input_text = params.input_text.strip()
        if not input_text:
            raise UserInputError("INPUT cannot be empty")
        if is_local_html_path(input_text):  # Check HTML first (more specific)
            return await _process_with(_SPECS["local-html"], input_text, params, "local HTML file")
        if is_local_archive_path(input_text):
            return await _process_with(_SPECS["local-archive"], input_text, params, "local archive")
        # IR pipeline currently only supports HTML parsing for content;
        # route LaTeX parser requests to the LaTeX pipeline.
        if params.parser == "latex":
            return await _process_with(_SPECS["arxiv-latex"], input_text, params, "arXiv paper")
        return await _process_with(_SPECS["arxiv"], input_text, params, "arXiv paper (IR pipeline)")


def run_convert_sync(params: ConvertParams) -> None:
    """Run convert flow in a fresh event loop (Typer entry)."""
    from arxiv2md_beta.network.http import run_async

    run_async(run_convert_flow(params))

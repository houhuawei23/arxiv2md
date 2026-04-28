"""Convert command runner."""

from __future__ import annotations

import asyncio
import warnings
from pathlib import Path

from arxiv2md_beta.cli.helpers import collect_sections
from arxiv2md_beta.cli.output_finalize import finalize_convert_output
from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.ingestion.local import ingest_local_archive
from arxiv2md_beta.ingestion.local_html import ingest_local_html
from arxiv2md_beta.output.layout import determine_output_dir
from arxiv2md_beta.query.parser import (
    is_local_archive_path,
    is_local_html_path,
    parse_arxiv_input,
    parse_local_archive,
    parse_local_html,
)
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.metrics import async_timed_operation

logger = get_logger()


async def run_convert_flow(params: ConvertParams) -> Path:
    """Route to local HTML, local archive, or arXiv ingestion; returns paper output directory."""
    async with async_timed_operation("run_convert_flow"):
        input_text = params.input_text.strip()
        if not input_text:
            raise ValueError("INPUT cannot be empty")
        if is_local_html_path(input_text):  # Check HTML first (more specific)
            return await _process_local_html(params)
        if is_local_archive_path(input_text):
            return await _process_local_archive(params)
        if params.use_legacy:
            warnings.warn(
                "The --legacy pipeline is deprecated and will be removed in v1.0.0. "
                "The IR pipeline is now the default and provides full feature parity.",
                DeprecationWarning,
                stacklevel=2,
            )
            return await _process_arxiv_paper(params)
        return await _process_arxiv_paper_ir(params)


def run_convert_sync(params: ConvertParams) -> None:
    """Run convert flow in a fresh event loop (Typer entry)."""
    asyncio.run(run_convert_flow(params))


async def _process_arxiv_paper(params: ConvertParams) -> Path:
    """Process an arXiv paper (HTML or LaTeX parser)."""
    query = parse_arxiv_input(params.input_text.strip())

    sections = collect_sections(params.sections, params.section)

    base_output_dir = determine_output_dir(params.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing arXiv paper: {query.arxiv_id}")
    logger.info(f"Parser mode: {params.parser}")

    result, metadata = await ingest_paper(
        arxiv_id=query.arxiv_id,
        version=query.version,
        html_url=query.html_url,
        ar5iv_url=query.ar5iv_url,
        parser=params.parser,
        remove_refs=params.remove_refs,
        remove_toc=params.remove_toc,
        remove_inline_citations=params.remove_inline_citations,
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

    base_id = query.arxiv_id.split("v")[0] if "v" in query.arxiv_id else query.arxiv_id
    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        result_key=query.arxiv_id,
        arxiv_id_for_sidecar=str(metadata.get("arxiv_id") or query.arxiv_id),
        fallback_md_stem=base_id,
        pdf_fetch=(query.arxiv_id, query.version),
        log_local_success=False,
    )


async def _process_arxiv_paper_ir(params: ConvertParams) -> Path:
    """Process an arXiv paper using the IR pipeline with full feature parity."""
    from arxiv2md_beta.ingestion.orchestrator import IngestionOrchestrator
    from arxiv2md_beta.query.parser import parse_arxiv_input

    query = parse_arxiv_input(params.input_text.strip())
    base_output_dir = determine_output_dir(params.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing arXiv paper (IR pipeline): {query.arxiv_id}")
    logger.info(f"Parser mode: {params.parser}")

    orchestrator = IngestionOrchestrator(params)
    result, metadata = await orchestrator.run()

    base_id = query.arxiv_id.split("v")[0] if "v" in query.arxiv_id else query.arxiv_id
    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        result_key=query.arxiv_id,
        arxiv_id_for_sidecar=str(metadata.get("arxiv_id") or query.arxiv_id),
        fallback_md_stem=base_id,
        pdf_fetch=(query.arxiv_id, query.version),
        log_local_success=False,
    )


async def _process_local_html(params: ConvertParams) -> Path:
    """Process a local HTML file."""
    query = parse_local_html(params.input_text.strip())

    sections = collect_sections(params.sections, params.section)

    base_output_dir = determine_output_dir(params.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing local HTML file: {query.html_path}")

    result, metadata = await ingest_local_html(
        query=query,
        base_output_dir=base_output_dir,
        source=params.source,
        short=params.short,
        no_images=params.no_images,
        remove_refs=params.remove_refs,
        remove_toc=params.remove_toc,
        remove_inline_citations=params.remove_inline_citations,
        section_filter_mode=params.section_filter_mode,
        sections=sections,
        structured_output=params.structured_output,
        emit_graph_csv=params.emit_graph_csv,
    )

    rk = str(metadata.get("arxiv_id") or query.html_path.stem)
    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        result_key=query.html_path.stem,
        arxiv_id_for_sidecar=rk,
        fallback_md_stem=query.html_path.stem,
        pdf_fetch=None,
        log_local_success=True,
    )


async def _process_local_archive(params: ConvertParams) -> Path:
    """Process a local archive file (tar.gz, tgz, or zip)."""
    query = parse_local_archive(params.input_text.strip())

    sections = collect_sections(params.sections, params.section)

    base_output_dir = determine_output_dir(params.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing local archive: {query.archive_path}")
    logger.info(f"Archive type: {query.archive_type}")

    result, metadata = await ingest_local_archive(
        query=query,
        base_output_dir=base_output_dir,
        source=params.source,
        short=params.short,
        no_images=params.no_images,
        remove_refs=params.remove_refs,
        remove_toc=params.remove_toc,
        remove_inline_citations=params.remove_inline_citations,
        section_filter_mode=params.section_filter_mode,
        sections=sections,
        structured_output=params.structured_output,
        emit_graph_csv=params.emit_graph_csv,
    )

    rk = str(metadata.get("arxiv_id") or query.archive_path.stem)
    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        result_key=query.archive_path.stem,
        arxiv_id_for_sidecar=rk,
        fallback_md_stem=query.archive_path.stem,
        pdf_fetch=None,
        log_local_success=True,
    )

"""Async workflows invoked by the Typer CLI (convert / images / batch)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from arxiv2md_beta.cli.helpers import collect_sections
from arxiv2md_beta.cli.output_finalize import finalize_convert_output, format_output
from arxiv2md_beta.cli.params import ConvertParams, ImagesParams, PaperYmlParams
from arxiv2md_beta.images.extract import extract_arxiv_images
from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.ingestion.local import ingest_local_archive
from arxiv2md_beta.network.arxiv_api import fetch_arxiv_metadata
from arxiv2md_beta.output.layout import determine_output_dir
from arxiv2md_beta.output.metadata import arxiv_id_from_paper_yml, write_paper_yml_file
from arxiv2md_beta.output.paper_yml_path import resolve_paper_yml_output_path
from arxiv2md_beta.query.parser import (
    is_local_archive_path,
    parse_arxiv_input,
    parse_local_archive,
)
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()

# Re-export for ``app.py`` and external callers
__all__ = [
    "ConvertParams",
    "ImagesParams",
    "PaperYmlParams",
    "format_output",
    "run_batch_flow",
    "run_convert_flow",
    "run_batch_sync",
    "run_convert_sync",
    "run_images_flow",
    "run_images_sync",
    "run_paper_yml_flow",
    "run_paper_yml_sync",
]


async def run_convert_flow(params: ConvertParams) -> Path:
    """Route to local archive or arXiv ingestion; returns paper output directory."""
    input_text = params.input_text.strip()
    if not input_text:
        raise ValueError("INPUT cannot be empty")
    if is_local_archive_path(input_text):
        return await _process_local_archive(params)
    return await _process_arxiv_paper(params)


async def run_images_flow(params: ImagesParams) -> None:
    """Download TeX source and write processed images only."""
    s = get_settings()
    raw = params.arxiv_input.strip()
    if not raw:
        raise ValueError("arxiv input cannot be empty")
    query = parse_arxiv_input(raw)
    base_output_dir = determine_output_dir(params.output, settings=s)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Images-only mode: arXiv {query.arxiv_id}")
    logger.info(f"Output root: {base_output_dir}")
    logger.info(f"Images subdirectory: {params.images_subdir}")

    processed = await extract_arxiv_images(
        arxiv_id=query.arxiv_id,
        version=query.version,
        output_dir=base_output_dir,
        images_subdir=params.images_subdir,
        use_tex_cache=not params.no_tex_cache,
    )

    n = len(processed.image_map)
    logger.info(f"Processed {n} image(s) -> {processed.images_dir}")
    print(f"Images directory: {processed.images_dir}")
    print(f"Image count: {n}")
    if n:
        for i, rel in sorted(processed.image_map.items()):
            print(f"  [{i}] {rel}")


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


async def run_paper_yml_flow(params: PaperYmlParams) -> Path:
    """Fetch arXiv metadata and write ``paper.yml`` (refresh existing or new path)."""
    if params.update_path is not None:
        path = Path(params.update_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"paper.yml not found: {path}")
        aid = arxiv_id_from_paper_yml(path)
        logger.info(f"paper-yml --update: read arXiv id {aid!r} from {path}")
        meta = await fetch_arxiv_metadata(aid)
        write_paper_yml_file(meta, path)
        print(str(path.resolve()))
        return path

    raw = (params.arxiv_input or "").strip()
    if not raw:
        raise ValueError("Provide ARXIV (id or URL) or use --update PATH")
    out = (params.output or "").strip()
    if not out:
        raise ValueError("Provide --output /path/to/paper.yml when not using --update")

    query = parse_arxiv_input(raw)
    logger.info(f"paper-yml: fetching metadata for {query.arxiv_id}")
    meta = await fetch_arxiv_metadata(query.arxiv_id)
    out_path = Path(out).expanduser()
    primary = out_path
    if primary.is_dir():
        primary = primary / "paper.yml"
    elif primary.suffix.lower() not in (".yml", ".yaml"):
        primary = primary / "paper.yml"
    primary = primary.resolve()
    dest = resolve_paper_yml_output_path(out_path, force=params.force)
    if not params.force and primary.exists() and dest.resolve() != primary:
        logger.info(
            f"Primary output {primary} exists; writing to {dest} (use --force to overwrite)"
        )
    write_paper_yml_file(meta, dest)
    print(str(dest.resolve()))
    return dest


def run_paper_yml_sync(params: PaperYmlParams) -> Path:
    """Run paper-yml flow in a fresh event loop."""
    return asyncio.run(run_paper_yml_flow(params))


def run_convert_sync(params: ConvertParams) -> None:
    """Run convert flow in a fresh event loop (Typer entry)."""
    asyncio.run(run_convert_flow(params))


def run_images_sync(params: ImagesParams) -> None:
    """Run images-only flow in a fresh event loop (Typer entry)."""
    asyncio.run(run_images_flow(params))


def run_batch_sync(
    lines: list[str],
    *,
    params_template: ConvertParams,
    max_concurrency: int,
    continue_on_error: bool,
    delay_seconds: float,
) -> list[tuple[str, str | None, str | None]]:
    """Run batch convert in a fresh event loop."""
    return asyncio.run(
        run_batch_flow(
            lines,
            params_template=params_template,
            max_concurrency=max_concurrency,
            continue_on_error=continue_on_error,
            delay_seconds=delay_seconds,
        )
    )


def _merge_convert_params(template: ConvertParams, input_text: str) -> ConvertParams:
    """Build params for one batch line from a template."""
    p = template
    return ConvertParams(
        input_text=input_text,
        parser=p.parser,
        output=p.output,
        source=p.source,
        short=p.short,
        no_images=p.no_images,
        remove_refs=p.remove_refs,
        remove_toc=p.remove_toc,
        remove_inline_citations=p.remove_inline_citations,
        section_filter_mode=p.section_filter_mode,
        sections=p.sections,
        section=p.section,
        include_tree=p.include_tree,
        emit_result_json=p.emit_result_json,
        structured_output=p.structured_output,
        emit_graph_csv=p.emit_graph_csv,
    )


async def run_batch_flow(
    lines: list[str],
    *,
    params_template: ConvertParams,
    max_concurrency: int,
    continue_on_error: bool,
    delay_seconds: float,
) -> list[tuple[str, str | None, str | None]]:
    """Run ``convert`` for each non-empty line.

    Returns tuples ``(input_line, error_or_none, paper_output_dir_or_none)``.
    Comment lines and blank lines yield ``(line, None, None)``.
    """
    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def run_one(line: str, index: int) -> tuple[str, str | None, str | None]:
        if delay_seconds > 0 and index > 0:
            await asyncio.sleep(delay_seconds)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return (line, None, None)
        merged = _merge_convert_params(params_template, stripped)
        async with sem:
            try:
                out = await run_convert_flow(merged)
                return (stripped, None, str(out.resolve()))
            except Exception as exc:
                return (stripped, str(exc), None)

    if continue_on_error:
        tasks = [run_one(line, i) for i, line in enumerate(lines)]
        return list(await asyncio.gather(*tasks))

    results: list[tuple[str, str | None, str | None]] = []
    for i, line in enumerate(lines):
        item = await run_one(line, i)
        results.append(item)
        err = item[1]
        if err is not None:
            break
    return results

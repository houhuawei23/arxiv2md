"""Convert command runner.

The four input modes share one handler shape: parse input -> prepare output
dir -> ingest -> finalize. A per-mode spec table drives the shared handler so
the four paths cannot drift.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arxiv2md_beta.cli.output_finalize import finalize_convert_output
from arxiv2md_beta.exceptions import UserInputError
from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.ingestion.local import ingest_local_archive
from arxiv2md_beta.ingestion.local_html import ingest_local_html
from arxiv2md_beta.output.layout import (
    determine_output_dir,
    find_completed_output_dir,
    identity_for_arxiv_id,
    identity_for_local_path,
)
from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.query.parser import (
    is_local_archive_path,
    is_local_html_path,
    parse_arxiv_input,
    parse_local_archive,
    parse_local_html,
)
from arxiv2md_beta.query.sections import collect_sections
from arxiv2md_beta.schemas import IngestionMetadata, IngestionResult
from arxiv2md_beta.utils.arxiv_ids import strip_version
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.timing import async_timed_operation

logger = get_logger()


@dataclass(frozen=True)
class _ModeSpec:
    """Per-input-mode wiring for the shared convert handler."""

    parse: Callable[[str], Any]
    ingest: Callable[[ConvertParams, Any, list[str], Path], Awaitable[tuple[IngestionResult, IngestionMetadata]]]
    fallback_stem: Callable[[Any], str]
    pdf_fetch: Callable[[Any], tuple[str, str | None] | None]
    log_local_success: bool
    log_extra: Callable[[Any], None]
    identity: Callable[[Any], str]


def _ingest_arxiv_html(params: ConvertParams, query, sections, base_output_dir: Path):
    """Remote-HTML ingestion, fed the context ``_process_with`` already derived.

    The orchestrator accepts the precomputed query / section selection /
    output dir so the shared front-half runs once, not twice.
    """
    from arxiv2md_beta.ingestion.orchestrator import IngestionOrchestrator

    return IngestionOrchestrator(
        params,
        query=query,
        selected_sections=sections,
        base_output_dir=base_output_dir,
    ).run()


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
    base_output_dir = determine_output_dir(params.output, params.settings)

    # Idempotency / resume: skip re-ingestion when this identity already has a
    # completed output (matching .arxiv2md-paper marker + non-empty Markdown).
    # --force bypasses the check. The identity must match the value each
    # ingestion path writes into the marker (see layout.identity_for_*).
    if not params.force:
        if params.completed_index is not None:
            # Batch supplied a shared one-scan index (audit5 X1): no rescan.
            done = params.completed_index.lookup(spec.identity(query))
        else:
            # Offloaded: the scan walks every sibling output directory (sync IO)
            # and would otherwise stall the loop for all batch workers.
            done = await asyncio.to_thread(find_completed_output_dir, base_output_dir, spec.identity(query))
    else:
        done = None

    # --dry-run (audit5 S8 F1): report the plan and stop. Everything the plan
    # needs is known before ingestion; the exact paper directory name is not
    # (it depends on title/date metadata only a real run fetches), so the base
    # dir is reported with that caveat. Nothing is created or downloaded.
    if params.dry_run:
        _echo_dry_run_plan(params=params, label=label, base_output_dir=base_output_dir, done=done)
        return base_output_dir

    base_output_dir.mkdir(parents=True, exist_ok=True)

    if done is not None:
        logger.info(f"Skip (already converted): {done}; use --force to re-convert")
        return done

    result, metadata = await spec.ingest(params, query, sections, base_output_dir)

    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        fallback_md_stem=spec.fallback_stem(query),
        pdf_fetch=spec.pdf_fetch(query),
        log_local_success=spec.log_local_success,
    )


def _echo_dry_run_plan(
    *,
    params: ConvertParams,
    label: str,
    base_output_dir: Path,
    done: Path | None,
) -> None:
    """Print the --dry-run plan (stdout, so scripts can capture it)."""
    import typer

    from arxiv2md_beta.settings import get_settings

    typer.echo(f"[dry-run] input: {params.input_text}")
    typer.echo(f"[dry-run] mode: {label}")
    if done is not None:
        typer.echo(f"[dry-run] verdict: would skip (already converted): {done}")
        return
    if params.force:
        typer.echo(
            "[dry-run] verdict: --force bypasses the idempotency check; a completed output would be re-converted"
        )
    scheme = params.naming_scheme or get_settings().output_naming.naming_scheme
    typer.echo(
        f"[dry-run] verdict: would convert into {base_output_dir}/ "
        f"(naming scheme: {scheme}; the final directory name is fixed after metadata fetch)"
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
        ingest=_ingest_arxiv_html,
        fallback_stem=lambda q: strip_version(q.arxiv_id),
        pdf_fetch=_arxiv_pdf_fetch,
        log_local_success=False,
        log_extra=lambda q: None,
        identity=lambda q: identity_for_arxiv_id(q.arxiv_id),
    ),
    "arxiv-latex": _ModeSpec(
        parse=parse_arxiv_input,
        ingest=ingest_paper,
        fallback_stem=lambda q: strip_version(q.arxiv_id),
        pdf_fetch=_arxiv_pdf_fetch,
        log_local_success=False,
        log_extra=lambda q: logger.info("Parser mode: latex"),
        identity=lambda q: identity_for_arxiv_id(q.arxiv_id),
    ),
    "local-html": _ModeSpec(
        parse=parse_local_html,
        ingest=ingest_local_html,
        fallback_stem=_stem_of,
        pdf_fetch=_no_pdf_fetch,
        log_local_success=True,
        log_extra=lambda q: logger.info(f"Processing local HTML file: {q.html_path}"),
        identity=lambda q: identity_for_local_path(q.html_path),
    ),
    "local-archive": _ModeSpec(
        parse=parse_local_archive,
        ingest=ingest_local_archive,
        fallback_stem=_stem_of,
        pdf_fetch=_no_pdf_fetch,
        log_local_success=True,
        log_extra=lambda q: logger.info(f"Processing local archive: {q.archive_path}\nArchive type: {q.archive_type}"),
        identity=lambda q: identity_for_local_path(q.archive_path),
    ),
}


def _select_spec(input_text: str, params: ConvertParams) -> tuple[_ModeSpec, str]:
    """Pick the mode spec for ``input_text``; returns ``(spec, label)``."""
    if is_local_html_path(input_text):  # Check HTML first (more specific)
        return _SPECS["local-html"], "local HTML file"
    if is_local_archive_path(input_text):
        return _SPECS["local-archive"], "local archive"
    # IR pipeline currently only supports HTML parsing for content;
    # route LaTeX parser requests to the LaTeX pipeline.
    if params.parser == "latex":
        return _SPECS["arxiv-latex"], "arXiv paper"
    return _SPECS["arxiv"], "arXiv paper (IR pipeline)"


async def _pdf_fallback_flow(query, params: ConvertParams, reason: Exception) -> Path:
    """TeX pipeline failed: download the arXiv PDF and point at mineru.

    Business shape (dir + PDF + manifest) lives in
    :func:`arxiv2md_beta.ingestion.persist.pdf_fallback_output`; this CLI
    wrapper only talks to the user and raises the typed partial-success
    signal :class:`PdfFallbackCompleted` (exit 7) so scripts can tell
    "PDF ready, needs external parsing" from a plain failure.
    """
    import typer

    from arxiv2md_beta.exceptions import PdfFallbackCompleted
    from arxiv2md_beta.ingestion.persist import pdf_fallback_output

    paper_output_dir = await pdf_fallback_output(query=query, params=params)
    pdf_path = paper_output_dir / f"{paper_output_dir.name}.pdf"

    typer.echo(f"TeX source unavailable ({reason}).", err=True)
    typer.echo(f"PDF downloaded to: {pdf_path}", err=True)
    typer.echo(f'Parse the PDF with: mineru-parse parse "{pdf_path}"', err=True)
    raise PdfFallbackCompleted(
        f"TeX conversion failed; PDF fallback completed for {query.arxiv_id} (no Markdown written): {paper_output_dir}",
        paper_output_dir=str(paper_output_dir),
    )


def find_completed_for_input(input_text: str, params: ConvertParams) -> Path | None:
    """Idempotency pre-check for batch: completed output dir for this input, if any.

    Shares the mode-spec selection and ``find_completed_output_dir`` logic with
    the single-convert runner so the two cannot drift. Callers that already
    pass ``--force`` get None unconditionally. Best-effort by design: any
    parse/lookup failure returns None so the real conversion path surfaces
    the proper error instead.
    """
    try:
        if params.force:
            return None
        spec, _label = _select_spec(input_text, params)
        query = spec.parse(input_text)
        if params.completed_index is not None:
            return params.completed_index.lookup(spec.identity(query))
        base_output_dir = determine_output_dir(params.output, params.settings)
        return find_completed_output_dir(base_output_dir, spec.identity(query))
    except Exception as e:
        # Best-effort pre-check by design — but a permission/disk error must
        # not vanish: log it, then fall through to the real conversion path
        # which surfaces proper errors.
        logger.warning(f"Idempotency pre-check failed (converting anyway): {e}")
        return None


async def run_convert_flow(params: ConvertParams) -> Path:
    """Route to local HTML, local archive, or arXiv ingestion; returns paper output directory.

    Embeddable: ``params.settings`` (audit5 S8 F7) injects a settings object
    for the duration of the flow via a ContextVar, so everything reading
    ``get_settings()`` — in this task, in child tasks and in
    ``asyncio.to_thread`` workers — sees it while the process-global
    singleton stays untouched. Concurrent flows with different settings are
    isolated.
    """
    from arxiv2md_beta.latex.tex_source import (
        ArchiveExtractionError,
        ImageExtractionError,
        TexSourceNotFoundError,
    )
    from arxiv2md_beta.settings import settings_context

    async def _impl() -> Path:
        async with async_timed_operation("run_convert_flow"):
            input_text = params.input_text.strip()
            if not input_text:
                raise UserInputError("INPUT cannot be empty")
            spec, label = _select_spec(input_text, params)
            if spec is _SPECS["arxiv-latex"]:
                try:
                    return await _process_with(spec, input_text, params, label)
                except (TexSourceNotFoundError, ImageExtractionError, ArchiveExtractionError) as exc:
                    # Broken/corrupt TeX tarball (playbook: "Invalid tar file") —
                    # degrade to PDF download + external parser instead of dying.
                    query = parse_arxiv_input(input_text)
                    return await _pdf_fallback_flow(query, params, exc)
            return await _process_with(spec, input_text, params, label)

    if params.settings is not None:
        with settings_context(params.settings):
            return await _impl()
    return await _impl()


def run_convert_sync(params: ConvertParams) -> None:
    """Run convert flow in a fresh event loop (Typer entry)."""
    from arxiv2md_beta.network.http import run_async

    run_async(run_convert_flow(params))

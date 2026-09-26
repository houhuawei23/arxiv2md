"""Persistence of ingestion results: naming, quality gate, writes, PDF, manifest.

This is the business half of output finalization, shared by every CLI entry
point (convert / batch / images callers). The CLI-facing presentation half
(result JSON line, summary and performance printing) lives in
``cli/output_finalize.py`` — ingestion produces the artifacts, the CLI layer
talks to the user.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from arxiv2md_beta.exceptions import NetworkError
from arxiv2md_beta.network.fetch import fetch_arxiv_pdf
from arxiv2md_beta.output.layout import FIXED_INTERNAL_SCHEMES, build_output_basename, create_paper_output_dir
from arxiv2md_beta.output.manifest import build_paper_manifest, write_paper_manifest
from arxiv2md_beta.output.markdown_utils import count_tokens
from arxiv2md_beta.output.quality_gate import ensure_not_stub, is_stub
from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.atomic_io import atomic_write_text
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()


async def write_split_markdown_sidecars(
    paper_output_dir: Path,
    output_filename: str,
    result: IngestionResult,
    naming_scheme: str = "arxiv-ym",
) -> None:
    """Write ``-References.md`` and ``-Appendix.md`` when HTML ingestion produced a split."""
    has_ref = bool(result.content_references and result.content_references.strip())
    has_app = bool(result.content_appendix and result.content_appendix.strip())
    if not has_ref and not has_app:
        return
    if naming_scheme in FIXED_INTERNAL_SCHEMES:
        ref_path = paper_output_dir / "References.md"
        app_path = paper_output_dir / "Appendix.md"
    else:
        stem = Path(output_filename).stem
        ref_path = paper_output_dir / f"{stem}-References.md"
        app_path = paper_output_dir / f"{stem}-Appendix.md"
    if has_ref:
        await atomic_write_text(ref_path, result.content_references or "", encoding="utf-8")
        logger.info(f"References written to: {ref_path}")
    elif ref_path.exists():
        # missing_ok: a concurrent run may have unlinked it between the
        # exists() check and here.
        ref_path.unlink(missing_ok=True)
        logger.info(f"References removed (empty split): {ref_path}")
    if has_app:
        await atomic_write_text(app_path, result.content_appendix or "", encoding="utf-8")
        logger.info(f"Appendix written to: {app_path}")
    elif app_path.exists():
        app_path.unlink(missing_ok=True)
        logger.info(f"Appendix removed (empty split): {app_path}")


def format_output(summary: str, tree: str, content: str, *, include_tree: bool) -> str:
    """Format final Markdown body (optional section tree)."""
    if include_tree:
        return f"{summary}\n\n{tree}\n\n{content}".strip()
    return f"{summary}\n\n{content}".strip()


def _manifest_stub_status(
    output_text: str,
    *,
    cli_allow_stub: bool,
    settings: Any,
    token_count: int | None = None,
) -> str:
    """Manifest status for stub gating, mirroring :func:`ensure_not_stub` exactly.

    The gate bypasses on ``cli flag OR settings.output.allow_stub``; the
    manifest used to consider only the CLI flag, so a settings-level stub
    pass-through landed on disk but was recorded as ``ok`` — unauditable
    (audit5 R-10).
    """
    allowed = cli_allow_stub or settings.output.allow_stub
    return "allowed_stub" if allowed and is_stub(output_text, settings=settings, token_count=token_count) else "ok"


def resolve_paper_output_dir(
    metadata: dict[str, Any],
    base_output_dir: Path,
    *,
    source: str,
    short: str | None,
) -> Path:
    """Normalize ``paper_output_dir`` from metadata or create under ``base_output_dir``."""
    submission_date = metadata.get("submission_date")
    title = metadata.get("title")
    paper_output_dir = metadata.get("paper_output_dir")
    if paper_output_dir is None:
        return create_paper_output_dir(
            base_output_dir,
            submission_date,
            title,
            source=source,
            short=short,
        )
    if isinstance(paper_output_dir, str):
        return Path(paper_output_dir)
    return paper_output_dir  # type: ignore[return-value]


def _resolve_output_filename(
    *,
    submission_date: str | None,
    title: str | None,
    fallback_md_stem: str,
    source: str,
    short: str | None,
    naming_scheme: str,
) -> str:
    """Main Markdown filename per the configured naming scheme."""
    s = get_settings()
    if naming_scheme in FIXED_INTERNAL_SCHEMES:
        return "paper.md"
    if submission_date and title:
        basename = build_output_basename(
            submission_date,
            title,
            source=source,
            short=short,
            max_basename_length=s.output_naming.max_md_basename_length,
            settings=s,
        )
        return f"{basename}.md"
    return f"{fallback_md_stem}.md"


def _pdf_filename_for(paper_output_dir: Path, output_filename: str, naming_scheme: str) -> str:
    if naming_scheme in FIXED_INTERNAL_SCHEMES:
        return f"{paper_output_dir.name}.pdf"
    return Path(output_filename).with_suffix(".pdf").name


async def _download_pdf_to(pdf_path: Path, arxiv_id: str, version: str | None, params: ConvertParams) -> bool:
    try:
        await fetch_arxiv_pdf(arxiv_id, pdf_path, version, use_cache=not params.no_cache)
        logger.info(f"PDF downloaded to: {pdf_path}")
        return True
    except (httpx.RequestError, httpx.HTTPStatusError, OSError, NetworkError) as e:
        logger.warning(f"Failed to download PDF: {e}")
        return False


async def _finalize_pdf_only_output(
    *,
    paper_output_dir: Path,
    metadata: dict[str, Any],
    params: ConvertParams,
    fallback_md_stem: str,
    pdf_fetch: tuple[str, str | None] | None,
) -> Path:
    """Finalize a PDF-only paper: download the PDF, record the manifest.

    No ``paper.md`` is written — the orchestrator's stub note can never pass
    the quality gate, so the gate is intentionally skipped here while its
    "no half-written stub" intent is preserved by simply not writing Markdown.
    The directory ends up with paper.yml (written by the orchestrator), the
    PDF when the download succeeds, and a ``status="pdf_only"`` manifest.
    """
    s = get_settings()
    title = metadata.get("title")
    submission_date = metadata.get("submission_date")
    naming_scheme = params.naming_scheme or s.output_naming.naming_scheme
    pdf_path = paper_output_dir / _pdf_filename_for(paper_output_dir, fallback_md_stem, naming_scheme)

    downloaded = False
    if pdf_fetch is not None and params.download_pdf:
        arxiv_id, version = pdf_fetch
        downloaded = await _download_pdf_to(pdf_path, arxiv_id, version, params)

    manifest = build_paper_manifest(
        arxiv_id=metadata.get("arxiv_id"),
        title=title,
        submission_date=submission_date,
        source_url=metadata.get("urls", {}).get("abstract") if isinstance(metadata.get("urls"), dict) else None,
        # Only record the path when the file is actually there — a phantom
        # path would break batch-level consistency checks.
        pdf_path=str(pdf_path) if downloaded else None,
        markdown_file=None,
        output_text="",
        parser=params.parser,
        naming_scheme=naming_scheme,
        duration_seconds=None,
        status="pdf_only",
    )
    write_paper_manifest(paper_output_dir, manifest)
    return paper_output_dir


async def persist_ingestion_output(
    *,
    result: IngestionResult,
    metadata: dict[str, Any],
    params: ConvertParams,
    base_output_dir: Path,
    fallback_md_stem: str,
    pdf_fetch: tuple[str, str | None] | None = None,
    log_local_success: bool = False,
) -> Path:
    """Quality-gate, name, write, and manifest an ingestion result.

    Business tail shared by the convert/batch/paper-yml runners: resolve the
    paper directory, pass the stub gate, write the main Markdown + split
    sidecars atomically, download the PDF concurrently, and record the
    manifest. Returns the resolved paper output directory.
    """
    s = get_settings()
    paper_output_dir = resolve_paper_output_dir(
        metadata,
        base_output_dir,
        source=params.source,
        short=params.short,
    )
    logger.info(f"Output directory: {paper_output_dir}")

    submission_date = metadata.get("submission_date")
    title = metadata.get("title")
    naming_scheme = params.naming_scheme or s.output_naming.naming_scheme

    # Markdown content/refs/appendix are already finalized at emission time
    # (emit_split_markdown applies the single format+clean pass), so no
    # further postprocessing is needed here.

    if metadata.get("pdf_only"):
        # pdf_only products are just the PDF + paper.yml.
        return await _finalize_pdf_only_output(
            paper_output_dir=paper_output_dir,
            metadata=metadata,
            params=params,
            fallback_md_stem=fallback_md_stem,
            pdf_fetch=pdf_fetch,
        )

    output_text = format_output(
        result.summary,
        result.sections_tree,
        result.content,
        include_tree=params.include_tree,
    )

    # Encode the full text once and pass the count to every consumer
    # (gate + manifest; audit5 X3) — the gate used to trigger up to three
    # full-text tiktoken encodes per paper.
    token_count = count_tokens(output_text)

    # Quality gate before any disk writes (including the PDF download task
    # below) so a rejected stub leaves no half-written artifacts behind.
    ensure_not_stub(output_text, settings=s, allow_stub=params.allow_stub, token_count=token_count)

    output_filename = _resolve_output_filename(
        submission_date=submission_date,
        title=title,
        fallback_md_stem=fallback_md_stem,
        source=params.source,
        short=params.short,
        naming_scheme=naming_scheme,
    )
    output_path = paper_output_dir / output_filename

    # Citation links are kept as #ref-N format (no file prefix)
    # If References are split, links will point to anchors in the separate file

    # PDF download is independent of the markdown writes — run concurrently
    # and re-raise-safely below before the summary.
    pdf_task: asyncio.Task | None = None
    pdf_path: Path | None = None
    if pdf_fetch is not None and params.download_pdf:
        arxiv_id, version = pdf_fetch
        pdf_path = paper_output_dir / _pdf_filename_for(paper_output_dir, output_filename, naming_scheme)
        pdf_task = asyncio.create_task(_download_pdf_to(pdf_path, arxiv_id, version, params))

    try:
        # Atomic write: a crash mid-write must not leave a torn paper.md that
        # the idempotency check would later adopt as a completed conversion.
        await atomic_write_text(output_path, output_text, encoding="utf-8")
        logger.info(f"Output written to: {output_path}")
        await write_split_markdown_sidecars(paper_output_dir, output_filename, result, naming_scheme=naming_scheme)
    finally:
        # The task must always be reaped, even when a disk write raises —
        # an unretrieved task otherwise leaks its exception into the loop.
        if pdf_task is not None:
            try:
                pdf_downloaded = await pdf_task
            except asyncio.CancelledError:
                # Flow cancelled while the download was in flight: reap the
                # task without letting CancelledError shadow the original
                # exception from the write path above.
                pdf_task.cancel()
                pdf_downloaded = False

    # Self-describing artifact: id/title/size/timing for downstream consistency
    # checks (batch manifests aggregate these).
    manifest_status = _manifest_stub_status(
        output_text, cli_allow_stub=params.allow_stub, settings=s, token_count=token_count
    )
    manifest = build_paper_manifest(
        arxiv_id=metadata.get("arxiv_id"),
        title=title,
        submission_date=submission_date,
        source_url=metadata.get("urls", {}).get("abstract") if isinstance(metadata.get("urls"), dict) else None,
        # Only a path that actually landed on disk goes into the manifest —
        # a phantom path breaks batch-level consistency checks downstream.
        pdf_path=str(pdf_path) if pdf_task is not None and pdf_downloaded else None,
        markdown_file=output_filename,
        output_text=output_text,
        parser=params.parser,
        naming_scheme=naming_scheme,
        duration_seconds=(result.performance or {}).get("total_seconds"),
        status=manifest_status,
        token_count=token_count,
    )
    write_paper_manifest(paper_output_dir, manifest)

    if log_local_success:
        logger.info("Local archive processed successfully (no PDF download for local archives)")

    return paper_output_dir

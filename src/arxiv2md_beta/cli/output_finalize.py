"""Shared post-ingestion steps: sidecars, main Markdown, optional PDF download."""

from __future__ import annotations

import json
import re
from pathlib import Path
from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.network.fetch import fetch_arxiv_pdf
from arxiv2md_beta.output.layout import build_output_basename
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()


def write_split_markdown_sidecars(
    paper_output_dir: Path,
    output_filename: str,
    result: IngestionResult,
) -> None:
    """Write ``-References.md`` and ``-Appendix.md`` when HTML ingestion produced a split."""
    has_ref = bool(result.content_references and result.content_references.strip())
    has_app = bool(result.content_appendix and result.content_appendix.strip())
    if not has_ref and not has_app:
        return
    stem = Path(output_filename).stem
    ref_path = paper_output_dir / f"{stem}-References.md"
    app_path = paper_output_dir / f"{stem}-Appendix.md"
    if has_ref:
        ref_path.write_text(result.content_references or "", encoding="utf-8")
        logger.info(f"References written to: {ref_path}")
    elif ref_path.exists():
        ref_path.unlink()
        logger.info(f"References removed (empty split): {ref_path}")
    if has_app:
        app_path.write_text(result.content_appendix or "", encoding="utf-8")
        logger.info(f"Appendix written to: {app_path}")
    elif app_path.exists():
        app_path.unlink()
        logger.info(f"Appendix removed (empty split): {app_path}")


def emit_result_json_line(
    paper_output_dir: Path,
    *,
    params: ConvertParams,
    structured: dict[str, object] | None = None,
) -> None:
    """单行机器可读结果，供父进程脚本解析（``ARXIV2MD_RESULT_JSON=...``）。"""
    if not params.emit_result_json:
        return
    payload: dict[str, object] = {"paper_output_dir": str(paper_output_dir.resolve())}
    if structured and structured.get("paths"):
        payload["schema_version"] = structured.get("schema_version")
        payload["structured_paths"] = structured.get("paths")
    line = f"ARXIV2MD_RESULT_JSON={json.dumps(payload, ensure_ascii=False)}"
    print(line, flush=True)


def _result_json_filename_key(arxiv_id: str) -> str:
    """用于结果侧车文件名：新式 ID 去掉 ``vN`` 后缀，与流水线侧查找一致。"""
    s = arxiv_id.strip()
    m = re.match(r"^(\d{4}\.\d{4,5})(v\d+)?$", s)
    if m:
        return m.group(1)
    return s.replace("/", "_")


def write_result_json_sidecar(
    base_output_dir: Path,
    paper_output_dir: Path,
    *,
    result_key: str,
    arxiv_id: str | None = None,
    structured: dict[str, object] | None = None,
) -> None:
    """在输出根目录写入 ``.arxiv2md-result-{key}.json``。"""
    payload: dict[str, object] = {
        "paper_output_dir": str(paper_output_dir.resolve()),
        "result_key": result_key,
    }
    if arxiv_id is not None:
        payload["arxiv_id"] = arxiv_id
    if structured and structured.get("paths"):
        payload["schema_version"] = structured.get("schema_version")
        payload["structured_paths"] = structured.get("paths")
    name = f".arxiv2md-result-{_result_json_filename_key(result_key)}.json"
    path = base_output_dir / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
    )


def format_output(summary: str, tree: str, content: str, *, include_tree: bool) -> str:
    """Format final Markdown body (optional section tree)."""
    if include_tree:
        return f"{summary}\n\n{tree}\n\n{content}".strip()
    return f"{summary}\n\n{content}".strip()


def resolve_paper_output_dir(
    metadata: dict[str, str | list[str] | None],
    base_output_dir: Path,
    *,
    source: str,
    short: str | None,
) -> Path:
    """Normalize ``paper_output_dir`` from metadata or create under ``base_output_dir``."""
    from arxiv2md_beta.output.layout import create_paper_output_dir

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


async def finalize_convert_output(
    *,
    result: IngestionResult,
    metadata: dict[str, str | list[str] | None],
    params: ConvertParams,
    base_output_dir: Path,
    result_key: str,
    arxiv_id_for_sidecar: str | None,
    fallback_md_stem: str,
    pdf_fetch: tuple[str, str | None] | None = None,
    log_local_success: bool = False,
) -> Path:
    """Write JSON sidecars, main Markdown, split sidecars; optionally fetch PDF (arXiv).

    Returns the resolved paper output directory.
    """
    s = get_settings()
    paper_output_dir = resolve_paper_output_dir(
        metadata,
        base_output_dir,
        source=params.source,
        short=params.short,
    )
    logger.info(f"Output directory: {paper_output_dir}")

    structured = metadata.get("structured_export")
    if not isinstance(structured, dict):
        structured = None

    emit_result_json_line(paper_output_dir, params=params, structured=structured)
    try:
        write_result_json_sidecar(
            base_output_dir,
            paper_output_dir,
            result_key=result_key,
            arxiv_id=arxiv_id_for_sidecar,
            structured=structured,
        )
    except OSError as e:
        logger.warning(f"Could not write arxiv2md result sidecar: {e}")

    submission_date = metadata.get("submission_date")
    title = metadata.get("title")
    output_text = format_output(
        result.summary,
        result.sections_tree,
        result.content,
        include_tree=params.include_tree,
    )

    if submission_date and title:
        basename = build_output_basename(
            submission_date,
            title,
            source=params.source,
            short=params.short,
            max_basename_length=s.output_naming.max_md_basename_length,
            settings=s,
        )
        output_filename = f"{basename}.md"
    else:
        output_filename = f"{fallback_md_stem}.md"

    output_path = paper_output_dir / output_filename
    output_path.write_text(output_text, encoding="utf-8")
    logger.info(f"Output written to: {output_path}")
    write_split_markdown_sidecars(paper_output_dir, output_filename, result)

    if pdf_fetch is not None:
        arxiv_id, version = pdf_fetch
        try:
            pdf_filename = output_filename.replace(".md", ".pdf")
            pdf_path = paper_output_dir / pdf_filename
            await fetch_arxiv_pdf(arxiv_id, pdf_path, version)
            logger.info(f"PDF downloaded to: {pdf_path}")
        except Exception as e:
            logger.warning(f"Failed to download PDF: {e}")

    if log_local_success:
        logger.info(
            "Local archive processed successfully (no PDF download for local archives)"
        )

    print("\nSummary:")
    try:
        print(result.summary)
    except UnicodeEncodeError:
        print(result.summary.encode("utf-8", errors="replace").decode("utf-8"))

    return paper_output_dir

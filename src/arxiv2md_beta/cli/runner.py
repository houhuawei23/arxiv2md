"""Async workflows invoked by the Typer CLI (convert / images)."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from arxiv2md_beta.cli.helpers import collect_sections
from arxiv2md_beta.network.fetch import fetch_arxiv_pdf
from arxiv2md_beta.images.extract import extract_arxiv_images
from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.ingestion.local import ingest_local_archive
from arxiv2md_beta.output.layout import (
    build_output_basename,
    create_paper_output_dir,
    determine_output_dir,
)
from arxiv2md_beta.query.parser import is_local_archive_path, parse_arxiv_input, parse_local_archive
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()


def _write_split_markdown_sidecars(
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


@dataclass(frozen=True)
class ConvertParams:
    """Parameters for the ``convert`` command."""

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


def _emit_result_json_line(
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
    # 不再重复写 stderr：父进程若合并 stdout+stderr 会得到重复行；侧车 JSON 已作唯一真源


def _result_json_filename_key(arxiv_id: str) -> str:
    """用于结果侧车文件名：新式 ID 去掉 ``vN`` 后缀，与流水线侧查找一致。"""
    s = arxiv_id.strip()
    m = re.match(r"^(\d{4}\.\d{4,5})(v\d+)?$", s)
    if m:
        return m.group(1)
    return s.replace("/", "_")


def _write_result_json_sidecar(
    base_output_dir: Path,
    paper_output_dir: Path,
    *,
    result_key: str,
    arxiv_id: str | None = None,
    structured: dict[str, object] | None = None,
) -> None:
    """在输出根目录写入 ``.arxiv2md-result-{key}.json``，供流水线在无捕获 stdio 时读取唯一真源。"""
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class ImagesParams:
    """Parameters for the ``images`` command."""

    arxiv_input: str
    output: str | None
    images_subdir: str
    no_tex_cache: bool


def format_output(summary: str, tree: str, content: str, *, include_tree: bool) -> str:
    """Format final Markdown body (optional section tree)."""
    if include_tree:
        return f"{summary}\n\n{tree}\n\n{content}".strip()
    return f"{summary}\n\n{content}".strip()


async def run_convert_flow(params: ConvertParams) -> None:
    """Route to local archive or arXiv ingestion."""
    input_text = params.input_text.strip()
    if not input_text:
        raise ValueError("INPUT cannot be empty")
    if is_local_archive_path(input_text):
        await _process_local_archive(params)
    else:
        await _process_arxiv_paper(params)


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


async def _process_arxiv_paper(params: ConvertParams) -> None:
    """Process an arXiv paper (HTML or LaTeX parser)."""
    s = get_settings()
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

    submission_date = metadata.get("submission_date")
    title = metadata.get("title")

    paper_output_dir = metadata.get("paper_output_dir")
    if paper_output_dir is None:
        paper_output_dir = create_paper_output_dir(
            base_output_dir,
            submission_date,
            title,
            source=params.source,
            short=params.short,
        )
    else:
        if isinstance(paper_output_dir, str):
            paper_output_dir = Path(paper_output_dir)
    logger.info(f"Output directory: {paper_output_dir}")
    structured = metadata.get("structured_export")
    if not isinstance(structured, dict):
        structured = None
    _emit_result_json_line(paper_output_dir, params=params, structured=structured)
    try:
        _write_result_json_sidecar(
            base_output_dir,
            paper_output_dir,
            result_key=query.arxiv_id,
            arxiv_id=metadata.get("arxiv_id") or query.arxiv_id,
            structured=structured,
        )
    except OSError as e:
        logger.warning(f"Could not write arxiv2md result sidecar: {e}")

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
        base_id = query.arxiv_id.split("v")[0] if "v" in query.arxiv_id else query.arxiv_id
        output_filename = f"{base_id}.md"

    output_path = paper_output_dir / output_filename

    output_path.write_text(output_text, encoding="utf-8")
    logger.info(f"Output written to: {output_path}")
    _write_split_markdown_sidecars(paper_output_dir, output_filename, result)

    try:
        pdf_filename = output_filename.replace(".md", ".pdf")
        pdf_path = paper_output_dir / pdf_filename
        await fetch_arxiv_pdf(query.arxiv_id, pdf_path, query.version)
        logger.info(f"PDF downloaded to: {pdf_path}")
    except Exception as e:
        logger.warning(f"Failed to download PDF: {e}")

    print("\nSummary:")
    try:
        print(result.summary)
    except UnicodeEncodeError:
        print(result.summary.encode("utf-8", errors="replace").decode("utf-8"))


async def _process_local_archive(params: ConvertParams) -> None:
    """Process a local archive file (tar.gz, tgz, or zip)."""
    s = get_settings()
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

    submission_date = metadata.get("submission_date")
    title = metadata.get("title")

    paper_output_dir = metadata.get("paper_output_dir")
    if paper_output_dir is None:
        paper_output_dir = create_paper_output_dir(
            base_output_dir,
            submission_date,
            title,
            source=params.source,
            short=params.short,
        )
    else:
        if isinstance(paper_output_dir, str):
            paper_output_dir = Path(paper_output_dir)
    logger.info(f"Output directory: {paper_output_dir}")
    structured = metadata.get("structured_export")
    if not isinstance(structured, dict):
        structured = None
    _emit_result_json_line(paper_output_dir, params=params, structured=structured)
    try:
        rk = str(metadata.get("arxiv_id") or query.archive_path.stem)
        _write_result_json_sidecar(
            base_output_dir,
            paper_output_dir,
            result_key=query.archive_path.stem,
            arxiv_id=rk,
            structured=structured,
        )
    except OSError as e:
        logger.warning(f"Could not write arxiv2md result sidecar: {e}")

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
        output_filename = f"{query.archive_path.stem}.md"

    output_path = paper_output_dir / output_filename

    output_path.write_text(output_text, encoding="utf-8")
    logger.info(f"Output written to: {output_path}")
    _write_split_markdown_sidecars(paper_output_dir, output_filename, result)

    logger.info("Local archive processed successfully (no PDF download for local archives)")

    print("\nSummary:")
    try:
        print(result.summary)
    except UnicodeEncodeError:
        print(result.summary.encode("utf-8", errors="replace").decode("utf-8"))


def run_convert_sync(params: ConvertParams) -> None:
    """Run convert flow in a fresh event loop (Typer entry)."""
    asyncio.run(run_convert_flow(params))


def run_images_sync(params: ImagesParams) -> None:
    """Run images-only flow in a fresh event loop (Typer entry)."""
    asyncio.run(run_images_flow(params))

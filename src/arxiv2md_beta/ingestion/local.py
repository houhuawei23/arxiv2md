"""Local archive ingestion pipeline for processing local tar.gz/zip files."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from arxiv2md_beta.exceptions import IngestionError, ParserNotAvailableError
from arxiv2md_beta.images.processor import process_images_async
from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.latex.tex_source import (
    ArchiveExtractionError,
    TexSourceInfo,
    extract_local_archive,
)
from arxiv2md_beta.schemas import IngestionResult, LocalArchiveQuery


class LocalIngestionError(IngestionError):
    """Raised when local archive ingestion fails."""

    pass


async def ingest_local_archive(
    query: LocalArchiveQuery,
    base_output_dir: Path,
    source: str = "Local",
    short: str | None = None,
    no_images: bool = False,
    remove_refs: bool = False,
    remove_inline_citations: bool = False,
    linked_citations: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
) -> tuple[IngestionResult, dict[str, Any]]:
    """Process a local archive file (tar.gz, tgz, or zip) and convert to Markdown.

    This function handles both LaTeX-based archives (containing .tex files)
    and HTML-based archives (containing .html files).

    Parameters
    ----------
    query : LocalArchiveQuery
        Parsed local archive query
    base_output_dir : Path
        Base output directory (paper-specific directory will be created inside)
    source : str
        Source identifier (e.g., "CVPR", "ICML")
    short : str | None
        Short name for the paper
    no_images : bool
        If True, skip image processing
    remove_refs : bool
        Remove bibliography sections
    remove_inline_citations : bool
        Remove inline citations
    linked_citations : bool
        Render inline citations as [N](#ref-N) links
    section_filter_mode : str
        "include" or "exclude" section filtering
    sections : list[str] | None
        Section titles to filter

    Returns:
    -------
    tuple[IngestionResult, dict]
        Ingestion result and metadata

    Raises:
    ------
    LocalIngestionError
        If ingestion fails
    """
    sections = sections or []

    # Extract the archive
    try:
        tex_source_info = extract_local_archive(
            query.archive_path,
            output_dir=query.cache_dir / "extracted",
            use_cache=True,
        )
    except ArchiveExtractionError as e:
        raise LocalIngestionError(f"Failed to extract archive: {e}") from e

    # Determine if this is a LaTeX or HTML archive
    if tex_source_info.main_tex_file:
        # LaTeX-based archive
        return await _ingest_latex_archive(
            query=query,
            tex_source_info=tex_source_info,
            base_output_dir=base_output_dir,
            source=source,
            short=short,
            no_images=no_images,
            remove_refs=remove_refs,
            remove_inline_citations=remove_inline_citations,
            linked_citations=linked_citations,
            section_filter_mode=section_filter_mode,
            sections=sections,
            structured_output=structured_output,
            emit_graph_csv=emit_graph_csv,
        )
    else:
        # Check for HTML files
        html_files = list(tex_source_info.extracted_dir.rglob("*.html"))
        if html_files:
            # HTML-based archive
            return await _ingest_html_archive(
                query=query,
                extracted_dir=tex_source_info.extracted_dir,
                html_files=html_files,
                base_output_dir=base_output_dir,
                source=source,
                short=short,
                no_images=no_images,
                remove_refs=remove_refs,
                remove_inline_citations=remove_inline_citations,
                linked_citations=linked_citations,
                section_filter_mode=section_filter_mode,
                sections=sections,
                structured_output=structured_output,
                emit_graph_csv=emit_graph_csv,
            )
        else:
            raise LocalIngestionError(
                "No main LaTeX file or HTML files found in archive. "
                "Archive must contain either .tex files or .html files."
            )


async def _ingest_latex_archive(
    query: LocalArchiveQuery,
    tex_source_info: TexSourceInfo,
    base_output_dir: Path,
    source: str,
    short: str | None,
    no_images: bool,
    remove_refs: bool = False,
    remove_inline_citations: bool = False,
    linked_citations: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
) -> tuple[IngestionResult, dict[str, Any]]:
    """Process a LaTeX-based local archive via the IR pipeline."""
    from arxiv2md_beta.output.layout import create_paper_output_dir

    # Pre-extract metadata from raw TeX (best-effort, for output-dir naming).
    assert tex_source_info.main_tex_file is not None, "main_tex_file is required for local LaTeX archive"
    try:
        tex_content_raw = tex_source_info.main_tex_file.read_text(encoding="utf-8", errors="ignore")
        title = _extract_title_from_tex(tex_content_raw) or query.title
        authors = _extract_authors_from_tex(tex_content_raw) or query.authors
        abstract = _extract_abstract_from_tex(tex_content_raw)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to extract metadata from LaTeX: {e}")
        title = query.title
        authors = query.authors
        abstract = None

    # Create paper-specific output directory
    paper_output_dir = create_paper_output_dir(
        base_output_dir,
        query.submission_date,
        title,
        source=source,
        short=short,
    )
    images_dir_name = "images"

    # Process images if enabled
    processed_images = None
    if not no_images:
        processed_images = await process_images_async(tex_source_info, paper_output_dir, images_dir_name)

    # Build image map from LaTeX labels/paths to local paths
    latex_image_map: dict[str, Path] = {}
    if processed_images:
        for idx, (label, source_path) in enumerate(tex_source_info.image_files.items()):
            if idx in processed_images.image_map:
                latex_image_map[label] = processed_images.image_map[idx]
                latex_image_map[source_path.name] = processed_images.image_map[idx]
                try:
                    rel_path = source_path.relative_to(tex_source_info.extracted_dir)
                    latex_image_map[str(rel_path)] = processed_images.image_map[idx]
                except ValueError:
                    pass

    arxiv_id = query.archive_path.stem

    # Build IR via LaTeXBuilder (offload blocking pandoc to thread pool).
    def _build_ir() -> DocumentIR:
        from arxiv2md_beta.ir import LaTeXBuilder
        from arxiv2md_beta.ir.resolvers import ImageResolver
        from arxiv2md_beta.ir.transforms import build_default_pipeline
        from arxiv2md_beta.latex.includes import resolve_latex_includes
        from arxiv2md_beta.settings import get_settings

        main_tex = tex_source_info.main_tex_file
        assert main_tex is not None
        tex_content = resolve_latex_includes(main_tex, tex_source_info.extracted_dir)
        doc = LaTeXBuilder(image_resolver=ImageResolver(path_map=latex_image_map)).build(
            tex_content,
            arxiv_id=arxiv_id,
            title=title,
            authors=list(authors) if authors else None,
            abstract=abstract,
            base_dir=tex_source_info.extracted_dir,
        )
        pipeline = build_default_pipeline(
            parser="latex",
            section_filter_mode=section_filter_mode,
            selected_sections=sections,
            remove_refs=remove_refs,
            reference_section_titles=get_settings().ingestion.reference_section_titles,
        )
        pipeline.run(doc)
        return doc

    try:
        doc = await asyncio.to_thread(_build_ir)
    except ParserNotAvailableError:
        raise
    except Exception as e:
        raise LocalIngestionError(f"Failed to parse LaTeX: {e}") from e

    # Shared finalize tail: split Markdown emission + paper.yml + structured export.
    from arxiv2md_beta.ingestion.ir_finalize import finalize_ingestion_output

    return await asyncio.to_thread(
        finalize_ingestion_output,
        doc,
        arxiv_id=arxiv_id,
        paper_output_dir=paper_output_dir,
        paper_yml_data={
            "title": doc.metadata.title or title,
            "authors": [a.name for a in doc.metadata.authors],
            "abstract": doc.metadata.abstract_text,
            "submission_date": query.submission_date,
            "source": source,
            "archive_path": str(query.archive_path),
        },
        linked_citations=linked_citations,
        remove_inline_citations=remove_inline_citations,
        structured_output=structured_output,
        emit_graph_csv=emit_graph_csv,
        images_subdir=images_dir_name,
        extra_metadata={
            "submission_date": query.submission_date,
            "archive_path": str(query.archive_path),
        },
    )


async def _ingest_html_archive(
    query: LocalArchiveQuery,
    extracted_dir: Path,
    html_files: list[Path],
    base_output_dir: Path,
    source: str,
    short: str | None,
    no_images: bool,
    remove_refs: bool,
    remove_inline_citations: bool,
    linked_citations: bool,
    section_filter_mode: str,
    sections: list[str],
    structured_output: str = "none",
    emit_graph_csv: bool = False,
) -> tuple[IngestionResult, dict[str, Any]]:
    """Process an HTML-based local archive via the IR pipeline."""
    from arxiv2md_beta.html.parser import parse_arxiv_html
    from arxiv2md_beta.output.layout import create_paper_output_dir

    # Find main HTML file (look for index.html, abstract.html, or largest file)
    main_html_file = _find_main_html_file(extracted_dir, html_files)

    try:
        html_content = main_html_file.read_text(encoding="utf-8", errors="ignore")
        parsed = parse_arxiv_html(html_content)
    except (OSError, ValueError, RuntimeError) as e:
        raise LocalIngestionError(f"Failed to parse HTML: {e}") from e

    # Use provided metadata if parsed is missing
    title = parsed.title or query.title or main_html_file.stem

    # Create paper-specific output directory
    paper_output_dir = create_paper_output_dir(
        base_output_dir,
        query.submission_date,
        title,
        source=source,
        short=short,
    )
    images_dir_name = "images"
    images_dir = paper_output_dir / images_dir_name
    images_dir.mkdir(parents=True, exist_ok=True)

    # Copy images from extracted archive to output directory
    if not no_images:
        _copy_local_images(extracted_dir, images_dir)

    # Build an image resolver from the copied files: map each image's name and
    # stem to its path relative to paper_output_dir (e.g. "images/foo.png").
    image_stem_map: dict[str, Path] = {}
    for img in images_dir.iterdir():
        if img.is_file():
            rel = Path(images_dir_name) / img.name
            image_stem_map[img.name] = rel
            image_stem_map[img.stem] = rel

    arxiv_id = query.archive_path.stem

    # Build IR via HTMLBuilder (consumes the same ParsedArxivHtml as the remote
    # HTML orchestrator).
    def _build_ir() -> DocumentIR:
        from arxiv2md_beta.ir import HTMLBuilder
        from arxiv2md_beta.ir.resolvers import ImageResolver
        from arxiv2md_beta.ir.transforms import build_default_pipeline
        from arxiv2md_beta.settings import get_settings

        doc = HTMLBuilder(image_resolver=ImageResolver(stem_map=image_stem_map)).build(parsed, arxiv_id=arxiv_id)
        pipeline = build_default_pipeline(
            parser="html",
            section_filter_mode=section_filter_mode,
            selected_sections=sections,
            remove_refs=remove_refs,
            reference_section_titles=get_settings().ingestion.reference_section_titles,
        )
        pipeline.run(doc)
        return doc

    try:
        doc = await asyncio.to_thread(_build_ir)
    except Exception as e:
        raise LocalIngestionError(f"Failed to build IR: {e}") from e

    # Shared finalize tail: split Markdown emission + paper.yml + structured export.
    from arxiv2md_beta.ingestion.ir_finalize import finalize_ingestion_output

    return await asyncio.to_thread(
        finalize_ingestion_output,
        doc,
        arxiv_id=arxiv_id,
        paper_output_dir=paper_output_dir,
        paper_yml_data={
            "title": doc.metadata.title or title,
            "authors": [a.name for a in doc.metadata.authors],
            "abstract": doc.metadata.abstract_text,
            "submission_date": query.submission_date,
            "source": source,
            "archive_path": str(query.archive_path),
        },
        linked_citations=linked_citations,
        remove_inline_citations=remove_inline_citations,
        structured_output=structured_output,
        emit_graph_csv=emit_graph_csv,
        images_subdir=images_dir_name,
        extra_metadata={
            "submission_date": query.submission_date,
            "archive_path": str(query.archive_path),
        },
    )


def _find_main_html_file(extracted_dir: Path, html_files: list[Path]) -> Path:
    """Find the main HTML file in the extracted archive."""
    # Priority order for main HTML files
    priority_names = [
        "index.html",
        "full_article.html",
        "article.html",
        "main.html",
        "abstract.html",
    ]

    for name in priority_names:
        for html_file in html_files:
            if html_file.name.lower() == name:
                return html_file

    # If no priority file found, return the largest HTML file
    return max(html_files, key=lambda p: p.stat().st_size)


def _copy_local_images(extracted_dir: Path, images_dir: Path) -> None:
    """Copy image files from extracted archive to the output directory.

    Names are flattened into ``images_dir``; same-named files from different
    subdirectories get ``_1``/``_2`` suffixes instead of silently overwriting
    each other (same policy as ``local_html._copy_associated_files``).
    """
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"}

    for ext in image_extensions:
        for img_file in extracted_dir.rglob(f"*{ext}"):
            try:
                dest_path = images_dir / img_file.name
                counter = 1
                original_dest = dest_path
                while dest_path.exists():
                    dest_path = images_dir / f"{original_dest.stem}_{counter}{original_dest.suffix}"
                    counter += 1
                shutil.copy2(img_file, dest_path)
                logger.debug(f"Copied image: {img_file} -> {dest_path}")
            except OSError as e:
                logger.warning(f"Failed to copy image {img_file}: {e}")


def _extract_title_from_tex(tex_content: str) -> str | None:
    """Extract title from LaTeX content."""
    # Try TexSoup first
    try:
        from TexSoup import TexSoup

        soup = TexSoup(tex_content)
        title_cmd = getattr(soup, "title", None)
        if title_cmd:
            title_text = _texsoup_extract_text(title_cmd)
            if title_text:
                return title_text.strip()
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback to regex with balanced brace matching
    pattern = r"\\title\s*\{"
    match = re.search(pattern, tex_content)
    if not match:
        return None

    start_pos = match.end()
    brace_count = 1
    i = start_pos

    while i < len(tex_content) and brace_count > 0:
        if tex_content[i] == "{":
            brace_count += 1
        elif tex_content[i] == "}":
            brace_count -= 1
        i += 1

    if brace_count == 0:
        title_content = tex_content[start_pos : i - 1]
        return _clean_latex_text(title_content)

    return None


def _extract_authors_from_tex(tex_content: str) -> list[str]:
    """Extract authors from LaTeX content."""
    authors: list[str] = []

    # Try TexSoup first
    try:
        from TexSoup import TexSoup

        soup = TexSoup(tex_content)
        author_cmd = getattr(soup, "author", None)
        if author_cmd:
            author_text = _texsoup_extract_text(author_cmd)
            if author_text:
                author_parts = re.split(r"\\and|\\AND", author_text)
                for part in author_parts:
                    cleaned = part.strip()
                    cleaned = re.sub(r"^\s*%\s*", "", cleaned)
                    cleaned = _clean_latex_text(cleaned)
                    if cleaned:
                        authors.append(cleaned)
                return authors
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback to regex
    pattern = r"\\author\s*\{"
    match = re.search(pattern, tex_content)
    if not match:
        return authors

    start_pos = match.end()
    brace_count = 1
    i = start_pos

    while i < len(tex_content) and brace_count > 0:
        if tex_content[i] == "{":
            brace_count += 1
        elif tex_content[i] == "}":
            brace_count -= 1
        i += 1

    if brace_count == 0:
        author_text = tex_content[start_pos : i - 1]
        author_parts = re.split(r"\\and|\\AND", author_text)
        for part in author_parts:
            cleaned = part.strip()
            cleaned = re.sub(r"^\s*%\s*", "", cleaned)
            cleaned = _clean_latex_text(cleaned)
            if cleaned:
                authors.append(cleaned)

    return authors


def _extract_abstract_from_tex(tex_content: str) -> str | None:
    """Extract abstract from LaTeX content."""
    abstract_match = re.search(
        r"\\begin\{abstract\}(.*?)\\end\{abstract\}",
        tex_content,
        re.DOTALL,
    )
    if abstract_match:
        return _clean_latex_text(abstract_match.group(1))
    return None


def _texsoup_extract_text(node) -> str:
    """Extract plain text from a TexSoup node."""
    if hasattr(node, "args") and node.args:
        parts = []
        for arg in node.args:
            if hasattr(arg, "contents"):
                for item in arg.contents:
                    if hasattr(item, "name"):
                        cmd_name = item.name
                        if cmd_name in (
                            "vspace",
                            "hspace",
                            "hfill",
                            "vfill",
                            "newline",
                            "linebreak",
                        ):
                            continue
                        parts.append(_texsoup_extract_text(item))
                    elif isinstance(item, str):
                        parts.append(item)
                    else:
                        parts.append(_texsoup_extract_text(item))
            elif isinstance(arg, str):
                parts.append(arg)
            else:
                parts.append(_texsoup_extract_text(arg))
        content = "".join(parts)
    elif hasattr(node, "string"):
        content = str(node.string) if node.string else ""
    elif hasattr(node, "contents"):
        parts = []
        for item in node.contents:
            if hasattr(item, "name"):
                cmd_name = item.name
                if cmd_name in (
                    "vspace",
                    "hspace",
                    "hfill",
                    "vfill",
                    "newline",
                    "linebreak",
                ):
                    continue
                parts.append(_texsoup_extract_text(item))
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(_texsoup_extract_text(item))
        content = "".join(parts)
    else:
        content = str(node)

    content = re.sub(r"\\[a-zA-Z]+\*?\s*(\[[^\]]*\])?\s*(\{[^\}]*\})?", "", content)
    content = re.sub(r"\{|\}", "", content)
    content = re.sub(r"\s+", " ", content)
    return content.strip()


def _clean_latex_text(text: str) -> str:
    """Clean LaTeX text by removing commands and formatting."""
    text = re.sub(r"^\s*%\s*", "", text)
    text = re.sub(r"\\[a-zA-Z]+\*?\s*(\[[^\]]*\])?\s*(\{[^\}]*\})?", "", text)
    text = re.sub(r"\{|\}", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

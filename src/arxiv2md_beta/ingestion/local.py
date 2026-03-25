"""Local archive ingestion pipeline for processing local tar.gz/zip files."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from loguru import logger

from arxiv2md_beta.images.resolver import process_images
from arxiv2md_beta.latex.parser import ParserNotAvailableError, parse_latex_to_markdown
from arxiv2md_beta.output.formatter import format_paper
from arxiv2md_beta.schemas import IngestionResult, LocalArchiveQuery, SectionNode
from arxiv2md_beta.latex.tex_source import (
    ArchiveExtractionError,
    TexSourceInfo,
    extract_local_archive,
)


class LocalIngestionError(Exception):
    """Raised when local archive ingestion fails."""

    pass


async def ingest_local_archive(
    query: LocalArchiveQuery,
    base_output_dir: Path,
    source: str = "Local",
    short: str | None = None,
    no_images: bool = False,
    remove_refs: bool = False,
    remove_toc: bool = False,
    remove_inline_citations: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
) -> tuple[IngestionResult, dict[str, str | list[str] | None]]:
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
    remove_toc : bool
        Remove table of contents
    remove_inline_citations : bool
        Remove inline citations
    section_filter_mode : str
        "include" or "exclude" section filtering
    sections : list[str] | None
        Section titles to filter

    Returns
    -------
    tuple[IngestionResult, dict]
        Ingestion result and metadata

    Raises
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
                remove_toc=remove_toc,
                remove_inline_citations=remove_inline_citations,
                section_filter_mode=section_filter_mode,
                sections=sections,
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
) -> tuple[IngestionResult, dict[str, str | list[str] | None]]:
    """Process a LaTeX-based local archive."""
    from arxiv2md_beta.output.layout import create_paper_output_dir

    # Parse LaTeX to extract metadata before creating output dir
    try:
        # Try to get title from LaTeX content first
        tex_content = tex_source_info.main_tex_file.read_text(encoding="utf-8", errors="ignore")
        title = _extract_title_from_tex(tex_content)
        authors = _extract_authors_from_tex(tex_content)
        abstract = _extract_abstract_from_tex(tex_content)
    except Exception as e:
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
        processed_images = process_images(tex_source_info, paper_output_dir, images_dir_name)

    # Build image map from LaTeX labels/paths to local paths
    latex_image_map: dict[str, Path] = {}
    if processed_images:
        for idx, (label, source_path) in enumerate(tex_source_info.image_files.items()):
            if idx in processed_images.image_map:
                latex_image_map[label] = processed_images.image_map[idx]
                # Also map by filename
                latex_image_map[source_path.name] = processed_images.image_map[idx]
                # Map by path relative to base_dir
                try:
                    rel_path = source_path.relative_to(tex_source_info.extracted_dir)
                    latex_image_map[str(rel_path)] = processed_images.image_map[idx]
                except ValueError:
                    pass

    # Parse LaTeX to Markdown
    try:
        parsed_latex = parse_latex_to_markdown(
            tex_source_info.main_tex_file,
            tex_source_info.extracted_dir,
            latex_image_map,
        )
    except ParserNotAvailableError:
        raise
    except Exception as e:
        raise LocalIngestionError(f"Failed to parse LaTeX: {e}") from e

    # Use parsed metadata if not already extracted
    if not title and parsed_latex.title:
        title = parsed_latex.title
    if not authors and parsed_latex.authors:
        authors = parsed_latex.authors
    if not abstract and parsed_latex.abstract:
        abstract = parsed_latex.abstract

    # Create a simple section structure from the markdown
    # For LaTeX mode, we create a single section with the full content
    sections_list = [
        SectionNode(
            title=title or "Document",
            level=1,
            anchor=None,
            html=None,
            markdown=parsed_latex.markdown,
            children=[],
        )
    ]

    # Format output
    result = format_paper(
        arxiv_id=query.archive_path.stem,  # Use archive name as ID
        version=None,
        title=title,
        authors=authors,
        abstract=abstract,
        sections=sections_list,
        include_toc=False,  # LaTeX mode doesn't use TOC
        include_abstract_in_tree=abstract is not None,
    )

    # Save paper metadata to paper.yml
    try:
        from arxiv2md_beta.output.metadata import save_paper_metadata

        metadata_dict = {
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "submission_date": query.submission_date,
            "source": source,
            "archive_path": str(query.archive_path),
        }
        save_paper_metadata(metadata_dict, paper_output_dir)
    except Exception as e:
        logger.warning(f"Failed to save paper.yml: {e}")

    metadata = {
        "title": title or "Unknown",
        "authors": authors,
        "abstract": abstract,
        "submission_date": query.submission_date,
        "paper_output_dir": paper_output_dir,
        "archive_path": str(query.archive_path),
    }

    return result, metadata


async def _ingest_html_archive(
    query: LocalArchiveQuery,
    extracted_dir: Path,
    html_files: list[Path],
    base_output_dir: Path,
    source: str,
    short: str | None,
    no_images: bool,
    remove_refs: bool,
    remove_toc: bool,
    remove_inline_citations: bool,
    section_filter_mode: str,
    sections: list[str],
) -> tuple[IngestionResult, dict[str, str | list[str] | None]]:
    """Process an HTML-based local archive."""
    from arxiv2md_beta.output.layout import create_paper_output_dir
    from arxiv2md_beta.html.parser import parse_arxiv_html
    from arxiv2md_beta.html.markdown import convert_fragment_to_markdown
    from arxiv2md_beta.html.sections import filter_sections

    # Find main HTML file (look for index.html, abstract.html, or largest file)
    main_html_file = _find_main_html_file(extracted_dir, html_files)

    try:
        html_content = main_html_file.read_text(encoding="utf-8", errors="ignore")
        parsed = parse_arxiv_html(html_content)
    except Exception as e:
        raise LocalIngestionError(f"Failed to parse HTML: {e}") from e

    # Use provided metadata if parsed is missing
    title = parsed.title or query.title or main_html_file.stem
    authors = parsed.authors if parsed.authors else query.authors
    abstract = parsed.abstract

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

    # Process images if enabled
    if not no_images:
        # Copy images from extracted archive to output directory
        _copy_local_images(extracted_dir, images_dir)

    # Filter sections
    filtered_sections = filter_sections(
        parsed.sections, mode=section_filter_mode, selected=sections
    )
    if remove_refs:
        _REFERENCE_TITLES = ("references", "bibliography")
        filtered_sections = filter_sections(
            filtered_sections, mode="exclude", selected=_REFERENCE_TITLES
        )

    # Check if abstract should be included
    selected_lower = [s.lower() for s in sections]
    if section_filter_mode == "exclude":
        include_abstract = "abstract" not in selected_lower
    else:
        include_abstract = not sections or "abstract" in selected_lower

    # Populate markdown for sections
    figure_counter: list[int] = [0]
    abstract_md: str | None = None
    if include_abstract:
        if parsed.abstract_html:
            abstract_md = convert_fragment_to_markdown(
                parsed.abstract_html,
                remove_inline_citations=remove_inline_citations,
                figure_counter=figure_counter,
                images_dir=images_dir,
            )
        elif abstract:
            abstract_md = abstract

    for section in filtered_sections:
        _populate_section_markdown(
            section,
            remove_inline_citations=remove_inline_citations,
            figure_counter=figure_counter,
            images_dir=images_dir,
        )

    # Format output
    result = format_paper(
        arxiv_id=query.archive_path.stem,
        version=None,
        title=title,
        authors=authors,
        abstract=abstract_md if include_abstract else None,
        sections=filtered_sections,
        include_toc=not remove_toc,
        include_abstract_in_tree=abstract is not None,
    )

    # Save metadata
    try:
        from arxiv2md_beta.output.metadata import save_paper_metadata

        metadata_dict = {
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "submission_date": query.submission_date,
            "source": source,
            "archive_path": str(query.archive_path),
        }
        save_paper_metadata(metadata_dict, paper_output_dir)
    except Exception as e:
        logger.warning(f"Failed to save paper.yml: {e}")

    metadata = {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "submission_date": query.submission_date,
        "paper_output_dir": paper_output_dir,
        "archive_path": str(query.archive_path),
    }

    return result, metadata


def _populate_section_markdown(
    section,
    *,
    remove_inline_citations: bool = False,
    figure_counter: list[int] | None = None,
    images_dir: Path | None = None,
) -> None:
    """Populate markdown for section and children."""
    from arxiv2md_beta.html.markdown import convert_fragment_to_markdown

    if figure_counter is None:
        figure_counter = [0]
    if section.html:
        section.markdown = convert_fragment_to_markdown(
            section.html,
            remove_inline_citations=remove_inline_citations,
            figure_counter=figure_counter,
            images_dir=images_dir,
        )
    for child in section.children:
        _populate_section_markdown(
            child,
            remove_inline_citations=remove_inline_citations,
            figure_counter=figure_counter,
            images_dir=images_dir,
        )


def _find_main_html_file(extracted_dir: Path, html_files: list[Path]) -> Path:
    """Find the main HTML file in the extracted archive."""
    # Priority order for main HTML files
    priority_names = ["index.html", "full_article.html", "article.html", "main.html", "abstract.html"]

    for name in priority_names:
        for html_file in html_files:
            if html_file.name.lower() == name:
                return html_file

    # If no priority file found, return the largest HTML file
    return max(html_files, key=lambda p: p.stat().st_size)


def _copy_local_images(extracted_dir: Path, images_dir: Path) -> None:
    """Copy image files from extracted archive to output directory."""
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"}

    for ext in image_extensions:
        for img_file in extracted_dir.rglob(f"*{ext}"):
            try:
                # Maintain directory structure relative to extracted_dir
                rel_path = img_file.relative_to(extracted_dir)
                dest_path = images_dir / rel_path.name  # Flatten structure
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(img_file, dest_path)
                logger.debug(f"Copied image: {img_file} -> {dest_path}")
            except Exception as e:
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

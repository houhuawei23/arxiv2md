"""Local HTML file ingestion pipeline for processing saved HTML papers."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any  # noqa: F401
from urllib.parse import unquote

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from loguru import logger

from arxiv2md_beta.html.markdown import convert_fragment_to_markdown
from arxiv2md_beta.html.sections import filter_sections
from arxiv2md_beta.output.formatter import format_paper
from arxiv2md_beta.schemas import IngestionResult, LocalHtmlQuery, SectionNode


class LocalHtmlIngestionError(Exception):
    """Raised when local HTML ingestion fails."""

    pass


@dataclass
class ParsedLocalHtml:
    """Parsed content extracted from local HTML paper."""

    title: str | None
    authors: list[str]
    abstract: str | None
    abstract_html: str | None
    sections: list[SectionNode]
    # Map of original image paths to new paths
    image_path_map: dict[str, str]


def parse_local_html(html: str, base_path: Path) -> ParsedLocalHtml:
    """Extract title, authors, abstract, and sections from local HTML.

    This parser is designed for saved HTML papers from various sources
    (Science.org, IEEE, ACM, etc.), not just arXiv.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted elements before parsing
    _remove_unwanted_elements(soup)

    title = _extract_local_title(soup)
    authors = _extract_local_authors(soup)
    abstract, abstract_html = _extract_local_abstract(soup)
    sections, image_path_map = _extract_local_sections(soup, base_path)

    return ParsedLocalHtml(
        title=title,
        authors=authors,
        abstract=abstract,
        abstract_html=abstract_html,
        sections=sections,
        image_path_map=image_path_map,
    )


def _remove_unwanted_elements(soup: BeautifulSoup) -> None:
    """Remove navigation, ads, and other unwanted elements."""
    # Remove nav elements
    for nav in soup.find_all("nav"):
        nav.decompose()

    # Remove header (usually contains site navigation)
    for header in soup.find_all("header"):
        # Check if it's site header, not article header
        if not header.find_parent("article"):
            header.decompose()

    # Remove footer
    for footer in soup.find_all("footer"):
        footer.decompose()

    # Remove aside elements (sidebars)
    for aside in soup.find_all("aside"):
        aside.decompose()

    # Remove elements with common ad/navigation classes
    unwanted_classes = [
        r".*advertisement.*",
        r".*toolbar.*",
        r".*metrics.*",
        r".*citations.*",
        r".*references-pop-up.*",
        r".*popup.*",
        r".*modal.*",
        r".*cookie.*",
        r".*newsletter.*",
        r".*signup.*",
        r".*social.*",
        r".*share.*",
        r".*info-panel.*",
        r".*core-nav.*",
        r".*data-core-nav.*",
        r".*altmetric.*",
        r".*download.*",
        r".*export.*",
        r".*cite-as.*",
        r".*view-options.*",
        r".*eletters.*",
        r".*related-content.*",
        r".*recommended.*",
    ]

    for cls_pattern in unwanted_classes:
        for elem in soup.find_all(class_=re.compile(cls_pattern, re.I)):
            elem.decompose()

    # Remove data-extent="frontmatter" elements (contains metadata)
    for elem in soup.find_all(attrs={"data-extent": "frontmatter"}):
        elem.decompose()

    # Remove data-core-nav elements
    for elem in soup.find_all(attrs={"data-core-nav": True}):
        elem.decompose()

    # Remove script and style tags
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    # Remove elements with role="navigation"
    for elem in soup.find_all(attrs={"role": "navigation"}):
        elem.decompose()

    # Remove elements with aria-label="Contents" or similar
    for elem in soup.find_all(attrs={"aria-label": re.compile(r"contents|navigation", re.I)}):
        elem.decompose()

    # Remove TOC lists - these are usually <ul> or <ol> that contain only links to section anchors
    for list_elem in soup.find_all(["ul", "ol"]):
        # Check if this looks like a TOC (all items are links to #anchors)
        items = list_elem.find_all("li", recursive=False)
        if not items:
            items = list_elem.find_all("li")

        if items:
            is_toc = True
            for li in items[:5]:  # Check first 5 items
                link = li.find("a", href=True)
                if not link:
                    is_toc = False
                    break
                href = link.get("href", "")
                # TOC links usually start with #
                if not (href.startswith("#") or "/doi/" in href):
                    is_toc = False
                    break

            if is_toc and len(items) > 2:  # Only remove if it looks like a TOC with multiple items
                list_elem.decompose()

    # Remove duplicate section content by tracking what we've seen
    seen_sections = set()
    for heading in soup.find_all(["h2", "h3", "h4", "h5", "h6"]):
        section_id = heading.get("id", "")
        if section_id and section_id in seen_sections:
            # Remove this heading and its content
            heading.decompose()
        elif section_id:
            seen_sections.add(section_id)


def _extract_local_title(soup: BeautifulSoup) -> str | None:
    """Extract title from local HTML."""
    # Try h1 first
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(" ", strip=True)
        text = re.sub(r"^\s*\[[^\]]+\]\s*", "", text)
        text = re.sub(r"\bContents\s*$", "", text).strip()
        if text:
            return text

    # Try article title
    article = soup.find("article")
    if article:
        h1 = article.find("h1")
        if h1:
            text = h1.get_text(" ", strip=True)
            if text:
                return text

    # Try meta title
    meta_title = soup.find("meta", property="og:title")
    if meta_title:
        return meta_title.get("content", "").strip()

    return None


def _extract_local_authors(soup: BeautifulSoup) -> list[str]:
    """Extract authors from local HTML."""
    authors = []

    # Try schema.org Person markup
    for author_elem in soup.find_all("span", property="author"):
        given = author_elem.find("span", property="givenName")
        family = author_elem.find("span", property="familyName")
        if given and family:
            name = f"{given.get_text(strip=True)} {family.get_text(strip=True)}"
            if name.strip():
                authors.append(name)
        else:
            # Try to get text directly
            text = author_elem.get_text(" ", strip=True)
            # Remove ORCID links
            text = re.sub(r"https://orcid\.org/\S+", "", text)
            text = text.strip()
            if text and text not in authors:
                authors.append(text)

    # Try common author classes
    if not authors:
        for cls in ["authors", "author", "contrib-author", "entry-author"]:
            container = soup.find(class_=cls)
            if container:
                for link in container.find_all("a"):
                    text = link.get_text(" ", strip=True)
                    if text and text not in authors:
                        authors.append(text)

    return authors


def _extract_local_abstract(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Extract abstract from local HTML."""
    # Try section with abstract id
    abstract_section = soup.find("section", id="abstract")
    if abstract_section:
        # Get text version
        text = abstract_section.get_text(" ", strip=True)
        # Get HTML version (excluding the heading)
        html_parts = []
        for child in abstract_section.children:
            if isinstance(child, Tag):
                if child.name in ("h2", "h3", "h4"):
                    continue
                html_parts.append(str(child))
        return text, "".join(html_parts) if html_parts else None

    # Try div with abstract class
    abstract_div = soup.find(class_=re.compile(r"abstract", re.I))
    if abstract_div:
        text = abstract_div.get_text(" ", strip=True)
        return text, str(abstract_div)

    return None, None


def _extract_local_sections(soup: BeautifulSoup, base_path: Path) -> tuple[list[SectionNode], dict[str, str]]:
    """Extract sections and collect image paths from local HTML."""
    sections: list[SectionNode] = []
    image_path_map: dict[str, str] = {}

    # Find the main article content
    article = soup.find("article")
    if not article:
        article = soup.find("main") or soup.find("div", class_=re.compile(r"content|main", re.I))
    if not article:
        article = soup

    # Find all headings in the article
    headings = article.find_all(["h2", "h3", "h4", "h5", "h6"])

    # Filter out unwanted headings
    filtered_headings = []
    for h in headings:
        text = h.get_text(strip=True).lower()
        # Skip empty headings
        if not text:
            continue
        # Skip navigation-like headings
        if text in ["contents", "metrics", "citations", "references", "abstract"]:
            continue
        # Skip if in nav or hidden
        if h.find_parent(["nav", "header", "footer", "aside"]):
            continue
        filtered_headings.append(h)

    # Build sections from headings
    stack: list[SectionNode] = []

    for i, heading in enumerate(filtered_headings):
        level = int(heading.name[1])
        title = heading.get_text(" ", strip=True)
        anchor = heading.get("id") or f"section-{i}"

        # Collect HTML content until next heading of same or higher level
        html_content = _collect_content_until_next_heading(heading, filtered_headings[i + 1:] if i + 1 < len(filtered_headings) else [])

        # Rewrite image paths in the HTML
        if html_content:
            html_content, section_image_map = _rewrite_image_paths(html_content, base_path)
            image_path_map.update(section_image_map)

        node = SectionNode(
            title=title,
            level=level,
            anchor=anchor,
            html=html_content,
        )

        # Manage stack for hierarchy
        while stack and stack[-1].level >= level:
            stack.pop()

        if stack:
            stack[-1].children.append(node)
        else:
            sections.append(node)

        stack.append(node)

    return sections, image_path_map


def _is_toc_list(elem: Tag) -> bool:
    """Check if an element is a TOC (table of contents) list."""
    if elem.name not in ["ul", "ol"]:
        return False

    items = elem.find_all("li", recursive=False)
    if not items:
        items = elem.find_all("li")

    if len(items) < 2:
        return False

    # Check if all items are links to #anchors
    toc_link_count = 0
    for li in items:
        link = li.find("a", href=True)
        if link:
            href = link.get("href", "")
            if href.startswith("#"):
                toc_link_count += 1

    # If most items are anchor links, it's likely a TOC
    return toc_link_count >= len(items) * 0.7


def _collect_content_until_next_heading(heading: Tag, next_headings: list[Tag]) -> str | None:
    """Collect all HTML content from heading until the next heading of same or higher level.

    This function first tries to collect content from the parent section element
    (common in Science.org, IEEE, etc.), and falls back to sibling traversal for
    flat structures (like arXiv).
    """
    parts = []

    # Ensure we have the actual heading element (h1-h6), not a child element
    # Headings may contain nested elements like <i>, <b>, etc.
    actual_heading = heading
    while actual_heading and actual_heading.name not in ("h1", "h2", "h3", "h4", "h5", "h6"):
        actual_heading = actual_heading.parent

    if not actual_heading:
        return None

    # First, try to find content within parent section
    section = actual_heading.find_parent("section")
    if section:
        # Find the heading within the section using text comparison
        # (heading object reference may differ after soup modifications)
        heading_text = actual_heading.get_text(strip=True)
        heading_name = actual_heading.name

        # Collect all children after the heading within the section
        started = False
        for child in section.children:
            if isinstance(child, Tag) and child.name == heading_name:
                if child.get_text(strip=True) == heading_text:
                    started = True
                    continue
            if not started:
                continue

            # Stop if we hit a nested section with its own heading
            if isinstance(child, Tag):
                if child.name == "section":
                    break
                if child.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
                    break
                # Skip navigation elements
                if child.name in ["nav", "footer", "aside"]:
                    continue
                if any(cls in str(child.get("class", [])) for cls in ["toolbar", "metrics", "popup"]):
                    continue
                # Skip TOC lists
                if _is_toc_list(child):
                    continue
                parts.append(str(child))
            elif isinstance(child, NavigableString):
                text = str(child)
                if text.strip():
                    parts.append(text)

        if parts:
            html = "".join(parts).strip()
            return html if html else None

    # Fallback: use sibling traversal for flat structures
    current = actual_heading.next_sibling

    stop_headings = set()
    heading_level = int(actual_heading.name[1])
    for h in next_headings:
        h_level = int(h.name[1])
        if h_level <= heading_level:
            stop_headings.add(id(h))
            break

    while current:
        # Stop if we hit another heading
        if isinstance(current, Tag) and current.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            if id(current) in stop_headings:
                break
            h_level = int(current.name[1])
            if h_level <= heading_level:
                break

        # Skip navigation elements
        if isinstance(current, Tag):
            # Stop at nested sections (for local HTML with nested section structure)
            if current.name == "section":
                break
            if current.name in ["nav", "footer", "aside"]:
                current = current.next_sibling
                continue
            if any(cls in str(current.get("class", [])) for cls in ["toolbar", "metrics", "popup"]):
                current = current.next_sibling
                continue
            # Skip TOC lists
            if _is_toc_list(current):
                current = current.next_sibling
                continue
            parts.append(str(current))
        elif isinstance(current, NavigableString):
            text = str(current)
            if text.strip():
                parts.append(text)

        current = current.next_sibling

    html = "".join(parts).strip()
    return html if html else None


def _rewrite_image_paths(html: str, base_path: Path) -> tuple[str, dict[str, str]]:
    """Rewrite image paths in HTML from _files/ to images/.

    Returns the modified HTML and a mapping of old paths to new paths.
    """
    image_path_map: dict[str, str] = {}

    soup = BeautifulSoup(html, "html.parser")

    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue

        # Parse the src to get just the filename
        original_src = src

        # Handle relative paths like ./filename_files/xxx.jpg
        if "_files/" in src or ".files/" in src:
            # Extract just the filename
            filename = Path(unquote(src)).name
            new_src = f"./images/{filename}"
            img["src"] = new_src
            image_path_map[original_src] = new_src
        elif src.startswith("http://") or src.startswith("https://"):
            # Keep external URLs as-is
            pass
        elif src.startswith("./") or not src.startswith("/"):
            # Handle other relative paths
            filename = Path(unquote(src)).name
            new_src = f"./images/{filename}"
            img["src"] = new_src
            image_path_map[original_src] = new_src

    return str(soup), image_path_map


async def ingest_local_html(
    query: LocalHtmlQuery,
    base_output_dir: Path,
    source: str = "Local",
    short: str | None = None,
    no_images: bool = False,
    remove_refs: bool = False,
    remove_toc: bool = False,
    remove_inline_citations: bool = False,
    section_filter_mode: str = "exclude",
    sections: list[str] | None = None,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
) -> tuple[IngestionResult, dict[str, str | list[str] | None]]:
    """Process a local HTML file and convert to Markdown."""
    sections = sections or []

    # Read HTML content
    try:
        html_content = query.html_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        raise LocalHtmlIngestionError(f"Failed to read HTML file: {e}") from e

    # Parse HTML using local parser
    try:
        parsed = parse_local_html(html_content, query.html_path.parent)
    except Exception as e:
        raise LocalHtmlIngestionError(f"Failed to parse HTML: {e}") from e

    # Use provided metadata or fall back to parsed
    title = parsed.title or query.title or query.html_path.stem
    authors = [a.name for a in parsed.authors] if parsed.authors else query.authors
    submission_date = query.submission_date

    # Create paper-specific output directory
    from arxiv2md_beta.output.layout import create_paper_output_dir

    paper_output_dir = create_paper_output_dir(
        base_output_dir,
        submission_date,
        title,
        source=source or query.source,
        short=short,
    )
    images_dir_name = "images"
    images_dir = paper_output_dir / images_dir_name
    images_dir.mkdir(parents=True, exist_ok=True)

    # Process associated files
    if not no_images:
        _copy_associated_files(query.html_path, images_dir)

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
        elif parsed.abstract:
            abstract_md = parsed.abstract

    for section in filtered_sections:
        _populate_section_markdown(
            section,
            remove_inline_citations=remove_inline_citations,
            figure_counter=figure_counter,
            images_dir=images_dir,
        )

    # Format output
    result = format_paper(
        arxiv_id=query.html_path.stem,
        version=None,
        title=title,
        authors=authors,
        abstract=abstract_md if include_abstract else None,
        sections=filtered_sections,
        include_toc=not remove_toc,
        include_abstract_in_tree=parsed.abstract is not None,
        split_for_reference=True,
    )

    # Save metadata
    try:
        from arxiv2md_beta.output.metadata import save_paper_metadata

        metadata_dict = {
            "title": title,
            "authors": authors,
            "abstract": parsed.abstract,
            "submission_date": submission_date,
            "source": source or query.source,
            "html_path": str(query.html_path),
        }
        save_paper_metadata(metadata_dict, paper_output_dir)
    except Exception as e:
        logger.warning(f"Failed to save paper.yml: {e}")

    metadata = {
        "title": title,
        "authors": authors,
        "abstract": parsed.abstract,
        "submission_date": submission_date,
        "paper_output_dir": paper_output_dir,
        "html_path": str(query.html_path),
        "arxiv_id": query.html_path.stem,
        "structured_export": {},
    }

    return result, metadata


def _populate_section_markdown(
    section: SectionNode,
    *,
    remove_inline_citations: bool = False,
    figure_counter: list[int] | None = None,
    images_dir: Path | None = None,
) -> None:
    """Populate markdown for section and children."""
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


def _copy_associated_files(html_path: Path, images_dir: Path) -> None:
    """Copy associated files from the HTML file's _files directory."""
    base_name = html_path.stem
    files_dir_patterns = [
        html_path.parent / f"{base_name}_files",
        html_path.parent / f"{base_name}.files",
        html_path.parent / f"{base_name}_resources",
    ]

    files_dir = None
    for pattern in files_dir_patterns:
        if pattern.exists() and pattern.is_dir():
            files_dir = pattern
            break

    if not files_dir:
        logger.debug(f"No associated files directory found for {html_path}")
        return

    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"}

    copied_count = 0
    for ext in image_extensions:
        for img_file in files_dir.rglob(f"*{ext}"):
            try:
                dest_path = images_dir / img_file.name
                counter = 1
                original_dest = dest_path
                while dest_path.exists():
                    stem = original_dest.stem
                    suffix = original_dest.suffix
                    dest_path = images_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                shutil.copy2(img_file, dest_path)
                copied_count += 1
                logger.debug(f"Copied associated file: {img_file} -> {dest_path}")
            except Exception as e:
                logger.warning(f"Failed to copy file {img_file}: {e}")

    if copied_count > 0:
        logger.info(f"Copied {copied_count} associated file(s) from {files_dir}")

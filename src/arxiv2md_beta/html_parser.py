"""Parse arXiv HTML into metadata and section structure."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from arxiv2md_beta.schemas import SectionNode

try:
    from bs4 import BeautifulSoup
    from bs4.element import NavigableString, Tag
except ImportError as exc:  # pragma: no cover - runtime dependency check
    raise RuntimeError("BeautifulSoup4 is required for HTML parsing (pip install beautifulsoup4).") from exc


_HEADING_RE = re.compile(r"^h[1-6]$")
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w.-]+\.\w+$")
# Keywords that indicate footnotes or contribution statements (case-insensitive check)
_SKIP_KEYWORDS = {"footnotemark:", "equal contribution", "work performed", "listing order"}
_MAX_AUTHOR_PART_LENGTH = 80  # Filter out long contribution statements


@dataclass
class ParsedArxivHtml:
    """Parsed content extracted from arXiv HTML."""

    title: str | None
    authors: list[str]
    abstract: str | None
    abstract_html: str | None  # Inner HTML of abstract div for figure-aware conversion
    front_matter_html: str | None  # HTML between abstract and first section (e.g. title-block figures)
    sections: list[SectionNode]
    submission_date: str | None = None  # Format: YYYYMMDD


def _extract_front_matter_html(soup: BeautifulSoup, document_root: Tag) -> str | None:
    """Extract HTML between abstract and first section (e.g. title-block figures)."""
    abstract = soup.find(class_=re.compile(r"ltx_abstract"))
    first_section = document_root.find("section", class_=re.compile(r"ltx_section"))
    if not abstract or not first_section:
        return None
    parts: list[str] = []
    for sib in abstract.find_next_siblings():
        if isinstance(sib, Tag) and sib.name == "section":
            break
        if isinstance(sib, Tag) and ("ltx_figure" in str(sib) or "ltx_para" in str(sib) or "ltx_logical-block" in " ".join(sib.get("class", []))):
            parts.append(str(sib))
    return "\n".join(parts) if parts else None


def parse_arxiv_html(html: str) -> ParsedArxivHtml:
    """Extract title, authors, abstract, and section tree from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    document_root = _find_document_root(soup)

    title = _extract_title(soup)
    authors = _extract_authors(soup)
    abstract = _extract_abstract(soup)
    abstract_html = _extract_abstract_html(soup)
    front_matter_html = _extract_front_matter_html(soup, document_root)
    sections = _extract_sections(document_root)
    submission_date = _extract_submission_date(soup)

    return ParsedArxivHtml(
        title=title,
        authors=authors,
        abstract=abstract,
        abstract_html=abstract_html,
        front_matter_html=front_matter_html,
        sections=sections,
        submission_date=submission_date,
    )


def _find_document_root(soup: BeautifulSoup) -> Tag:
    root = soup.find("article", class_=re.compile(r"ltx_document"))
    if root:
        return root
    article = soup.find("article")
    if article:
        return article
    if soup.body:
        return soup.body
    return soup


def _extract_title(soup: BeautifulSoup) -> str | None:
    title_tag = soup.find("h1", class_=re.compile(r"ltx_title"))
    if title_tag:
        return title_tag.get_text(" ", strip=True)
    if soup.title:
        return soup.title.get_text(" ", strip=True)
    return None


def _extract_authors(soup: BeautifulSoup) -> list[str]:
    authors_container = soup.find("div", class_="ltx_authors")
    if not authors_container:
        document_root = _find_document_root(soup)
        authors_container = document_root.find("div", class_="ltx_authors")
    if not authors_container:
        return []

    author_nodes = authors_container.find_all(
        lambda tag: tag.name == "span"
        and "ltx_text" in tag.get("class", [])
        and "ltx_font_bold" in tag.get("class", [])
    )
    if not author_nodes:
        author_nodes = authors_container.find_all(class_=re.compile(r"ltx_author|ltx_personname"))

    authors: list[str] = []
    for node in author_nodes:
        for text in _clean_author_text(node):
            if text and text not in authors:
                authors.append(text)
    return authors


def _clean_author_text(node: Tag) -> list[str]:
    """Extract clean author names/affiliations, filtering out emails and footnotes."""
    clone = BeautifulSoup(str(node), "html.parser")
    # Remove superscripts (footnote markers)
    for sup in clone.find_all("sup"):
        sup.decompose()
    # Remove note elements
    for note in clone.find_all(class_=re.compile(r"ltx_note|ltx_role_footnote")):
        note.decompose()

    text = clone.get_text("\n", strip=True)
    parts = [re.sub(r"\s+", " ", part).strip() for part in text.splitlines()]

    cleaned: list[str] = []
    for part in parts:
        if not part:
            continue
        # Strip leading & (author separator in some papers)
        part = part.lstrip("&").strip()
        if not part:
            continue
        # Skip emails
        if _EMAIL_RE.match(part):
            continue
        # Skip pure numbers (footnote references)
        if part.isdigit():
            continue
        # Skip footnote markers and contribution keywords
        part_lower = part.lower()
        if any(marker in part_lower for marker in _SKIP_KEYWORDS):
            continue
        # Skip very long text (likely contribution statements)
        if len(part) > _MAX_AUTHOR_PART_LENGTH:
            continue
        # Skip text that looks like a sentence (contains multiple periods or common sentence patterns)
        if part.count(".") > 1 or (part.endswith(".") and len(part) > 40):
            continue
        cleaned.append(part)
    return cleaned


def _extract_abstract(soup: BeautifulSoup) -> str | None:
    abstract = soup.find(class_=re.compile(r"ltx_abstract"))
    if not abstract:
        return None
    return abstract.get_text(" ", strip=True)


def _extract_abstract_html(soup: BeautifulSoup) -> str | None:
    """Extract the abstract div's inner HTML for conversion to markdown with figures.

    Returns the HTML content of the abstract div (excluding the div itself),
    or None if no abstract found. This allows processing figures inside the abstract.
    """
    abstract = soup.find(class_=re.compile(r"ltx_abstract"))
    if not abstract:
        return None
    inner = "".join(str(c) for c in abstract.children)
    return inner.strip() if inner.strip() else None


def _extract_submission_date(soup: BeautifulSoup) -> str | None:
    """Extract submission date from arXiv HTML.
    
    Returns date in YYYYMMDD format, or None if not found.
    """
    # Try to find date in various formats
    # Look for date patterns in the HTML
    date_patterns = [
        # Look for meta tags with date
        (lambda s: s.find("meta", attrs={"name": "citation_date"}), lambda tag: tag.get("content")),
        # Look for date in header/footer
        (lambda s: s.find(class_=re.compile(r"ltx_date|arxiv-date|submission-date")), lambda tag: tag.get_text()),
        # Look for date in title area
        (lambda s: s.find("time"), lambda tag: tag.get("datetime") or tag.get_text()),
    ]
    
    for finder, extractor in date_patterns:
        try:
            element = finder(soup)
            if element:
                date_str = extractor(element)
                if date_str:
                    # Try to parse and format as YYYYMMDD
                    parsed_date = _parse_date_string(date_str)
                    if parsed_date:
                        return parsed_date
        except Exception:
            continue
    
    # Fallback: try to extract from arXiv ID pattern in URL or metadata
    # This is less reliable but sometimes works
    return None


def _parse_date_string(date_str: str) -> str | None:
    """Parse various date formats and return YYYYMMDD."""
    import re
    from datetime import datetime
    
    # Try common date formats
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d %B %Y",
        "%B %d, %Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    
    # Clean the string
    date_str = date_str.strip()
    
    # Try to extract YYYY-MM-DD pattern
    match = re.search(r"(\d{4})[-\/](\d{1,2})[-\/](\d{1,2})", date_str)
    if match:
        year, month, day = match.groups()
        return f"{year}{month.zfill(2)}{day.zfill(2)}"
    
    # Try parsing with datetime
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y%m%d")
        except ValueError:
            continue
    
    return None


def _extract_sections(root: Tag) -> list[SectionNode]:
    headings = [heading for heading in _iter_headings(root) if not _is_title_heading(heading)]
    sections: list[SectionNode] = []
    stack: list[SectionNode] = []

    for heading in headings:
        level = int(heading.name[1])
        title = heading.get_text(" ", strip=True)
        anchor = heading.get("id") or heading.parent.get("id")
        html = _collect_section_html(heading)

        node = SectionNode(title=title, level=level, anchor=anchor, html=html)

        while stack and stack[-1].level >= level:
            stack.pop()

        if stack:
            stack[-1].children.append(node)
        else:
            sections.append(node)

        stack.append(node)

    return sections


def _iter_headings(root: Tag) -> Iterable[Tag]:
    for heading in root.find_all(_HEADING_RE):
        if heading.find_parent("nav"):
            continue
        if heading.find_parent(class_=re.compile(r"ltx_abstract")):
            continue
        yield heading


def _is_title_heading(heading: Tag) -> bool:
    classes = heading.get("class", [])
    return "ltx_title_document" in classes


def _collect_section_html(heading: Tag) -> str | None:
    section = heading.find_parent("section")
    if not section:
        return None

    parts: list[str] = []
    started = False
    for child in section.children:
        if child == heading:
            started = True
            continue
        if not started:
            continue
        if isinstance(child, Tag) and child.name == "section":
            continue
        if isinstance(child, Tag) and any(
            cls.startswith("ltx_section") or cls.startswith("ltx_subsection") or cls.startswith("ltx_subsubsection")
            for cls in child.get("class", [])
        ):
            continue
        if isinstance(child, NavigableString):
            text = str(child)
            if text.strip():
                parts.append(text)
            continue
        if isinstance(child, Tag):
            parts.append(str(child))
    html = "".join(parts).strip()
    return html or None

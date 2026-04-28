"""Parse arXiv HTML into metadata and section structure."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from arxiv2md_beta.schemas import SectionNode
from arxiv2md_beta.settings import get_settings

try:
    from bs4 import BeautifulSoup
    from bs4.element import NavigableString, Tag
except ImportError as exc:  # pragma: no cover - runtime dependency check
    raise RuntimeError("BeautifulSoup4 is required for HTML parsing (pip install beautifulsoup4).") from exc


_HEADING_RE = re.compile(r"^h[1-6]$")
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w.-]+\.\w+$")
# Keywords that indicate footnotes or contribution statements (case-insensitive check)
_SKIP_KEYWORDS = {"footnotemark:", "equal contribution", "work performed", "listing order"}
# Keywords that strongly suggest an affiliation line
_AFFILIATION_KEYWORDS = {
    "university", "college", "institute", "institution", "laboratory", "lab",
    "school", "center", "centre", "department", "faculty", "division",
    "academy", "consortium", "corporation", "corp", "inc", "ltd", "gmbh",
    "research", "google", "microsoft", "meta", "amazon", "apple", "deepmind",
    "openai", "anthropic", "nvidia", "intel", "ibm", "facebook", "twitter",
    "tesla", "uber", "lyft", "airbnb", "netflix", "stripe", "square",
    "berkeley", "stanford", "mit", "harvard", "caltech", "cmu", "princeton",
    "yale", "columbia", "cornell", "oxford", "cambridge", "eth", "epfl",
    "mpi", "inria", "cern", "nasa", "flatiron", "allen", "astera",
}
# Regex for footnote markers like *, **, 1, 12, †, ‡
_FOOTNOTE_MARKER_RE = re.compile(r"^[\*†‡§¶‖#♯\d]+$")


@dataclass
class ParsedAuthor:
    """An author record with optional affiliation(s)."""

    name: str
    affiliations: list[str] = field(default_factory=list)


@dataclass
class ParsedArxivHtml:
    """Parsed content extracted from arXiv HTML."""

    title: str | None
    authors: list[ParsedAuthor]
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
    authors = _extract_authors_with_affiliations(soup)
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
    # Try h1.ltx_title first (ar5iv HTML)
    title_tag = soup.find("h1", class_=re.compile(r"ltx_title"))
    if title_tag:
        # Filter out document type markers like [cs/0309048] Contents
        text = title_tag.get_text(" ", strip=True)
        # Remove arXiv ID patterns like [cs/0309048] or [math.AG/0211234]
        text = re.sub(r"^\s*\[[^\]]+\]\s*", "", text)
        # Remove "Contents" suffix if it's just a TOC page
        text = re.sub(r"\bContents\s*$", "", text).strip()
        if text:
            return text
    # Try other common title selectors
    for selector in ["h1.title", "h1.paper-title", "meta[property='og:title']"]:
        tag = soup.select_one(selector)
        if tag:
            text = tag.get("content") if tag.name == "meta" else tag.get_text(" ", strip=True)
            text = re.sub(r"^\s*\[[^\]]+\]\s*", "", text)
            text = re.sub(r"\bContents\s*$", "", text).strip()
            if text:
                return text
    # Fallback: try <title> but filter out site names
    if soup.title:
        text = soup.title.get_text(" ", strip=True)
        # Filter out common site title patterns
        text = re.sub(r"\s*[\|\-–—]\s*arXiv.*$", "", text, flags=re.I)
        text = re.sub(r"^\s*\[[^\]]+\]\s*", "", text)
        text = re.sub(r"\bContents\s*$", "", text).strip()
        if text and text.lower() not in ("arxiv", "", "contents"):
            return text
    return None


def _extract_authors(soup: BeautifulSoup) -> list[str]:
    """Extract author name strings (backward-compatible wrapper)."""
    return [a.name for a in _extract_authors_with_affiliations(soup)]


def _extract_authors_with_affiliations(soup: BeautifulSoup) -> list[ParsedAuthor]:
    """Extract structured author records (name + affiliations) from arXiv HTML.

    Handles multiple ar5iv HTML patterns:

    1. Structured ``ltx_creator ltx_role_author`` blocks containing
       ``ltx_personname`` + ``ltx_author_notes``.
    2. ``ltx_author`` / ``ltx_personname`` wrappers without explicit
       affiliation containers.
    3. Sequential flat spans where bold spans are names and following
       non-bold spans are affiliations (e.g. arXiv 2604.21691v1).
    """
    authors_container = soup.find("div", class_="ltx_authors")
    if not authors_container:
        document_root = _find_document_root(soup)
        authors_container = document_root.find("div", class_="ltx_authors")
    if not authors_container:
        return []

    # --- Strategy 1: structured author blocks ---
    structured = _parse_structured_author_blocks(authors_container)
    if structured:
        return structured

    # --- Strategy 2: sequential flat spans ---
    sequential = _parse_sequential_author_spans(authors_container)
    if sequential:
        return sequential

    return []


def _parse_structured_author_blocks(container: Tag) -> list[ParsedAuthor]:
    """Parse ``ltx_creator`` / ``ltx_role_author`` / ``ltx_author`` blocks."""
    creators = container.find_all(class_=re.compile(r"ltx_creator|ltx_role_author|ltx_author"))
    if not creators:
        return []

    results: list[ParsedAuthor] = []
    for creator in creators:
        # --- Strategy A: tabular layout with multiple authors per creator ---
        tabular = _parse_tabular_authors_in_creator(creator)
        if tabular:
            results.extend(tabular)
            continue

        # --- Strategy B: single author per creator (classic pattern) ---
        personname = creator.find(class_=re.compile(r"ltx_personname"))
        if not personname:
            continue
        name = _clean_single_author_text(personname)
        if not name:
            continue

        # Look for affiliation in sibling/following elements within the creator
        affils: list[str] = []
        # Direct affiliation containers
        for aff_class in ("ltx_author_notes", "ltx_role_address", "ltx_address"):
            aff_node = creator.find(class_=re.compile(aff_class))
            if aff_node:
                aff_text = _clean_single_author_text(aff_node)
                if aff_text and aff_text != name:
                    affils.append(aff_text)

        # Also check for italic text spans inside the creator (common pattern)
        if not affils:
            for span in creator.find_all("span", class_=re.compile(r"ltx_text")):
                classes = span.get("class", [])
                if "ltx_font_italic" in classes or "ltx_font_bold" not in classes:
                    aff_text = _clean_single_author_text(span)
                    if aff_text and aff_text != name and _looks_like_affiliation(aff_text):
                        affils.append(aff_text)

        affils = _dedupe_strings(affils)
        results.append(ParsedAuthor(name=name, affiliations=affils))

    if results:
        return results
    return []


def _parse_tabular_authors_in_creator(creator: Tag) -> list[ParsedAuthor]:
    """Extract multiple author/affiliation pairs from a tabular creator layout.

    Some ar5iv papers (e.g. 2604.21691v1) render all authors in a single
    ``ltx_creator`` using a table where each cell contains a bold name span
    and an italic affiliation span.
    """
    # Look for table cells — ar5iv sometimes uses <span class="ltx_td"> rather
    # than real <td> elements, so we check both.
    cells: list[Tag] = creator.find_all("span", class_=re.compile(r"ltx_td"))
    if not cells:
        cells = creator.find_all("td", class_=re.compile(r"ltx_td"))
    if cells:
        return _extract_authors_from_cells(cells)

    # Fallback: bold + italic spans directly inside the personname without cells
    personname = creator.find(class_=re.compile(r"ltx_personname"))
    if personname:
        bolds = personname.find_all("span", class_=re.compile(r"ltx_font_bold"))
        italics = personname.find_all("span", class_=re.compile(r"ltx_font_italic"))
        if len(bolds) > 1 and len(italics) >= len(bolds):
            return _pair_bold_italic_spans(bolds, italics)

    return []


def _extract_authors_from_cells(cells: list[Tag]) -> list[ParsedAuthor]:
    """Extract author + affiliation from table cells."""
    results: list[ParsedAuthor] = []
    for cell in cells:
        bolds = cell.find_all("span", class_=re.compile(r"ltx_font_bold"))
        if not bolds:
            continue
        name = _clean_single_author_text(bolds[0])
        if not name:
            continue
        affils: list[str] = []
        for italic in cell.find_all("span", class_=re.compile(r"ltx_font_italic")):
            aff_text = _clean_single_author_text(italic)
            if aff_text and aff_text != name and _looks_like_affiliation(aff_text):
                affils.append(aff_text)
        results.append(ParsedAuthor(name=name, affiliations=_dedupe_strings(affils)))
    return results


def _pair_bold_italic_spans(bolds: list[Tag], italics: list[Tag]) -> list[ParsedAuthor]:
    """Pair bold spans (names) with the nearest following italic spans (affiliations)."""
    # Build a position map using the order they appear in the DOM
    # (BeautifulSoup iterates in document order)
    all_nodes: list[tuple[Tag, str]] = []
    for b in bolds:
        all_nodes.append((b, "bold"))
    for i in italics:
        all_nodes.append((i, "italic"))
    # Sort by sourceline; if equal, use the original list order as tie-breaker
    all_nodes.sort(key=lambda x: (x[0].sourceline or 0, id(x[0])))

    results: list[ParsedAuthor] = []
    i = 0
    while i < len(all_nodes):
        node, kind = all_nodes[i]
        if kind == "bold":
            name = _clean_single_author_text(node)
            if name:
                # Collect following italic spans until next bold
                affils: list[str] = []
                j = i + 1
                while j < len(all_nodes) and all_nodes[j][1] == "italic":
                    aff_text = _clean_single_author_text(all_nodes[j][0])
                    if aff_text and aff_text != name and _looks_like_affiliation(aff_text):
                        affils.append(aff_text)
                    j += 1
                results.append(ParsedAuthor(name=name, affiliations=_dedupe_strings(affils)))
        i += 1

    return results


def _parse_sequential_author_spans(container: Tag) -> list[ParsedAuthor]:
    """Parse flat sequential spans: bold = name, following non-bold = affiliation."""
    # Gather all meaningful text nodes from the container's children
    candidates: list[tuple[Tag | NavigableString, bool]] = []
    for child in container.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                candidates.append((child, False))
        elif isinstance(child, Tag):
            if child.name in ("script", "style"):
                continue
            # Skip nested structures that are already handled
            classes = " ".join(child.get("class", []))
            if "ltx_creator" in classes or "ltx_role_author" in classes or "ltx_author" in classes:
                # Let structured parser handle these
                continue
            text = _get_clean_text(child)
            if text:
                is_bold = "ltx_font_bold" in classes or child.name in ("b", "strong")
                candidates.append((child, is_bold))

    if not candidates:
        return []

    # Try to pair names with affiliations
    results: list[ParsedAuthor] = []
    i = 0
    while i < len(candidates):
        node, is_bold = candidates[i]
        text = _get_clean_text(node) if isinstance(node, Tag) else str(node).strip()
        text = re.sub(r"\s+", " ", text).strip()

        # Skip empty / invalid
        if not text or _EMAIL_RE.match(text) or _FOOTNOTE_MARKER_RE.match(text):
            i += 1
            continue

        # Skip footnote keywords
        if any(marker in text.lower() for marker in _SKIP_KEYWORDS):
            i += 1
            continue

        # Skip very long text (likely contribution statements)
        if len(text) > get_settings().parsing.max_author_part_length:
            i += 1
            continue

        # Skip sentences
        if text.count(".") > 1 or (text.endswith(".") and len(text) > 40):
            i += 1
            continue

        # Determine if this is a name or affiliation
        if is_bold or _looks_like_name(text):
            # It's a name
            name = text.lstrip("&").strip()
            if not name:
                i += 1
                continue

            # Look ahead for affiliation(s)
            affils: list[str] = []
            j = i + 1
            while j < len(candidates):
                next_node, next_bold = candidates[j]
                next_text = (
                    _get_clean_text(next_node)
                    if isinstance(next_node, Tag)
                    else str(next_node).strip()
                )
                next_text = re.sub(r"\s+", " ", next_text).strip()

                if not next_text or _EMAIL_RE.match(next_text) or _FOOTNOTE_MARKER_RE.match(next_text):
                    j += 1
                    continue
                if any(marker in next_text.lower() for marker in _SKIP_KEYWORDS):
                    j += 1
                    continue

                # If we hit another name, stop
                if next_bold or _looks_like_name(next_text):
                    break

                # If it looks like an affiliation, collect it
                if _looks_like_affiliation(next_text):
                    affils.append(next_text)
                    j += 1
                else:
                    # Ambiguous: if short, might be part of name; otherwise skip
                    break

            affils = _dedupe_strings(affils)
            results.append(ParsedAuthor(name=name, affiliations=affils))
            i = j
        else:
            # Not a name, skip
            i += 1

    return results


def _looks_like_name(text: str) -> bool:
    """Heuristic: does ``text`` look like a person name (not affiliation)?"""
    # Strip footnote markers
    cleaned = re.sub(r"[\*†‡§¶‖#♯\d]+$", "", text).strip()
    if not cleaned:
        return False

    words = cleaned.split()
    # Names are typically 2-5 words
    if len(words) < 1 or len(words) > 6:
        return False

    # Affiliation keywords in the text → probably not a name
    lower = cleaned.lower()
    for kw in _AFFILIATION_KEYWORDS:
        if kw in lower:
            return False

    # Contains comma → likely affiliation or multi-part address
    if "," in cleaned:
        return False

    # All caps → likely acronym/institution
    if cleaned.isupper() and len(cleaned) > 3:
        return False

    # Looks like an email
    if "@" in cleaned:
        return False

    return True


def _looks_like_affiliation(text: str) -> bool:
    """Heuristic: does ``text`` look like an affiliation line?"""
    cleaned = text.strip()
    if not cleaned or len(cleaned) < 2:
        return False

    # Contains affiliation keyword
    lower = cleaned.lower()
    for kw in _AFFILIATION_KEYWORDS:
        if kw in lower:
            return True

    # Contains address-like elements (city, country)
    if re.search(r"\b[A-Z][a-z]+,\s*[A-Z][a-zA-Z\s]+\b", cleaned):
        return True

    # Looks like "Org1 and Org2"
    if " and " in lower and len(cleaned.split()) <= 6:
        for part in lower.split(" and "):
            if any(kw in part for kw in _AFFILIATION_KEYWORDS):
                return True

    # Short text (1-4 words) that doesn't look like a name → could be affiliation
    words = cleaned.split()
    if 1 <= len(words) <= 4:
        # If it contains digits or special chars, likely not a name
        if re.search(r"[\d()\[\]]", cleaned):
            return True
        # Single-word capitalized text could be an org (e.g. "DeepMind")
        if len(words) == 1 and cleaned[0].isupper():
            return True

    return False


def _get_clean_text(tag: Tag) -> str:
    """Get normalized text from a tag, removing footnote markers."""
    clone = BeautifulSoup(str(tag), "html.parser")
    for sup in clone.find_all("sup"):
        sup.decompose()
    for note in clone.find_all(class_=re.compile(r"ltx_note|ltx_role_footnote")):
        note.decompose()
    text = clone.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _clean_single_author_text(node: Tag | NavigableString) -> str:
    """Extract a single clean text string from a node, filtering out noise."""
    if isinstance(node, NavigableString):
        text = str(node).strip()
    else:
        text = _get_clean_text(node)

    if not text:
        return ""

    # Strip leading &
    text = text.lstrip("&").strip()

    # Skip emails
    if _EMAIL_RE.match(text):
        return ""

    # Skip pure numbers
    if text.isdigit():
        return ""

    # Skip footnote markers
    if _FOOTNOTE_MARKER_RE.match(text):
        return ""

    # Skip footnote keywords
    if any(marker in text.lower() for marker in _SKIP_KEYWORDS):
        return ""

    # Skip very long text
    if len(text) > get_settings().parsing.max_author_part_length:
        return ""

    # Skip sentences
    if text.count(".") > 1 or (text.endswith(".") and len(text) > 40):
        return ""

    return text


def _dedupe_strings(parts: list[str]) -> list[str]:
    """Remove duplicates preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


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
        if len(part) > get_settings().parsing.max_author_part_length:
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

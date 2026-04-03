"""Format arXiv sections into summary, tree, and content outputs."""

from __future__ import annotations

import re
from typing import Iterable

try:
    import tiktoken
except ImportError:  # pragma: no cover - optional dependency
    tiktoken = None

from arxiv2md_beta.html.sections import split_sections_at_reference
from arxiv2md_beta.schemas import IngestionResult, SectionNode
from arxiv2md_beta.settings import get_settings

_ABSTRACT_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+abstract\s*\n+", re.IGNORECASE)
_SLUGIFY_PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUGIFY_HYPHEN_RE = re.compile(r"-{2,}")
_ANCHOR_APPENDIX_RE = re.compile(r"Appendix\s+([A-Z])\b", re.I)
_ANCHOR_SUBSECTION_RE = re.compile(r"^(\d+)\.(\d+)\s")
_ANCHOR_SECTION_RE = re.compile(r"^(\d+)\s")
_ANCHOR_TAG_NEWLINE_RE = re.compile(r'(<a id="[^"]+"></a>)\n(?!\n)(?!\s*$)')
_TABLE_CAPTION_RE = re.compile(r'\n\*\*(Table\s+\d+[^*]*)\*\*\s*\n(\|[^\n]*)')
_DISPLAY_MATH_RE = re.compile(r'\$\$\n(.*?)\n\$\$', re.DOTALL)
_DUPLICATE_BULLET_RE = re.compile(r"(?m)^(\s*-\s+)[•·◦]\s+")
_EXCESS_EMPTY_LINES_RE = re.compile(r"\n{3,}")
_WHITESPACE_TO_HYPHEN_RE = re.compile(r"\s+")


def format_paper(
    *,
    arxiv_id: str,
    version: str | None,
    title: str | None,
    authors: list[str],
    abstract: str | None,
    sections: list[SectionNode],
    include_toc: bool,
    include_abstract_in_tree: bool = True,
    split_for_reference: bool = False,
) -> IngestionResult:
    """Create summary, section tree, and content.

    If ``split_for_reference`` is True, ``content`` is only the main body
    (TOC + abstract + sections before the first References/Bibliography heading);
    ``content_references`` and ``content_appendix`` hold the rest. Summary and
    section tree still describe the full section tree.
    """
    tree_lines = ["Sections:"]
    if include_abstract_in_tree:
        tree_lines.append("Abstract")
    tree_lines.append(_create_sections_tree(sections))
    tree = "\n".join(tree_lines)

    content_references: str | None = None
    content_appendix: str | None = None

    if split_for_reference:
        ing = get_settings().ingestion
        main_sections, ref_sections, appendix_sections = split_sections_at_reference(
            sections, reference_titles=ing.reference_section_titles
        )
        content = _render_content(abstract=abstract, sections=main_sections, include_toc=include_toc)
        content = _format_markdown_output(content)
        ref_raw = _render_content(abstract=None, sections=ref_sections, include_toc=False)
        content_references = _format_markdown_output(ref_raw) if ref_raw.strip() else None
        app_raw = _render_content(abstract=None, sections=appendix_sections, include_toc=False)
        content_appendix = _format_markdown_output(app_raw) if app_raw.strip() else None
    else:
        content = _render_content(abstract=abstract, sections=sections, include_toc=include_toc)
        content = _format_markdown_output(content)

    summary_lines = []
    if title:
        summary_lines.append(f"# Title: {title}")
    summary_lines.append(f"- ArXiv: {arxiv_id}")
    if version:
        summary_lines.append(f"- Version: {version}")
    if authors:
        summary_lines.append(f"- Authors: {', '.join(authors)}")
    summary_lines.append(f"- Sections: {count_sections(sections)}")

    token_body = content
    if content_references is not None:
        token_body = "\n".join(
            x for x in (content, content_references, content_appendix or "") if x
        )
    token_estimate = _format_token_count(tree + "\n" + token_body)
    if token_estimate:
        summary_lines.append(f"- Estimated tokens: {token_estimate}")

    summary = "\n".join(summary_lines)

    return IngestionResult(
        summary=summary,
        sections_tree=tree,
        content=content,
        content_references=content_references,
        content_appendix=content_appendix,
    )


def count_sections(sections: Iterable[SectionNode]) -> int:
    """Count total sections in the tree."""
    total = 0
    for section in sections:
        total += 1
        total += count_sections(section.children)
    return total


def _render_content(
    *,
    abstract: str | None,
    sections: list[SectionNode],
    include_toc: bool,
) -> str:
    blocks: list[str] = []
    if include_toc:
        toc = _render_toc(sections, include_abstract=bool(abstract))
        if toc:
            blocks.append("## Contents\n" + toc)

    if abstract:
        blocks.append("## Abstract")
        blocks.append(_normalize_abstract_markdown(abstract))

    for section in sections:
        blocks.extend(_render_section(section))

    return "\n\n".join(block for block in blocks if block).strip()


def _normalize_abstract_markdown(abstract: str) -> str:
    """Remove duplicated in-body abstract headings from parsed HTML fragments."""
    text = abstract.strip()
    text = _ABSTRACT_HEADING_RE.sub("", text)
    return text.strip()


def _slugify_markdown_anchor(title: str) -> str:
    """Create a GitHub-style markdown heading anchor from a section title."""
    text = (title or "").strip().lower()
    text = _SLUGIFY_PUNCT_RE.sub("", text)
    text = text.replace("_", "")
    text = _WHITESPACE_TO_HYPHEN_RE.sub("-", text)
    text = _SLUGIFY_HYPHEN_RE.sub("-", text).strip("-")
    return text or "section"


def _link_for_heading_title(title: str) -> str:
    """Render markdown link for a heading title using slug anchor."""
    anchor = _slugify_markdown_anchor(title)
    return f"[{title}](#{anchor})"


def _anchor_for_section_title(title: str) -> str | None:
    """Extract markdown anchor id from section title for internal links.

    Examples: "1 Introduction" -> "section-1", "Appendix A More Details" -> "appendix-a",
    "4.1 Learning World Model" -> "section-4-1".
    """
    if not title:
        return None
    # Appendix A, Appendix B, ...
    m = _ANCHOR_APPENDIX_RE.match(title)
    if m:
        return f"appendix-{m.group(1).lower()}"
    # 4.1, 5.2, ... (subsection)
    m = _ANCHOR_SUBSECTION_RE.match(title)
    if m:
        return f"section-{m.group(1)}-{m.group(2)}"
    # 1, 2, 3, ... (main section)
    m = _ANCHOR_SECTION_RE.match(title)
    if m:
        return f"section-{m.group(1)}"
    return None


def _render_section(section: SectionNode) -> list[str]:
    blocks: list[str] = []
    heading_prefix = "#" * min(section.level, 6)
    anchor_id = _anchor_for_section_title(section.title)
    if anchor_id:
        blocks.append(f'<a id="{anchor_id}"></a>')
    blocks.append(f"{heading_prefix} {section.title}")
    if section.markdown:
        blocks.append(section.markdown)
    if section.children:
        child_toc = _render_child_toc(section.children)
        if child_toc:
            blocks.append(child_toc)
    for child in section.children:
        blocks.extend(_render_section(child))
    return blocks


def _render_child_toc(sections: list[SectionNode]) -> str:
    """Render direct child-section TOC for a section block."""
    lines = [f"- {_link_for_heading_title(section.title)}" for section in sections]
    return "\n".join(lines)


def _render_toc(sections: list[SectionNode], indent: int = 0, include_abstract: bool = False) -> str:
    lines: list[str] = []
    if indent == 0 and include_abstract:
        lines.append("- [Abstract](#abstract)")
    for section in sections:
        prefix = "  " * indent + "- "
        lines.append(prefix + _link_for_heading_title(section.title))
        if section.children:
            lines.append(_render_toc(section.children, indent + 1, include_abstract=False))
    return "\n".join(lines)


def _create_sections_tree(sections: list[SectionNode], indent: int = 0) -> str:
    lines: list[str] = []
    for section in sections:
        lines.append(" " * (indent * 4) + section.title)
        if section.children:
            lines.append(_create_sections_tree(section.children, indent + 1))
    return "\n".join(lines)


def _format_markdown_output(markdown: str) -> str:
    """Apply formatting rules for tags, table captions, and display math.

    - Ensure newline after anchor tags (<a id="..."></a>) when followed by content
    - Convert table captions **Table N: ...** to blockquote > Table N: ... with newline before table
    - Simplify display math ($$...$$) to remove $ that break markdown parsing
    """
    if not markdown:
        return markdown

    # 1. Ensure newline after anchor tags when followed immediately by non-blank content
    markdown = _ANCHOR_TAG_NEWLINE_RE.sub(r'\1\n\n', markdown)

    # 2. Table captions: **Table N: ...** before | -> > Table N: ... with newline before table
    markdown = _TABLE_CAPTION_RE.sub(r'\n\n> \1\n\n\2', markdown)

    # 3. Simplify display math blocks: remove/sanitize $ inside $$...$$ for markdown compatibility
    from arxiv2md_beta.html.markdown import _simplify_display_math

    def _replace_display_math(m: re.Match) -> str:
        inner = _simplify_display_math(m.group(1))
        return f"$$\n{inner}\n$$"

    markdown = _DISPLAY_MATH_RE.sub(_replace_display_math, markdown)

    # 4. Normalize duplicated bullet markers generated by source text (e.g., "- • item")
    markdown = _DUPLICATE_BULLET_RE.sub(r"\1", markdown)

    # 5. Avoid excessive empty lines for cleaner reading
    markdown = _EXCESS_EMPTY_LINES_RE.sub("\n\n", markdown)

    return markdown.strip()


def _format_token_count(text: str) -> str | None:
    if not tiktoken:
        return None
    try:
        encoding = tiktoken.get_encoding(get_settings().output.tiktoken_encoding)
        total_tokens = len(encoding.encode(text, disallowed_special=()))
    except Exception:
        return None

    if total_tokens >= 1_000_000:
        return f"{total_tokens / 1_000_000:.1f}M"
    if total_tokens >= 1_000:
        return f"{total_tokens / 1_000:.1f}k"
    return str(total_tokens)

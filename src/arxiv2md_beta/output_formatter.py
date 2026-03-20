"""Format arXiv sections into summary, tree, and content outputs."""

from __future__ import annotations

import re
from typing import Iterable

try:
    import tiktoken
except ImportError:  # pragma: no cover - optional dependency
    tiktoken = None

from arxiv2md_beta.schemas import IngestionResult, SectionNode


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
) -> IngestionResult:
    """Create summary, section tree, and content."""
    tree_lines = ["Sections:"]
    if include_abstract_in_tree:
        tree_lines.append("Abstract")
    tree_lines.append(_create_sections_tree(sections))
    tree = "\n".join(tree_lines)
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

    token_estimate = _format_token_count(tree + "\n" + content)
    if token_estimate:
        summary_lines.append(f"- Estimated tokens: {token_estimate}")

    summary = "\n".join(summary_lines)

    return IngestionResult(summary=summary, sections_tree=tree, content=content)


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
        toc = _render_toc(sections)
        if toc:
            blocks.append("## Contents\n" + toc)

    if abstract:
        blocks.append("## Abstract")
        blocks.append(abstract.strip())

    for section in sections:
        blocks.extend(_render_section(section))

    return "\n\n".join(block for block in blocks if block).strip()


def _anchor_for_section_title(title: str) -> str | None:
    """Extract markdown anchor id from section title for internal links.

    Examples: "1 Introduction" -> "section-1", "Appendix A More Details" -> "appendix-a",
    "4.1 Learning World Model" -> "section-4-1".
    """
    if not title:
        return None
    # Appendix A, Appendix B, ...
    m = re.match(r"Appendix\s+([A-Z])\b", title, re.I)
    if m:
        return f"appendix-{m.group(1).lower()}"
    # 4.1, 5.2, ... (subsection)
    m = re.match(r"^(\d+)\.(\d+)\s", title)
    if m:
        return f"section-{m.group(1)}-{m.group(2)}"
    # 1, 2, 3, ... (main section)
    m = re.match(r"^(\d+)\s", title)
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
    for child in section.children:
        blocks.extend(_render_section(child))
    return blocks


def _render_toc(sections: list[SectionNode], indent: int = 0) -> str:
    lines: list[str] = []
    for section in sections:
        prefix = "  " * indent + "- "
        lines.append(prefix + section.title)
        if section.children:
            lines.append(_render_toc(section.children, indent + 1))
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
    markdown = re.sub(r'(<a id="[^"]+"></a>)\n(?!\n)(?!\s*$)', r'\1\n\n', markdown)

    # 2. Table captions: **Table N: ...** before | -> > Table N: ... with newline before table
    markdown = re.sub(
        r'\n\*\*(Table\s+\d+[^*]*)\*\*\s*\n(\|[^\n]*)',
        r'\n\n> \1\n\n\2',
        markdown,
    )

    # 3. Simplify display math blocks: remove/sanitize $ inside $$...$$ for markdown compatibility
    from arxiv2md_beta.markdown import _simplify_display_math

    def _replace_display_math(m: re.Match) -> str:
        inner = _simplify_display_math(m.group(1))
        return f"$$\n{inner}\n$$"

    markdown = re.sub(r'\$\$\n(.*?)\n\$\$', _replace_display_math, markdown, flags=re.DOTALL)

    return markdown.strip()


def _format_token_count(text: str) -> str | None:
    if not tiktoken:
        return None
    try:
        encoding = tiktoken.get_encoding("o200k_base")
        total_tokens = len(encoding.encode(text, disallowed_special=()))
    except Exception:
        return None

    if total_tokens >= 1_000_000:
        return f"{total_tokens / 1_000_000:.1f}M"
    if total_tokens >= 1_000:
        return f"{total_tokens / 1_000:.1f}k"
    return str(total_tokens)

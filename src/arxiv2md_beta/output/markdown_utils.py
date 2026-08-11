"""Shared Markdown utilities used by both the IR orchestrator and the legacy formatter.

These helpers were previously private (``_``-prefixed) symbols living inside
``output/formatter.py`` and ``html/markdown.py``. The IR orchestrator imported
them across module boundaries, which coupled the new IR path to the legacy
formatter's internals. They now live here as public, dependency-light utilities.

Depends only on :mod:`schemas`, :mod:`settings`, ``re`` and ``tiktoken`` — a leaf
module with no circular-import surface.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

from arxiv2md_beta.schemas import SectionNode
from arxiv2md_beta.settings import get_settings

try:
    import tiktoken
except ImportError:  # pragma: no cover - optional dependency
    tiktoken = None  # type: ignore[assignment]

# ── tiktoken encoding cache ──────────────────────────────────────────────────

_tiktoken_encoding_cache: dict[str, Any] = {}


def _get_cached_encoding(encoding_name: str) -> Any:
    """Return a cached tiktoken encoding, or None if tiktoken is unavailable."""
    if encoding_name in _tiktoken_encoding_cache:
        return _tiktoken_encoding_cache[encoding_name]
    if tiktoken is None:
        return None
    enc = tiktoken.get_encoding(encoding_name)
    _tiktoken_encoding_cache[encoding_name] = enc
    return enc


# ── Section tree helpers ─────────────────────────────────────────────────────


def count_sections(sections: Iterable[SectionNode]) -> int:
    """Count total sections in the tree (recursive)."""
    total = 0
    for section in sections:
        total += 1
        total += count_sections(section.children)
    return total


def create_sections_tree(sections: list[SectionNode], indent: int = 0) -> str:
    """Render a section tree as indented text (one title per line)."""
    lines: list[str] = []
    for section in sections:
        lines.append(" " * (indent * 4) + section.title)
        if section.children:
            lines.append(create_sections_tree(section.children, indent + 1))
    return "\n".join(lines)


# ── Display-math simplification ──────────────────────────────────────────────
# Moved verbatim from html/markdown.py so the legacy converter and the shared
# ``format_markdown_output`` can both use it without one importing the other.

_RAISEBOX_RE = re.compile(
    r"\\raisebox\{[^}]+\}\{\\hbox to 0\.0\s*pt\{\\hss\\vbox to 0\.0\s*pt\{\\hbox\{\$([^$]*)\$\}\\vss\}\}\}",
    re.DOTALL,
)
_TRAIL_EQN_RE = re.compile(r"\$\s*\((\d+)\)\s*$")
_UNESCAPED_DOLLAR_RE = re.compile(r"(?<!\\)\$")


def simplify_display_math(content: str) -> str:
    r"""Simplify display math for Markdown compatibility.

    ar5iv annotations can contain ``$`` (e.g. ``\\hbox{$...$}``) and
    ``\\raisebox``/``\\hbox``/``\\vbox`` that cause Markdown parsers to treat
    inner ``$`` as inline math delimiters and KaTeX/MathJax to fail on complex
    layout. We simplify by removing ``$`` and replacing complex layout with its
    semantic content.
    """
    # 1. Simplify \raisebox{\hbox to 0.0pt{\hss\vbox to 0.0pt{\hbox{$X$}\vss}}} -> X
    content = _RAISEBOX_RE.sub(r"\1", content)
    # 2. Remove trailing $ before equation number: "$ (1)" -> "(1)"
    content = _TRAIL_EQN_RE.sub(r"(\1)", content)
    # 3. Replace $} with } (fix \hbox{...$} without breaking brace structure)
    content = content.replace("$}", "}")
    # 4. Remove all remaining unescaped $ (they break markdown $$ block parsing)
    content = _UNESCAPED_DOLLAR_RE.sub("", content)
    return content


# ── Markdown output formatting ───────────────────────────────────────────────

_ANCHOR_TAG_NEWLINE_RE = re.compile(r'(<a id="[^"]+"></a>)\n(?!\n)(?!\s*$)')
_TABLE_CAPTION_RE = re.compile(r"\n\*\*(Table\s+\d+[^*]*)\*\*\s*\n(\|[^\n]*)")
_DISPLAY_MATH_RE = re.compile(
    r"^(\s*\$\$\n)(.*?)(\n\s*\$\$)",
    re.DOTALL | re.MULTILINE,
)
_DUPLICATE_BULLET_RE = re.compile(r"(?m)^(\s*-\s+)[•·◦]\s+")
_EXCESS_EMPTY_LINES_RE = re.compile(r"\n{3,}")


def format_markdown_output(markdown: str) -> str:
    """Apply formatting rules for anchor tags, table captions, and display math.

    - Ensure newline after anchor tags (``<a id="..."></a>``) when followed by content.
    - Convert table captions ``**Table N: ...**`` to blockquote ``> Table N: ...``
      with a newline before the table.
    - Simplify display math (``$$...$$``) to remove ``$`` that break Markdown parsing.
    - Collapse duplicate bullet markers (e.g. ``- • item`` → ``- item``).
    - Collapse excessive empty lines.
    """
    if not markdown:
        return markdown

    # 1. Ensure newline after anchor tags when followed immediately by non-blank content
    markdown = _ANCHOR_TAG_NEWLINE_RE.sub(r"\1\n\n", markdown)

    # 2. Table captions: **Table N: ...** before | -> > Table N: ... with newline before table
    markdown = _TABLE_CAPTION_RE.sub(r"\n\n> \1\n\n\2", markdown)

    # 3. Simplify display math blocks: remove/sanitize $ inside $$...$$ for markdown compatibility
    def _replace_display_math(m: re.Match) -> str:
        inner = simplify_display_math(m.group(2))
        return f"{m.group(1)}{inner}{m.group(3)}"

    markdown = _DISPLAY_MATH_RE.sub(_replace_display_math, markdown)

    # 4. Normalize duplicated bullet markers generated by source text (e.g., "- • item")
    markdown = _DUPLICATE_BULLET_RE.sub(r"\1", markdown)

    # 5. Avoid excessive empty lines for cleaner reading
    markdown = _EXCESS_EMPTY_LINES_RE.sub("\n\n", markdown)

    return markdown.strip()


# ── Token counting ───────────────────────────────────────────────────────────


def format_token_count(text: str) -> str | None:
    """Return a human-readable tiktoken token count, or None if unavailable."""
    encoding = _get_cached_encoding(get_settings().output.tiktoken_encoding)
    if encoding is None:
        return None
    try:
        total_tokens = len(encoding.encode(text, disallowed_special=()))
    except Exception:
        return None

    if total_tokens >= 1_000_000:
        return f"{total_tokens / 1_000_000:.1f}M"
    if total_tokens >= 1_000:
        return f"{total_tokens / 1_000:.1f}k"
    return str(total_tokens)

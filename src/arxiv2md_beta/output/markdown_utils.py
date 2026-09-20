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
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from arxiv2md_beta.settings import get_settings

if TYPE_CHECKING:
    from arxiv2md_beta.schemas import SectionNode

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


# ── Fenced-code protection ───────────────────────────────────────────────────
# Regex-based postprocessing rules must not rewrite the *contents* of code
# blocks (dollar amounts read as math, ``**Table 1**`` turned into a quote,
# blank lines collapsed inside a fence...). Callers wrap their rule passes in
# :func:`protect_fenced_code` / :func:`restore_protected_code`: fences are
# lifted out into a placeholder line and put back verbatim afterwards.

_FENCE_OPEN_RE = re.compile(r"( {0,7})(`{3,}|~{3,})")
_FENCED_PLACEHOLDER_RE = re.compile(r"^\x00FENCED_CODE_(\d+)\x00$", re.MULTILINE)


def protect_fenced_code(text: str) -> tuple[str, list[str]]:
    """Lift fenced code blocks out of *text*, leaving one placeholder line each.

    Returns ``(protected_text, saved_blocks)``; pass both to
    :func:`restore_protected_code` after the rule passes. Unclosed fences are
    safe: the whole tail is saved and restored verbatim.
    """
    saved: list[str] = []
    out: list[str] = []
    current: list[str] | None = None
    fence_char: str | None = None
    for line in text.split("\n"):
        if fence_char is None:
            m = _FENCE_OPEN_RE.match(line)
            if m:
                fence_char = m.group(2)[0]
                current = [line]
                saved.append("")  # slot for this block, filled on close
                out.append(f"\x00FENCED_CODE_{len(saved) - 1}\x00")
                continue
            out.append(line)
        else:
            assert current is not None  # fence open implies a block in progress
            current.append(line)
            # Concatenated (not an f-string): "{0,7}" is a regex quantifier;
            # inside an f-string it would be a format field, silently
            # breaking closing-fence detection.
            if re.match(r" {0,7}" + re.escape(fence_char) + r"{3,}\s*$", line):
                saved[-1] = "\n".join(current)
                current = None
                fence_char = None
    if current is not None:  # unclosed fence — restore the tail verbatim
        saved[-1] = "\n".join(current)
    return "\n".join(out), saved


def restore_protected_code(text: str, saved: list[str]) -> str:
    """Put fenced blocks saved by :func:`protect_fenced_code` back in place."""
    if not saved:
        return text
    return _FENCED_PLACEHOLDER_RE.sub(lambda m: saved[int(m.group(1))], text)


# ── Markdown output formatting ───────────────────────────────────────────────

_ANCHOR_TAG_NEWLINE_RE = re.compile(r'(<a id="[^"]+"></a>)\n(?!\n)(?!\s*$)')
_TABLE_CAPTION_RE = re.compile(r"\n\*\*(Table\s+\d+[^*]*)\*\*\s*\n(\|[^\n]*)")
_DISPLAY_MATH_RE = re.compile(
    r"^(\s*\$\$\n)(.*?)(\n\s*\$\$)",
    re.DOTALL | re.MULTILINE,
)
_DUPLICATE_BULLET_RE = re.compile(r"(?m)^(\s*-\s+)[•·◦]\s+")


def format_markdown_output(markdown: str) -> str:
    """Apply formatting rules for anchor tags, table captions, and display math.

    - Ensure newline after anchor tags (``<a id="..."></a>``) when followed by content.
    - Convert table captions ``**Table N: ...**`` to blockquote ``> Table N: ...``
      with a newline before the table.
    - Simplify display math (``$$...$$``) to remove ``$`` that break Markdown parsing.
    - Collapse duplicate bullet markers (e.g. ``- • item`` → ``- item``).

    Fenced code blocks are lifted out first: none of the rules may rewrite
    their contents.
    """
    if not markdown:
        return markdown

    markdown, saved_fences = protect_fenced_code(markdown)

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

    # Blank-line collapsing happens once, in finalize_markdown (the last
    # postprocess step) — no per-step collapse here.

    markdown = restore_protected_code(markdown, saved_fences)
    return markdown.strip()


# ── Token counting ───────────────────────────────────────────────────────────


def count_tokens(text: str) -> int | None:
    """Return the raw tiktoken token count, or None if unavailable.

    Unlike :func:`format_token_count` this returns a comparable integer, so
    callers can use it for threshold checks (e.g. the stub quality gate).
    """
    encoding = _get_cached_encoding(get_settings().output.tiktoken_encoding)
    if encoding is None:
        return None
    try:
        return len(encoding.encode(text, disallowed_special=()))
    except Exception:
        return None


def format_token_count(text: str) -> str | None:
    """Return a human-readable tiktoken token count, or None if unavailable."""
    total_tokens = count_tokens(text)
    if total_tokens is None:
        return None

    if total_tokens >= 1_000_000:
        return f"{total_tokens / 1_000_000:.1f}M"
    if total_tokens >= 1_000:
        return f"{total_tokens / 1_000:.1f}k"
    return str(total_tokens)

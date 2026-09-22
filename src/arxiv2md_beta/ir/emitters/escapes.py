"""Single-source escaping policies for the Markdown emitter.

Every context that interpolates IR text into Markdown syntax (link text, URL
position, pipe-table cells, raw-HTML attributes) goes through exactly one
function from this module — the emitter never escapes ad hoc. Keeping the
policies in one place is what let the single-image and multi-image figure
paths drift into emitting broken output while the grid and inline paths
escaped correctly (audit4 B1).
"""

from __future__ import annotations

import re

# ── Markdown syntax positions ─────────────────────────────────────────


def escape_md_text(text: str) -> str:
    r"""Escape characters that would break ``[text](url)`` link/image syntax."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def escape_url(url: str) -> str:
    r"""Percent-encode whitespace/parens so they cannot terminate ``(url)``."""
    return re.sub(r"([ ()])", lambda m: f"%{ord(m.group(1)):02X}", url)


def escape_pipe_cell(text: str) -> str:
    """Escape unescaped ``|`` characters inside a pipe-table cell."""
    return re.sub(r"(?<!\\)\|", r"\\|", text)


def escape_math_pipes(latex: str) -> str:
    r"""Rewrite bare ``|`` in math as ``\vert `` so a pipe table cell survives.

    Math has its own escape character: ``\|`` is the norm delimiter (‖), so a
    pipe may only be rewritten when it is *not* escaped by an odd backslash
    run — ``$P(a|b)$`` becomes ``$P(a\vert b)$`` (audit4 B2) while
    ``$\|x\|_F$`` keeps its norms (audit5 C3: the first cut replaced every
    pipe and degraded norms into single-bar pairs). The trailing space keeps
    ``\vert`` from absorbing the next letters into an undefined command name;
    math mode ignores it.
    """
    out: list[str] = []
    i, n = 0, len(latex)
    while i < n:
        ch = latex[i]
        if ch == "|":
            out.append(r"\vert ")
            i += 1
            continue
        if ch == "\\":
            j = i
            while j < n and latex[j] == "\\":
                j += 1
            out.append(latex[i:j])
            if j < n and latex[j] == "|":
                if (j - i) % 2 == 0:
                    out.append(r"\vert ")  # even run: the pipe is bare
                else:
                    out.append("|")  # odd run: \| is the norm delimiter
                j += 1
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ── Raw-HTML attribute position ───────────────────────────────────────


def escape_html_attr(text: str) -> str:
    """Escape a string for safe inclusion in a double-quoted HTML attribute.

    Used by the multi-image strip and grid figure renderers, which emit raw
    ``<img src="…" alt="…" />``. Without it, a ``"`` in an alt or src (LaTeX
    captions flow in as plain text with no bracket cleaning) truncates the
    attribute and swallows the rest of the tag.
    """
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def code_fence(text: str) -> str:
    r"""Fence run that safely wraps *text* as a code block (audit5 G2-1).

    Per CommonMark a content line starting with a backtick run at least as
    long as the opening fence closes it, so the fence must be longer than
    the longest backtick run in the content — ``max(3, longest + 1)``,
    minimum three.
    """
    longest = 0
    for run in re.findall(r"`+", text):
        longest = max(longest, len(run))
    return "`" * max(3, longest + 1)


def inline_code_delims(text: str) -> tuple[str, str]:
    """Backtick delimiters for inline code spans (audit5 G2-3).

    Content containing a backtick needs a longer run than that run;
    content starting or ending with a backtick additionally gets a space
    pad — CommonMark strips delimiter-adjacent spaces otherwise, merging
    the content backtick into the fence.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    if text.startswith("`") or text.endswith("`"):
        return f"{ticks} ", f" {ticks}"
    return ticks, ticks


# Block syntax a paragraph's first line must not read as: ATX heading,
# bullet/thematic-break marker, ordered-list marker, blockquote.
_LINE_START_BLOCK_RE = re.compile(r"^(?:#{1,6}(?:\s|$)|[-+*]+(?:\s|$)|\d{1,9}[.)](?:\s|$)|>)")


def escape_line_start(text: str) -> str:
    """Neutralize block-syntax openings on a paragraph's first line (audit5 G2-2).

    Body text like "# Looks like a heading" or "1. First finding" would
    otherwise render as a fake heading/list. Only the first line is at
    risk: later lines are ordinary paragraph continuation.
    """
    m = _LINE_START_BLOCK_RE.match(text)
    if not m:
        return text
    head = m.group(0)
    if head[0].isdigit():
        # "1. " → "1\. ": escaping the punctuation is what CommonMark
        # honors when disabling list parsing (an escaped digit is not).
        return f"{text[: len(head) - 2]}\\{head[-2:]}{text[len(head) :]}"
    return f"\\{text}"

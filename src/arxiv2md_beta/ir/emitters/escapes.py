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

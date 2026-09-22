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


# ── Raw-HTML attribute position ───────────────────────────────────────


def escape_html_attr(text: str) -> str:
    """Escape a string for safe inclusion in a double-quoted HTML attribute.

    Used by the multi-image strip and grid figure renderers, which emit raw
    ``<img src="…" alt="…" />``. Without it, a ``"`` in an alt or src (LaTeX
    captions flow in as plain text with no bracket cleaning) truncates the
    attribute and swallows the rest of the tag.
    """
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

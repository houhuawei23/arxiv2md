r"""Shared math-LaTeX normalization regexes used by both builders.

The HTML builder (ar5iv annotations) and the LaTeX builder (Pandoc AST)
normalize overlapping TeX-only constructs before math reaches a Markdown
math renderer. The shared regex definitions live here; each builder keeps
its own application order (their rule sequences genuinely differ — e.g.
color-artifact stripping is HTML-only, and the ``\\text{...}`` split
heuristics match different inputs).
"""

from __future__ import annotations

import re

# Independence symbol: \mbox{${}\perp\mkern-11.0mu\perp{}$} -> \perp \!\!\! \perp.
# \mkern is math-mode-only but sits inside the text-mode \mbox, so the box
# never renders. The optional '$' covers the pre-simplify annotation ('${}' /
# '{}') — the real ar5iv form has both a leading and a trailing '$' inside
# the box. The replacement's trailing space is load-bearing: without it the
# symbol glues to its successor ('\perpX').
PERP_IN_MATH_RE = re.compile(r"\\mbox\{\$?\{\}\\perp(?:\\mkern-[0-9]+(?:\.[0-9]+)?mu|\\!+)\\perp\{\}\$?\}")
PERP_REPLACEMENT = r"\\perp \\!\\!\\! \\perp "

# Text-mode \mbox breaks math renderers; \text renders. Use as
# ``MBOX_TO_TEXT_RE.sub(r"\\text\1{\2}", latex)``.
MBOX_TO_TEXT_RE = re.compile(r"\\mbox(\s*)\{([^{}]*)\}")

# TeX line-break hints (\nolinebreak) are unsupported by some renderers and
# visually no-ops in math; drop them.
NOLINEBREAK_RE = re.compile(r"\\nolinebreak(?:\s*\[[^\]]*\])?")

# Runs of spaces collapse to one.
MULTI_SPACE_RE = re.compile(r" {2,}")

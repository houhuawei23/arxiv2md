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

# An explicit \tag{...} inside the math body. When the emitter adds the
# authoritative number itself, a source-carried tag would render twice
# (audit5 G1-2): html.py's equation-table path strips it at build time and
# the emitter strips defensively before adding its own.
TAG_RE = re.compile(r"\\tag\{[^{}]*\}\s*")

# ── Math leaked into \text{...} ────────────────────────────────────────
# Both builders must rescue math that ended up inside a text-mode
# \text{} run (KaTeX rejects math macros inside \text{}), but the two
# inputs differ, so there are two distinct matcher/replacer pairs here —
# they are intentionally NOT one implementation:
#
# - HTML (ar5iv annotations): the inner ``$...$`` delimiters survived, so
#   the trigger is a ``\text{}`` group containing a literal ``$`` and the
#   splitter cuts on ``$...$`` pairs, moving the inner math outside.
# - LaTeX (Pandoc \mbox→\text translation): Pandoc DROPPED the inner
#   ``$`` (``\mbox{... $\alpha$ ...}`` → ``\text{... \alpha ...}``), so
#   the trigger is a ``\text{}`` group containing a backslash macro and
#   the splitter partitions on math macros, keeping plain words inside.

# HTML path: \text{ can be rejected at level $\alpha$}
#   → \text{ can be rejected at level } \alpha
TEXT_WITH_DOLLAR_MATH_RE = re.compile(r"\\text\{([^{}]*\$[^{}]*)\}")


def split_dollar_math_in_text(m: re.Match) -> str:
    r"""Re-split literal ``$...$`` math out of a ``\text{...}`` group."""
    parts = re.split(r"\$([^$]*)\$", m.group(1))
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part:
                out.append(f"\\text{{{part}}}")
        else:
            out.append(part)
    return "".join(out)


# LaTeX path: \text{ at level \alpha with } → \text{ at level } \alpha \text{ with }
TEXT_WITH_MACRO_MATH_RE = re.compile(r"\\text\{([^{}]*\\[a-zA-Z]+[^{}]*)\}")


def split_macro_math_in_text(m: re.Match[str]) -> str:
    r"""Partition ``\text{}`` content on math macros.

    Only moves tokens that are math-mode macros (backslash commands) outside
    the ``\text{}`` run; plain words stay inside.
    """
    inner = m.group(1)
    parts = re.split(r"(\\[a-zA-Z]+)", inner)
    out: list[str] = []
    buf: list[str] = []
    for p in parts:
        if p.startswith("\\") and len(p) > 1 and p[1].isalpha():
            if buf:
                out.append("\\text{" + "".join(buf) + "}")
                buf = []
            out.append(p)
        else:
            buf.append(p)
    if buf:
        out.append("\\text{" + "".join(buf) + "}")
    return "".join(out)

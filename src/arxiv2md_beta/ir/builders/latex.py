"""LaTeX builder: Pandoc JSON AST → DocumentIR."""

from __future__ import annotations

import copy
import json
import re
from collections import deque
from pathlib import Path
from typing import Any, Literal, cast

from arxiv2md_beta.exceptions import BuilderError
from arxiv2md_beta.ir.blocks import (
    BlockQuoteIR,
    BlockUnion,
    CodeIR,
    EquationIR,
    FigureIR,
    HeadingIR,
    ListIR,
    ParagraphIR,
    RawBlockIR,
    RuleIR,
    TableIR,
)
from arxiv2md_beta.ir.builders.base import IRBuilder
from arxiv2md_beta.ir.core import SourceLoc
from arxiv2md_beta.ir.document import AuthorIR, DocumentIR, PaperMetadata, SectionIR
from arxiv2md_beta.ir.inlines import (
    BreakIR,
    EmphasisIR,
    ImageRefIR,
    InlineUnion,
    LinkIR,
    MathIR,
    RawInlineIR,
    SubscriptIR,
    SuperscriptIR,
    TextIR,
)
from arxiv2md_beta.ir.resolvers import ImageResolver

_SHARED_SOURCE = SourceLoc(parser="latex")


def _pandoc_attrs_id(attrs: list[Any]) -> str:
    """Extract the element id from Pandoc Attr."""
    if isinstance(attrs, list) and len(attrs) > 0 and isinstance(attrs[0], str):
        return attrs[0]
    return ""


def _pandoc_node_c(node: Any) -> list[Any]:
    """Return the content list of a Pandoc node, format-agnostic.

    Pandoc JSON comes in two flavors: ``{"t": "...", "c": [...]}`` (typed dict)
    and a bare list ``[...]`` (compact form emitted by some pandoc versions for
    Table internals). Return ``c`` for dicts and the list itself for lists, so
    callers can index positionally without branching on format.
    """
    if isinstance(node, dict):
        c = node.get("c")
        return c if isinstance(c, list) else []
    if isinstance(node, list):
        return node
    return []


def _pandoc_attrs_classes(attrs: list[Any]) -> list[str]:
    """Extract CSS classes from Pandoc Attr."""
    if isinstance(attrs, list) and len(attrs) > 1 and isinstance(attrs[1], list):
        return [str(c) for c in attrs[1]]
    return []


# Macro-definition keywords whose name token must follow on the same logical
# line. LaTeX tolerates a newline between the keyword and the name (the newline
# is absorbed as inter-token space), but Pandoc's LaTeX reader rejects a bare
# name token after a newline — e.g. ``\DeclareRobustCommand\n  \foo{...}`` raises
# "unexpected ..." and aborts the whole document parse (observed on arXiv
# 2603.04780 whose header.tex uses this form). Match the keyword plus optional
# ``*``, trailing spaces, a newline, leading spaces, lookahead to the name token
# (``\name`` or ``{...}``); join onto one line with a single space.
_DEF_KEYWORDS = (
    r"newcommand|renewcommand|providecommand|DeclareRobustCommand|"
    r"DeclareMathOperator|DeclarePairedDelimiter|DeclareMathSymbol|"
    r"DeclareMathDelimiter|DeclareDocumentCommand|NewDocumentCommand|"
    r"newcites|newenvironment"
)
_SPLIT_DEF_NAME_RE = re.compile(r"(\\(?:" + _DEF_KEYWORDS + r")\*?)[ \t]*\n[ \t]*(?=[\\{])")

# How many trailing ``}`` we'll append before ``\end{document}`` to close stray
# groups Pandoc can't recover from (see the retry loop in ``LaTeXBuilder.build``).
_MAX_UNCLOSED_GROUP_RETRIES = 25

# ``\label{eq:foo}`` leaked into a display-math string by Pandoc (align/equation
# environments). KaTeX treats it as an undefined control sequence.
_LABEL_IN_MATH_RE = re.compile(r"\\label\{[^}]*\}")

# Independence symbol from ``\newcommand{\independent}{\mbox{${}\perp\mkern-11mu\perp{}$}}``
# — Pandoc expands it inside math, leaving literal ``$`` characters that unbalance
# the emitted ``$...$`` delimiters. Same normalization as the HTML builder.
_PERP_IN_MATH_RE = re.compile(r"\\mbox\{\$?\{\}\\perp(?:\\mkern-[0-9]+(?:\.[0-9]+)?mu|\\!+)\\perp\{\}\$?\}")

# ``\text{... math ...}`` after \mbox→\text translation may still contain math
# macros (\alpha etc.) when the source was ``\mbox{... at level $\alpha$}`` and
# Pandoc dropped the inner ``$``. KaTeX rejects math macros inside \text{}.
# Split the former ``$...$`` sub-expression back into math mode — mirrors the
# HTML builder's ``_split_math_from_text``.
_SPLIT_MATH_FROM_TEXT_RE = re.compile(r"\\text\{([^{}]*\\[a-zA-Z]+[^{}]*)\}")


def _split_math_from_text(m: re.Match[str]) -> str:
    r"""Re-split escaped ``$...$`` math out of ``\text{...}``.

    Only moves tokens that are math-mode macros (backslash commands) outside the
    ``\text{}`` run; plain words stay inside.
    """
    inner = m.group(1)
    # Partition on math macros: keep text runs in \text{}, macros outside.
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


# TeX glue primitives Pandoc's LaTeX reader aborts on (e.g. the end-part of a
# ``\newenvironment{proof}`` definition ``...\hfill$\square$\vskip\baselineskip``
# expands to ``\vskip`` inside the body, killing the whole parse with
# "unexpected \vskip expecting \end{document}"). ``\vskip``/``\hskip``/``\mskip``
# are spacing-only — dropping the token plus an optional ``{...}`` group is
# lossless. A bare trailing length/control-sequence (``\baselineskip``) is fine:
# Pandoc ignores unknown control sequences.
_GLUE_STRIP_RE = re.compile(r"\\(?:v|h|m)skip(?:\s*\{[^{}]*\})?")

# TeX conditionals with a statically-false literal condition. Pandoc does NOT
# evaluate ``\if0...\fi`` / ``\iffalse...\fi`` (it keeps BOTH branches), so a
# disabled block that carries unbalanced ``\begin{enumerate}``/``\end{...}``
# makes Pandoc report a bogus ``\end{document}`` error at the end of the file.
# arXiv sources use ``\if0`` as the "comment out this chunk" idiom (verified on
# 1501.01332). We strip the whole false block (keeping the ``\else``/``\or``
# branch, which is what real TeX would typeset).
_IF_TOKEN_RE = re.compile(r"\\unless\s*\\if[a-zA-Z@0-9]*|\\if[a-zA-Z@0-9]*")
_SCAN_TOKEN_RE = re.compile(r"\\unless\s*\\if[a-zA-Z@0-9]*|\\if[a-zA-Z@0-9]*|\\else|\\or|\\fi")


def _is_false_conditional(token: str, pos: int, tex: str) -> bool:
    r"""True when *token* is a conditional Pandoc can't evaluate and TeX reads as false.

    ``\if0<X>`` for X != 0 is false (compares "0" with X); ``\if00`` compares
    0==0 (true) and is left alone.
    """
    if token == r"\iffalse":
        return True
    return token.startswith(r"\if0") and not token.startswith(r"\if00")


def _strip_false_conditionals(tex: str) -> str:
    r"""Remove ``\\if0 ... [\\else ...] \fi`` / ``\\iffalse ... \fi`` blocks.

    Nesting-aware (any ``\\if`` variant increments the depth, matching ``\fi``
    decrements it) and keeps the ``\\else``/``\\or`` branch when present, since
    real TeX would typeset exactly that. Blocks whose condition is not a
    statically-false literal are left untouched (Pandoc evaluates ``\newif``
    conditionals itself).
    """
    out: list[str] = []
    i, n = 0, len(tex)
    while i < n:
        m = _IF_TOKEN_RE.match(tex, i)
        if m and _is_false_conditional(m.group(0), m.end(), tex):
            depth = 1
            j = m.end()
            else_start: int | None = None
            block_end: int | None = None  # position just past the closing ``\fi``
            while depth > 0 and j < n:
                mm = _SCAN_TOKEN_RE.match(tex, j)
                if mm:
                    tok = mm.group(0)
                    if tok in (r"\else", r"\or"):
                        if depth == 1:
                            else_start = j + len(tok)
                    elif tok == r"\fi":
                        depth -= 1
                        if depth == 0:
                            block_end = mm.end()
                            break
                    else:
                        depth += 1
                    j = mm.end()
                else:
                    j += 1
            if block_end is not None:
                if else_start is not None:
                    # Keep only the ``\else``/``\or`` branch, minus the ``\fi``.
                    out.append(tex[else_start : block_end - len(r"\fi")])
                i = block_end
            else:
                i = n  # unterminated conditional — drop the rest
        else:
            out.append(tex[i])
            i += 1
    return "".join(out)


def _sanitize_tex_for_pandoc(tex_content: str) -> str:
    r"""Normalize LaTeX that Pandoc's reader rejects but LaTeX accepts.

    Applied after ``\input`` resolution, immediately before the Pandoc call in
    :meth:`LaTeXBuilder.build`. Current fix-ups:

    * Macro-definition families whose name token sits on the line *after* the
      keyword (``\DeclareRobustCommand\n  \foo{...}``) — otherwise Pandoc aborts
      with a misleading "expecting end of input" at line 1.
    * TeX glue primitives ``\vskip``/``\hskip``/``\mskip`` (see ``_GLUE_STRIP_RE``).
    * Statically-false conditionals ``\if0``/``\iffalse`` (see
      ``_strip_false_conditionals``).

    Parameters
    ----------
    tex_content : str
        Expanded LaTeX content.

    Returns:
    -------
    str
        Sanitized content (same text unless a fix-up applied).
    """
    tex = _SPLIT_DEF_NAME_RE.sub(r"\1 ", tex_content)
    tex = _GLUE_STRIP_RE.sub("", tex)
    tex = _strip_false_conditionals(tex)
    return tex


class LaTeXBuilder(IRBuilder):
    r"""Build a :class:`DocumentIR` from LaTeX source via Pandoc JSON AST.

    Parameters
    ----------
    image_map : dict[str, Path] | None
        Mapping from LaTeX image paths/names to local processed image paths.
        Keys are the original ``\\includegraphics`` path, filename, or stem;
        values are the local :class:`Path` to the processed image file.
    image_resolver : ImageResolver | None
        Unified resolver (preferred).  If provided, *image_map* is ignored.
    """

    def __init__(
        self,
        image_map: dict[str, Path] | None = None,
        image_resolver: ImageResolver | None = None,
    ) -> None:
        self.image_map: dict[str, Path] = image_map or {}
        self._image_resolver = image_resolver or ImageResolver(
            path_map=self.image_map,
        )
        # Footnote state — flushed at block boundaries
        self._pending_footnotes: deque[tuple[int, list[dict]]] = deque()
        self._footnote_counter: int = 0
        # base_dir for extension probing when an image src is extensionless
        # (LaTeX ``\includegraphics`` allows omitting the extension).
        self._base_dir: Path | None = None
        self._images_subdir = "images"
        # citation key → 1-based reference number, built from ``\bibitem{}``
        # order in the source. Populated in ``build()``.
        self._cite_key_to_num: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def build(self, source: Any, **kwargs: Any) -> DocumentIR:
        r"""Parse LaTeX *source* and return a :class:`DocumentIR`.

        Parameters
        ----------
        source : str
            Expanded LaTeX content (after ``\\input``/``\\include`` resolution).
        **kwargs : Any
            * ``arxiv_id``: str – arXiv identifier.
            * ``title``: str | None – pre-extracted title.
            * ``authors``: list[str] | None – pre-extracted author names.
         * ``abstract``: str | None – pre-extracted abstract text.
         * ``base_dir``: Path | None – directory for relative-path resolution
           (forwarded to pandoc).
         * ``images_subdir``: str – output subdirectory for images (default
           ``"images"``); used for extension-probe fallback paths.

        Returns:
        -------
        DocumentIR
        """
        arxiv_id = kwargs.get("arxiv_id", "")
        title: str | None = kwargs.get("title")
        authors: list[str] = kwargs.get("authors", [])
        abstract_text: str | None = kwargs.get("abstract")

        tex_content = cast("str", source)
        base_dir = kwargs.get("base_dir")
        self._base_dir = Path(base_dir) if base_dir else None
        self._images_subdir = str(kwargs.get("images_subdir") or "images")

        # Build citation key → reference-number map from ``\bibitem{key}`` order.
        # The bibliography environment lists entries in reference order, so the
        # Nth ``\bibitem`` is reference [N]. Used by the Cite handler to render
        # ``\cite{key}`` as ``[N]`` instead of leaking the raw key.
        self._cite_key_to_num = self._extract_bibitem_numbers(tex_content)

        # Normalize LaTeX constructs Pandoc rejects but LaTeX accepts (e.g. a
        # macro name on the line after ``\DeclareRobustCommand``). Without this
        # Pandoc aborts the whole parse.
        tex_content = _sanitize_tex_for_pandoc(tex_content)

        # Convert LaTeX → Pandoc JSON AST
        try:
            import pypandoc
        except ImportError as e:
            from arxiv2md_beta.exceptions import ParserNotAvailableError

            raise ParserNotAvailableError(
                "pypandoc is required for LaTeX parsing. Install it with: pip install pypandoc"
            ) from e

        # Convert LaTeX → Pandoc JSON AST. arXiv sources sometimes carry an
        # unclosed ``{`` group (LaTeX only warns "\end occurred inside a group";
        # Pandoc aborts with "unexpected \end" at ``\end{document}``). The group
        # is transparent to Pandoc's block output, so closing it with a ``}``
        # before ``\end{document}`` recovers the whole document. Retry a few
        # times; 25 balances any real paper.
        json_str: str | None = None
        last_error: Exception | None = None
        for _ in range(_MAX_UNCLOSED_GROUP_RETRIES):
            try:
                json_str = pypandoc.convert_text(tex_content, "json", format="latex", extra_args=["--wrap=none"])
                break
            except RuntimeError as e:
                last_error = e
                if "unexpected \\end" not in str(e):
                    break  # not the unclosed-group failure mode; surface as-is
                if r"\end{document}" not in tex_content:
                    break  # nothing to anchor the closing brace to
                tex_content = tex_content.replace(r"\end{document}", "}" + r"\end{document}", 1)
            except OSError as e:
                raise BuilderError(f"Failed to convert LaTeX (pandoc not found?): {e}") from e

        if json_str is None:
            raise BuilderError(f"Failed to convert LaTeX to Pandoc AST: {last_error}") from last_error

        ast = json.loads(json_str)
        blocks: list[dict] = ast.get("blocks", [])

        # Try to read metadata from Pandoc meta if not provided
        meta = ast.get("meta", {})
        if not title:
            title = self._meta_to_text(meta.get("title"))
        if not authors:
            extracted_authors = self._meta_to_authors(meta.get("author"))
            if extracted_authors:
                authors = extracted_authors
        if not abstract_text:
            abstract_text = self._meta_to_text(meta.get("abstract"))

        # Build sections from all blocks. The bibliography (a Pandoc Div with
        # class ``thebibliography``) is turned into a "References" section by
        # ``_build_sections`` so ``split_ir_sections`` can route it to the
        # References.md sidecar — matching the HTML builder contract (where
        # references stay in ``doc.sections`` rather than ``doc.bibliography``).
        abstract_blocks: list[BlockUnion] = []
        sections = self._build_sections(blocks)

        # Pandoc places \begin{abstract} content in meta.abstract (a string),
        # not in the block stream, so abstract_blocks stays empty. Synthesize a
        # paragraph so the MarkdownEmitter (which walks doc.abstract) emits it —
        # otherwise the abstract is silently dropped from IR output.
        if not abstract_blocks and abstract_text:
            abstract_blocks = [ParagraphIR(inlines=[TextIR(text=abstract_text)])]

        author_irs = [AuthorIR(name=a) for a in authors]
        return DocumentIR(
            metadata=PaperMetadata(
                arxiv_id=arxiv_id,
                title=title,
                authors=author_irs,
                abstract_text=abstract_text,
                parser="latex",
            ),
            abstract=abstract_blocks,
            sections=sections,
        )

    # ------------------------------------------------------------------
    # Pandoc meta → plain text
    # ------------------------------------------------------------------

    @staticmethod
    def _meta_to_text(meta_value: Any) -> str | None:
        """Convert a Pandoc MetaValue (JSON form) to plain text."""
        if not meta_value:
            return None
        # MetaInlines: {"t": "MetaInlines", "c": [inlines...]}
        if isinstance(meta_value, dict) and meta_value.get("t") == "MetaInlines":
            return LaTeXBuilder._raw_inlines_to_text(meta_value.get("c", []))
        # MetaBlocks: {"t": "MetaBlocks", "c": [blocks...]}
        if isinstance(meta_value, dict) and meta_value.get("t") == "MetaBlocks":
            parts: list[str] = []
            for blk in meta_value.get("c", []):
                txt = LaTeXBuilder._blocks_to_plain_text([blk])
                if txt:
                    parts.append(txt)
            return " ".join(parts) if parts else None
        # MetaString: {"t": "MetaString", "c": "string"}
        if isinstance(meta_value, dict) and meta_value.get("t") == "MetaString":
            return str(meta_value.get("c", ""))
        if isinstance(meta_value, str):
            return meta_value
        return str(meta_value)

    @staticmethod
    def _meta_to_authors(meta_value: Any) -> list[str]:
        """Convert a Pandoc MetaValue for authors to a list of name strings."""
        if not meta_value:
            return []
        # MetaList: {"t": "MetaList", "c": [...]}
        if isinstance(meta_value, dict) and meta_value.get("t") == "MetaList":
            return [name for item in meta_value.get("c", []) if (name := LaTeXBuilder._meta_to_text(item))]
        text = LaTeXBuilder._meta_to_text(meta_value)
        if not text:
            return []
        # Split by \and
        import re

        return [a.strip() for a in re.split(r"\\and|\\AND", text) if a.strip()]

    # ------------------------------------------------------------------
    # Block-level conversion
    # ------------------------------------------------------------------

    def _build_sections(self, blocks: list[dict]) -> list[SectionIR]:
        """Split a flat list of Pandoc blocks into a flat list of SectionIR.

        Parent-child hierarchy is built separately by _build_section_hierarchy.
        Sections are appended in document order; the bibliography Div
        (thebibliography) is materialized as a top-level References section so
        split_ir_sections can route it to the sidecar.
        """
        sections: list[SectionIR] = []

        current_blocks: list[dict] = []
        current_header: tuple[int, str, str, bool] | None = None  # level, title, anchor, unnumbered

        def _flush_section() -> SectionIR | None:
            nonlocal current_blocks, current_header
            if current_header is None:
                # No heading seen yet: skip — pre-header blocks such as
                # permission notices and maketitle artifacts belong to
                # front matter, not to a real section.
                return None
            level, title, anchor, unnumbered = current_header
            sec = SectionIR(
                title=title or "",
                level=level,
                anchor=anchor if anchor else None,
                unnumbered=unnumbered,
                blocks=self._blocks_from_pandoc(current_blocks, section_id=""),
            )
            current_blocks = []
            current_header = None
            return sec

        for blk in blocks:
            t = blk.get("t")
            if t == "Header":
                c = blk.get("c", [1, ["", [], []], []])
                raw_level = c[0] if isinstance(c, list) and len(c) > 0 else 1
                level = max(1, min(6, raw_level + 1))
                attrs = c[1] if isinstance(c, list) and len(c) > 1 else ["", [], []]
                anchor = _pandoc_attrs_id(attrs)
                classes = _pandoc_attrs_classes(attrs)
                inlines = self._inlines_from_pandoc(c[2]) if len(c) > 2 else []
                title = self._inlines_to_plain_text(inlines)
                unnumbered = "unnumbered" in classes

                sec = _flush_section()
                if sec:
                    sections.append(sec)
                else:
                    # First heading: discard any pre-header blocks
                    # (permission notices, maketitle artifacts).
                    current_blocks = []

                current_header = (level, title, anchor, unnumbered)
            elif self._is_thebibliography_div(blk):
                c_list = blk.get("c", [["", [], []], []])
                inner = c_list[1] if isinstance(c_list, list) and len(c_list) > 1 else []
                inner_blocks = inner if isinstance(inner, list) else []

                # Build a bullet list from bibliography entries so the
                # MarkdownEmitter renders ``- Author. Title. …`` instead of
                # plain paragraphs — matching ar5iv HTML output.
                # Pandoc sometimes emits a leading bibitem-label Para (e.g.
                # ``10`` for ``\\bibitem[10]{key}``); skip those so the list
                # doesn't start with a stray ``- 10``.
                ref_items: list[list[BlockUnion]] = []
                for ref_blk in inner_blocks:
                    ref_ir = self._block_from_pandoc(ref_blk, section_id="", order=0)
                    if ref_ir is None:
                        continue
                    # Skip empty paragraphs and bibitem label paragraphs
                    # (text is only a number like "10").
                    if isinstance(ref_ir, ParagraphIR):
                        plain = self._inlines_to_plain_text(ref_ir.inlines).strip()
                        if not plain or re.match(r"^\d+\s*$", plain):
                            continue
                    if isinstance(ref_ir, list):
                        non_empty = [
                            b
                            for b in ref_ir
                            if not (
                                isinstance(b, ParagraphIR)
                                and (
                                    not b.inlines
                                    or re.match(r"^\d+\s*$", self._inlines_to_plain_text(b.inlines).strip())
                                )
                            )
                        ]
                        if non_empty:
                            ref_items.append(non_empty)
                    else:
                        ref_items.append([ref_ir])
                ref_list = ListIR(
                    ordered=False,
                    items=ref_items,
                    source=_SHARED_SOURCE,
                    section_id="",
                    order_index=0,
                )

                # Flush any pending section before creating References.
                # This handles the common case where a section (e.g.
                # Acknowledgements) appears between the last body heading
                # and the bibliography.
                sec = _flush_section()
                if sec:
                    sections.append(sec)

                # Always create a "References" section with the bibliography
                # list, regardless of whether a header preceded the Div.
                sections.append(
                    SectionIR(
                        title="References",
                        level=2,
                        unnumbered=True,
                        blocks=[ref_list],
                    )
                )
                current_header = None
                current_blocks = []
            else:
                current_blocks.append(blk)

        sec = _flush_section()
        if sec:
            sections.append(sec)

        # When the document has no section headings, wrap orphan blocks
        # (e.g. a minimal ``\begin{document} Hello. \end{document}``) into
        # a single unnamed section so they are not silently dropped.
        if not sections and current_blocks:
            sections.append(
                SectionIR(
                    title="",
                    level=1,
                    blocks=self._blocks_from_pandoc(current_blocks, section_id=""),
                )
            )

        sections = self._build_section_hierarchy(sections)
        return sections

    @staticmethod
    def _build_section_hierarchy(flat_sections: list[SectionIR]) -> list[SectionIR]:
        """Build parent-child relationships from section levels."""
        result: list[SectionIR] = []
        stack: list[SectionIR] = []

        for sec in flat_sections:
            while stack and stack[-1].level >= sec.level:
                stack.pop()
            if stack:
                stack[-1].children.append(sec)
            else:
                result.append(sec)
            stack.append(sec)

        return result

    # ------------------------------------------------------------------
    # Bibliography / citation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_bibitem_numbers(tex_content: str) -> dict[str, int]:
        r"""Parse ``\\bibitem{key}`` commands and return a key to 1-based-number map.

        The bibliography lists entries in citation-number order, so the
        *N*-th ``\\bibitem`` in the source is reference [*N*].
        """
        pattern = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}")
        mapping: dict[str, int] = {}
        for idx, m in enumerate(pattern.finditer(tex_content), start=1):
            key = m.group(1)
            if key not in mapping:
                mapping[key] = idx
        return mapping

    @staticmethod
    def _is_thebibliography_div(blk: dict) -> bool:
        """Return True if *blk* is a Pandoc Div with class ``thebibliography``."""
        if blk.get("t") != "Div":
            return False
        c = blk.get("c", [])
        if not isinstance(c, list) or len(c) == 0:
            return False
        attrs = c[0]
        if isinstance(attrs, list) and len(attrs) > 1:
            classes = attrs[1]
            if isinstance(classes, list) and "thebibliography" in classes:
                return True
        return False

    @staticmethod
    def _is_reference_title(title: str) -> bool:
        """Return True if *title* looks like a References / Bibliography heading."""
        normalized = title.strip().lower().rstrip(".")
        ref_titles = {"references", "bibliography", "reference", "works cited", "literature"}
        return normalized in ref_titles

    @staticmethod
    def _is_raw_cite_command(content: str) -> bool:
        """Return True if *content* is a raw LaTeX cite-like command."""
        return bool(
            re.match(
                r"\\(cite|citet|citep|citetext|citealp|citealt|citep\*|citenum|citeyear|citeauthor|nocite)\*?\{",
                content.strip(),
            )
        )

    def _blocks_from_pandoc(self, blocks: list[dict], section_id: str = "") -> list[BlockUnion]:
        """Convert a list of Pandoc block dicts to IR blocks."""
        result: list[BlockUnion] = []
        order = 0
        for blk in blocks:
            ir_block = self._block_from_pandoc(blk, section_id, order)
            if ir_block is not None:
                if isinstance(ir_block, list):
                    for b in ir_block:
                        b.order_index = order
                        b.section_id = section_id
                        order += 1
                        result.append(b)
                else:
                    ir_block.order_index = order
                    ir_block.section_id = section_id
                    order += 1
                    result.append(ir_block)
            else:
                order += 1
            # Flush any pending footnotes generated by inline elements
            # in this block.
            while self._pending_footnotes:
                fn_num, fn_raw_blocks = self._pending_footnotes.popleft()
                # Convert footnote blocks without recursion to avoid
                # re-entrant flush issues.
                fn_ir_blocks: list[BlockUnion] = []
                for fb in fn_raw_blocks:
                    fb_ir = self._block_from_pandoc(fb, section_id, order)
                    if fb_ir is not None:
                        if isinstance(fb_ir, list):
                            fn_ir_blocks.extend(fb_ir)
                        else:
                            fn_ir_blocks.append(fb_ir)
                # Footnote reference marker. Use a semantic LinkIR(kind="footnote")
                # so the MarkdownEmitter renders [^N] and other emitters can choose
                # their own representation. Previously [^N] was baked into a TextIR,
                # leaking Markdown syntax into the IR.
                marker = ParagraphIR(
                    inlines=[
                        LinkIR(
                            kind="footnote",
                            target_id=f"fn-{fn_num}",
                            inlines=[TextIR(text=str(fn_num))],
                        )
                    ],
                    source=_SHARED_SOURCE,
                    section_id=section_id,
                    order_index=order,
                )
                result.append(marker)
                order += 1
                for b in fn_ir_blocks:
                    b.section_id = section_id
                    b.order_index = order
                    order += 1
                    result.append(b)
        return result

    def _contains_display_math(self, inlines: list[InlineUnion]) -> bool:
        """True if any inline (recursively, through container inlines) is display math."""
        for il in inlines:
            if isinstance(il, MathIR) and il.display:
                return True
            if hasattr(il, "inlines") and self._contains_display_math(il.inlines):
                return True
        return False

    def _partition_display_math(self, inlines: list[InlineUnion]) -> list[tuple[str, list[InlineUnion] | str]]:
        r"""Partition *inlines* into ``("text", inlines)`` / ``("eq", latex)`` segments.

        Display math inside container inlines (e.g. an italic ``EmphasisIR`` wrapping
        a ``\begin{assumption}`` body) is lifted out; the surrounding text is
        re-wrapped in a clone of the container so styling is preserved.
        """
        segments: list[tuple[str, list[InlineUnion] | str]] = []
        current: list[InlineUnion] = []

        def flush() -> None:
            if current:
                segments.append(("text", list(current)))
                current.clear()

        for il in inlines:
            if isinstance(il, MathIR) and il.display:
                flush()
                segments.append(("eq", il.latex))
            elif hasattr(il, "inlines") and self._contains_display_math(il.inlines):
                sub = self._partition_display_math(il.inlines)
                for kind, val in sub:
                    if kind == "eq":
                        flush()
                        segments.append(("eq", val))
                    else:
                        cloned = copy.deepcopy(il)
                        cloned.inlines = list(val)  # type: ignore[attr-defined]
                        current.append(cloned)
            else:
                current.append(il)
        flush()
        return segments

    def _split_display_math_paragraph(
        self, inlines: list[InlineUnion], section_id: str, order: int
    ) -> BlockUnion | list[BlockUnion]:
        r"""Split a paragraph's inlines at display-math inlines.

        Pandoc places ``\begin{align}``/``$$`` display math INSIDE a Para as a
        DisplayMath inline (possibly nested inside an emphasis container); the
        HTML builder routes display equations to ``EquationIR`` blocks instead.
        Splitting here keeps both parser paths' IR shape consistent so the
        shared MarkdownEmitter renders display math on its own ``$$...$$`` block
        rather than mid-paragraph — mid-paragraph ``$$`` is absorbed into the
        surrounding text and breaks KaTeX/MathJax.
        """
        if not self._contains_display_math(inlines):
            return ParagraphIR(
                inlines=inlines,
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )
        blocks: list[BlockUnion] = []
        for kind, val in self._partition_display_math(inlines):
            if kind == "eq":
                blocks.append(
                    EquationIR(
                        latex=cast(str, val),
                        source=_SHARED_SOURCE,
                        section_id=section_id,
                        order_index=order,
                    )
                )
            else:
                blocks.append(
                    ParagraphIR(
                        inlines=cast(list[InlineUnion], val),
                        source=_SHARED_SOURCE,
                        section_id=section_id,
                        order_index=order,
                    )
                )
        return blocks

    def _block_from_pandoc(
        self, blk: dict, section_id: str = "", order: int = 0
    ) -> BlockUnion | list[BlockUnion] | None:
        """Convert a single Pandoc block dict to an IR block."""
        t = blk.get("t", "")
        c = blk.get("c", [])

        if t == "Para" or t == "Plain":
            inlines = self._inlines_from_pandoc(c) if isinstance(c, list) else []
            return self._split_display_math_paragraph(inlines, section_id=section_id, order=order)
        elif t == "Header":
            c_list = c if isinstance(c, list) else [1, ["", [], []], []]
            level = c_list[0] if len(c_list) > 0 else 1
            anchor = _pandoc_attrs_id(c_list[1])
            inlines = self._inlines_from_pandoc(c_list[2] if len(c_list) > 2 else [])
            return HeadingIR(
                level=level,
                inlines=inlines,
                anchor=anchor if anchor else None,
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )
        elif t == "CodeBlock":
            c_list = c if isinstance(c, list) else [["", [], []], "", ""]
            attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
            lang = str(c_list[1]) if len(c_list) > 1 else ""
            code = str(c_list[2]) if len(c_list) > 2 else ""
            anchor = _pandoc_attrs_id(attrs)
            classes = _pandoc_attrs_classes(attrs)
            language = lang if lang else (classes[0] if classes else None)
            return CodeIR(
                language=language,
                text=code,
                anchor=anchor if anchor else None,
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )
        elif t == "BlockQuote":
            inner = self._blocks_from_pandoc(c if isinstance(c, list) else [], section_id)
            return BlockQuoteIR(
                blocks=inner,
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )
        elif t == "OrderedList":
            items_list = c[1] if isinstance(c, list) and len(c) > 1 else c if isinstance(c, list) else []
            items_structure: list[list[BlockUnion]] = []
            for item in items_list:
                item_blocks = self._blocks_from_pandoc(item if isinstance(item, list) else [], section_id)
                items_structure.append(item_blocks)
            return ListIR(
                ordered=True,
                items=items_structure,
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )
        elif t == "BulletList":
            items_list = c if isinstance(c, list) else []
            items_structure = []
            for item in items_list:
                item_blocks = self._blocks_from_pandoc(item if isinstance(item, list) else [], section_id)
                items_structure.append(item_blocks)
            return ListIR(
                ordered=False,
                items=items_structure,
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )
        elif t == "Table":
            return self._build_table_from_pandoc(c, section_id, order)
        elif t == "Figure":
            return self._build_figure_from_pandoc(c, section_id, order)
        elif t in ("HorizontalRule",):
            return RuleIR(
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )
        elif t == "Div":
            attrs = c[0] if isinstance(c, list) and len(c) > 0 else ["", [], []]
            inner_blocks = c[1] if isinstance(c, list) and len(c) > 1 else []
            anchor = _pandoc_attrs_id(attrs)
            div_blocks = self._blocks_from_pandoc(inner_blocks if isinstance(inner_blocks, list) else [], section_id)
            if anchor:
                for b in div_blocks:
                    if not b.anchor:
                        b.anchor = anchor
            return div_blocks if len(div_blocks) > 0 else None
        elif t == "RawBlock":
            fmt = str(c[0]) if isinstance(c, list) and len(c) > 0 else "latex"
            content = str(c[1]) if isinstance(c, list) and len(c) > 1 else ""
            return RawBlockIR(
                format=cast("Literal['html', 'latex', 'markdown']", fmt if fmt in ("html", "latex") else "latex"),
                content=content,
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )
        elif t == "LineBlock":
            inlines_list = c if isinstance(c, list) else []
            all_inlines: list[InlineUnion] = []
            for line in inlines_list:
                line_inlines = self._inlines_from_pandoc(line if isinstance(line, list) else [])
                if all_inlines:
                    all_inlines.append(BreakIR())
                all_inlines.extend(line_inlines)
            return ParagraphIR(
                inlines=all_inlines,
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )
        elif t == "Null":
            return None
        else:
            # Unknown block type: emit a Markdown comment marker instead of
            # json.dumps(blk), which previously leaked raw JSON into the
            # rendered Markdown. The comment keeps the block visible for
            # debugging without corrupting the document.
            return RawBlockIR(
                format="markdown",
                content=f"<!-- unhandled pandoc block: {t} -->",
                source=_SHARED_SOURCE,
                section_id=section_id,
                order_index=order,
            )

    # ------------------------------------------------------------------
    # Inline-level conversion
    # ------------------------------------------------------------------

    def _inlines_from_pandoc(self, inlines: list) -> list[InlineUnion]:
        """Convert a list of Pandoc inline dicts to IR inlines."""
        result: list[InlineUnion] = []
        for il in inlines:
            if not isinstance(il, dict):
                continue  # skip strings or other non-dict items
            converted = self._inline_from_pandoc(il)
            if converted is not None:
                if isinstance(converted, list):
                    result.extend(converted)
                else:
                    result.append(converted)
        return result

    def _inline_from_pandoc(self, il: dict) -> InlineUnion | list[InlineUnion] | None:
        """Convert a single Pandoc inline dict to an IR inline."""
        t = il.get("t", "")
        c = il.get("c", [])

        if t == "Str":
            return TextIR(text=str(c) if isinstance(c, str) else str(c))
        elif t == "Space" or t == "SoftBreak":
            return TextIR(text=" ")
        elif t == "LineBreak":
            return BreakIR()
        elif t == "Emph":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return EmphasisIR(style="italic", inlines=inner)
        elif t == "Strong":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return EmphasisIR(style="bold", inlines=inner)
        elif t == "Underline":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return EmphasisIR(style="underline", inlines=inner)
        elif t == "Strikeout":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return EmphasisIR(style="strikethrough", inlines=inner)
        elif t == "Superscript":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return SuperscriptIR(inlines=inner)
        elif t == "Subscript":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            return SubscriptIR(inlines=inner)
        elif t == "SmallCaps":
            inner = self._inlines_from_pandoc(c if isinstance(c, list) else [])
            # SmallCaps has no Markdown equivalent; carry it as a neutral
            # emphasis style (emitter renders it as plain text). Previously
            # mislabelled as italic, corrupting the semantics.
            return EmphasisIR(style="smallcaps", inlines=inner)
        elif t == "Code":
            c_list = c if isinstance(c, list) else [["", [], []], ""]
            text = str(c_list[1]) if len(c_list) > 1 else ""
            return EmphasisIR(style="code", inlines=[TextIR(text=text)])
        elif t == "Math":
            c_list = c if isinstance(c, list) else [{"t": "InlineMath"}, ""]
            mathtype = c_list[0] if len(c_list) > 0 else {}
            latex = str(c_list[1]) if len(c_list) > 1 else ""
            display = mathtype.get("t") == "DisplayMath" if isinstance(mathtype, dict) else False
            # Pandoc keeps \label{...} inside display-math strings (align/
            # equation environments). KaTeX rejects it as an undefined control
            # sequence; the label is a cross-reference artifact, not content.
            latex = _LABEL_IN_MATH_RE.sub("", latex)
            # \mbox{${}\perp...\perp{}$} (from a \newcommand{\independent})
            # nests literal $ inside the math string, unbalancing the emitted
            # $...$ delimiters — normalize to the same \perp \!\!\! \perp form
            # the HTML path produces. Trailing space is load-bearing: without
            # it the replacement glues to the next token (\perpX).
            latex = _PERP_IN_MATH_RE.sub(r"\\perp \\!\\!\\! \\perp ", latex)
            # \mbox{argmin} (from \DeclareMathOperator{\argmin}{argmin}):
            # text-mode \mbox breaks KaTeX; \text renders.
            latex = re.sub(r"\\mbox(\s*)\{([^{}]*)\}", r"\\text\1{\2}", latex)
            # KaTeX rejects math macros directly inside \text{...} (e.g.
            # \mbox{... at level $\alpha$} expanded by Pandoc loses the inner
            # $...$). Split the math back out (same as the HTML builder).
            latex = _SPLIT_MATH_FROM_TEXT_RE.sub(_split_math_from_text, latex)
            # TeX line-break hints unsupported by some renderers (same as the
            # HTML builder's normalization).
            latex = re.sub(r"\\nolinebreak(?:\s*\[[^\]]*\])?", "", latex)
            latex = re.sub(r" {2,}", " ", latex).strip()
            return MathIR(latex=latex, display=display)
        elif t == "RawInline":
            fmt = str(c[0]) if isinstance(c, list) and len(c) > 0 else "latex"
            raw_content = str(c[1]) if isinstance(c, list) and len(c) > 1 else ""
            # Drop stray \cite{...} / \citep{...} commands that pandoc
            # didn't parse as Cite nodes (e.g. inside captions). Otherwise they
            # leak raw LaTeX into the rendered output.
            if fmt in ("latex", "tex") and self._is_raw_cite_command(raw_content):
                return None
            return RawInlineIR(
                format=cast("Literal['html', 'latex', 'markdown']", fmt if fmt in ("html", "latex") else "latex"),
                content=raw_content,
            )
        elif t == "Link":
            c_list = c if isinstance(c, list) else [["", [], []], [], ["", ""]]
            attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
            inner = self._inlines_from_pandoc(c_list[1] if len(c_list) > 1 else [])
            target = c_list[2] if len(c_list) > 2 else ["", ""]
            url = str(target[0]) if isinstance(target, list) and len(target) > 0 else ""
            anchor = _pandoc_attrs_id(attrs)
            # Determine link kind
            kind: str = "external"
            if url.startswith("#"):
                kind = "internal"
                if "cite" in url.lower() or "ref" in url.lower():
                    kind = "citation"
            elif not url:
                kind = "internal"
            return LinkIR(
                url=url if url else None,
                inlines=inner,
                kind=kind,  # type: ignore[arg-type]
                target_id=anchor if anchor else None,
            )
        elif t == "Image":
            c_list = c if isinstance(c, list) else [["", [], []], [], ["", ""]]
            attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
            alt_inlines = self._inlines_from_pandoc(c_list[1] if len(c_list) > 1 else [])
            target = c_list[2] if len(c_list) > 2 else ["", ""]
            src = str(target[0]) if isinstance(target, list) and len(target) > 0 else ""
            alt = self._inlines_to_plain_text(alt_inlines)
            anchor = _pandoc_attrs_id(attrs)

            # Resolve via image_map
            local_src = self._resolve_image_src(src)
            return ImageRefIR(
                src=local_src,
                alt=alt,
            )
        elif t == "Quoted":
            c_list = c if isinstance(c, list) else [{"t": "DoubleQuote"}, []]
            inner = self._inlines_from_pandoc(c_list[1] if len(c_list) > 1 else [])
            # Add quote marks around the inlines
            qt = c_list[0] if len(c_list) > 0 else {}
            if isinstance(qt, dict) and qt.get("t") == "DoubleQuote":
                left, right = "\u201c", "\u201d"
            else:
                left, right = "\u2018", "\u2019"
            return [TextIR(text=left)] + inner + [TextIR(text=right)]
        elif t == "Cite":
            c_list = c if isinstance(c, list) else [[], []]
            inner = self._inlines_from_pandoc(c_list[1] if len(c_list) > 1 else [])
            citations = c_list[0] if len(c_list) > 0 else []
            # Resolve citationId keys to reference numbers via the bibitem map.
            ref_nums: list[str] = []
            if isinstance(citations, list):
                for cit in citations:
                    if isinstance(cit, dict):
                        cid = str(cit.get("citationId", ""))
                        if cid and cid in self._cite_key_to_num:
                            ref_nums.append(str(self._cite_key_to_num[cid]))
                        elif cid:
                            ref_nums.append(cid)  # fallback: keep key
            if ref_nums:
                # Build inlines with individual [N] markers, one per ref.
                # MarkdownEmitter wraps LinkIR(kind="citation") as [{inlines}]
                # when target_id is set, producing [[35], [2], [5]] to match
                # ar5iv HTML format.
                marker_inlines: list[InlineUnion] = []
                for idx, num in enumerate(ref_nums):
                    if idx > 0:
                        marker_inlines.append(TextIR(text=", "))
                    marker_inlines.append(TextIR(text=f"[{num}]"))
                cite_link = LinkIR(
                    kind="citation",
                    target_id=",".join(ref_nums),
                    inlines=marker_inlines,
                )
                if inner:
                    return inner + [cite_link]
                return cite_link
            return inner if inner else None
        elif t == "Note":
            # Footnote: Pandoc Note contains [Blocks]
            note_blocks = c if isinstance(c, list) else []
            self._footnote_counter += 1
            fn_num = self._footnote_counter
            self._pending_footnotes.append((fn_num, note_blocks))
            return SuperscriptIR(inlines=[TextIR(text=str(fn_num))])
        elif t == "Span":
            c_list = c if isinstance(c, list) else [["", [], []], []]
            attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
            inner = self._inlines_from_pandoc(c_list[1] if len(c_list) > 1 else [])
            anchor = _pandoc_attrs_id(attrs)
            _pandoc_attrs_classes(attrs)
            if anchor:
                inner.insert(0, RawInlineIR(format="html", content=f'<a id="{anchor}"></a>'))
            return inner
        else:
            return None

    # ------------------------------------------------------------------
    # Figure / Table builders
    # ------------------------------------------------------------------

    def _build_figure_from_pandoc(self, c: Any, section_id: str, order: int) -> BlockUnion | None:
        """Build a FigureIR from Pandoc Figure AST.

        Pandoc Figure (≥ 1.23): ``Figure Attr Caption [Body]``
        where *Caption* is ``[ShortCaption | null, [Blocks]]`` and
        *Body* is a list of blocks.
        """
        c_list = c if isinstance(c, list) else [["", [], []], [None, []], []]
        attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
        caption_data = c_list[1] if len(c_list) > 1 else [None, []]
        body_blocks = c_list[2] if len(c_list) > 2 else []
        anchor = _pandoc_attrs_id(attrs)

        # Extract caption inlines from Caption structure
        caption_inlines: list[InlineUnion] = []
        if isinstance(caption_data, list) and len(caption_data) >= 2:
            # caption_data[1] is a list of blocks (usually [Plain])
            cap_blocks = caption_data[1] if caption_data[1] else []
            if isinstance(cap_blocks, list):
                for cb in cap_blocks:
                    if isinstance(cb, dict) and cb.get("t") in ("Plain", "Para"):
                        caption_inlines.extend(
                            self._inlines_from_pandoc(cb.get("c", []) if isinstance(cb.get("c"), list) else [])
                        )

        images: list[ImageRefIR] = []
        for blk in body_blocks if isinstance(body_blocks, list) else []:
            if isinstance(blk, dict) and blk.get("t") in ("Plain", "Para"):
                # Process all inlines (handles Span-wrapped Images)
                inlines = self._inlines_from_pandoc(blk.get("c", []) if isinstance(blk.get("c"), list) else [])
                for inline in inlines:
                    if isinstance(inline, ImageRefIR):
                        images.append(inline)

        return FigureIR(
            images=images,
            caption=caption_inlines,
            figure_id=anchor if anchor else None,
            anchor=anchor if anchor else None,
            source=_SHARED_SOURCE,
            section_id=section_id,
            order_index=order,
        )

    def _build_table_from_pandoc(self, c: Any, section_id: str, order: int) -> BlockUnion | None:
        """Build a TableIR from Pandoc Table AST.

        Pandoc Table (≥ 1.23): ``Table Attr Caption [ColSpec] TableHead [TableBody] TableFoot``
        """
        c_list = c if isinstance(c, list) else [["", [], []], [None, []], [], ["", [], [], []], [], ["", [], []]]
        attrs = c_list[0] if len(c_list) > 0 else ["", [], []]
        caption_data = c_list[1] if len(c_list) > 1 else [None, []]
        # c_list[2] = ColSpec (ignored)
        head = c_list[3] if len(c_list) > 3 else ["", [], []]
        body = c_list[4] if len(c_list) > 4 else []
        foot = c_list[5] if len(c_list) > 5 else ["", [], []]
        anchor = _pandoc_attrs_id(attrs)

        # Extract caption inlines from Caption structure: [ShortCaption, [Blocks]]
        caption_inlines: list[InlineUnion] = []
        if isinstance(caption_data, list) and len(caption_data) >= 2:
            cap_blocks = caption_data[1] if caption_data[1] else []
            if isinstance(cap_blocks, list):
                for cb in cap_blocks:
                    if isinstance(cb, dict) and cb.get("t") in ("Plain", "Para"):
                        caption_inlines.extend(
                            self._inlines_from_pandoc(cb.get("c", []) if isinstance(cb.get("c"), list) else [])
                        )

        # Body: list of table bodies, each has [body_attr, row_count, colspecs, [rows]]
        # Pandoc emits TableBody either as {"t":"TableBody","c":[...]} (older) or as
        # a bare list [...] (newer compact JSON) — handle both.
        rows: list[list[list[InlineUnion]]] = []

        # Head: [head_attr, [head_rows]] (dict or bare-list form).
        # TableIR.headers is a SINGLE row of cells (list[list[InlineUnion]]);
        # the first head row becomes headers, any extra head rows fold into rows.
        headers: list[list[InlineUnion]] = []
        head_c = _pandoc_node_c(head)
        head_rows = head_c[1] if len(head_c) >= 2 and isinstance(head_c[1], list) else []
        for row_data in head_rows:
            row_cells = self._extract_table_row_cells(row_data)
            if not row_cells:
                continue
            if not headers:
                headers = row_cells
            else:
                rows.append(row_cells)

        if isinstance(body, list):
            for body_part in body:
                body_c = _pandoc_node_c(body_part)
                body_rows = body_c[3] if len(body_c) >= 4 else []
                if isinstance(body_rows, list):
                    for row_data in body_rows:
                        row_cells = self._extract_table_row_cells(row_data)
                        if row_cells:
                            rows.append(row_cells)

        # Foot: similar to head
        # Foot: [foot_attr, [foot_rows]] (dict or bare-list form)
        foot_c = _pandoc_node_c(foot)
        if len(foot_c) >= 2 and isinstance(foot_c[1], list):
            for row_data in foot_c[1]:
                row_cells = self._extract_table_row_cells(row_data)
                if row_cells:
                    rows.append(row_cells)

        # Use first header row as headers; the rest as data
        if not headers and rows:
            headers = rows[0] if isinstance(rows[0], list) else []
            rows = rows[1:] if len(rows) > 1 else []

        return TableIR(
            headers=headers,
            rows=rows,
            caption=caption_inlines,
            anchor=anchor if anchor else None,
            table_id=anchor if anchor else None,
            source=_SHARED_SOURCE,
            section_id=section_id,
            order_index=order,
        )

    def _extract_table_row_cells(self, row_data: Any) -> list[list[InlineUnion]] | None:
        """Extract cell inlines from a Pandoc Row.

        Handles both formats: ``{"t":"Row","c":[attr, [cells]]}`` (older) and a
        bare list ``[attr, [cells]]`` (newer compact JSON).
        """
        row_c = _pandoc_node_c(row_data)
        # Row: [attr, [cells...]]
        cells_data = row_c[1] if len(row_c) >= 2 else []
        cells: list[list[InlineUnion]] = []
        if isinstance(cells_data, list):
            for cell in cells_data:
                cell_c = _pandoc_node_c(cell)
                # Cell: [attr, alignment, rowspan, colspan, [blocks]]
                blocks = cell_c[4] if len(cell_c) >= 5 else []
                cell_inlines: list[InlineUnion] = []
                if isinstance(blocks, list):
                    for b in blocks:
                        if isinstance(b, dict) and b.get("t") in ("Plain", "Para"):
                            cell_inlines.extend(
                                self._inlines_from_pandoc(b.get("c", []) if isinstance(b.get("c"), list) else [])
                            )
                cells.append(cell_inlines)
        return cells if cells else None

    # ------------------------------------------------------------------
    # Image path resolution
    # ------------------------------------------------------------------

    def _resolve_image_src(self, src: str) -> str:
        r"""Resolve a LaTeX image path to a local processed image path.

        When the resolver returns *src* unchanged (no match in the image map)
        and *src* has no extension, probe the *base_dir* for a file matching
        the stem.  LaTeX ``\\includegraphics`` omits the extension by
        convention. Probed hits are reported as ``<images_subdir>/<name>``
        relative paths — never absolute paths, which would make the emitted
        Markdown non-portable (base_dir is a temporary extraction directory).
        """
        resolved = self._image_resolver.resolve(src)
        if resolved != src:
            return resolved
        # No match: try extension probing when src has no suffix.
        if "." not in src.rsplit("/", 1)[-1] and self._base_dir:
            for ext in (".png", ".jpg", ".jpeg", ".pdf", ".svg", ".eps"):
                candidate = self._base_dir / f"{src}{ext}"
                if candidate.is_file():
                    return f"{self._images_subdir}/{candidate.name}"
        return src

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _inlines_to_plain_text(inlines: list[InlineUnion]) -> str:
        """Extract plain text from a list of InlineIR nodes."""
        parts: list[str] = []
        for il in inlines:
            if isinstance(il, TextIR):
                parts.append(il.text)
            elif isinstance(il, MathIR):
                parts.append(f"${il.latex}$" if not il.display else f"$${il.latex}$$")
            elif isinstance(il, EmphasisIR | LinkIR | SuperscriptIR | SubscriptIR):
                inner = LaTeXBuilder._inlines_to_plain_text(il.inlines)
                parts.append(inner)
            elif isinstance(il, ImageRefIR):
                parts.append(il.alt or "[image]")
            elif isinstance(il, BreakIR | RawInlineIR):
                pass  # skip breaks and raw
        return "".join(parts)

    @staticmethod
    def _blocks_to_plain_text(blocks: list[dict]) -> str:
        """Extract plain text from a list of Pandoc block dicts (best-effort)."""
        parts: list[str] = []
        for blk in blocks:
            t = blk.get("t", "")
            c = blk.get("c", [])
            if t in ("Para", "Plain"):
                inlines = c if isinstance(c, list) else []
                text = LaTeXBuilder._raw_inlines_to_text(inlines)
                parts.append(text)
            elif t == "Header":
                c_list = c if isinstance(c, list) else [1, ["", [], []], []]
                inlines = c_list[2] if len(c_list) > 2 else []
                text = LaTeXBuilder._raw_inlines_to_text(inlines)
                parts.append(text)
        return " ".join(parts)

    @staticmethod
    def _raw_inlines_to_text(inlines: list[dict]) -> str:
        """Quick plain-text extraction from raw Pandoc inline JSON."""
        parts: list[str] = []
        for il in inlines:
            t = il.get("t", "")
            c = il.get("c", "")
            if t == "Str":
                parts.append(str(c))
            elif t == "Space":
                parts.append(" ")
            elif t in (
                "Emph",
                "Strong",
                "Underline",
                "Strikeout",
                "Superscript",
                "Subscript",
                "SmallCaps",
                "Link",
                "Span",
            ):
                inner = c[1] if isinstance(c, list) and len(c) > 1 else c if isinstance(c, list) else []
                parts.append(LaTeXBuilder._raw_inlines_to_text(inner if isinstance(inner, list) else []))
            elif t == "Code" or t == "Math":
                parts.append(str(c[1]) if isinstance(c, list) and len(c) > 1 else "")
            elif t == "Quoted":
                inner = c[1] if isinstance(c, list) and len(c) > 1 else []
                parts.append(LaTeXBuilder._raw_inlines_to_text(inner if isinstance(inner, list) else []))
        return "".join(parts)

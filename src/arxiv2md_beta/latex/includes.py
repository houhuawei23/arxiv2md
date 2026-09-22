r"""Recursive ``\\input`` / ``\\include`` resolution for LaTeX source.

Extracted from the deleted ``latex/parser.py`` (legacy pandoc post-processing
pipeline, ~1900 LOC, replaced by ``ir/builders/latex.py::LaTeXBuilder``). This
module is the only piece of that pipeline still on the live path: the IR LaTeX
builder and the local-archive ingestion need a single flat TeX string with all
includes expanded before handing it to pandoc.
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

_INCLUDE_PATTERN = re.compile(r"\\(?:input|include)\{([^}]+)\}")
_LSTINPUT_PATTERN = re.compile(r"\\lstinputlisting(?:\[[^\]]*\])?\{([^}]+)\}")
_BIBLIOGRAPHY_PATTERN = re.compile(r"\\bibliography\{([^}]+)\}")
_ENV_PATTERN = re.compile(r"\\(begin|end)\{([a-zA-Z*]+)\}")


def _after_unescaped_comment(text: str, pos: int) -> bool:
    r"""True when *pos* sits after an unescaped ``%`` earlier on its line.

    The old check only recognized comments at column 0, so ``foo % \input{x}``
    silently inlined the include (audit5 R-4). Backslash-run parity decides
    whether the percent sign is escaped.
    """
    line_start = text.rfind("\n", 0, pos) + 1
    i = line_start
    while True:
        j = text.find("%", i, pos)
        if j == -1:
            return False
        k = j - 1
        run = 0
        while k >= line_start and text[k] == "\\":
            run += 1
            k -= 1
        if run % 2 == 0:
            return True
        i = j + 1


def _within_base_dir(path: Path, base_dir: Path) -> bool:
    r"""True when *path* stays inside *base_dir* after resolving.

    ``\input{../../..}`` escaped the extracted archive and read arbitrary
    readable files; the zip layer has zip-slip protection, the include
    resolver needed the same containment (audit5 G3-4).
    """
    try:
        path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return False
    return True


class _IncludeContext:
    r"""Shared state for one include resolution (audit5 X2).

    Previously every unresolved ``\input`` triggered its own ``rglob`` walk of
    the whole extracted archive; the fallback now consults a filename index
    built lazily from a single walk and shared across the whole recursion.
    The per-mode flags let the image/affiliation expansion path reuse this
    resolver with ``\lstinputlisting``/``\bibliography``/orphan handling
    disabled (the old duplicate implementation's behavior).
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.stack: set[Path] = set()  # active recursion chain (cycle guard)
        self.expand_lstinputlisting = True
        self.expand_bibliography = True
        self.fix_orphan_ends = True
        self._index: dict[str, list[Path]] | None = None

    def filename_index(self) -> dict[str, list[Path]]:
        """Paths under base_dir keyed by basename, in directory-walk order."""
        if self._index is None:
            index: dict[str, list[Path]] = {}
            for p in self.base_dir.rglob("*"):
                if p.is_file():
                    index.setdefault(p.name, []).append(p)
            self._index = index
        return self._index


def resolve_latex_includes(
    main_file: Path,
    base_dir: Path,
    *,
    expand_lstinputlisting: bool = True,
    expand_bibliography: bool = True,
    fix_orphan_ends: bool = True,
) -> str:
    r"""Recursively expand ``\\input`` / ``\\include`` / ``\\lstinputlisting``.

    Returns the complete LaTeX content of *main_file* with all includes inlined
    into a single string. Missing files emit a warning and are substituted with
    empty content (so pandoc does not choke on a dangling ``\input``). Circular
    includes are detected and broken; a file included twice by *disjoint*
    subtrees (a diamond) expands both times — only the active recursion chain
    guards the cycle check (audit4 P2: the old global visited set dropped the
    second inclusion's content).

    The three flags are off for the image/affiliation expansion path, which
    used to run a second, diverging implementation (audit5 X2 unification).
    """
    ctx = _IncludeContext(base_dir)
    ctx.expand_lstinputlisting = expand_lstinputlisting
    ctx.expand_bibliography = expand_bibliography
    ctx.fix_orphan_ends = fix_orphan_ends
    return _resolve_includes_recursive(main_file, ctx)


def _resolve_includes_recursive(
    tex_file: Path,
    ctx: _IncludeContext,
) -> str:
    """Recursively resolve includes in a LaTeX file (*ctx.stack* = active chain)."""
    stack = ctx.stack
    base_dir = ctx.base_dir
    if tex_file in stack:
        logger.warning(f"Circular include detected: {tex_file}")
        return ""

    stack.add(tex_file)

    if not tex_file.exists():
        logger.warning(f"LaTeX file not found: {tex_file}")
        stack.discard(tex_file)
        return ""

    content = tex_file.read_text(encoding="utf-8", errors="ignore")

    def replace_include(match: re.Match[str]) -> str:
        # Skip commented-out includes (a mid-line "% \\input" hides them too)
        if _after_unescaped_comment(content, match.start()):
            return match.group(0)
        included_file_str = match.group(1).strip()
        # Normalize: LaTeX adds .tex automatically for \input/\include
        stem = included_file_str[:-4] if included_file_str.endswith(".tex") else included_file_str

        # Try multiple paths: as-is, with .tex, and basename index
        candidates = [
            base_dir / included_file_str,  # e.g. data/prompt_summary.md
            base_dir / f"{stem}.tex",
            base_dir / stem,
        ]
        included_file = None
        for cand in candidates:
            if cand.exists() and cand.is_file() and _within_base_dir(cand, base_dir):
                included_file = cand
                break
        if included_file is None:
            # Basename fallback (handles tables/safety_cot etc.) via the one
            # prebuilt index instead of a full rglob per miss (audit5 X2).
            name = Path(included_file_str).name
            index = ctx.filename_index()
            for p in index.get(name, []) + index.get(f"{stem}.tex", []):
                # A pattern with ".." components escapes base_dir (rglob
                # follows them), hence the containment check here too.
                if p.is_file() and _within_base_dir(p, base_dir):
                    included_file = p
                    break
        if included_file is None:
            logger.warning(f"Included file not found: {included_file_str}")
            # Replace with empty to avoid Pandoc failing on missing \input
            return ""

        # Recursively resolve includes in the included file
        return _resolve_includes_recursive(included_file, ctx)

    def replace_lstinputlisting(match: re.Match[str]) -> str:
        r"""Replace ``\lstinputlisting{file}`` with file content as a code block."""
        # Skip commented-out lstinputlisting
        if _after_unescaped_comment(content, match.start()):
            return match.group(0)
        path_str = match.group(1).strip()
        candidates = [
            base_dir / path_str,
            (tex_file.parent / path_str).resolve(),
        ]
        for p in candidates:
            if p.exists() and p.is_file() and _within_base_dir(p, base_dir):
                try:
                    body = p.read_text(encoding="utf-8", errors="ignore")
                    return "\n```\n" + body.rstrip() + "\n```\n"
                except (OSError, PermissionError, UnicodeDecodeError):
                    pass
        logger.warning(f"lstinputlisting file not found: {path_str}")
        return ""

    content = _INCLUDE_PATTERN.sub(replace_include, content)
    if ctx.expand_lstinputlisting:
        content = _LSTINPUT_PATTERN.sub(replace_lstinputlisting, content)
    if ctx.expand_bibliography:
        content = _resolve_bibliography(content, base_dir, tex_file)
    if ctx.fix_orphan_ends:
        content = _fix_orphan_ends(content)
    stack.discard(tex_file)
    return content


def _resolve_bibliography(content: str, base_dir: Path, tex_file: Path) -> str:
    r"""Inline ``.bbl`` content at the ``\bibliography{...}`` call site.

    arXiv LaTeX sources ship the BibTeX-generated ``.bbl`` (a
    ``thebibliography`` environment) but not the ``.bib`` database. Without
    this, ``\bibliography{X}`` resolves to nothing, Pandoc sees no references,
    and every ``\cite{key}`` renders as the raw key instead of ``[N]``.
    """
    stem = tex_file.stem

    def replace_bib(match: re.Match[str]) -> str:
        # Skip commented-out \bibliography (mid-line "% \\bibliography" too)
        if _after_unescaped_comment(content, match.start()):
            return match.group(0)
        bib_str = match.group(1).strip().split(",")[0].strip()
        candidates = [
            base_dir / f"{stem}.bbl",  # arXiv convention: main .bbl alongside .tex
            base_dir / f"{bib_str}.bbl",
        ]
        for cand in candidates:
            if cand.exists() and cand.is_file():
                try:
                    body = cand.read_text(encoding="utf-8", errors="ignore")
                    return "\n" + _strip_bbl_preamble(body) + "\n"
                except (OSError, UnicodeDecodeError):
                    pass
        logger.warning(f"Bibliography .bbl not found for \\bibliography{{{bib_str}}}")
        return ""

    return _BIBLIOGRAPHY_PATTERN.sub(replace_bib, content)


# .bbl preamble lines between ``\begin{thebibliography}`` and the first
# ``\bibitem``: ``\providecommand``, ``\expandafter\ifx ... \fi`` guards etc.
# Pandoc renders some of their argument tokens as literal text ("72 urlstyle").
_BBL_PREAMBLE_LINE_RE = re.compile(
    r"^(?:\\providecommand\b|\\expandafter\b|\\newcommand\b|\\ifx\b|\\fi\b|\\else\b|\\begingroup\b|\\endgroup\b|\\Url\b)"
)


def _strip_bbl_preamble(body: str) -> str:
    r"""Drop the guard-macro preamble lines BibTeX emits before the first ``\bibitem``.

    Keeps ``\begin{thebibliography}{N}`` (the ``{N}`` width arg is dropped by
    Pandoc's reader) intact so the environment stays balanced; only the
    ``\providecommand``/``\expandafter`` plumbing lines are removed.
    """
    lines = body.split("\n")
    first_bibitem = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith(r"\bibitem")), None)
    if first_bibitem is None or first_bibitem == 0:
        return body
    # Find the \begin{thebibliography} line to preserve it.
    begin_idx = next(
        (i for i, ln in enumerate(lines[:first_bibitem]) if r"\begin{thebibliography}" in ln),
        None,
    )
    if begin_idx is None:
        return body
    head = [lines[begin_idx]]  # keep only the \begin line
    # Drop the {N} widest-label argument: Pandoc's reader renders it as
    # literal text ("72") at the top of the reference list.
    head[0] = re.sub(r"(\\begin\{thebibliography\})\{[^}]*\}", r"\1", head[0])
    return "\n".join(head + lines[first_bibitem:])


def _fix_orphan_ends(tex_content: str) -> str:
    r"""Comment out orphan ``\end{env}`` tokens with no matching ``\begin{env}``.

    Position-aware per line: the comment prefix must land on the exact orphan
    token (the old ``str.replace`` could rewrite an earlier, *legitimate*
    token with the same text), and scanning continues after an orphan so later
    ``\begin``/``\end`` tokens on the same line still update the stack
    (audit4 P2).
    """
    stack: list[str] = []
    result_lines: list[str] = []
    for line in tex_content.split("\n"):
        out: list[str] = []
        pos = 0
        for m in _ENV_PATTERN.finditer(line):
            out.append(line[pos : m.start()])
            pos = m.end()
            cmd, env = m.group(1), m.group(2)
            if cmd == "begin":
                stack.append(env)
                out.append(m.group(0))
            elif stack and stack[-1] == env:
                stack.pop()
                out.append(m.group(0))
            else:
                out.append("% " + m.group(0))
        out.append(line[pos:])
        result_lines.append("".join(out))
    return "\n".join(result_lines)

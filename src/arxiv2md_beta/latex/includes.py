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


def resolve_latex_includes(main_file: Path, base_dir: Path) -> str:
    r"""Recursively expand ``\\input`` / ``\\include`` / ``\\lstinputlisting``.

    Returns the complete LaTeX content of *main_file* with all includes inlined
    into a single string. Missing files emit a warning and are substituted with
    empty content (so pandoc does not choke on a dangling ``\input``). Circular
    includes are detected and broken.
    """
    visited: set[Path] = set()
    return _resolve_includes_recursive(main_file, base_dir, visited)


def _resolve_includes_recursive(
    tex_file: Path,
    base_dir: Path,
    visited: set[Path],
) -> str:
    """Recursively resolve includes in a LaTeX file."""
    if tex_file in visited:
        logger.warning(f"Circular include detected: {tex_file}")
        return ""

    visited.add(tex_file)

    if not tex_file.exists():
        logger.warning(f"LaTeX file not found: {tex_file}")
        return ""

    content = tex_file.read_text(encoding="utf-8", errors="ignore")

    def replace_include(match: re.Match[str]) -> str:
        # Skip commented-out includes (line starts with %)
        start = content.rfind("\n", 0, match.start()) + 1
        line_start = content[start : match.start()]
        if line_start.strip().startswith("%"):
            return match.group(0)
        included_file_str = match.group(1).strip()
        # Normalize: LaTeX adds .tex automatically for \input/\include
        stem = included_file_str[:-4] if included_file_str.endswith(".tex") else included_file_str

        # Try multiple paths: as-is, with .tex, and rglob
        candidates = [
            base_dir / included_file_str,  # e.g. data/prompt_summary.md
            base_dir / f"{stem}.tex",
            base_dir / stem,
        ]
        included_file = None
        for cand in candidates:
            if cand.exists() and cand.is_file():
                included_file = cand
                break
        if included_file is None:
            # Try rglob for basename (handles tables/safety_cot etc.)
            name = Path(included_file_str).name
            for p in base_dir.rglob(name):
                if p.is_file():
                    included_file = p
                    break
            if included_file is None:
                for p in base_dir.rglob(f"{stem}.tex"):
                    if p.is_file():
                        included_file = p
                        break
        if included_file is None:
            logger.warning(f"Included file not found: {included_file_str}")
            # Replace with empty to avoid Pandoc failing on missing \input
            return ""

        # Recursively resolve includes in the included file
        return _resolve_includes_recursive(included_file, base_dir, visited)

    def replace_lstinputlisting(match: re.Match[str]) -> str:
        r"""Replace ``\lstinputlisting{file}`` with file content as a code block."""
        # Skip commented-out lstinputlisting
        start = content.rfind("\n", 0, match.start()) + 1
        line_start = content[start : match.start()]
        if line_start.strip().startswith("%"):
            return match.group(0)
        path_str = match.group(1).strip()
        candidates = [
            base_dir / path_str,
            (tex_file.parent / path_str).resolve(),
        ]
        for p in candidates:
            if p.exists() and p.is_file():
                try:
                    body = p.read_text(encoding="utf-8", errors="ignore")
                    return "\n```\n" + body.rstrip() + "\n```\n"
                except (OSError, PermissionError, UnicodeDecodeError):
                    pass
        logger.warning(f"lstinputlisting file not found: {path_str}")
        return ""

    content = _INCLUDE_PATTERN.sub(replace_include, content)
    content = _LSTINPUT_PATTERN.sub(replace_lstinputlisting, content)
    content = _resolve_bibliography(content, base_dir, tex_file)
    content = _fix_orphan_ends(content)
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
        # Skip commented-out \bibliography (line starts with %)
        start = content.rfind("\n", 0, match.start()) + 1
        line_start = content[start : match.start()]
        if line_start.strip().startswith("%"):
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
    r"""Remove or comment orphan ``\end{env}`` with no matching ``\begin{env}``."""
    stack: list[str] = []
    result_lines: list[str] = []
    for line in tex_content.split("\n"):
        for m in _ENV_PATTERN.finditer(line):
            cmd, env = m.group(1), m.group(2)
            if cmd == "begin":
                stack.append(env)
            else:  # end
                if stack and stack[-1] == env:
                    stack.pop()
                else:
                    # Orphan \end - comment it out
                    line = line.replace(m.group(0), "% " + m.group(0))
                    break
        result_lines.append(line)
    return "\n".join(result_lines)

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
    content = _fix_orphan_ends(content)
    return content


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

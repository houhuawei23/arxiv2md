"""Parse LaTeX source to Markdown."""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from loguru import logger

from arxiv2md_beta.schemas import SectionNode


class ParserNotAvailableError(Exception):
    """Raised when LaTeX parser (pypandoc) is not available."""

    pass


class ParsedLatex(NamedTuple):
    """Parsed LaTeX content."""

    markdown: str
    title: str | None
    authors: list[str]
    abstract: str | None
    sections: list[SectionNode] | None = None  # 新增：章节树结构


class ParsedLatexStructured(NamedTuple):
    """Parsed LaTeX content with structured sections."""

    sections: list[SectionNode]  # 章节树结构
    flat_markdown: str  # 完整内容（向后兼容）
    title: str | None
    authors: list[str]
    abstract: str | None
    bibliography_start_idx: int | None = None  # 参考文献章节索引
    appendix_start_idx: int | None = None  # 附录章节索引


# Pre-compiled regex patterns for LaTeX parsing
_INCLUDE_PATTERN = re.compile(r"\\(?:input|include)\{([^}]+)\}")
_LSTINPUT_PATTERN = re.compile(r"\\lstinputlisting(?:\[[^\]]*\])?\{([^}]+)\}")
_ENV_PATTERN = re.compile(r"\\(begin|end)\{([a-zA-Z*]+)\}")
_TITLE_PATTERN = re.compile(r"\\title\s*\{")
_AUTHOR_PATTERN = re.compile(r"\\author\s*\{")
_ABSTRACT_PATTERN = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL)
_AND_SPLIT_RE = re.compile(r"\\and|\\AND")
_COMMENT_CLEAN_RE = re.compile(r'^\s*%\s*')
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?\s*(\[[^\]]*\])?\s*(\{[^\}]*\})?")
_BRACES_RE = re.compile(r"\{|\}")
_WHITESPACE_RE = re.compile(r"\s+")

# 新增：章节解析相关正则
_SECTION_PATTERN = re.compile(r"\\section\s*\*?\s*\{")
_SUBSECTION_PATTERN = re.compile(r"\\subsection\s*\*?\s*\{")
_SUBSUBSECTION_PATTERN = re.compile(r"\\subsubsection\s*\*?\s*\{")
_CHAPTER_PATTERN = re.compile(r"\\chapter\s*\*?\s*\{")
_LABEL_PATTERN = re.compile(r"\\label\{([^}]+)\}")
_CITE_PATTERN = re.compile(r"\\(?:cite|citep|citet|citealp|citeauthor|citeyear|parencite)\{([^}]+)\}")
_BIBITEM_PATTERN = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_BEGIN_BIBLIOGRAPHY = re.compile(r"\\begin\{thebibliography\}")
_END_BIBLIOGRAPHY = re.compile(r"\\end\{thebibliography\}")
_APPENDIX_CMD = re.compile(r"\\appendix\b")


def parse_latex_to_markdown(
    main_tex_file: Path,
    base_dir: Path,
    image_map: dict[str, Path],
) -> ParsedLatex:
    """Parse LaTeX file to Markdown.

    Parameters
    ----------
    main_tex_file : Path
        Main LaTeX file path
    base_dir : Path
        Base directory for resolving includes
    image_map : dict[str, Path]
        Mapping from LaTeX image labels/paths to local image paths

    Returns:
    -------
    ParsedLatex
        Parsed content with metadata

    Raises:
    ------
    ParserNotAvailableError
        If pypandoc is not available
    """
    # Check if pypandoc is available
    try:
        import pypandoc
    except ImportError:
        raise ParserNotAvailableError(
            "pypandoc is required for LaTeX parsing. "
            "Install it with: pip install pypandoc"
        )

    # Resolve all includes/inputs recursively
    full_tex_content = _resolve_latex_includes(main_tex_file, base_dir)

    # Extract metadata before conversion
    title = _extract_title(full_tex_content)
    authors = _extract_authors(full_tex_content)
    abstract = _extract_abstract(full_tex_content)

    # Convert LaTeX to Markdown using pandoc
    try:
        markdown = pypandoc.convert_text(
            full_tex_content,
            "md",
            format="latex",
            extra_args=["--wrap=none"],  # Don't wrap lines
        )
    except RuntimeError as e:
        raise RuntimeError(f"Failed to convert LaTeX to Markdown: {e}") from e
    except OSError as e:
        raise RuntimeError(f"Failed to convert LaTeX to Markdown (pandoc not found?): {e}") from e

    # Post-process markdown: fix equations, tables, figures, references, remove divs
    markdown = _postprocess_markdown(markdown, image_map)

    # 新增：提取章节结构
    sections = _extract_sections_from_latex(full_tex_content, markdown)

    return ParsedLatex(
        markdown=markdown,
        title=title,
        authors=authors,
        abstract=abstract,
        sections=sections,
    )


def _resolve_latex_includes(main_file: Path, base_dir: Path) -> str:
    """Resolve all \\input and \\include commands recursively.

    Parameters
    ----------
    main_file : Path
        Main LaTeX file
    base_dir : Path
        Base directory for resolving relative paths

    Returns:
    -------
    str
        Complete LaTeX content with includes resolved
    """
    visited_files: set[Path] = set()
    return _resolve_includes_recursive(main_file, base_dir, visited_files)


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
        line_start = content[start:match.start()]
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
        included_content = _resolve_includes_recursive(included_file, base_dir, visited)
        return included_content

    def replace_lstinputlisting(match: re.Match[str]) -> str:
        """Replace \\lstinputlisting{file} with file content as a code block."""
        # Skip commented-out lstinputlisting
        start = content.rfind("\n", 0, match.start()) + 1
        line_start = content[start:match.start()]
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

    # Replace all includes
    content = _INCLUDE_PATTERN.sub(replace_include, content)
    content = _LSTINPUT_PATTERN.sub(replace_lstinputlisting, content)

    # Fix orphan \end{...} that can occur when missing/circular includes are replaced with ""
    content = _fix_orphan_ends(content)

    return content


def _fix_orphan_ends(tex_content: str) -> str:
    """Remove or comment orphan \\end{env} that have no matching \\begin{env}."""
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


def _extract_title(tex_content: str) -> str | None:
    """Extract title from LaTeX content.
    
    Uses TexSoup if available for proper nested brace handling,
    otherwise falls back to regex with balanced brace matching.
    """
    # Try TexSoup first (handles nested braces correctly)
    try:
        from TexSoup import TexSoup

        soup = TexSoup(tex_content)
        # Use attribute access instead of find() to avoid regex escape issues
        title_cmd = getattr(soup, 'title', None)
        if title_cmd:
            # Extract text from title command, handling nested braces
            title_text = _texsoup_extract_text(title_cmd)
            if title_text:
                return title_text.strip()
    except ImportError:
        # TexSoup not available, fall back to regex
        pass
    except (AttributeError, TypeError, ValueError) as e:
        logger.debug(f"TexSoup extraction failed, falling back to regex: {e}")

    # Fallback: regex with balanced brace matching
    return _extract_title_regex(tex_content)


def _extract_title_regex(tex_content: str) -> str | None:
    """Extract title using regex with balanced brace matching."""
    # Find \title{...} with balanced braces
    match = _TITLE_PATTERN.search(tex_content)
    if not match:
        return None

    start_pos = match.end()
    brace_count = 1
    i = start_pos

    while i < len(tex_content) and brace_count > 0:
        if tex_content[i] == '{':
            brace_count += 1
        elif tex_content[i] == '}':
            brace_count -= 1
        i += 1

    if brace_count == 0:
        title_content = tex_content[start_pos:i-1]
        return _clean_latex_text(title_content)

    return None


def _extract_authors(tex_content: str) -> list[str]:
    """Extract authors from LaTeX content.
    
    Uses TexSoup if available for proper nested brace handling,
    otherwise falls back to regex with balanced brace matching.
    """
    authors: list[str] = []

    # Try TexSoup first (handles nested braces correctly)
    try:
        from TexSoup import TexSoup

        soup = TexSoup(tex_content)
        # Use attribute access instead of find() to avoid regex escape issues
        author_cmd = getattr(soup, 'author', None)
        if author_cmd:
            # Extract text from author command
            author_text = _texsoup_extract_text(author_cmd)
            if author_text:
                # Split by \and or \AND
                author_parts = _AND_SPLIT_RE.split(author_text)
                for part in author_parts:
                    cleaned = part.strip()
                    # Remove comment markers and clean
                    cleaned = _COMMENT_CLEAN_RE.sub('', cleaned)  # Remove leading % and whitespace
                    cleaned = _clean_latex_text(cleaned)
                    if cleaned:
                        authors.append(cleaned)
                return authors
    except ImportError:
        # TexSoup not available, fall back to regex
        pass
    except (AttributeError, TypeError, ValueError) as e:
        logger.debug(f"TexSoup extraction failed, falling back to regex: {e}")

    # Fallback: regex with balanced brace matching
    return _extract_authors_regex(tex_content)


def _extract_authors_regex(tex_content: str) -> list[str]:
    """Extract authors using regex with balanced brace matching."""
    authors: list[str] = []
    # Find \author{...} with balanced braces
    match = _AUTHOR_PATTERN.search(tex_content)
    if not match:
        return authors

    start_pos = match.end()
    brace_count = 1
    i = start_pos

    while i < len(tex_content) and brace_count > 0:
        if tex_content[i] == '{':
            brace_count += 1
        elif tex_content[i] == '}':
            brace_count -= 1
        i += 1

    if brace_count == 0:
        author_text = tex_content[start_pos:i-1]
        # Split by \and or \AND
        author_parts = re.split(r"\\and|\\AND", author_text)
        for part in author_parts:
            cleaned = part.strip()
            # Remove comment markers
            cleaned = re.sub(r'^\s*%\s*', '', cleaned)
            cleaned = _clean_latex_text(cleaned)
            if cleaned:
                authors.append(cleaned)

    return authors


def _extract_abstract(tex_content: str) -> str | None:
    """Extract abstract from LaTeX content."""
    # Look for \begin{abstract}...\end{abstract}
    abstract_match = _ABSTRACT_PATTERN.search(tex_content)
    if abstract_match:
        return _clean_latex_text(abstract_match.group(1))
    return None


def _texsoup_extract_text(node) -> str:
    """Extract plain text from a TexSoup node, removing LaTeX commands.
    
    Recursively processes the node tree to extract text content while
    removing formatting commands like \vspace, \textbf, etc.
    """
    # Handle TexSoup command nodes - extract from args
    if hasattr(node, 'args') and node.args:
        parts = []
        for arg in node.args:
            if hasattr(arg, 'contents'):
                # Process contents recursively
                for item in arg.contents:
                    if hasattr(item, 'name'):
                        # Skip certain commands like \vspace, \hspace, etc.
                        cmd_name = item.name
                        if cmd_name in ('vspace', 'hspace', 'hfill', 'vfill', 'newline', 'linebreak'):
                            continue
                        # Recursively extract from nested content
                        parts.append(_texsoup_extract_text(item))
                    elif isinstance(item, str):
                        parts.append(item)
                    else:
                        parts.append(_texsoup_extract_text(item))
            elif isinstance(arg, str):
                parts.append(arg)
            else:
                parts.append(_texsoup_extract_text(arg))
        content = "".join(parts)
    elif hasattr(node, 'string'):
        # For commands with string content
        content = str(node.string) if node.string else ""
    elif hasattr(node, 'contents'):
        # For environments or nodes with contents
        parts = []
        for item in node.contents:
            if hasattr(item, 'name'):
                # Skip certain commands like \vspace, \hspace, etc.
                cmd_name = item.name
                if cmd_name in ('vspace', 'hspace', 'hfill', 'vfill', 'newline', 'linebreak'):
                    continue
                # Recursively extract from nested content
                parts.append(_texsoup_extract_text(item))
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(_texsoup_extract_text(item))
        content = "".join(parts)
    else:
        content = str(node)

    # Clean up: remove LaTeX commands that might remain
    content = _LATEX_CMD_RE.sub("", content)
    content = _BRACES_RE.sub("", content)
    content = _WHITESPACE_RE.sub(" ", content)
    return content.strip()


def _clean_latex_text(text: str) -> str:
    """Clean LaTeX text by removing commands and formatting."""
    # Remove comment markers first
    text = _COMMENT_CLEAN_RE.sub('', text)
    # Remove common LaTeX commands
    text = _LATEX_CMD_RE.sub("", text)
    text = _BRACES_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _postprocess_markdown(markdown: str, image_map: dict[str, Path]) -> str:
    """Post-process Pandoc output to fix equations, tables, figures, references, and remove divs.
    
    Parameters
    ----------
    markdown : str
        Raw markdown from Pandoc
    image_map : dict[str, Path]
        Mapping from LaTeX image labels/paths to local image paths
        
    Returns:
    -------
    str
        Post-processed markdown
    """
    # Shared figure counter across all figure processing functions
    figure_counter = [0]

    # Apply post-processing steps in order
    markdown = _fix_equation_labels(markdown)
    markdown = _fix_tables(markdown)
    markdown = _fix_figures(markdown, image_map, figure_counter)
    markdown = _fix_markdown_images_with_attributes(markdown, figure_counter)  # Handle ![...](path){#fig:xxx}
    markdown = _fix_references(markdown)
    markdown = _remove_pandoc_divs(markdown)
    markdown = _replace_image_references(markdown, image_map)

    return markdown


def _fix_equation_labels(markdown: str) -> str:
    """Fix equation labels: extract \\label{eq:xxx} and convert to <a id="eq:xxx"></a>.
    
    Also removes \begin{gathered} and \\end{gathered}, keeping only \begin{aligned}...\\end{aligned}.
    """
    # Pattern to match $$...\begin{gathered}...\begin{aligned}...\label{eq:xxx}...\end{aligned}...\end{gathered}...$$
    # We need to handle nested structures
    # FIX: Use \\end instead of \end to avoid escape sequence error (\e is invalid)
    pattern = r'\$\$\s*\\begin\{gathered\}(.*?)\\end\{gathered\}\s*\$\$'

    def replace_equation(match: re.Match[str]) -> str:
        content = match.group(1)

        # Extract label if present
        label_match = re.search(r'\\label\{([^}]+)\}', content)
        label_id = None
        if label_match:
            label_id = label_match.group(1)
            # Remove the \label command
            content = re.sub(r'\\label\{[^}]+\}', '', content)

        # Remove \begin{gathered} and \end{gathered} if they exist
        # FIX: Use \\end instead of \end to avoid escape sequence error (\e is invalid)
        content = re.sub(r'\\begin\{gathered\}|\\end\{gathered\}', '', content)
        content = content.strip()

        # Build result
        result_parts = []
        if label_id:
            result_parts.append(f'<a id="eq:{label_id}"></a>')
        result_parts.append('$$')
        result_parts.append(content)
        result_parts.append('$$')

        return '\n'.join(result_parts)

    markdown = re.sub(pattern, replace_equation, markdown, flags=re.DOTALL)

    return markdown


def _fix_tables(markdown: str) -> str:
    """Convert Pandoc mytabular blocks to standard markdown pipe tables."""
    # Pattern to match ::: mytabular blocks
    pattern = r'::: mytabular\s*\n(.*?)\n:::'

    def convert_table(match: re.Match[str]) -> str:
        table_content = match.group(1)
        lines = table_content.strip().split('\n')

        # Parse rows - rows are separated by \ at the end of line
        rows = []
        current_row_parts = []

        for line in lines:
            line = line.strip()
            # Skip metadata lines
            if not line or line.startswith('colspec') or line.startswith('row1') or line.startswith('stretch'):
                continue

            # Check if line ends with \ (row separator)
            if line.endswith('\\'):
                # This is a complete row
                row_line = line[:-1].strip()  # Remove trailing \
                if '&' in row_line:
                    cells = [cell.strip() for cell in row_line.split('&')]
                    rows.append(cells)
                current_row_parts = []
            elif '&' in line:
                # Row continues (multi-line row)
                current_row_parts.append(line)
            else:
                # End of multi-line row
                if current_row_parts:
                    combined = ' '.join(current_row_parts)
                    if '&' in combined:
                        cells = [cell.strip() for cell in combined.split('&')]
                        rows.append(cells)
                    current_row_parts = []

        # Handle any remaining row parts
        if current_row_parts:
            combined = ' '.join(current_row_parts)
            if '&' in combined:
                cells = [cell.strip() for cell in combined.split('&')]
                rows.append(cells)

        if not rows:
            return match.group(0)  # Return original if parsing failed

        # Convert to markdown table
        result_lines = []
        for i, row in enumerate(rows):
            # Clean cells: remove HTML comments, handle math
            cleaned_row = []
            for cell in row:
                # Remove HTML comments like `<!-- -->`{=html}
                cell = re.sub(r'`<!-- -->`\{=html\}', '', cell)
                # Remove backticks around math if present
                cell = re.sub(r'`(\$[^$]+\$)`', r'\1', cell)
                # Clean up extra whitespace
                cell = re.sub(r'\s+', ' ', cell)
                cleaned_row.append(cell.strip())

            # Build markdown row
            row_str = '| ' + ' | '.join(cleaned_row) + ' |'
            result_lines.append(row_str)

            # Add separator after header
            if i == 0:
                separator = '| ' + ' | '.join(['---'] * len(cleaned_row)) + ' |'
                result_lines.append(separator)

        return '\n'.join(result_lines)

    markdown = re.sub(pattern, convert_table, markdown, flags=re.DOTALL)

    return markdown


def _fix_figures(markdown: str, image_map: dict[str, Path], figure_counter: list[int] | None = None) -> str:
    """Convert <figure> blocks to standard markdown image format.
    
    Converts:
    <figure id="fig:xxx">
    <p><img src="path" alt="..." /></p>
    <figcaption>caption</figcaption>
    </figure>
    
    To:
    <a id="fig:xxx"></a>
    ![](./images/xxx.png)
    > Figure N: caption
    """
    # Pattern to match <figure> blocks
    pattern = r'<figure\s+id="([^"]+)">\s*(.*?)\s*</figure>'

    if figure_counter is None:
        figure_counter = [0]  # Use list to allow modification in nested function

    def convert_figure(match: re.Match[str]) -> str:
        fig_id = match.group(1)
        fig_content = match.group(2)

        # Extract images
        img_pattern = r'<img\s+src="([^"]+)"[^>]*>'
        images = re.findall(img_pattern, fig_content)

        # Extract caption
        caption_match = re.search(r'<figcaption>(.*?)</figcaption>', fig_content, re.DOTALL)
        caption = caption_match.group(1).strip() if caption_match else ""

        # Build result
        result_parts = []
        result_parts.append(f'<a id="{fig_id}"></a>')
        result_parts.append("")  # Newline after tag to separate from content
        # Convert images
        for img_src in images:
            # Find corresponding image in image_map
            img_path = _find_image_path(img_src, image_map)
            if img_path:
                result_parts.append(f'![](./images/{img_path.name})')
            else:
                # Fallback: try to construct path
                result_parts.append(f'![]({img_src})')
            result_parts.append("")  # Newline after image
        # Add caption
        if caption:
            figure_counter[0] += 1
            result_parts.append(f'> Figure {figure_counter[0]}: {caption}')

        return '\n'.join(result_parts)

    markdown = re.sub(pattern, convert_figure, markdown, flags=re.DOTALL)

    return markdown


def _find_image_path(img_src: str, image_map: dict[str, Path]) -> Path | None:
    """Find the corresponding image path from image_map.
    
    Tries multiple matching strategies:
    - Exact match
    - Filename match (stem or full name)
    - Path match (relative path)
    """
    img_path_obj = Path(img_src)
    img_stem = img_path_obj.stem
    img_name = img_path_obj.name

    # Try exact match
    if img_src in image_map:
        return image_map[img_src]

    # Try filename match
    if img_name in image_map:
        return image_map[img_name]

    # Try stem match
    if img_stem in image_map:
        return image_map[img_stem]

    # Try path variations
    for key, path in image_map.items():
        if Path(key).stem == img_stem or Path(key).name == img_name:
            return path

    return None


def _fix_markdown_images_with_attributes(markdown: str, figure_counter: list[int] | None = None) -> str:
    """Convert markdown images with Pandoc attributes to standard format.
    
    Converts:
    ![caption text](./images/imag.png){#fig:imag width="\\linewidth"}
    
    To:
    <a id="fig:imag"></a>
    ![](./images/imag.png)
    > Figure N: caption text
    """
    # Pattern to match markdown images with Pandoc attributes: ![...](path){#fig:xxx ...}
    # The attributes can contain various things like width, height, etc.
    pattern = r'!\[([^\]]*)\]\(([^)]+)\)\{#([^}\s]+)[^}]*\}'

    if figure_counter is None:
        figure_counter = [0]  # Use list to allow modification in nested function

    def convert_image(match: re.Match[str]) -> str:
        caption = match.group(1).strip()
        img_path = match.group(2).strip()
        fig_id = match.group(3).strip()

        # Build result
        result_parts = []

        # Add anchor
        result_parts.append(f'<a id="{fig_id}"></a>')
        result_parts.append("")  # Newline after tag to separate from content
        # Add image (without caption in alt text)
        result_parts.append(f'![]({img_path})')
        result_parts.append("")  # Newline after image before caption
        # Add caption as blockquote if present
        if caption:
            figure_counter[0] += 1
            result_parts.append(f'> Figure {figure_counter[0]}: {caption}')

        return '\n'.join(result_parts)

    markdown = re.sub(pattern, convert_image, markdown)

    return markdown


def _fix_references(markdown: str) -> str:
    r"""Simplify reference format from Pandoc output.
    
    Converts:
    [\[eq:tok\]](#eq:tok){reference-type="ref+label" reference="eq:tok"}
    
    To:
    [公式 eq:tok](#eq:tok) or [eq:tok](#eq:tok)
    """
    # Pattern for equation references
    eq_pattern = r'\[\\\[([^\]]+)\\\]\]\(#([^)]+)\)\{reference-type="ref\+label"\s+reference="[^"]+"\}'

    def replace_eq_ref(match: re.Match[str]) -> str:
        label = match.group(1)
        anchor = match.group(2)
        return f'[公式 {label}](#{anchor})'

    markdown = re.sub(eq_pattern, replace_eq_ref, markdown)

    # Pattern for other references (figures, tables, etc.)
    ref_pattern = r'\[([^\]]+)\]\(#([^)]+)\)\{reference-type="ref\+label"\s+reference="[^"]+"\}'

    def replace_ref(match: re.Match[str]) -> str:
        text = match.group(1)
        anchor = match.group(2)
        # If text is just a number or simple label, use it as-is
        if re.match(r'^\d+$', text.strip()):
            return f'[{text}](#{anchor})'
        return f'[{text}](#{anchor})'

    markdown = re.sub(ref_pattern, replace_ref, markdown)

    return markdown


def _remove_pandoc_divs(markdown: str) -> str:
    """Remove Pandoc div blocks (::: ... :::).
    
    Removes blocks like:
    ::: center
    :::
    ::::
    :::::
    etc.
    
    And keeps only the content inside.
    """
    # Pattern to match ::: blocks with optional content
    # Handle nested ::: blocks
    lines = markdown.split('\n')
    result_lines = []
    div_depth = 0

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Count consecutive colons at start
        if stripped.startswith(':::'):
            colon_count = len(stripped) - len(stripped.lstrip(':'))
            if colon_count >= 3:
                # This is a div marker
                if div_depth == 0:
                    # Start of div block - skip this line
                    div_depth = colon_count
                else:
                    # End of div block
                    div_depth = 0
                i += 1
                continue

        # If we're inside a div block and it's empty or just whitespace, skip
        if div_depth > 0 and (not stripped or stripped.startswith(':::')):
            i += 1
            continue

        # Normal line - add it
        result_lines.append(line)
        i += 1

    # Also remove standalone ::: lines
    result_lines = [line for line in result_lines if not line.strip().startswith(':::')]

    # Remove consecutive empty lines (more than 2)
    final_lines = []
    prev_empty = False
    for line in result_lines:
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue  # Skip consecutive empty lines
        final_lines.append(line)
        prev_empty = is_empty

    return '\n'.join(final_lines)


def _replace_image_references(markdown: str, image_map: dict[str, Path]) -> str:
    """Replace LaTeX image references with Markdown image syntax.

    Parameters
    ----------
    markdown : str
        Markdown content
    image_map : dict[str, Path]
        Mapping from LaTeX labels/paths to local image paths

    Returns:
    -------
    str
        Markdown with image references replaced
    """
    # Build a comprehensive mapping: try multiple key formats
    # Key formats: full path, filename, stem, relative path
    replacement_map: dict[str, str] = {}

    for latex_path, local_path in image_map.items():
        # Ensure local_path is relative to images directory
        if isinstance(local_path, Path):
            # If path is absolute or doesn't start with images/, make it relative
            local_path_str = str(local_path)
            if not local_path_str.startswith('images/') and not local_path_str.startswith('./images/'):
                # Extract just the filename
                local_path_str = f'./images/{local_path.name}'
            else:
                local_path_str = f'./{local_path_str}' if not local_path_str.startswith('./') else local_path_str
        else:
            local_path_str = str(local_path)

        # Add multiple mappings
        path_obj = Path(latex_path)
        replacement_map[str(latex_path)] = local_path_str
        replacement_map[path_obj.name] = local_path_str
        replacement_map[path_obj.stem] = local_path_str

        # Also try without extension
        if path_obj.suffix:
            name_no_ext = path_obj.stem
            replacement_map[name_no_ext] = local_path_str

    # Replace in markdown: handle both <img src="..."> and ![...](...)
    # Pattern for <img src="path">
    img_pattern = r'<img\s+src="([^"]+)"'

    def replace_img_src(match: re.Match[str]) -> str:
        src = match.group(1)
        # Try to find replacement
        path_obj = Path(src)
        replacement = None

        # Try exact match
        if src in replacement_map:
            replacement = replacement_map[src]
        # Try filename match
        elif path_obj.name in replacement_map:
            replacement = replacement_map[path_obj.name]
        # Try stem match
        elif path_obj.stem in replacement_map:
            replacement = replacement_map[path_obj.stem]

        if replacement:
            return f'<img src="{replacement}"'
        return match.group(0)

    markdown = re.sub(img_pattern, replace_img_src, markdown)

    # Pattern for markdown images: ![...](path)
    md_img_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'

    def replace_md_img(match: re.Match[str]) -> str:
        alt = match.group(1)
        path = match.group(2)
        path_obj = Path(path)
        replacement = None

        # Try exact match
        if path in replacement_map:
            replacement = replacement_map[path]
        # Try filename match
        elif path_obj.name in replacement_map:
            replacement = replacement_map[path_obj.name]
        # Try stem match
        elif path_obj.stem in replacement_map:
            replacement = replacement_map[path_obj.stem]

        if replacement:
            return f'![{alt}]({replacement})'
        return match.group(0)

    markdown = re.sub(md_img_pattern, replace_md_img, markdown)

    return markdown


# =============================================================================
# Section extraction and structured parsing (新增)
# =============================================================================

def _extract_sections_from_latex(tex_content: str, markdown: str) -> list[SectionNode]:
    """Extract section structure from LaTeX and match with Markdown content.
    
    Parameters
    ----------
    tex_content : str
        Full LaTeX content
    markdown : str
        Converted Markdown content from pandoc
        
    Returns:
    -------
    list[SectionNode]
        Hierarchical section tree
    """
    # Parse sections from LaTeX
    section_info = _parse_section_headers(tex_content)

    if not section_info:
        # No sections found, return single section with all content
        return [
            SectionNode(
                title="Document",
                level=1,
                anchor=None,
                html=None,
                markdown=markdown,
                children=[],
            )
        ]

    # Split markdown by section headers to match content
    md_sections = _split_markdown_by_headers(markdown)

    # Build section tree
    sections: list[SectionNode] = []
    stack: list[SectionNode] = []

    for i, (level, title, label) in enumerate(section_info):
        # Find matching markdown content
        md_content = md_sections[i] if i < len(md_sections) else ""

        # Generate anchor from label or title
        anchor = _generate_section_anchor(label, title, i, level)

        node = SectionNode(
            title=title,
            level=level,
            anchor=anchor,
            html=None,
            markdown=md_content,
            children=[],
        )

        # Build hierarchy
        while stack and stack[-1].level >= level:
            stack.pop()

        if stack:
            stack[-1].children.append(node)
        else:
            sections.append(node)

        stack.append(node)

    return sections


def _parse_section_headers(tex_content: str) -> list[tuple[int, str, str | None]]:
    """Parse LaTeX section headers.
    
    Returns:
    -------
    list[tuple[int, str, str | None]]
        List of (level, title, label) tuples
    """
    sections: list[tuple[int, str, str | None]] = []

    # Find all section commands with their positions
    pattern = r'\\(chapter|section|subsection|subsubsection)\s*\*?\s*\{([^}]+)\}'
    label_pattern = r'\\label\{([^}]+)\}'

    level_map = {
        'chapter': 1,
        'section': 1,
        'subsection': 2,
        'subsubsection': 3,
    }

    for match in re.finditer(pattern, tex_content, re.MULTILINE):
        cmd = match.group(1)
        title = match.group(2).strip()
        level = level_map.get(cmd, 1)

        # Look for a label after this section (within reasonable distance)
        start_pos = match.end()
        label = None
        label_match = re.search(label_pattern, tex_content[start_pos:start_pos + 500])
        if label_match:
            label = label_match.group(1)

        sections.append((level, title, label))

    return sections


def _split_markdown_by_headers(markdown: str) -> list[str]:
    """Split markdown content by section headers.
    
    Returns:
    -------
    list[str]
        Content for each section (excluding the header itself)
    """
    # Pattern to match markdown headers
    header_pattern = r'^(#{1,6})\s+(.+)$'

    parts: list[str] = []
    current_part: list[str] = []

    for line in markdown.split('\n'):
        if re.match(header_pattern, line):
            # Save current part
            if current_part:
                parts.append('\n'.join(current_part).strip())
                current_part = []
        current_part.append(line)

    # Don't forget the last part
    if current_part:
        parts.append('\n'.join(current_part).strip())

    return parts


def _generate_section_anchor(label: str | None, title: str, index: int, level: int) -> str | None:
    """Generate anchor ID for a section.
    
    Parameters
    ----------
    label : str | None
        LaTeX label if available
    title : str
        Section title
    index : int
        Section index (0-based)
    level : int
        Section level (1-3)
        
    Returns:
    -------
    str | None
        Anchor ID or None
    """
    # If there's a LaTeX label, use it
    if label:
        # Map common label prefixes to anchor types
        if label.startswith('sec:'):
            return label[4:]  # Remove 'sec:' prefix
        if label.startswith('ch:'):
            return f"chapter-{label[3:]}"
        return label

    # Generate from title
    # Extract numeric prefix if present
    m = re.match(r'^(\d+(?:\.\d+)*)\s+', title)
    if m:
        numbers = m.group(1).replace('.', '-')
        return f"section-{numbers}"

    # Check for appendix pattern
    if re.match(r'^[A-Z]\b', title, re.IGNORECASE):
        letter = title[0].upper()
        return f"appendix-{letter.lower()}"

    # Fallback: use index
    return f"section-{index + 1}"


def _extract_bibliography_info(tex_content: str) -> tuple[int | None, int | None]:
    """Extract bibliography and appendix positions.
    
    Returns:
    -------
    tuple[int | None, int | None]
        (bibliography_section_index, appendix_section_index) or None if not found
    """
    bib_idx: int | None = None
    app_idx: int | None = None

    # Check for \appendix command
    appendix_match = _APPENDIX_CMD.search(tex_content)
    if appendix_match:
        # Find which section comes after \appendix
        # This is approximate - we count sections before the command
        pre_content = tex_content[:appendix_match.start()]
        app_idx = len(re.findall(r'\\(?:chapter|section)\s*\*?\s*\{', pre_content))

    # Check for bibliography environment
    bib_match = _BEGIN_BIBLIOGRAPHY.search(tex_content)
    if bib_match:
        pre_content = tex_content[:bib_match.start()]
        bib_idx = len(re.findall(r'\\(?:chapter|section)\s*\*?\s*\{', pre_content))

    return bib_idx, app_idx


def _convert_citations_to_links(markdown: str, bib_keys: dict[str, int]) -> str:
    """Convert LaTeX citation commands to Markdown links.
    
    Parameters
    ----------
    markdown : str
        Markdown content
    bib_keys : dict[str, int]
        Mapping from citation key to reference number
        
    Returns:
    -------
    str
        Markdown with citations converted to links
    """
    def replace_cite(match: re.Match[str]) -> str:
        keys_str = match.group(1)
        keys = [k.strip() for k in keys_str.split(',')]

        numbers: list[str] = []
        for key in keys:
            if key in bib_keys:
                num = bib_keys[key]
                numbers.append(f"[{num}](#ref-{num})")
            else:
                numbers.append(f"[{key}]")

        return ', '.join(numbers)

    return _CITE_PATTERN.sub(replace_cite, markdown)


def _extract_bibliography_keys(tex_content: str) -> dict[str, int]:
    """Extract bibliography keys and assign numbers.
    
    Parameters
    ----------
    tex_content : str
        Full LaTeX content
        
    Returns:
    -------
    dict[str, int]
        Mapping from citation key to reference number (1-based)
    """
    keys: dict[str, int] = {}
    counter = 0

    for match in _BIBITEM_PATTERN.finditer(tex_content):
        counter += 1
        key = match.group(1)
        keys[key] = counter

    return keys


def _add_bibliography_anchors(markdown: str, bib_keys: dict[str, int]) -> str:
    """Add anchors to bibliography entries in markdown.
    
    Parameters
    ----------
    markdown : str
        Markdown content (References section)
    bib_keys : dict[str, int]
        Mapping from citation key to reference number
        
    Returns:
    -------
    str
        Markdown with anchors added
    """
    lines = markdown.split('\n')
    result: list[str] = []

    for line in lines:
        # Look for bibliography entry patterns
        # Pandoc typically converts \bibitem to lines starting with [number] or just the entry
        match = re.match(r'^(\s*\[?(\d+)\]?\s+)', line)
        if match:
            num = int(match.group(2))
            anchor = f'<a id="ref-{num}"></a>'
            result.append(f"{anchor} {line}")
        else:
            result.append(line)

    return '\n'.join(result)


def _extract_labels(tex_content: str) -> dict[str, tuple[str, int]]:
    """Extract all LaTeX labels with their context.
    
    Parameters
    ----------
    tex_content : str
        Full LaTeX content
        
    Returns:
    -------
    dict[str, tuple[str, int]]
        Mapping from label key to (type, number)
        type: 'fig', 'tab', 'eq', 'sec', etc.
    """
    labels: dict[str, tuple[str, int]] = {}

    # Counters for each type
    counters: dict[str, int] = {}

    # Find all \label commands with surrounding context
    for match in _LABEL_PATTERN.finditer(tex_content):
        label = match.group(1)

        # Determine type from label prefix or context
        label_type = 'unknown'
        if ':' in label:
            label_type = label.split(':')[0]
        else:
            # Look at surrounding context (previous 200 chars)
            start = max(0, match.start() - 200)
            context = tex_content[start:match.start()]

            if re.search(r'\\begin\{(figure|figure\*|wrapfigure)', context):
                label_type = 'fig'
            elif re.search(r'\\begin\{(table|table\*|tabular)', context):
                label_type = 'tab'
            elif re.search(r'\\begin\{(equation|align|gather|eqnarray)', context):
                label_type = 'eq'
            elif re.search(r'\\(chapter|section|subsection)', context):
                label_type = 'sec'

        # Increment counter for this type
        counters[label_type] = counters.get(label_type, 0) + 1
        labels[label] = (label_type, counters[label_type])

    return labels


def _convert_refs_to_links(markdown: str, labels: dict[str, tuple[str, int]]) -> str:
    """Convert LaTeX \ref commands to Markdown links.
    
    Parameters
    ----------
    markdown : str
        Markdown content
    labels : dict[str, tuple[str, int]]
        Mapping from label to (type, number)
        
    Returns:
    -------
    str
        Markdown with refs converted to links
    """
    ref_pattern = re.compile(r'~(\\ref\{([^}]+)\})')

    def replace_ref(match: re.Match[str]) -> str:
        label = match.group(2)
        if label in labels:
            label_type, number = labels[label]
            anchor_map = {
                'fig': 'fig',
                'tab': 'table',
                'eq': 'eq',
                'sec': 'section',
            }
            anchor_prefix = anchor_map.get(label_type, label_type)
            return f"[{number}](#{anchor_prefix}-{label if ':' not in label else label.split(':', 1)[1]})"
        return match.group(0)

    return ref_pattern.sub(replace_ref, markdown)



def _postprocess_markdown_enhanced(
    markdown: str,
    image_map: dict[str, Path],
    tex_content: str,
) -> str:
    """Enhanced post-process with citation links and bibliography anchors.
    
    Parameters
    ----------
    markdown : str
        Raw markdown from Pandoc
    image_map : dict[str, Path]
        Mapping from LaTeX image labels/paths to local image paths
    tex_content : str
        Original LaTeX content for extracting citation info
        
    Returns:
    -------
    str
        Post-processed markdown with links and anchors
    """
    # Extract bibliography keys
    bib_keys = _extract_bibliography_keys(tex_content)
    labels = _extract_labels(tex_content)

    # Apply standard post-processing
    figure_counter = [0]
    markdown = _fix_equation_labels(markdown)
    markdown = _fix_tables(markdown)
    markdown = _fix_figures(markdown, image_map, figure_counter)
    markdown = _fix_markdown_images_with_attributes(markdown, figure_counter)
    markdown = _fix_references(markdown)

    # Convert citations to links
    markdown = _convert_citations_to_links(markdown, bib_keys)

    # Convert refs to links
    markdown = _convert_refs_to_links(markdown, labels)

    # Remove divs and fix images
    markdown = _remove_pandoc_divs(markdown)
    markdown = _replace_image_references(markdown, image_map)

    return markdown


def _identify_special_sections(sections: list[SectionNode], tex_content: str) -> tuple[list[SectionNode], list[SectionNode], list[SectionNode]]:
    """Split sections into main, references, and appendix.
    
    Parameters
    ----------
    sections : list[SectionNode]
        All sections
    tex_content : str
        Original LaTeX content
        
    Returns:
    -------
    tuple[list[SectionNode], list[SectionNode], list[SectionNode]]
        (main_sections, ref_sections, appendix_sections)
    """
    from arxiv2md_beta.settings import get_settings

    ing = get_settings().ingestion
    ref_titles = {t.lower() for t in ing.reference_section_titles}

    # Find bibliography position
    bib_idx, app_idx = _extract_bibliography_info(tex_content)

    # Also check for \appendix command
    appendix_match = _APPENDIX_CMD.search(tex_content)

    main_sections: list[SectionNode] = []
    ref_sections: list[SectionNode] = []
    app_sections: list[SectionNode] = []

    in_refs = False
    in_appendix = False

    for i, sec in enumerate(sections):
        title_lower = sec.title.lower()

        # Check if this is the start of references
        if not in_refs and not in_appendix:
            if (bib_idx is not None and i >= bib_idx) or title_lower in ref_titles:
                in_refs = True

        # Check if this is the start of appendix
        if not in_appendix:
            if (app_idx is not None and i >= app_idx) or title_lower.startswith('appendix'):
                in_refs = False
                in_appendix = True

        # Categorize
        if in_appendix:
            app_sections.append(sec)
        elif in_refs:
            ref_sections.append(sec)
        else:
            main_sections.append(sec)

    return main_sections, ref_sections, app_sections


def _enhance_section_markdown(sections: list[SectionNode]) -> None:
    """Add anchors and enhance markdown for all sections.
    
    This mutates the sections in place.
    """
    for sec in sections:
        if sec.anchor and sec.markdown:
            # Add anchor at the beginning of the section markdown
            sec.markdown = f'<a id="{sec.anchor}"></a>\n\n{sec.markdown}'

        # Recursively enhance children
        if sec.children:
            _enhance_section_markdown(sec.children)



# =============================================================================
# Enhanced figure, table, and equation anchors (Phase 3)
# =============================================================================

def _add_figure_anchors(markdown: str, tex_content: str) -> str:
    r"""Add anchors to figures based on LaTeX labels.
    
    Scans the original LaTeX for \label{fig:xxx} and adds corresponding
    anchors to figures in the markdown output.
    
    Parameters
    ----------
    markdown : str
        Markdown content
    tex_content : str
        Original LaTeX content
        
    Returns:
    -------
    str
        Markdown with figure anchors
    """
    # Find all figure labels
    fig_labels: dict[str, int] = {}
    fig_counter = 0

    # Pattern to match figure environments with labels
    fig_pattern = re.compile(
        r'\\begin\{(figure|figure\*|wrapfigure|float)\}[\s\S]*?\\end\{\1\}',
        re.MULTILINE
    )

    for fig_match in fig_pattern.finditer(tex_content):
        fig_env = fig_match.group(0)
        label_match = re.search(r'\\label\{([^}]+)\}', fig_env)
        if label_match:
            label = label_match.group(1)
            if label.startswith('fig:'):
                fig_counter += 1
                fig_labels[label] = fig_counter

    if not fig_labels:
        return markdown

    # Add anchors to markdown figures
    # Match markdown image syntax: ![caption](path)
    md_img_pattern = re.compile(r'(!\[([^\]]*)\]\(([^)]+)\))')

    def replace_figure(match: re.Match[str]) -> str:
        full_match = match.group(1)
        alt = match.group(2)
        path = match.group(3)

        # Check if this image has a corresponding label
        # We use the figure counter to match
        nonlocal fig_counter
        for label, num in fig_labels.items():
            label_name = label.split(':', 1)[1] if ':' in label else label
            # Check if alt text or path contains the label reference
            if label_name in alt or label_name in path:
                return f'<a id="{label}"></a>\n\n{full_match}'

        return full_match

    return md_img_pattern.sub(replace_figure, markdown)


def _add_table_anchors(markdown: str, tex_content: str) -> str:
    """Add anchors to tables based on LaTeX labels.
    
    Parameters
    ----------
    markdown : str
        Markdown content
    tex_content : str
        Original LaTeX content
        
    Returns:
    -------
    str
        Markdown with table anchors
    """
    # Find all table labels
    table_labels: dict[str, int] = {}
    table_counter = 0

    # Pattern to match table environments with labels
    table_pattern = re.compile(
        r'\\begin\{(table|table\*|tabular|tabularx)\}[\s\S]*?\\end\{(\1)\}',
        re.MULTILINE
    )

    for table_match in table_pattern.finditer(tex_content):
        table_env = table_match.group(0)
        label_match = re.search(r'\\label\{([^}]+)\}', table_env)
        if label_match:
            label = label_match.group(1)
            if label.startswith('tab:'):
                table_counter += 1
                table_labels[label] = table_counter

    if not table_labels:
        return markdown

    # Add anchors before markdown tables
    # Find markdown table patterns (lines starting with |)
    lines = markdown.split('\n')
    result: list[str] = []
    table_idx = 0

    for i, line in enumerate(lines):
        # Check if this is the start of a table
        if line.strip().startswith('|') and '|' in line[1:]:
            # Check if previous line is not a table (start of new table)
            if i == 0 or not lines[i-1].strip().startswith('|'):
                table_idx += 1
                # Find matching label
                for label, num in table_labels.items():
                    if num == table_idx:
                        label_short = label.split(':', 1)[1] if ':' in label else label
                        result.append(f'<a id="table-{label_short}"></a>')
                        break
        result.append(line)

    return '\n'.join(result)


def _enhance_equation_anchors(markdown: str, tex_content: str) -> str:
    """Enhance equation handling with better anchors.
    
    Parameters
    ----------
    markdown : str
        Markdown content
    tex_content : str
        Original LaTeX content
        
    Returns:
    -------
    str
        Markdown with enhanced equation anchors
    """
    # Find equation labels
    eq_labels: dict[str, int] = {}
    eq_counter = 0

    # Match equation environments
    eq_envs = [
        r'\\begin\{equation\}',
        r'\\begin\{equation\*\}',
        r'\\begin\{align\}',
        r'\\begin\{align\*\}',
        r'\\begin\{gather\}',
        r'\\begin\{gather\*\}',
        r'\\\[',
    ]

    for pattern in eq_envs:
        for match in re.finditer(pattern, tex_content):
            start = match.start()
            # Look for label within the next 500 chars
            snippet = tex_content[start:start + 500]
            label_match = re.search(r'\\label\{([^}]+)\}', snippet)
            if label_match:
                label = label_match.group(1)
                if label.startswith('eq:'):
                    eq_counter += 1
                    eq_labels[label] = eq_counter

    if not eq_labels:
        return markdown

    # Match display math blocks in markdown
    eq_pattern = re.compile(r'(\$\$[\s\S]*?\$\$)')
    eq_idx = [0]  # Use list for mutable counter in nested function

    def replace_equation(match: re.Match[str]) -> str:
        eq_content = match.group(1)
        eq_idx[0] += 1

        # Find matching label
        for label, num in eq_labels.items():
            if num == eq_idx[0]:
                label_short = label.split(':', 1)[1] if ':' in label else label
                return f'<a id="eq:{label_short}"></a>\n\n{eq_content}'

        return eq_content

    return eq_pattern.sub(replace_equation, markdown)


def _convert_figure_refs_to_links(markdown: str, tex_content: str) -> str:
    """Convert figure references to markdown links.
    
    Parameters
    ----------
    markdown : str
        Markdown content
    tex_content : str
        Original LaTeX content
        
    Returns:
    -------
    str
        Markdown with figure refs as links
    """
    # Find all figure labels to build number mapping
    fig_labels: dict[str, int] = {}
    fig_counter = 0

    fig_pattern = re.compile(
        r'\\begin\{(figure|figure\*|wrapfigure)\}[\s\S]*?\\end\{\1\}',
        re.MULTILINE
    )

    for fig_match in fig_pattern.finditer(tex_content):
        fig_env = fig_match.group(0)
        label_match = re.search(r'\\label\{([^}]+)\}', fig_env)
        if label_match:
            label = label_match.group(1)
            if label.startswith('fig:'):
                fig_counter += 1
                fig_labels[label] = fig_counter

    if not fig_labels:
        return markdown

    # Replace \ref{fig:xxx} with links
    ref_pattern = re.compile(r'(?:~)?\\ref\{([^}]+)\}')

    def replace_ref(match: re.Match[str]) -> str:
        label = match.group(1)
        if label in fig_labels:
            num = fig_labels[label]
            label_short = label.split(':', 1)[1] if ':' in label else label
            return f"[{num}](#fig:{label_short})"
        return match.group(0)

    return ref_pattern.sub(replace_ref, markdown)


def _convert_table_refs_to_links(markdown: str, tex_content: str) -> str:
    """Convert table references to markdown links.
    
    Parameters
    ----------
    markdown : str
        Markdown content
    tex_content : str
        Original LaTeX content
        
    Returns:
    -------
    str
        Markdown with table refs as links
    """
    # Find all table labels
    table_labels: dict[str, int] = {}
    table_counter = 0

    table_pattern = re.compile(
        r'\\begin\{(table|table\*)\}[\s\S]*?\\end\{\1\}',
        re.MULTILINE
    )

    for table_match in table_pattern.finditer(tex_content):
        table_env = table_match.group(0)
        label_match = re.search(r'\\label\{([^}]+)\}', table_env)
        if label_match:
            label = label_match.group(1)
            if label.startswith('tab:'):
                table_counter += 1
                table_labels[label] = table_counter

    if not table_labels:
        return markdown

    # Replace \ref{tab:xxx} with links
    ref_pattern = re.compile(r'(?:~)?\\ref\{([^}]+)\}')

    def replace_ref(match: re.Match[str]) -> str:
        label = match.group(1)
        if label in table_labels:
            num = table_labels[label]
            label_short = label.split(':', 1)[1] if ':' in label else label
            return f"[{num}](#table-{label_short})"
        return match.group(0)

    return ref_pattern.sub(replace_ref, markdown)



# =============================================================================
# Markdown formatting beautification (Phase 5)
# =============================================================================

def _beautify_tables(markdown: str) -> str:
    """Enhance table formatting from LaTeX conversion.
    
    Improvements:
    - Better handling of booktabs (\toprule, \\midrule, \bottomrule)
    - Cleaner column alignment
    - Proper escaping of special characters
    
    Parameters
    ----------
    markdown : str
        Markdown content
        
    Returns:
    -------
    str
        Enhanced markdown with better table formatting
    """
    # Pattern to match markdown tables
    table_pattern = re.compile(
        r'^(\|[^\n]+\|)\n'  # Header row
        r'(\|[-:\s|]+\|)\n'  # Separator row
        r'((?:\|[^\n]+\|\n?)*)',  # Body rows
        re.MULTILINE
    )

    def beautify_table(match: re.Match[str]) -> str:
        header = match.group(1)
        separator = match.group(2)
        body = match.group(3)

        # Clean up the header row
        header_cells = [cell.strip() for cell in header.split('|')[1:-1]]
        cleaned_header = '| ' + ' | '.join(header_cells) + ' |'

        # Ensure proper separator format
        sep_cells = separator.split('|')[1:-1]
        cleaned_sep = '| ' + ' | '.join(['---'] * len(header_cells)) + ' |'

        # Clean body rows
        body_lines = []
        for line in body.strip().split('\n'):
            if line.strip():
                cells = [cell.strip() for cell in line.split('|')[1:-1]]
                # Pad cells if needed
                while len(cells) < len(header_cells):
                    cells.append('')
                body_lines.append('| ' + ' | '.join(cells[:len(header_cells)]) + ' |')

        return cleaned_header + '\n' + cleaned_sep + '\n' + '\n'.join(body_lines)

    return table_pattern.sub(beautify_table, markdown)


def _beautify_figure_captions(markdown: str) -> str:
    """Enhance figure caption formatting.
    
    Converts plain figure captions to blockquote format for better visual
    separation and consistency.
    
    Parameters
    ----------
    markdown : str
        Markdown content
        
    Returns:
    -------
    str
        Enhanced markdown with better figure captions
    """
    # Pattern to match figure captions (various formats)
    # Format 1: > Figure N: caption
    # Format 2: **Figure N:** caption
    caption_pattern = re.compile(
        r'^\*\*(Figure\s+\d+[^*]*)\*\*\s*(.+)$',
        re.MULTILINE
    )

    def enhance_caption(match: re.Match[str]) -> str:
        fig_label = match.group(1).strip()
        caption = match.group(2).strip()
        return f'> **{fig_label}** {caption}'

    return caption_pattern.sub(enhance_caption, markdown)


def _beautify_code_blocks(markdown: str) -> str:
    """Enhance code block formatting.
    
    - Detects language from context
    - Ensures proper fenced code block format
    - Handles lstlisting environments better
    
    Parameters
    ----------
    markdown : str
        Markdown content
        
    Returns:
    -------
    str
        Enhanced markdown with better code blocks
    """
    # Pattern to match code blocks with language hints in comments
    code_pattern = re.compile(
        r'```\s*\n'
        r'(\s*#\s*(?:python|java|c\+\+|cpp|c|javascript|js|typescript|ts|bash|sh|shell|yaml|json|xml|html|css|sql|rust|go)\s*\n)'
        r'([\s\S]*?)'
        r'```',
        re.IGNORECASE
    )

    def extract_language(match: re.Match[str]) -> str:
        lang_comment = match.group(1)
        code = match.group(2)

        # Extract language from comment
        lang_match = re.search(r'#\s*(\w+)', lang_comment)
        if lang_match:
            lang = lang_match.group(1).lower()
            return f'```{lang}\n{code}```'

        return match.group(0)

    return code_pattern.sub(extract_language, markdown)


def _cleanup_latex_artifacts(markdown: str) -> str:
    """Clean up common LaTeX to Markdown conversion artifacts.
    
    Parameters
    ----------
    markdown : str
        Markdown content
        
    Returns:
    -------
    str
        Cleaned markdown
    """
    # Remove leftover LaTeX commands that pandoc missed
    cleanup_patterns = [
        # Remove \label commands that weren't converted
        (r'\\label\{[^}]+\}', ''),
        # Remove \ref commands that weren't converted (keep text)
        (r'\\(?:ref|cite)\{([^}]+)\}', r'\1'),
        # Remove \emph and \textbf with content
        (r'\\emph\{([^}]+)\}', r'*\1*'),
        (r'\\textbf\{([^}]+)\}', r'**\1**'),
        # Clean up multiple consecutive empty lines
        (r'\n{4,}', '\n\n\n'),
        # Remove trailing whitespace
        (r'[ \t]+$', '', re.MULTILINE),
    ]

    result = markdown
    for pattern, replacement in cleanup_patterns:
        if len(pattern) == 3:
            # Has flags
            result = re.sub(pattern[0], replacement, result, flags=pattern[2])
        else:
            result = re.sub(pattern, replacement, result)

    return result.strip()


def _beautify_math_display(markdown: str) -> str:
    """Beautify math display blocks.
    
    Ensures proper spacing around display math and consistent formatting.
    
    Parameters
    ----------
    markdown : str
        Markdown content
        
    Returns:
    -------
    str
        Enhanced markdown with better math formatting
    """
    # Pattern to match display math blocks
    math_pattern = re.compile(r'\$\$\s*\n?([\s\S]*?)\n?\s*\$\$')

    def beautify_math(match: re.Match[str]) -> str:
        math_content = match.group(1).strip()

        # Clean up the math content
        math_content = re.sub(r'\n{3,}', '\n\n', math_content)
        math_content = math_content.strip()

        return f'$$\n{math_content}\n$$'

    return math_pattern.sub(beautify_math, markdown)


def _format_algorithm_blocks(markdown: str) -> str:
    """Format algorithm environments nicely.
    
    Parameters
    ----------
    markdown : str
        Markdown content
        
    Returns:
    -------
    str
        Enhanced markdown with better algorithm formatting
    """
    # Pattern to match algorithm captions
    alg_pattern = re.compile(
        r'^(Algorithm\s+\d+[:.]?)\s*\n?(.+?)(?=\n\n|\n```|\Z)',
        re.MULTILINE | re.DOTALL
    )

    def format_algorithm(match: re.Match[str]) -> str:
        alg_label = match.group(1).strip()
        alg_content = match.group(2).strip()

        return f'**{alg_label}** {alg_content}'

    return alg_pattern.sub(format_algorithm, markdown)


def beautify_markdown(markdown: str) -> str:
    """Apply all beautification steps to markdown.
    
    This is the main entry point for Phase 5 beautification.
    
    Parameters
    ----------
    markdown : str
        Raw markdown content
        
    Returns:
    -------
    str
        Beautified markdown
    """
    # Apply beautification steps
    markdown = _beautify_tables(markdown)
    markdown = _beautify_figure_captions(markdown)
    markdown = _beautify_code_blocks(markdown)
    markdown = _beautify_math_display(markdown)
    markdown = _format_algorithm_blocks(markdown)
    markdown = _cleanup_latex_artifacts(markdown)

    return markdown


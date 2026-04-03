"""Parse LaTeX source to Markdown."""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from loguru import logger


class ParserNotAvailableError(Exception):
    """Raised when LaTeX parser (pypandoc) is not available."""

    pass


class ParsedLatex(NamedTuple):
    """Parsed LaTeX content."""

    markdown: str
    title: str | None
    authors: list[str]
    abstract: str | None


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

    Returns
    -------
    ParsedLatex
        Parsed content with metadata

    Raises
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

    return ParsedLatex(
        markdown=markdown,
        title=title,
        authors=authors,
        abstract=abstract,
    )


def _resolve_latex_includes(main_file: Path, base_dir: Path) -> str:
    """Resolve all \\input and \\include commands recursively.

    Parameters
    ----------
    main_file : Path
        Main LaTeX file
    base_dir : Path
        Base directory for resolving relative paths

    Returns
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
        
    Returns
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
    """Fix equation labels: extract \label{eq:xxx} and convert to <a id="eq:xxx"></a>.
    
    Also removes \begin{gathered} and \end{gathered}, keeping only \begin{aligned}...\end{aligned}.
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
    """Simplify reference format from Pandoc output.
    
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

    Returns
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

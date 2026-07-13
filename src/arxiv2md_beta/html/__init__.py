"""HTML parsing (arXiv HTML → ParsedArxivHtml) and section filtering.

The legacy HTML→Markdown fragment converter (html/markdown.py) was removed;
all HTML→Markdown conversion now goes through the IR pipeline
(HTMLBuilder → MarkdownEmitter).
"""

from arxiv2md_beta.html.parser import parse_arxiv_html
from arxiv2md_beta.html.sections import filter_sections

__all__ = ["filter_sections", "parse_arxiv_html"]

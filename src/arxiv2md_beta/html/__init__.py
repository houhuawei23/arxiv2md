"""HTML parsing (arXiv HTML → ParsedArxivHtml).

The legacy HTML→Markdown fragment converter (html/markdown.py) was removed;
all HTML→Markdown conversion now goes through the IR pipeline
(HTMLBuilder → MarkdownEmitter). Section filtering lives in
ir/transforms/section_filter.py.
"""

from arxiv2md_beta.html.parser import parse_arxiv_html

__all__ = ["parse_arxiv_html"]

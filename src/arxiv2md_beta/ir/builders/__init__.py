"""IR builders — convert raw sources to DocumentIR."""

from arxiv2md_beta.ir.builders.base import IRBuilder  # noqa: F401
from arxiv2md_beta.ir.builders.html import HTMLBuilder  # noqa: F401
from arxiv2md_beta.ir.builders.latex import LaTeXBuilder  # noqa: F401

__all__ = ["IRBuilder", "HTMLBuilder", "LaTeXBuilder"]

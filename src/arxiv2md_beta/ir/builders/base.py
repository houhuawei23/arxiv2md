"""Abstract base class for IR builders."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from arxiv2md_beta.ir.document import DocumentIR


class IRBuilder(ABC):
    """Convert a raw source representation into a :class:`DocumentIR`.

    Subclasses implement :meth:`build` for specific source formats
    (HTML, LaTeX/Pandoc AST, etc.).
    """

    @abstractmethod
    def build(self, source: Any, **kwargs: Any) -> DocumentIR:
        """Parse *source* and return a :class:`DocumentIR`."""
        ...

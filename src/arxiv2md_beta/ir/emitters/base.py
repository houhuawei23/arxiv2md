"""Abstract base class for IR emitters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from arxiv2md_beta.ir.document import DocumentIR


class IREmitter(ABC):
    """Serialize a :class:`DocumentIR` to a target format.

    Subclasses implement :meth:`emit` for specific output formats
    (Markdown, JSON, plain text, etc.).
    """

    format_name: str = ""

    @abstractmethod
    def emit(self, doc: DocumentIR) -> str:
        """Convert *doc* to the target format string."""
        ...

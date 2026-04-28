"""Base classes for IR transform passes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from arxiv2md_beta.ir.document import DocumentIR


class IRPass(ABC):
    """A pure(ish) transform: :class:`DocumentIR` → :class:`DocumentIR`.

    Each pass should document whether it mutates the input or returns a
    (deep) copy.  The default pipeline pattern is to work on copies so
    passes remain composable and debuggable.
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    def run(self, doc: DocumentIR) -> DocumentIR:
        """Apply the transform and return (possibly the same) document."""
        ...


class PassPipeline:
    """Ordered composition of :class:`IRPass` instances.

    Usage::

        pp = PassPipeline()
        pp.add(NumberingPass())
        pp.add(AnchorPass())
        doc = pp.run(doc)
    """

    def __init__(self, passes: list[IRPass] | None = None):
        self._passes: list[IRPass] = passes or []

    def add(self, p: IRPass) -> "PassPipeline":
        self._passes.append(p)
        return self

    def run(self, doc: DocumentIR) -> DocumentIR:
        for p in self._passes:
            doc = p.run(doc)
        return doc

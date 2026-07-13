"""Base classes for IR transform passes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from arxiv2md_beta.ir.document import DocumentIR


class IRPass(ABC):
    """A transform: :class:`DocumentIR` → :class:`DocumentIR`.

    Passes mutate the document **in place** and return the same object. They
    are not pure — running the same pass twice on one document (e.g.
    NumberingPass) would double-apply its effect. The pipeline runs each pass
    exactly once per document, so this is safe in practice; callers that need
    to preserve the pre-transform document must ``copy.deepcopy`` it before
    running the pipeline.
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

    def add(self, p: IRPass) -> PassPipeline:
        self._passes.append(p)
        return self

    def run(self, doc: DocumentIR) -> DocumentIR:
        for p in self._passes:
            doc = p.run(doc)
        return doc

"""Section filter pass: include or exclude sections by title."""

from __future__ import annotations

from typing import Literal

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.transforms.base import IRPass


class SectionFilterPass(IRPass):
    """Filter sections by title or struct_id.

    Parameters
    ----------
    mode : str
        ``"include"`` — keep only the named sections.
        ``"exclude"`` — remove the named sections.
    selected : list[str]
        Section titles or struct_ids to include/exclude.
    """

    name = "section_filter"
    description = "Include or exclude sections by title."

    def __init__(
        self,
        mode: Literal["include", "exclude"] = "exclude",
        selected: list[str] | None = None,
    ):
        self.mode = mode
        self.selected = selected or []

    def run(self, doc: DocumentIR) -> DocumentIR:
        doc.sections = [
            s for s in doc.sections
            if self._should_keep(s, self.mode)
        ]
        return doc

    def _should_keep(self, section: SectionIR, mode: str) -> bool:
        """Check if section should be kept.

        Also filters children recursively.
        """
        section.children = [
            c for c in section.children
            if self._should_keep(c, mode)
        ]

        matches = any(
            kw.lower() in section.title.lower()
            for kw in self.selected
        ) or section.struct_id in self.selected

        if mode == "include":
            return matches
        else:  # exclude
            return not matches

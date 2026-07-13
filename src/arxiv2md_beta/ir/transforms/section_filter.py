"""Section filter pass: include or exclude sections by title."""

from __future__ import annotations

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.transforms.base import IRPass


def split_ir_sections(
    sections: list[SectionIR],
    reference_titles: list[str],
) -> tuple[list[SectionIR], list[SectionIR], list[SectionIR]]:
    """Split IR sections into ``(main, references, appendix)`` for sidecar output.

    The first section whose title matches a reference-section title (e.g.
    ``References``, ``Bibliography``) starts the references group; the first
    section whose title starts with ``appendix`` starts the appendix group.
    If a references section exists, it is its own group and everything after it
    is treated as appendix. Returns copies/shares suitable for separate emission.
    """
    ref_set = {t.strip().lower() for t in reference_titles if t and t.strip()}
    if not ref_set:
        return list(sections), [], []

    first_ref_idx: int | None = None
    first_app_idx: int | None = None
    for i, sec in enumerate(sections):
        n = (sec.title or "").strip().lower()
        if first_ref_idx is None and n in ref_set:
            first_ref_idx = i
        if first_app_idx is None and n.startswith("appendix"):
            first_app_idx = i

    if first_ref_idx is not None:
        return (
            sections[:first_ref_idx],
            [sections[first_ref_idx]],
            sections[first_ref_idx + 1 :],
        )
    if first_app_idx is not None:
        return sections[:first_app_idx], [], sections[first_app_idx:]

    return list(sections), [], []


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
        mode: str = "exclude",
        selected: list[str] | None = None,
    ):
        self.mode = mode
        self.selected = selected or []

    def run(self, doc: DocumentIR) -> DocumentIR:
        doc.sections = [s for s in doc.sections if self._should_keep(s, self.mode)]
        return doc

    def _should_keep(self, section: SectionIR, mode: str) -> bool:
        """Check if section should be kept.

        Also filters children recursively.
        """
        section.children = [c for c in section.children if self._should_keep(c, mode)]

        matches = any(kw.lower() in section.title.lower() for kw in self.selected) or section.struct_id in self.selected

        if mode == "include":
            return matches
        else:  # exclude
            return not matches

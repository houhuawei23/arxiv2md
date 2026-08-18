"""Section filter pass: include or exclude sections by title."""

from __future__ import annotations

import re

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.transforms.base import IRPass
from arxiv2md_beta.utils.section_titles import normalize_section_title

# Matches a section-number prefix added by SectionNumberingPass, e.g.
# ``"8 "``, ``"3.1 "``, ``"A "``.  Used by ``split_ir_sections`` to
# recover the original title for reference / appendix detection.
_NUMBER_PREFIX_RE = re.compile(r"^[\d.]+\s+")


def _title_without_number(title: str) -> str:
    """Return *title* with a section-number prefix stripped, if present."""
    return _NUMBER_PREFIX_RE.sub("", title.strip()).strip()


def split_ir_sections(
    sections: list[SectionIR],
    reference_titles: list[str],
) -> tuple[list[SectionIR], list[SectionIR], list[SectionIR]]:
    """Split IR sections into ``(main, references, appendix)`` for sidecar output.

    The first section whose title (after stripping any numbering prefix like
    ``"8 "`` or ``"A "``) matches a reference-section title (e.g.
    ``References``, ``Bibliography``) starts the references group; the first
    section whose stripped title starts with ``appendix`` starts the appendix
    group.  If a references section exists, it is its own group and everything
    after it is treated as appendix.
    """
    ref_set = {t.strip().lower() for t in reference_titles if t and t.strip()}
    if not ref_set:
        return list(sections), [], []

    first_ref_idx: int | None = None
    first_app_idx: int | None = None
    for i, sec in enumerate(sections):
        n = _title_without_number(sec.title or "").lower()
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
        self._selected_titles = {normalize_section_title(kw) for kw in self.selected if kw.strip()}

    def run(self, doc: DocumentIR) -> DocumentIR:
        doc.sections = [s for s in doc.sections if self._should_keep(s, self.mode)]
        return doc

    def _should_keep(self, section: SectionIR, mode: str) -> bool:
        """Check if section should be kept.

        Also filters children recursively. Title matching uses the same
        normalized-exact semantics as the sections tree (``--sections
        Introduction`` does not match "Introduction and Related Work"), plus
        struct_id matching.
        """
        section.children = [c for c in section.children if self._should_keep(c, mode)]

        normalized = normalize_section_title(section.title or "")
        matches = normalized in self._selected_titles or section.struct_id in self.selected

        if mode == "include":
            return matches
        else:  # exclude
            return not matches

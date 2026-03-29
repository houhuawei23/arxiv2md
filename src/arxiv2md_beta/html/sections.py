"""Section filtering and utilities."""

from __future__ import annotations

import re
from typing import Iterable

from arxiv2md_beta.schemas import SectionNode


def normalize_section_title(title: str) -> str:
    """Normalize section titles for comparison."""
    title = title.strip().lower()
    title = re.sub(r"^[\dA-Za-z.\-]+\s+", "", title)
    return re.sub(r"\s+", " ", title)


def filter_sections(
    sections: list[SectionNode],
    *,
    mode: str = "exclude",
    selected: Iterable[str] | None = None,
) -> list[SectionNode]:
    """Filter sections by title using include or exclude mode."""
    selected_titles = {normalize_section_title(title) for title in (selected or []) if title.strip()}
    if not selected_titles:
        return sections

    def _filter(nodes: list[SectionNode]) -> list[SectionNode]:
        result: list[SectionNode] = []
        for node in nodes:
            normalized = normalize_section_title(node.title)
            in_selected = normalized in selected_titles
            if mode == "include":
                if in_selected:
                    result.append(node)
                else:
                    children = _filter(node.children)
                    if children:
                        node.children = children
                        result.append(node)
            else:
                if in_selected:
                    continue
                node.children = _filter(node.children)
                result.append(node)
        return result

    return _filter(list(sections))


def split_sections_at_reference(
    sections: list[SectionNode],
    *,
    reference_titles: Iterable[str],
) -> tuple[list[SectionNode], list[SectionNode], list[SectionNode]]:
    """Split top-level sections into (main, references, appendix).

    The first top-level section whose normalized title matches one of
    ``reference_titles`` (after normalization) starts the references block;
    that section and its subtree are the references file; following top-level
    sections are appendix. If no match, main is the full list and the other
    two lists are empty.
    """
    ref_set = {normalize_section_title(t) for t in reference_titles if str(t).strip()}
    if not ref_set:
        return list(sections), [], []

    for i, sec in enumerate(sections):
        if normalize_section_title(sec.title) in ref_set:
            return sections[:i], [sections[i]], sections[i + 1 :]

    return list(sections), [], []

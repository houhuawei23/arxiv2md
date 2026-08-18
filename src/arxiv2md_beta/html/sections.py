"""Section filtering and utilities."""

from __future__ import annotations

from collections.abc import Iterable

from arxiv2md_beta.schemas import SectionNode
from arxiv2md_beta.utils.section_titles import normalize_section_title

__all__ = ["normalize_section_title", "filter_sections"]


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

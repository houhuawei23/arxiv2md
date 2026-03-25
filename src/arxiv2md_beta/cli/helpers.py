"""Small CLI helpers shared by Typer commands."""

from __future__ import annotations


def collect_sections(sections_csv: str | None, section_list: list[str] | None) -> list[str]:
    """Collect section filters from comma-separated string and repeated --section values."""
    values: list[str] = []
    if sections_csv:
        values.extend(sections_csv.split(","))
    if section_list:
        values.extend(section_list)
    return [value.strip() for value in values if value and value.strip()]

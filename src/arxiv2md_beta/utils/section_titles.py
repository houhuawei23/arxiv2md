"""Section-title normalization (shared by tree filtering and the IR pass)."""

from __future__ import annotations

import re


def normalize_section_title(title: str) -> str:
    """Normalize section titles for comparison.

    Strips numeric/alphanumeric index prefixes (e.g., "1 ", "4.2 ", "A.1 ")
    but keeps semantic words like "Appendix" / "References", collapses
    whitespace, and lowercases.
    """
    title = title.strip().lower()
    title = re.sub(r"^(?:\d+(?:\.\d+)*\.?|[a-z]\.\d+|[a-z]\.)\s+", "", title)
    return re.sub(r"\s+", " ", title)

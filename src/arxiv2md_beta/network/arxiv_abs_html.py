"""Parse arXiv abstract (abs) HTML for author display names and optional affiliation hints."""

from __future__ import annotations

import re
from html import unescape

from bs4 import BeautifulSoup


def parse_abs_page_for_authors(html: str) -> tuple[list[str], list[str]]:
    """Extract author names and any affiliation strings from ``arxiv.org/abs/*`` HTML.

    Returns
    -------
    names : list[str]
        Display names in order (from ``div.authors`` links).
    affiliation_hints : list[str]
        Extra lines sometimes present as ``citation_author_institution`` meta tags,
        or ``div``/``span`` with affiliation-related classes (best-effort).
    """
    soup = BeautifulSoup(html, "html.parser")
    names: list[str] = []
    authors_div = soup.select_one("div.authors")
    if authors_div:
        for a in authors_div.find_all("a", href=True):
            t = a.get_text(strip=True)
            if t:
                names.append(unescape(t))

    hints: list[str] = []
    for meta in soup.find_all("meta"):
        if meta.get("name") == "citation_author_institution" and meta.get("content"):
            hints.append(meta["content"].strip())

    abs_block = soup.select_one("#abs") or soup.select_one("div#abs")
    root = abs_block or soup
    for cls in ("affiliation", "institutions", "author-affiliation"):
        for el in root.find_all(class_=re.compile(cls, re.I)):
            txt = el.get_text(" ", strip=True)
            if txt and len(txt) < 500:
                hints.append(txt)

    # De-dupe hints preserving order
    seen: set[str] = set()
    uniq_hints: list[str] = []
    for h in hints:
        key = h.lower()
        if key not in seen:
            seen.add(key)
            uniq_hints.append(h)

    return names, uniq_hints

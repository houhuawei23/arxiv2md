"""Parse arXiv abstract (abs) HTML for metadata, authors, and affiliation hints."""

from __future__ import annotations

import re
from html import unescape
from typing import Any

from bs4 import BeautifulSoup

from arxiv2md_beta.utils.html_attrs import attr_optional


def parse_abs_page_metadata(html: str) -> dict[str, Any]:
    """Extract title/authors/abstract/date from an ``arxiv.org/abs/*`` page.

    PDF-only submissions never yield an HTML rendering, so the abs landing
    page is their only rich-metadata source when the Atom API is disabled or
    unreachable. All fields are best-effort; missing ones are simply absent
    from the returned dict.
    """
    soup = BeautifulSoup(html, "html.parser")

    citation: dict[str, list[str]] = {}
    for meta in soup.find_all("meta"):
        name = attr_optional(meta, "name")
        if name and name.startswith("citation_") and attr_optional(meta, "content"):
            citation.setdefault(name, []).append(str(meta["content"]).strip())

    out: dict[str, Any] = {}
    if citation.get("citation_title"):
        out["title"] = citation["citation_title"][0]
    if citation.get("citation_author"):
        out["authors"] = citation["citation_author"]
    for date_key in ("citation_online_date", "citation_date"):
        if citation.get(date_key):
            out["date"] = citation[date_key][0]
            break

    abs_block = soup.select_one("div.abstract") or soup.select_one("blockquote.abstract")
    if abs_block:
        text = re.sub(r"^Abstract:\s*", "", abs_block.get_text(" ", strip=True), flags=re.I)
        if text:
            out["summary"] = text

    return out


def parse_abs_page_for_authors(html: str) -> tuple[list[str], list[str]]:
    """Extract author names and any affiliation strings from ``arxiv.org/abs/*`` HTML.

    Returns:
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
        if attr_optional(meta, "name") == "citation_author_institution" and attr_optional(meta, "content"):
            hints.append(str(meta["content"]).strip())

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

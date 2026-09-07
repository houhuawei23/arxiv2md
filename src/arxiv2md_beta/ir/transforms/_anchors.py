"""Shared anchor-slug helpers used by the numbering and anchor passes."""

from __future__ import annotations

import re

_SLUG_RE_NONWORD = re.compile(r"[^\w\s-]")
_SLUG_RE_SPACE = re.compile(r"\s+")


def slugify(title: str) -> str:
    """Convert a section title to a URL-friendly slug."""
    slug = title.lower().strip()
    slug = _SLUG_RE_NONWORD.sub("", slug)
    slug = _SLUG_RE_SPACE.sub("-", slug)
    return slug[:60]


def unique_slug(base: str, used: set[str]) -> str:
    """First of ``base``, ``base-2``, ``base-3``, … not yet in *used*.

    The chosen anchor is added to *used* so consecutive calls never collide.
    """
    if base not in used:
        used.add(base)
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    anchor = f"{base}-{n}"
    used.add(anchor)
    return anchor

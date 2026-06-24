"""Typed helpers for reading BeautifulSoup tag attributes.

BeautifulSoup's ``Tag.get`` returns ``str | AttributeValueList | None`` for
string attributes, which produces a large number of mypy errors under strict
mode. These small helpers coerce attribute values to the expected Python types
without changing runtime behaviour.
"""

from __future__ import annotations

from bs4.element import Tag  # type: ignore[import-untyped]


def attr_str(tag: Tag, name: str, default: str = "") -> str:
    """Return tag attribute *name* as a plain string.

    If the attribute is missing, a list, or otherwise non-string, return
    *default*.
    """
    value = tag.get(name, default)  # type: ignore[arg-type]
    if isinstance(value, str):
        return value
    return default


def attr_optional(tag: Tag, name: str) -> str | None:
    """Return tag attribute *name* as a string, or ``None`` if missing/non-string."""
    value = tag.get(name)
    if isinstance(value, str):
        return value
    return None


def classes(tag: Tag) -> list[str]:
    """Return the ``class`` attribute of *tag* as a list of strings.

    BeautifulSoup stores ``class`` as a list of tokens by default. This helper
    normalises that to ``list[str]`` regardless of the exact attribute type.
    """
    value = tag.get("class", [])  # type: ignore[arg-type]
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return value.split()
    return []

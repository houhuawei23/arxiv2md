"""Flat YAML front matter for the generated ``paper.md``.

Builds the ``--- … ---`` block that replaces the legacy human-readable
``# Title: …`` / ``- ArXiv: …`` bullet header at the top of the written
Markdown file. The data comes from the same ``paper.yml`` dict the write
layer serializes (:func:`arxiv2md_beta.output.metadata._metadata_to_paper_yml`),
so the file header and ``paper.yml`` can never drift.

The layout mirrors the paper-library format
(``title/arxiv/authors/publication/meta_status/workflow/urls/categories/sections``)
so a converted paper drops straight into the 20_Papers pipeline. The stdout
summary printed by the CLI stays human-readable — only the file changes.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

import yaml


def build_front_matter(paper: dict, *, sections: int, today: str | None = None) -> str:
    r"""Render a flat YAML front-matter block from a schema-v2 ``paper`` dict.

    Parameters
    ----------
    paper
        The ``paper`` object of a schema-v2 paper.yml (from
        :func:`arxiv2md_beta.output.metadata._metadata_to_paper_yml`).
    sections
        Number of sections in the converted document.
    today
        ``YYYY-MM-DD`` for ``meta_checked``; defaults to today (UTC).

    Returns:
    -------
    str
        ``---\\n<yaml>---`` (no trailing newline; callers join with blank
        lines). Empty string when *paper* carries no usable identity.
    """
    if not isinstance(paper, dict) or not (paper.get("title") or paper.get("identifiers")):
        return ""
    if today is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fm: OrderedDict[str, Any] = OrderedDict()
    fm["title"] = paper.get("title")

    ids = paper.get("identifiers") or {}
    if ids.get("arxiv"):
        fm["arxiv"] = ids["arxiv"]
    if ids.get("doi"):
        fm["doi"] = ids["doi"]

    authors = []
    for au in paper.get("authors") or []:
        if not isinstance(au, dict) or not au.get("name"):
            continue
        entry: OrderedDict[str, Any] = OrderedDict()
        entry["name"] = au["name"]
        if au.get("affiliations"):
            entry["affiliations"] = list(au["affiliations"])
        if au.get("orcid"):
            entry["orcid"] = au["orcid"]
        authors.append(entry)
    if authors:
        fm["authors"] = authors

    pub = paper.get("publication") or {}
    if pub:
        sub = OrderedDict()
        for key in ("type", "venue", "date_published", "year"):
            if pub.get(key) is not None:
                sub[key] = pub[key]
        if sub:
            fm["publication"] = sub

    # Library-management fields (20_Papers pipeline contract): a fresh
    # conversion starts as unreviewed.
    fm["meta_status"] = "pending"
    fm["meta_checked"] = today

    workflow = paper.get("workflow") or {}
    if workflow:
        fm["workflow"] = {k: workflow[k] for k in ("status", "priority", "date_added") if k in workflow}

    urls = paper.get("urls") or {}
    if urls:
        fm["urls"] = {k: urls[k] for k in ("pdf", "abstract") if k in urls}

    classification = paper.get("classification") or {}
    if classification.get("categories"):
        fm["categories"] = list(classification["categories"])

    fm["sections"] = sections

    dump = yaml.safe_dump(_plain(fm), allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{dump}---"


def _plain(value: Any) -> Any:
    """Recursively convert OrderedDict → dict (safe_dump can't represent the former)."""
    if isinstance(value, OrderedDict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value

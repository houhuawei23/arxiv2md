"""Enrich author records with abs-page HTML and OpenAlex (affiliations, ORCID)."""

from __future__ import annotations

import re
from typing import Any

import httpx
from loguru import logger

from arxiv2md_beta.network.arxiv_abs_html import parse_abs_page_for_authors
from arxiv2md_beta.network.openalex_api import arxiv_base_id, fetch_openalex_work_for_arxiv
from arxiv2md_beta.settings import get_settings


def _norm_name(s: str) -> str:
    return " ".join(s.lower().replace(".", " ").split())


def _names_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if _norm_name(a) == _norm_name(b):
        return True
    pa = a.split()
    pb = b.split()
    if not pa or not pb:
        return False
    if pa[-1].lower() == pb[-1].lower():
        return True
    # "Last, First" vs "First Last"
    if "," in a:
        last_a = a.split(",")[0].strip().lower()
        if last_a == pb[-1].lower():
            return True
    if "," in b:
        last_b = b.split(",")[0].strip().lower()
        if last_b == pa[-1].lower():
            return True
    return False


def _orcid_id(url_or_id: str | None) -> str | None:
    if not url_or_id:
        return None
    s = url_or_id.strip()
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[0-9X])", s, re.I)
    return m.group(1) if m else None


def _dedupe_affiliation_strings(parts: list[str]) -> list[str]:
    """Remove case-insensitive duplicates and shorter strings contained in a longer one."""
    if not parts:
        return []
    seen: set[str] = set()
    uniq: list[str] = []
    for p in parts:
        p = str(p).strip()
        if not p:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    kept: list[str] = []
    for p in uniq:
        pl = p.lower()
        redundant = False
        for q in uniq:
            if p is q:
                continue
            ql = q.lower()
            if pl in ql and len(q) > len(p):
                redundant = True
                break
        if not redundant:
            kept.append(p)
    return kept


def _merge_openalex_into_authors(
    authors: list[dict[str, Any]],
    work: dict[str, Any],
) -> int:
    """Mutate ``authors`` in place with institutions / ORCID from OpenAlex ``authorships``.

    Returns
    -------
    int
        Number of local authors matched to an OpenAlex authorship.
    """
    authorships = work.get("authorships") or []
    matched = 0
    for au in authors:
        name = (au.get("name") or "").strip()
        if not name:
            continue
        for ash in authorships:
            oa_name = ((ash.get("author") or {}).get("display_name") or "").strip()
            if not oa_name or not _names_match(name, oa_name):
                continue
            insts = ash.get("institutions") or []
            inst_names = [i.get("display_name") for i in insts if isinstance(i, dict) and i.get("display_name")]
            raw = ash.get("raw_affiliation_strings") or []
            if isinstance(raw, str):
                raw = [raw]
            aff_parts: list[str] = []
            for x in list(inst_names) + [r for r in raw if r]:
                if x:
                    aff_parts.append(str(x).strip())
            aff_parts = _dedupe_affiliation_strings(aff_parts)
            aff_joined = "; ".join(aff_parts[:5])
            if aff_parts and not au.get("affiliation"):
                au["affiliation"] = aff_joined
            elif aff_parts and au.get("affiliation"):
                if len(aff_joined) > len(str(au.get("affiliation", ""))):
                    au["affiliation"] = aff_joined
            if aff_parts:
                au["affiliations"] = aff_parts
            oid = _orcid_id((ash.get("author") or {}).get("orcid"))
            if oid and not au.get("orcid"):
                au["orcid"] = oid
            matched += 1
            break
    return matched


async def fetch_abs_html(base_id: str) -> str | None:
    """GET ``https://arxiv.org/abs/{base_id}`` (canonical abs page)."""
    s = get_settings()
    h = s.http
    url = f"https://arxiv.org/abs/{base_id}"
    timeout = httpx.Timeout(h.fetch_timeout_s)
    headers = {"User-Agent": h.user_agent}
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.text
    except Exception as e:
        logger.debug(f"abs HTML fetch failed for {base_id}: {e}")
        return None


def _apply_abs_hints_to_authors(
    authors: list[dict[str, Any]],
    abs_names: list[str],
    hints: list[str],
) -> None:
    """If abs lists the same author count, attach shared hint strings (rare)."""
    if not hints or not authors:
        return
    # Single global hint (one institution line for all) — common in meta tags
    if len(hints) == 1 and not any(a.get("affiliation") for a in authors):
        for au in authors:
            au.setdefault("affiliation", hints[0])
    # Per-name count match (very rare)
    if len(abs_names) == len(authors) == len(hints):
        for au, h in zip(authors, hints, strict=False):
            if not au.get("affiliation"):
                au["affiliation"] = h


async def enrich_authors_with_abs_html_and_openalex(
    metadata: dict[str, Any],
    arxiv_id: str,
) -> dict[str, Any]:
    """Augment ``metadata['authors']`` using abs HTML, then OpenAlex.

    Order: (1) ``arxiv.org/abs/{id}`` HTML — author link order + optional ``citation_author_institution`` /
    affiliation-class hints; (2) OpenAlex work resolved via DataCite DOI ``10.48550/arXiv.{base_id}``
    — institutions, raw affiliation lines, ORCID.

    Safe no-op if requests fail or no extra data is found.
    """
    base_id = arxiv_base_id(arxiv_id)
    authors = metadata.get("authors")
    if not isinstance(authors, list) or not authors:
        return metadata

    logger.info(
        "Author enrichment [{}]: pipeline — (1) arXiv Atom API authors already loaded; "
        "(2) abs HTML; (3) OpenAlex (DOI 10.48550/arXiv.{})",
        base_id,
        base_id,
    )

    # 1) abs page
    html = await fetch_abs_html(base_id)
    if html:
        abs_names, hints = parse_abs_page_for_authors(html)
        _apply_abs_hints_to_authors(authors, abs_names, hints)
        hint_note = ""
        if hints:
            h0 = hints[0]
            hint_note = f"; first hint: {h0[:160]!r}" + ("…" if len(h0) > 160 else "")
        logger.info(
            "Author enrichment [{}]: abs HTML — parsed {} author link(s), {} affiliation hint(s){}",
            base_id,
            len(abs_names),
            len(hints),
            hint_note,
        )
        # Fill missing names only if API gave empty strings (shouldn't happen)
        if abs_names and len(abs_names) == len(authors):
            for au, n in zip(authors, abs_names, strict=False):
                if not (au.get("name") or "").strip():
                    au["name"] = n
    else:
        logger.info(
            "Author enrichment [{}]: abs HTML — not available (network or error); skipping hints",
            base_id,
        )

    # 2) OpenAlex (primary source for institutions for most arXiv DOIs)
    work = await fetch_openalex_work_for_arxiv(base_id)
    if work:
        n_matched = _merge_openalex_into_authors(authors, work)
        wid = work.get("id")
        if wid and not metadata.get("openalex_work_id"):
            metadata["openalex_work_id"] = wid
        logger.info(
            "Author enrichment [{}]: OpenAlex — work {}, authorships={}, "
            "matched {} local author(s) to OpenAlex records (affiliations/ORCID filled where available)",
            base_id,
            wid or "(no id)",
            len(work.get("authorships") or []),
            n_matched,
        )
    else:
        logger.info(
            "Author enrichment [{}]: OpenAlex — no work found for DOI 10.48550/arXiv.{} (404 or fetch failed); "
            "affiliations rely on arXiv API + abs hints only",
            base_id,
            base_id,
        )

    return metadata

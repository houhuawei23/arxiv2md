"""Shared text-matching helpers for the three author/affiliation parsers.

The HTML (``html/parser.py``), TeX (``latex/author_affiliations.py``) and
OpenAlex (``network/author_enrichment.py``) author heuristics all need name
matching and affiliation dedup; the helpers used to live in
``network/author_enrichment`` and were imported across module boundaries —
sometimes as private names, dragging a latex→network dependency along.
"""

from __future__ import annotations


def norm_name(s: str) -> str:
    """Lowercase, dot-free, whitespace-normalized form of a personal name."""
    return " ".join(s.lower().replace(".", " ").split())


def names_match(a: str, b: str) -> bool:
    """Loose personal-name equality for merging author identities.

    Matches on the normalized full name or on the shared last name
    (tolerating "Last, First" vs "First Last" order).
    """
    if not a or not b:
        return False
    if norm_name(a) == norm_name(b):
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


def dedupe_strings(parts: list[str]) -> list[str]:
    """Remove case-insensitive duplicates, preserving order (verbatim entries)."""
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def dedupe_affiliation_strings(parts: list[str]) -> list[str]:
    """Dedupe affiliations, dropping entries subsumed by a longer one.

    Strips entries, drops case-insensitive duplicates and drops strings
    subsumed by a longer one. Deliberately stronger than
    :func:`dedupe_strings`: affiliation lists regularly contain both "MIT"
    and "Massachusetts Institute of Technology, Cambridge, MA", and the
    shorter form adds nothing.
    """
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


def find_matching_brace_end(text: str, open_brace_idx: int) -> int | None:
    """Index of the ``}`` closing the ``{`` at *open_brace_idx* (nesting-aware).

    Returns ``None`` when the index doesn't point at a ``{`` or the group is
    unterminated.
    """
    if open_brace_idx >= len(text) or text[open_brace_idx] != "{":
        return None
    depth = 0
    for i in range(open_brace_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return None

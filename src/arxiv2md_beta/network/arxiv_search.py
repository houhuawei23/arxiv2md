"""arXiv API title/phrase search used by the ``search`` command.

Batch-download post-mortems showed the single biggest failure mode is
converting the wrong paper from a mis-remembered arXiv ID (~15% error rate,
silent — the download "succeeds" with someone else's content). This wrapper
verifies a title phrase against the arXiv Atom API so IDs can be confirmed
before any conversion. Honors the shared rate limiter; arXiv officially asks
for at most ~1 request per 3 seconds (set
``ARXIV2MD_BETA_HTTP__MAX_REQUESTS_PER_SECOND=0.33`` for bulk searches).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from arxiv2md_beta.exceptions import UserInputError
from arxiv2md_beta.network.arxiv_api import parse_api_entries
from arxiv2md_beta.network.retry import request_with_retries
from arxiv2md_beta.settings import get_settings

_FIELD_PREFIX_RE = re.compile(r"\b(ti|au|abs|all|cat|id|co|jr):")
_VALID_FIELDS = ("ti", "all", "abs")
_VALID_SORTS = ("relevance", "submitted")
# External CLI syntax → arXiv API sortBy value. "submitted" is not a legal
# API value: passing it verbatim made the API silently fall back to
# relevance while the user believed results were time-ordered (audit5 G4-3).
_SORT_TO_API = {"relevance": "relevance", "submitted": "submittedDate"}


def build_search_query(query: str, authors: list[str] | None = None, *, field: str = "all") -> str:
    """Build an arXiv API ``search_query`` expression.

    - A query already using arXiv field syntax (``ti:...``, ``au:...``) is
      passed through untouched.
    - Otherwise the phrase is quoted inside the chosen field:
      ``ti:"fourier neural operator"``.
    - Each author surname is appended as ``AND au:"..."``.
    """
    if field not in _VALID_FIELDS:
        raise UserInputError(f"Invalid --field {field!r}; expected one of {', '.join(_VALID_FIELDS)}.")
    q = query.strip()
    if not q:
        raise UserInputError("Search query cannot be empty.")
    if _FIELD_PREFIX_RE.search(q):
        # Already field syntax: passthrough verbatim, quotes included.
        return q
    # Auto-quoted phrase: embedded quotes would terminate the phrase early
    # and let arbitrary field syntax through (audit4 P3); inside a quoted
    # phrase they carry no meaning, so drop them rather than interpolate raw.
    q = q.replace('"', "")
    expr = f'{field}:"{q}"'
    for author in authors or []:
        name = author.strip().replace('"', "")
        if name:
            expr += f' AND au:"{name}"'
    return expr


def build_search_url(
    query: str,
    *,
    authors: list[str] | None = None,
    field: str = "all",
    sort: str = "relevance",
    start: int = 0,
    max_results: int = 10,
) -> str:
    """Compose the full arXiv API search URL from user arguments."""
    if sort not in _VALID_SORTS:
        raise UserInputError(f"Invalid --sort {sort!r}; expected one of {', '.join(_VALID_SORTS)}.")
    if max_results < 1:
        raise UserInputError("--max-results must be >= 1.")
    if start < 0:
        raise UserInputError("--start must be >= 0.")
    expr = build_search_query(query, authors, field=field)
    template = get_settings().urls.arxiv_api_search_template
    return template.format(
        query=quote(expr),
        start=start,
        max_results=max_results,
        sort=_SORT_TO_API[sort],
    )


async def search_arxiv(
    query: str,
    *,
    authors: list[str] | None = None,
    field: str = "all",
    sort: str = "relevance",
    start: int = 0,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """Run the search and return parsed Atom entries (see parse_api_entries)."""
    url = build_search_url(
        query,
        authors=authors,
        field=field,
        sort=sort,
        start=start,
        max_results=max_results,
    )
    # request_with_retries already honors the shared rate limiter; 404 / None
    # means "no results" here, not an error.
    response = await request_with_retries(url)
    if response is None:
        return []
    return parse_api_entries(response.text)

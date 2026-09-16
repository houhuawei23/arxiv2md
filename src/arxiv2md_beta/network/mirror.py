"""arxiv.org → export.arxiv.org mirror fallback.

arxiv.org PDF/src endpoints occasionally 404 or rate-limit (429) while the
export mirror — a separate origin with its own rate limiting — still serves
the file. Both hosts support ``/pdf/``, ``/src/`` and ``/html/`` paths, so a
mirror retry is a single URL rewrite. Configuration lives under
``urls.arxiv_mirror_host`` (empty string disables the fallback) with the
trigger toggles in ``http.mirror_on_404`` / ``http.mirror_on_rate_limit``.
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from arxiv2md_beta.exceptions import NetworkError
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.settings.schema import AppSettings


def to_export_mirror(url: str, *, settings: AppSettings | None = None) -> str | None:
    """Rewrite an arxiv.org URL to the export mirror; None when not applicable.

    Returns None when the mirror is disabled, the URL points at a different
    host (ar5iv, crossref, …), or the URL is already on the mirror.
    """
    s = settings or get_settings()
    mirror_host = s.urls.arxiv_mirror_host
    if not mirror_host:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    primary_host = s.urls.arxiv_host
    if parsed.hostname != primary_host or parsed.hostname == mirror_host:
        return None
    return urlunparse(parsed._replace(netloc=mirror_host))


def mirror_worth_try(exc: BaseException, *, settings: AppSettings | None = None) -> bool:
    """True when the failure class is known to differ between the two hosts.

    - 404: the origins serve from different backends; one may have the file
      where the other 404s.
    - 429 (as the final status after retries): the mirrors rate-limit
      independently, so the mirror often still has headroom.
    """
    s = settings or get_settings()
    if not isinstance(exc, NetworkError):
        return False
    status = getattr(exc, "status_code", None)
    return (status == 404 and s.http.mirror_on_404) or (status == 429 and s.http.mirror_on_rate_limit)

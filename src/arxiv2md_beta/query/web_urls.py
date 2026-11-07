"""Web article URL detection and parsing (WeChat official accounts + generic blog pages).

The convert router treats two kinds of remote non-arXiv URLs as "web article"
sources: ``mp.weixin.qq.com`` article links and any other ``http(s)`` page.
Detection is a pure function so the router stays cheap and the classification
is unit-testable without network access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urldefrag

from arxiv2md_beta.exceptions import UserInputError

WEB_WECHAT = "wechat"
WEB_PAGE = "webpage"

_KINDS = (WEB_WECHAT, WEB_PAGE)


@dataclass(frozen=True)
class WebQuery:
    """Parsed web-article input (the web analogue of LocalHtmlQuery)."""

    url: str
    kind: str  # WEB_WECHAT or WEB_PAGE


def detect_web_source(input_text: str) -> str | None:
    """Classify *input_text* as a web article kind, or None when it is not one.

    Only absolute ``http(s)`` URLs are candidates. arXiv links are excluded so
    the existing arXiv specs keep priority; anything else remote (WeChat
    ``mp.weixin.qq.com`` article links first, then generic pages) becomes a
    web-article source. Local paths / bare IDs return None and are handled by
    the pre-existing routing logic.
    """
    text = input_text.strip()
    if not text.startswith(("http://", "https://")):
        return None
    host = _host_of(text)
    if host is None:
        return None
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        return None
    if host == "mp.weixin.qq.com" or host.endswith(".mp.weixin.qq.com"):
        return WEB_WECHAT
    return WEB_PAGE


def parse_web_input(input_text: str, kind: str | None = None) -> WebQuery:
    """Parse and validate a web-article URL into a :class:`WebQuery`.

    ``kind`` may be precomputed by the router (``detect_web_source``); when
    omitted it is derived here.
    """
    text = input_text.strip()
    detected = detect_web_source(text)
    if detected is None:
        raise UserInputError(f"INPUT is not a supported web article URL: {text[:120]}")
    if kind is not None and kind != detected:
        raise UserInputError(f"INPUT looks like a {detected} URL, not {kind}")
    return WebQuery(url=text, kind=detected)


def identity_for_web_url(url: str) -> str:
    """Canonical identity written to ``.arxiv2md-paper`` for web inputs.

    Fragments (#anchors) never change content and trailing slashes are
    normalized so the idempotency pre-check treats them as the same article.
    Query strings are kept — WeChat short links carry the whole article id in
    the path, but generic blogs may paginate via query params.
    """
    without_fragment = urldefrag(url.strip())[0]
    return without_fragment[:-1] if without_fragment.endswith("/") else without_fragment


def slugify_web_title(title: str) -> str:
    """Filesystem-safe directory name for a web article title.

    Keeps CJK characters (Chinese titles stay readable), replaces path-hostile
    punctuation and whitespace runs with single dashes, and truncates to 60
    characters so output dirs stay manageable.
    """
    s = re.sub(r"[\\/:*?\"<>|#\[\]\s：？]+", "-", title.strip())
    s = re.sub(r"-{2,}", "-", s).strip("-.")
    return (s or "web-article")[:60]


def _host_of(url: str) -> str | None:
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return None
    return host.lower() or None


__all__ = [
    "WEB_PAGE",
    "WEB_WECHAT",
    "WebQuery",
    "detect_web_source",
    "identity_for_web_url",
    "parse_web_input",
    "slugify_web_title",
]

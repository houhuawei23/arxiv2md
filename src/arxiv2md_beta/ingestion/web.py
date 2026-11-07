"""Web article ingestion: WeChat official account articles + generic blog pages.

Blog-style output contract (unlike the paper-oriented ingestion paths, this
family intentionally does **not** run the paper persist tail — no paper.yml,
no sidecars, no PDF):

    <base_output_dir>/<slug-of-title>/
    ├── index.md           # YAML front matter + article body
    └── images/            # localized article images, md rewritten to them

Shared infrastructure that IS reused: the HTTP client pool / rate limiter,
the ``.arxiv2md-paper`` claim marker (idempotency), and settings.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from arxiv2md_beta.exceptions import IngestionError, NetworkError, NonRetryableNetworkError
from arxiv2md_beta.network.http import acquire_rate_slot, get_http_client, http_request_slot
from arxiv2md_beta.output.layout import _claim_paper_output_dir
from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.query.web_urls import (
    WEB_WECHAT,
    WebQuery,
    identity_for_web_url,
    slugify_web_title,
)
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()

# WeChat rejects non-browser UAs on some edges; mp.weixin.qq.com article pages
# are public (no login), a desktop Chrome UA + empty Referer is enough.
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_DOWNLOAD_CONCURRENCY = 8

_EXT_BY_WX_FMT = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif", "webp": "webp", "svg": "svg"}
_MAGIC_EXTS = ((b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"), (b"RIFF", "webp"), (b"<svg", "svg"))


@dataclass
class WebArticle:
    """Extracted article before image localization."""

    title: str
    markdown: str  # body with original (remote/absolute) image URLs
    account: str = ""
    author: str = ""
    date: str = ""  # YYYY-MM-DD when known
    source_url: str = ""
    warnings: list[str] = field(default_factory=list)


# ── Entry point ──────────────────────────────────────────────────────


async def ingest_web_article(params: ConvertParams, query: WebQuery, base_output_dir: Path) -> Path:
    """Fetch, extract, localize images and write blog-style output for *query*.

    Idempotency / --force / --dry-run are handled by the caller (mirrors
    ``cli.runner.convert._process_with``); this function always converts.
    """
    html = await _fetch_page(query)
    article = await asyncio.to_thread(_extract_article, html, query)

    if not article.markdown.strip():
        raise IngestionError(f"No extractable content found on {query.url}")

    # Claim via the same marker machinery as paper outputs → idempotency,
    # collision suffixes and find_completed_output_dir all work unchanged.
    article_dir = _claim_paper_output_dir(
        base_output_dir, slugify_web_title(article.title), identity_for_web_url(query.url)
    )
    images_dir = article_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    article.markdown = await _localize_images(article.markdown, images_dir, base_url=query.url)
    await asyncio.to_thread(_write_output, article_dir, article)

    logger.info(f"[web] {article.title!r} → {article_dir}")
    return article_dir


# ── Fetch ────────────────────────────────────────────────────────────


async def _fetch_page(query: WebQuery) -> str:
    await acquire_rate_slot()
    try:
        async with http_request_slot():
            headers = (
                {"User-Agent": _CHROME_UA, "Accept-Language": "zh-CN,zh;q=0.9"} if query.kind == WEB_WECHAT else None
            )
            resp = await get_http_client().get(query.url, headers=headers)
    except Exception as exc:  # httpx errors share no useful common base here
        raise NetworkError(f"Failed to fetch {query.url}: {exc}") from exc
    if resp.status_code == 404:
        raise NonRetryableNetworkError(f"Page not found: {query.url}", status_code=404)
    if resp.status_code >= 400:
        raise NetworkError(f"HTTP {resp.status_code} fetching {query.url}", status_code=resp.status_code)
    return resp.text


# ── Extraction ───────────────────────────────────────────────────────


def _extract_article(html: str, query: WebQuery) -> WebArticle:
    if query.kind == WEB_WECHAT:
        return _extract_wechat(html, query.url)
    return _extract_generic_page(html, query.url)


def _extract_wechat(html: str, url: str) -> WebArticle:
    soup = BeautifulSoup(html, "lxml")

    title = _text_of(soup, "#activity-name") or _meta_of(soup, "og:title") or _title_tag(soup) or url
    account = _text_of(soup, "#js_name").strip() if _text_of(soup, "#js_name") else ""
    author = _text_of(soup, "#js_author_name") or ""
    date = _wechat_date(html)

    root = soup.select_one("#js_content")
    if root is None:
        raise IngestionError(
            f"WeChat article body (#js_content) not found on {url} — "
            "the link may point at a non-article page or an anti-crawler verification page"
        )
    for tag in root.find_all(["script", "style", "link", "svg"]):
        tag.decompose()
    for img in root.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        img.attrs = {"src": _absolutize(src, url)}
    for pre in root.find_all("pre"):  # WeChat code-snippet blocks → fenced code
        lang = pre.get("data-lang") or ""
        code = BeautifulSoup((pre.find("code") or pre).get_text(), "lxml").get_text()
        fresh = soup.new_tag("pre")
        if lang:
            fresh["data-lang"] = lang
        inner = soup.new_tag("code")
        inner.string = code
        fresh.append(inner)
        pre.replace_with(fresh)

    markdown = _soup_to_markdown(root)
    return WebArticle(title=title, markdown=markdown, account=account, author=author, date=date, source_url=url)


def _extract_generic_page(html: str, url: str) -> WebArticle:
    warnings: list[str] = []
    soup = BeautifulSoup(html, "lxml")
    page_meta = _page_meta(soup)  # title/date candidates from <head>, read before pruning

    pruned = _prune_chrome(soup)
    root = pruned.body or pruned
    _absolutize_page_images(root, url)

    # Tier 1: trafilatura main-content extraction on the pruned DOM (drops
    # nav/ads/infoboxes/edit-section chrome before identification).
    markdown, meta = _trafilatura_extract(str(pruned), url)
    if not markdown or not markdown.strip() or _has_mega_paragraph(markdown) or _dropped_content_images(markdown, root):
        # Tier 2: whole-page markdownify on the same pruned DOM. A
        # mega-paragraph (e.g. a references <table> flattened into one 10k-char
        # line) counts as extraction failure — structure was destroyed. A page
        # with content images that trafilatura dropped also degrades: layout
        # fidelity beats boilerplate stripping at that point.
        if markdown.strip():
            reason = "mega-paragraph" if _has_mega_paragraph(markdown) else "content images dropped"
            warnings.append(f"trafilatura output degenerated ({reason}); fell back to whole-page markdownify")
        else:
            warnings.append("trafilatura extraction empty; fell back to whole-page markdownify")
        markdown = _soup_to_markdown(root)
        meta = {}

    title = page_meta["title"] or (meta.get("title") or "").strip() or _title_tag(BeautifulSoup(html, "lxml")) or url
    author = (meta.get("author") or "").strip()
    date = page_meta["date"]
    return WebArticle(title=title, markdown=markdown, author=author, date=date, source_url=url, warnings=warnings)


# Selectors removed before extraction/whole-page conversion: site chrome that
# pollutes article Markdown (MediaWiki edit links, navboxes, banners, CMS
# boilerplate). Deliberately NOT pruned: references/reflists are content.
_PRUNE_SELECTORS = [
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "form",
    "nav",
    "aside",
    "header",
    "footer",
    '[role="navigation"]',
    ".navbox",
    ".vertical-navbox",
    ".sidebar",
    ".infobox",
    ".mw-editsection",
    ".mw-jump-link",
    ".noprint",
    ".ambox",
    ".catlinks",
    ".printfooter",
    "#siteSub",
    "#toc",
    ".toc",
]


def _prune_chrome(soup: BeautifulSoup) -> BeautifulSoup:
    """Decompose site-chrome nodes listed in ``_PRUNE_SELECTORS`` (in place)."""
    # Lazy-load pattern: <noscript><img …></noscript> holds the real figure —
    # unwrap its imgs before dropping the noscript itself.
    for noscript in soup.find_all("noscript"):
        for img in noscript.find_all("img"):
            noscript.insert_before(img.extract())
    for selector in _PRUNE_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
    return soup


def _page_meta(soup: BeautifulSoup) -> dict:
    """Head metadata: title/date candidates.

    Dates are meta-only — trafilatura text-scanned dates are citation noise
    (e.g. reference years).
    """
    site_name = _meta_content(soup.select_one("meta[property='og:site_name']"))
    title = _meta_content(soup.select_one("meta[property='og:title']"))
    h1 = soup.select_one("h1")
    h1_text = h1.get_text(strip=True) if isinstance(h1, Tag) else ""
    # Suffixed variants are always longer than the clean title; shortest wins.
    candidates = [c for c in (title, h1_text) if c]
    title = min(candidates, key=len) if candidates else ""
    if site_name and title:
        for sep in (" - ", " – ", " — ", " | "):
            suffix = f"{sep}{site_name}"
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
                break
    date = ""
    for attr, prefer_modified in (
        ("article:modified_time", True),
        ("dcterms.modified", True),
        ("og:updated_time", True),
        ("article:published_time", False),
        ("dcterms.created", False),
    ):
        node = soup.select_one(f"meta[property='{attr}']") or soup.select_one(f"meta[name='{attr}']")
        if node:
            norm = _normalize_date(_meta_content(node))
            if norm:
                date = norm
                if prefer_modified:
                    break
    return {"title": title, "date": date, "site_name": site_name}


def _absolutize_page_images(root, base_url: str) -> None:
    """Reduce every img to absolute src (+alt) so extraction/localization see them."""
    for img in root.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        attrs = {"src": _absolutize(src, base_url)}
        alt = img.get("alt")
        if alt:
            attrs["alt"] = alt
        img.attrs = attrs


def _is_content_image(src: str, *, width: str = "", height: str = "") -> bool:
    """Heuristic: figures worth keeping (skip icons/spacers)."""
    if "/thumb/" in src or "upload.wikimedia.org" in src:
        return True
    for raw in (width, height):
        digits = re.sub(r"[^0-9]", "", str(raw))
        if digits and int(digits) >= 150:
            return True
    return False


def _dropped_content_images(markdown: str, root) -> bool:
    """True when the DOM has content figures but the extraction kept none."""
    has_content_img = any(
        _is_content_image(img.get("src") or "", width=str(img.get("width") or ""), height=str(img.get("height") or ""))
        for img in root.find_all("img")
    )
    if not has_content_img:
        return False
    return not re.search(r"!\[[^\]]*\]\([^)]+\)", markdown)


def _trafilatura_extract(html: str, url: str) -> tuple[str, dict]:
    """Trafilatura → markdown, with metadata; returns ("", {}) when unavailable."""
    try:
        import trafilatura
    except ImportError:  # pragma: no cover - hard dependency, defensive only
        return "", {}

    meta = trafilatura.bare_extraction(
        html, url=url, include_tables=True, include_images=True, include_links=True, with_metadata=True
    )
    if not meta:
        return "", {}
    meta_dict = meta.as_dict() if hasattr(meta, "as_dict") else dict(meta)
    markdown = (
        trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_images=True,
            include_links=True,
        )
        or ""
    )

    # Resolve relative image srcs against the page URL.
    def _abs_img(m: re.Match[str]) -> str:
        src = m.group(2).strip()
        return m.group(1) + (src if src.startswith("http") else _absolutize(src, url)) + m.group(3)

    markdown = re.sub(r"(!\[[^\]]*\]\()([^)]+)(\))", _abs_img, markdown)
    return markdown, meta_dict


def _has_mega_paragraph(markdown: str, *, max_line_chars: int = 5000) -> bool:
    """True when any single line is absurdly long — structure was flattened."""
    return any(len(line) > max_line_chars for line in markdown.splitlines())


# ── Image localization ───────────────────────────────────────────────


async def _localize_images(markdown: str, images_dir: Path, *, base_url: str) -> str:
    """Download http(s) images referenced in *markdown* into *images_dir*.

    Rewrites every successfully downloaded reference to a relative
    ``images/<name>`` path; failed downloads keep their remote URL (with a
    warning) so the document never loses the source.
    """
    pattern = re.compile(r"(!\[[^\]]*\]\()(https?://[^)\s]+)(\))")
    urls: list[str] = []
    for m in pattern.finditer(markdown):
        u = m.group(2)
        if u not in urls:
            urls.append(u)
    # Raw-HTML tables keep <img src="..."> tags — localize those too.
    img_tag_re = re.compile(r'(<img[^>]+?src=")(https?://[^"]+)(")')
    for m in img_tag_re.finditer(markdown):
        u = m.group(2)
        if u not in urls:
            urls.append(u)

    client = get_http_client()
    sem = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)

    async def grab(u: str) -> tuple[str, bytes | None, str]:
        async with sem:
            try:
                resp = await client.get(u)
                resp.raise_for_status()
                return u, resp.content, _guess_ext(u, resp)
            except Exception as exc:
                logger.warning(f"[web] image download failed, keeping remote URL: {u} ({exc})")
                return u, None, ""

    results = await asyncio.gather(*(grab(u) for u in urls))
    for u, data, ext in results:
        if not data:
            continue
        name = f"img_{hashlib.md5(u.encode()).hexdigest()[:8]}.{ext}"
        await asyncio.to_thread((images_dir / name).write_bytes, data)
        markdown = markdown.replace(f"]({u})", f"](images/{name})")
        markdown = markdown.replace(f'src="{u}"', f'src="images/{name}"')
    return markdown


def _guess_ext(url: str, resp) -> str:
    fmt_list = parse_qs(urlparse(url).query).get("wx_fmt")
    fmt = fmt_list[0].lower() if fmt_list else None
    if fmt in _EXT_BY_WX_FMT:
        return _EXT_BY_WX_FMT[fmt]
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    by_type = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/svg+xml": "svg",
    }
    if ctype in by_type:
        return by_type[ctype]
    for magic, ext in _MAGIC_EXTS:
        if resp.content.startswith(magic):
            return ext
    return "jpg"


# ── Output ───────────────────────────────────────────────────────────


def _write_output(article_dir: Path, article: WebArticle) -> None:
    fm = ["---", f'title: "{_yaml_escape(article.title)}"']
    if article.account:
        fm.append(f'account: "{_yaml_escape(article.account)}"')
    if article.author:
        fm.append(f'author: "{_yaml_escape(article.author)}"')
    if article.date:
        fm.append(f"date: {article.date}")
    fm += [f"source: {article.source_url}", "---", ""]
    (article_dir / "index.md").write_text("\n".join(fm) + article.markdown, encoding="utf-8")
    for w in article.warnings:
        logger.warning(f"[web] {w}")


# ── Small helpers ────────────────────────────────────────────────────


def _soup_to_markdown(root) -> str:
    from markdownify import MarkdownConverter

    # markdownify flattens <table> into inline text (mega-paragraphs); keep
    # tables as raw HTML instead — every renderer displays them correctly.
    tables: list[str] = []
    for i, table in enumerate(root.find_all("table")):
        placeholder = BeautifulSoup(f"<p>RAW-TABLE-PLACEHOLDER-{i}</p>", "lxml").p
        assert placeholder is not None
        tables.append(table.prettify())
        table.replace_with(placeholder)

    def code_language_callback(el) -> str:
        """Per-<pre> fence language: code's ``language-*`` class or pre's ``data-lang``."""
        lang = el.get("data-lang") or ""
        code = el.find("code")
        if isinstance(code, Tag):
            classes = code.get("class") or []
            if isinstance(classes, str):
                classes = classes.split()
            for cls in classes:
                if cls.startswith("language-"):
                    return cls[len("language-") :]
        return lang if isinstance(lang, str) else ""

    md = MarkdownConverter(
        heading_style="ATX", strip=["span"], code_language_callback=code_language_callback
    ).convert_soup(root)
    for i, raw_table in enumerate(tables):
        md = md.replace(f"RAW-TABLE-PLACEHOLDER-{i}", raw_table)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def _absolutize(src: str, base_url: str) -> str:
    return urljoin(base_url, src.strip()) if src.strip() else src


def _text_of(soup: BeautifulSoup, selector: str) -> str:
    node = soup.select_one(selector)
    return node.get_text(strip=True) if isinstance(node, Tag) else ""


def _meta_of(soup: BeautifulSoup, prop: str) -> str:
    node = soup.find("meta", property=prop)
    if not isinstance(node, Tag):
        return ""
    return _meta_content(node)


def _meta_content(node) -> str:
    """Strip a meta tag's content attr (Tag.get may return str | list[str])."""
    if not isinstance(node, Tag):
        return ""
    content = node.get("content")
    return (content or "").strip() if isinstance(content, str) else ""


def _title_tag(soup: BeautifulSoup) -> str:
    return soup.title.get_text(strip=True) if soup.title else ""


def _wechat_date(html: str) -> str:
    m = re.search(r"var ?create_time ?= ?[\"'](\d+)[\"']?", html) or re.search(
        r"createTime[^>]*time\s*=\s*[\"']?(\d+)", html
    )
    if not m:
        return ""
    return datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d")


def _normalize_date(raw: str) -> str:
    raw = raw.strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    return m.group(0) if m else ""


def _yaml_escape(value: str) -> str:
    return value.replace('"', "'")

"""Web article ingestion (WeChat + generic blog pages): routing, extraction, output.

All tests are offline — network fetches and image downloads are mocked at the
HTTP client boundary; extraction and layout are exercised on fixture HTML.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from arxiv2md_beta.cli.runner.convert import run_convert_flow
from arxiv2md_beta.exceptions import IngestionError, UserInputError
from arxiv2md_beta.ingestion.web import (
    _extract_article,
    _guess_ext,
    _localize_images,
    ingest_web_article,
)
from arxiv2md_beta.output.layout import find_completed_output_dir
from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.query.web_urls import (
    WEB_PAGE,
    WEB_WECHAT,
    detect_web_source,
    identity_for_web_url,
    parse_web_input,
    slugify_web_title,
)

WECHAT_HTML = """
<html><head><title>cached-title</title></head><body>
<h1 id="activity-name">Jeff Dean李飞飞对谈：RSI</h1>
<a id="js_name">量子位</a>
<span id="js_author_name">听雨</span>
<script>var create_time = "1794042000";</script>
<div id="js_content">
  <p>正文段落，含<img src="" data-src="https://mmbiz.qpic.cn/mmbiz_png/abc?wx_fmt=png">图片。</p>
  <pre class="code-snippet__js" data-lang="python"><code><span>def f():</span>
    <span>pass</span></code></pre>
  <p><span>span 应被剥掉但文本保留</span></p>
</div>
</body></html>
"""

BLOG_HTML = """
<html><head><title>Juergen Schmidhuber</title>
<meta property="og:title" content="Recursive Self Improvement">
</head><body>
<nav>Home | Papers | Press</nav>
<article><h1>Recursive Self Improvement</h1>
<p>RSI discussion with <a href="https://example.com">a link</a>.</p>
<table><tr><td>a</td><td>b</td></tr></table>
<img src="/img/logo.png">
</article>
<footer>copyright</footer>
</body></html>
"""


@dataclass
class FakeResp:
    content: bytes
    status_code: int = 200
    headers: dict | None = None

    def __post_init__(self) -> None:
        self.headers = self.headers or {"content-type": "image/png"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@dataclass
class FakeClient:
    responses: dict

    async def get(self, url: str, headers: dict | None = None) -> FakeResp:
        return self.responses[url]


def _params(**overrides: object) -> ConvertParams:
    defaults: dict[str, object] = {
        "input_text": "https://mp.weixin.qq.com/s/abc",
        "parser": "html",
        "output": None,
        "source": "Arxiv",
        "short": None,
        "no_images": False,
        "remove_refs": False,
        "remove_inline_citations": False,
        "section_filter_mode": "include",
        "sections": None,
        "section": None,
        "include_tree": False,
        "dry_run": False,
    }
    defaults.update(overrides)
    return ConvertParams(**defaults)  # type: ignore[arg-type]


# ── Detection / parsing / identity / slug ────────────────────────────


def test_detect_wechat_url() -> None:
    assert detect_web_source("https://mp.weixin.qq.com/s/o-fWqG46Tw_4BgZKoPxf9w") == WEB_WECHAT


def test_detect_generic_webpage() -> None:
    assert detect_web_source("https://people.idsia.ch/~juergen/recursive-self-improvement.html") == WEB_PAGE
    assert detect_web_source("http://blog.example.com/post/1") == WEB_PAGE


def test_detect_excludes_arxiv_and_non_urls() -> None:
    assert detect_web_source("https://arxiv.org/abs/2401.12345") is None
    assert detect_web_source("2401.12345") is None
    assert detect_web_source("/home/user/paper.html") is None
    assert detect_web_source("ftp://example.com/x") is None


def test_parse_web_input_rejects_non_web() -> None:
    with pytest.raises(UserInputError):
        parse_web_input("2401.12345")


def test_identity_strips_fragment_and_trailing_slash() -> None:
    assert identity_for_web_url("https://example.com/post/one/#section") == "https://example.com/post/one"
    assert identity_for_web_url("https://example.com/post/one/") == "https://example.com/post/one"


def test_slugify_keeps_cjk_and_strips_hostile_chars() -> None:
    assert slugify_web_title("Jeff Dean李飞飞最新对谈：全是RSI非共识") == "Jeff-Dean李飞飞最新对谈-全是RSI非共识"
    assert slugify_web_title("a/b\\c:d*e?f") == "a-b-c-d-e-f"
    assert slugify_web_title("   ") == "web-article"


# ── WeChat extraction ────────────────────────────────────────────────


def test_extract_wechat_metadata_and_markdown() -> None:
    query = parse_web_input("https://mp.weixin.qq.com/s/abc")
    article = _extract_article(WECHAT_HTML, query)
    assert article.title == "Jeff Dean李飞飞对谈：RSI"
    assert article.account == "量子位"
    assert article.author == "听雨"
    assert article.date  # unix create_time parsed
    assert "![](https://mmbiz.qpic.cn/mmbiz_png/abc?wx_fmt=png)" in article.markdown
    assert "```python" in article.markdown  # code-snippet → fenced
    assert "def f():" in article.markdown
    assert "span 应被剥掉但文本保留" in article.markdown
    assert "<span>" not in article.markdown


def test_extract_wechat_missing_js_content_raises() -> None:
    html = "<html><body><div id='other'>verify page</div></body></html>"
    with pytest.raises(IngestionError, match="js_content"):
        _extract_article(html, parse_web_input("https://mp.weixin.qq.com/s/abc"))


# ── Generic page extraction ──────────────────────────────────────────


def test_extract_generic_trafilatura_primary() -> None:
    query = parse_web_input("https://people.idsia.ch/~juergen/recursive-self-improvement.html")
    article = _extract_article(BLOG_HTML, query)
    assert article.title == "Recursive Self Improvement"
    assert "RSI discussion" in article.markdown
    assert not article.warnings  # primary tier, no fallback


def test_trafilatura_relative_images_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trafilatura output goes through our relative→absolute URL resolution."""
    import trafilatura as tf

    import arxiv2md_beta.ingestion.web as web

    def fake_extract(html, **kw):  # noqa: ANN001, ARG001 - test stub
        return "body text\n\n![logo](/img/logo.png)"

    def fake_bare(html, **kw):  # noqa: ANN001, ARG001 - test stub
        return {"title": "T", "author": "", "date": ""}

    monkeypatch.setattr(tf, "extract", fake_extract)
    monkeypatch.setattr(tf, "bare_extraction", lambda *a, **k: fake_bare(*a, **k))
    md, meta = web._trafilatura_extract("<html/>", "https://example.com/post/one")
    assert "![logo](https://example.com/img/logo.png)" in md
    assert meta["title"] == "T"


def test_extract_generic_fallback_when_trafilatura_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import arxiv2md_beta.ingestion.web as web

    monkeypatch.setattr(web, "_trafilatura_extract", lambda html, url: ("", {}))
    query = parse_web_input("https://example.com/post")
    article = _extract_article(BLOG_HTML, query)
    assert article.warnings  # fallback recorded
    assert "RSI discussion" in article.markdown
    assert "Home | Papers" not in article.markdown  # nav pruned


def test_extract_generic_fallback_on_mega_paragraph(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flattened references table (one huge line) triggers tier-2 fallback."""
    import arxiv2md_beta.ingestion.web as web

    mega = "x" * 8000 + " [ref1](https://a) [ref2](https://b)"
    monkeypatch.setattr(web, "_trafilatura_extract", lambda html, url: (mega, {"title": "T"}))
    query = parse_web_input("https://example.com/post")
    article = _extract_article(BLOG_HTML, query)
    assert any("mega-paragraph" in w for w in article.warnings)
    assert "RSI discussion" in article.markdown  # whole-page output replaces it


def test_extract_prunes_wiki_chrome() -> None:
    """mw-editsection / navbox / sidebar chrome must not leak into markdown."""
    wiki_html = """
    <html><head><title>RSI - Wikipedia</title></head><body>
    <div class="navbox">Other RSI pages</div>
    <aside class="sidebar">series on AI</aside>
    <div id="content"><h1>Recursive self-improvement</h1>
    <span class="mw-editsection">[edit]</span>
    <p>RSI is a process in which an AI system iteratively improves itself.</p>
    </div>
    <div class="catlinks">Categories: AI</div>
    </body></html>
    """
    article = _extract_article(wiki_html, parse_web_input("https://en.wikipedia.org/wiki/RSI"))
    assert "[edit]" not in article.markdown
    assert "Other RSI pages" not in article.markdown
    assert "series on AI" not in article.markdown
    assert "Categories: AI" not in article.markdown
    assert "iteratively improves" in article.markdown


def test_extract_title_from_h1_and_site_name_suffix_strip() -> None:
    html = """
    <html><head><title>Recursive self-improvement - Wikipedia</title></head><body>
    <h1>Recursive self-improvement</h1><p>Body text about RSI.</p>
    </body></html>
    """
    article = _extract_article(html, parse_web_input("https://en.wikipedia.org/wiki/RSI"))
    assert article.title == "Recursive self-improvement"  # h1 beats <title>, no suffix


def test_extract_title_strips_site_name_suffix() -> None:
    html = """
    <html><head><meta property="og:site_name" content="Wikipedia">
    <title>Recursive self-improvement - Wikipedia</title></head><body>
    <p>Body text about RSI.</p></body></html>
    """
    article = _extract_article(html, parse_web_input("https://en.wikipedia.org/wiki/RSI"))
    assert article.title == "Recursive self-improvement"


def test_extract_title_prefers_shortest_candidate() -> None:
    """og:title often carries a site suffix; clean h1 is the shortest candidate."""
    html = """
    <html><head><meta property="og:title" content="Recursive self-improvement - Wikipedia">
    <title>Recursive self-improvement - Wikipedia</title></head><body>
    <h1>Recursive self-improvement</h1><p>Body text about RSI.</p></body></html>
    """
    article = _extract_article(html, parse_web_input("https://en.wikipedia.org/wiki/RSI"))
    assert article.title == "Recursive self-improvement"


def test_prune_unwraps_noscript_lazy_images() -> None:
    html = """
    <html><body><article>
    <p>RSI body text.</p>
    <noscript><img src="https://example.com/lazy-fig.png" alt="fig" width="300"></noscript>
    </article></body></html>
    """
    article = _extract_article(html, parse_web_input("https://example.com/post"))
    assert "![fig](https://example.com/lazy-fig.png)" in article.markdown


def test_extract_date_from_meta_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dates come from head metas; trafilatura text-scanned dates are ignored."""
    import arxiv2md_beta.ingestion.web as web

    html = """
    <html><head><meta property="article:modified_time" content="2026-05-01T10:00:00Z"></head>
    <body><h1>T</h1><p>Cited paper from 2004-11-14 here.</p></body></html>
    """
    monkeypatch.setattr(web, "_trafilatura_extract", lambda html, url: ("body", {"date": "2004-11-14"}))
    article = _extract_article(html, parse_web_input("https://example.com/post"))
    assert article.date == "2026-05-01"  # meta wins, citation noise ignored


def test_extract_falls_back_when_content_images_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trafilatura keeping zero content images while DOM has them → tier-2."""
    import arxiv2md_beta.ingestion.web as web

    html = """
    <html><head><title>T</title></head><body>
    <h1>T</h1>
    <figure><img src="https://upload.wikimedia.org/w/200px-fig.png" width="200" alt="fig"></figure>
    <p>Body text about RSI.</p>
    </body></html>
    """
    monkeypatch.setattr(web, "_trafilatura_extract", lambda html, url: ("Body text about RSI.", {}))
    article = _extract_article(html, parse_web_input("https://example.com/post"))
    assert any("content images dropped" in w for w in article.warnings)
    assert "![fig](https://upload.wikimedia.org/w/200px-fig.png)" in article.markdown


def test_content_image_heuristic() -> None:
    from arxiv2md_beta.ingestion.web import _is_content_image

    assert _is_content_image("https://upload.wikimedia.org/a/200px-x.png")
    assert _is_content_image("https://x/img.png", width="300")
    assert _is_content_image("https://x/img.png", height="150px")
    assert not _is_content_image("https://x/icon.png", width="32")
    assert not _is_content_image("https://x/pic.png")  # no size signal, non-wikimedia


# ── Image localization ───────────────────────────────────────────────


def test_localize_images_downloads_and_rewrites(tmp_path: Path) -> None:

    png = b"\x89PNG fake"
    jpg = b"\xff\xd8\xff fake"
    fake = FakeClient(
        {
            "https://mmbiz.qpic.cn/a?wx_fmt=png": FakeResp(png),
            "https://example.com/pic": FakeResp(jpg, headers={"content-type": "image/jpeg"}),
            "https://example.com/broken": FakeResp(b"", status_code=404),
        }
    )
    monkeypatch_client(fake)
    md = (
        "前![one](https://mmbiz.qpic.cn/a?wx_fmt=png)中"
        "![two](https://example.com/pic)后"
        "![kept](https://example.com/broken)"
    )
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True)
    out = asyncio.run(_localize_images(md, images_dir, base_url="https://example.com/"))
    files = sorted(p.name for p in images_dir.iterdir())
    assert len(files) == 2
    assert all(name.startswith("img_") for name in files)
    assert "](images/" in out
    assert out.count("](images/") == 2  # broken one keeps remote URL
    assert "https://example.com/broken" in out


def test_guess_ext_precedence() -> None:
    resp = FakeResp(b"\x89PNG", headers={"content-type": "image/gif"})
    assert _guess_ext("https://mmbiz.qpic.cn/x?wx_fmt=png", resp) == "png"  # wx_fmt wins on mmbiz
    plain = FakeResp(b"\x89PNG", headers={"content-type": "image/gif"})
    assert _guess_ext("https://example.com/x.bin", plain) == "gif"  # content-type beats magic
    magic_only = FakeResp(b"\x89PNG", headers={})
    assert _guess_ext("https://example.com/x.bin", magic_only) == "png"  # magic fallback


# ── End-to-end (mocked network) ──────────────────────────────────────


def test_ingest_wechat_article_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import arxiv2md_beta.ingestion.web as web

    monkeypatch.setattr(web, "_fetch_page", _fake_fetch(WECHAT_HTML))
    monkeypatch_client(FakeClient({"https://mmbiz.qpic.cn/mmbiz_png/abc?wx_fmt=png": FakeResp(b"\x89PNG fake")}))
    query = parse_web_input("https://mp.weixin.qq.com/s/abc")
    article_dir = asyncio.run(ingest_web_article(_params(), query, tmp_path))

    index = (article_dir / "index.md").read_text(encoding="utf-8")
    assert index.startswith("---\n")
    assert 'title: "Jeff Dean李飞飞对谈：RSI"' in index
    assert 'account: "量子位"' in index
    assert "date: 2026-" in index  # create_time parsed → date line
    assert "source: https://mp.weixin.qq.com/s/abc" in index
    assert "](images/img_" in index
    assert list((article_dir / "images").iterdir())  # image landed
    assert find_completed_output_dir(tmp_path, "https://mp.weixin.qq.com/s/abc") == article_dir


def test_convert_flow_dry_run_reports_web_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    params = _params(input_text="https://mp.weixin.qq.com/s/abc", output=str(tmp_path), dry_run=True)
    out = asyncio.run(run_convert_flow(params))
    assert out == tmp_path  # nothing created
    assert "WeChat article" in capsys.readouterr().out


def _fake_fetch(html: str):
    async def _fetch(query) -> str:  # noqa: ANN001 - test stub
        return html

    return _fetch


def monkeypatch_client(fake: FakeClient) -> None:
    import arxiv2md_beta.ingestion.web as web

    web.get_http_client = lambda: fake  # type: ignore[assignment]

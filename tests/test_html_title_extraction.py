r"""Regression tests for h1.ltx_title extraction (2102.11107 thanks-in-title bug).

LaTeXML inlines ``\\thanks{...}`` pubnotes into ``h1.ltx_title_document`` as
``<span class="ltx_pubnotes">``; the extracted title must exclude them.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from arxiv2md_beta.html.parser import _extract_title, parse_arxiv_html

_THANKS_TITLE_HTML = """
<article class="ltx_document">
<h1 class="ltx_title ltx_title_document">Towards Causal Representation Learning<span
  class="ltx_pubnotes"><span class="ltx_pubnotes_content"><span
  class="ltx_pubnote ltx_role_thanks"><span class="ltx_note_name">Thanks: </span>†:
  equal contribution.</span><span class="ltx_pubnote ltx_role_thanks"><span
  class="ltx_note_name">Thanks: </span>B. Schölkopf is at the Max-Planck Institute for
  Intelligent Systems.</span></span></span></h1>
<div class="ltx_authors">
  <span class="ltx_creator ltx_role_author"><span class="ltx_personname">Bernhard
  Schölkopf</span></span>
</div>
<section class="ltx_section"><h2 class="ltx_title ltx_title_section">Introduction</h2>
<p class="ltx_para">Body text.</p></section>
</article>
"""


def test_title_excludes_thanks_pubnotes() -> None:
    soup = BeautifulSoup(_THANKS_TITLE_HTML, "html.parser")
    assert _extract_title(soup) == "Towards Causal Representation Learning"


def test_title_extraction_does_not_mutate_soup() -> None:
    soup = BeautifulSoup(_THANKS_TITLE_HTML, "html.parser")
    _extract_title(soup)
    # The pubnotes must survive for downstream section/author parsing.
    h1 = soup.find("h1", class_="ltx_title_document")
    assert h1 is not None
    assert h1.find(class_="ltx_pubnotes") is not None


def test_parse_arxiv_html_title_clean() -> None:
    parsed = parse_arxiv_html(_THANKS_TITLE_HTML)
    assert parsed.title == "Towards Causal Representation Learning"
    assert [a.name for a in parsed.authors] == ["Bernhard Schölkopf"]

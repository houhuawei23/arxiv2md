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


_CAPTION_LEAK_TITLE_HTML = """
<article class="ltx_document">
<figure class="ltx_figure"><figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 1</span>: <span
  class="ltx_font_bold">MetaRSI-v1</span> at a glance. 1: loops close where verification is
machine-checkable. 2: one kernel, three operators, one writable surface each. 3: five of six
orderings admissible. 4: three nested improvement levels. 5: horizontal composition, vertical
policy rewriting. The band runs from freely verifiable to unverifiable.</figcaption></figure>
<section class="ltx_section"><h2 class="ltx_title ltx_title_section">Introduction</h2>
<p class="ltx_para">Body text.</p></section>
</article>
"""


def test_title_tag_partial_caption_leak_detected() -> None:
    r"""ArXiv 2609.06396v2: caption leaked into <title> with the tag half-stripped.

    When \title never renders, LaTeXML puts the first caption in <title> with
    the "Figure " tag only partially stripped — "1MetaRSI-v1 at a glance..."
    (tag digit glued to the caption body). The strict "Figure N:" prefix check
    misses it; the fuzzy caption comparison must catch it.
    """
    soup = BeautifulSoup(
        f"<html><head><title>1MetaRSI-v1 at a glance. 1: loops close where verification is "
        f"machine-checkable. 2: one kernel, three operators, one writable surface each. "
        f"3: five of six orderings admissible. 4: three nested improvement levels. "
        f"5: horizontal composition, vertical policy rewriting. The band runs from freely "
        f"verifiable to unverifiable.</title></head><body>{_CAPTION_LEAK_TITLE_HTML}</body></html>",
        "html.parser",
    )
    assert _extract_title(soup) is None

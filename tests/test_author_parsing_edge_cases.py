r"""Regression tests for author/affiliation parsing edge cases.

- LaTeXML documents that put every author inside ONE ``ltx_personname`` with
  the authors after the first wrapped in an unclosed inner ``<span>`` and
  separated by ``<br class="ltx_break">`` (e.g. arXiv 2609.36054). The old
  direct-child iteration saw only the first author.
- Single-word organisation tokens (GovAI, Guidelight, Forethought) in those
  blocks used to pass the name heuristic and become phantom authors.
- TeX templates defining bespoke ``\\formatauthor{}`` / ``\\formataffil{}``
  macros (no ICML/IEEE/acmart affiliation commands) produced no affiliations.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from arxiv2md_beta.html.parser import _extract_authors_with_affiliations
from arxiv2md_beta.latex.author_affiliations import parse_author_affiliations_from_tex

# Minimal reproduction of the 2609.36054 author block: all 22 authors in one
# personname; everything after the first author is nested in <span id="id1">.
NESTED_SPAN_PERSONNAME = """<html><body>
<article class="ltx_document">
<h1 class="ltx_title ltx_title_document">Test Paper</h1>
<div class="ltx_authors">
<span class="ltx_creator ltx_role_author">
<span class="ltx_personname">Alan Chan*
<span id="id1" class="ltx_text">GovAI
<br class="ltx_break">
<span id="id1.1" class="ltx_text">Christoph Winter
</span>CASP, University of Cambridge, Institute for Law &amp; AI
<br class="ltx_break">
<span id="id1.2" class="ltx_text">Andrew Barto
</span>University of Massachusetts Amherst
<br class="ltx_break">
<span id="id1.3" class="ltx_text">Jakub Pachocki
</span>OpenAI
<br class="ltx_break">
<span id="id1.4" class="ltx_text">Jeff Clune
</span>University of British Columbia, Vector Institute
<br class="ltx_break">
<span id="id1.5" class="ltx_text">Sam Manning
</span>GovAI, Foundation for American Innovation
<br class="ltx_break">
<span id="id1.6" class="ltx_text">Girish Sastry
</span>Guidelight
<br class="ltx_break">
<span id="id1.7" class="ltx_text">Tom Davidson
</span>Forethought
<br class="ltx_break">
<span id="id1.8" class="ltx_text">Daniel Eth
</span>AI Policy Institute
<br class="ltx_break">
<span id="id1.9" class="ltx_text">Sören Mindermann*
</span>CASP, University of Cambridge
</span>
</span>
</div>
</article></body></html>"""


class TestNestedSpanPersonname:
    def _parse(self) -> list:
        soup = BeautifulSoup(NESTED_SPAN_PERSONNAME, "html.parser")
        return _extract_authors_with_affiliations(soup)

    def test_all_authors_extracted(self) -> None:
        names = [a.name for a in self._parse()]
        assert names == [
            "Alan Chan",
            "Christoph Winter",
            "Andrew Barto",
            "Jakub Pachocki",
            "Jeff Clune",
            "Sam Manning",
            "Girish Sastry",
            "Tom Davidson",
            "Daniel Eth",
            "Sören Mindermann",
        ]

    def test_no_footnote_markers_in_names(self) -> None:
        for a in self._parse():
            assert "*" not in a.name, f"footnote marker leaked into {a.name!r}"

    def test_affiliations_attached(self) -> None:
        by_name = {a.name: a.affiliations for a in self._parse()}
        assert by_name["Alan Chan"] == ["GovAI"]
        assert by_name["Andrew Barto"] == ["University of Massachusetts Amherst"]
        assert by_name["Jakub Pachocki"] == ["OpenAI"]
        assert by_name["Sam Manning"] == ["GovAI, Foundation for American Innovation"]
        assert by_name["Girish Sastry"] == ["Guidelight"]
        assert by_name["Tom Davidson"] == ["Forethought"]
        assert by_name["Daniel Eth"] == ["AI Policy Institute"]
        assert by_name["Sören Mindermann"] == ["CASP, University of Cambridge"]

    def test_single_word_orgs_never_become_authors(self) -> None:
        names = {a.name for a in self._parse()}
        for org in ("GovAI", "OpenAI", "Guidelight", "Forethought", "AI Policy Institute"):
            assert org not in names

    def test_surname_colliding_with_keyword_not_swallowed(self) -> None:
        """The surname "Eth" trips the "eth" (ETH Zurich) keyword.

        Strict name→affiliation alternation must still recover the author.
        """
        names = [a.name for a in self._parse()]
        assert "Daniel Eth" in names


class TestClassicBrDelimitedStillWorks:
    """The ar5iv style (1706.03762): name / affiliation / email per author."""

    def test_classic_pattern(self) -> None:
        html = (
            "<html><body><article class='ltx_document'>"
            "<div class='ltx_authors'><span class='ltx_creator ltx_role_author'>"
            "<span class='ltx_personname'>"
            "Ashish Vaswani<br>Google Brain<br>avaswani@google.com<br>"
            "&amp;Noam Shazeer<br>Google Brain<br>noam@google.com"
            "</span></span></div>"
            "</article></body></html>"
        )
        soup = BeautifulSoup(html, "html.parser")
        authors = _extract_authors_with_affiliations(soup)
        assert [(a.name, a.affiliations) for a in authors] == [
            ("Ashish Vaswani", ["Google Brain"]),
            ("Noam Shazeer", ["Google Brain"]),
        ]


# ── TeX: custom \formatauthor / \formataffil macros ────────────────────────


class TestCustomAuthorAffilMacros:
    def test_formatauthor_formataffil_pairs(self) -> None:
        tex = r"""
\documentclass{article}
\author{
\formatauthor{Alan Chan*}
\formataffil{GovAI}
\formatauthor{Christoph Winter}
\formataffil{CASP, University of Cambridge, Institute for Law \& AI}
\formatauthor{Andrew Barto}
\formataffil{University of Massachusetts Amherst}
}
\begin{document}
\maketitle
\end{document}
"""
        parsed = parse_author_affiliations_from_tex(tex)
        assert [(p["name"], p["affiliations"]) for p in parsed] == [
            ("Alan Chan", ["GovAI"]),
            ("Christoph Winter", ["CASP, University of Cambridge, Institute for Law & AI"]),
            ("Andrew Barto", ["University of Massachusetts Amherst"]),
        ]

    def test_no_match_leaves_generic_parsers(self) -> None:
        # A single \author block with no affiliations must still fall through
        # to the generic parser (returns names without affiliations).
        tex = r"\author{Alice Smith\and Bob Jones}\begin{document}\maketitle"
        parsed = parse_author_affiliations_from_tex(tex)
        assert [p["name"] for p in parsed] == ["Alice Smith", "Bob Jones"]

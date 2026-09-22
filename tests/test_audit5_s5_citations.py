"""audit5 S5 citation regression tests (G4-5 bibtex key Unicode)."""

from __future__ import annotations

from arxiv2md_beta.network.arxiv_api import _generate_bibtex


class TestBibtexKeyUnicode:
    r"""audit5 G4-5: ``[^a-zA-Z]`` stripped every non-ASCII surname to "".

    "Müller" collapsed the key to ``{year}{yymm}``, easily colliding across
    papers; the citation-key generator in formatter.py was already
    Unicode-aware (``[^\\w]``) — this brings the arXiv-side generator in line.
    """

    def test_accented_surname_kept(self) -> None:
        bib = _generate_bibtex(
            "A Title", [{"name": "Hans Müller"}], "2024", "2401.00001", "cs.LG", "https://arxiv.org/abs/2401.00001"
        )
        assert "@misc{müller2024" in bib

    def test_cjk_surname_kept(self) -> None:
        bib = _generate_bibtex(
            "A Title", [{"name": "刘 骏"}], "2024", "2401.00001", "cs.LG", "https://arxiv.org/abs/2401.00001"
        )
        # Surname heuristic takes the last whitespace part; the point is that
        # CJK characters survive into the key instead of being stripped to "".
        assert "@misc{骏2024" in bib

    def test_unusable_surname_falls_back_not_empty(self) -> None:
        bib = _generate_bibtex(
            "A Title", [{"name": "🎉"}], "2024", "2401.00001", "cs.LG", "https://arxiv.org/abs/2401.00001"
        )
        # Key must not start with the bare year: the surname slot keeps a
        # placeholder so different papers don't share one key shape.
        assert "@misc{" in bib
        key = bib.split("@misc{", 1)[1].split(",", 1)[0]
        assert not key.startswith("2024")

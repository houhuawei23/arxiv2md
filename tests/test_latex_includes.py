"""Tests for LaTeX include resolution and orphan-end fixing (audit4 PR4.3)."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.latex.includes import _fix_orphan_ends, resolve_latex_includes


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_diamond_include_expands_both_times(tmp_path: Path) -> None:
    """audit4 P2: a second legitimate input of one file lost its content."""
    _write(tmp_path, "shared.tex", "SHARED-TABLE\n")
    _write(
        tmp_path,
        "main.tex",
        "\\input{shared}\nsection one\n\\input{shared}\nsection two\n",
    )
    out = resolve_latex_includes(tmp_path / "main.tex", tmp_path)
    assert out.count("SHARED-TABLE") == 2, f"second inclusion lost: {out!r}"


def test_circular_include_terminates(tmp_path: Path) -> None:
    """Regression guard: a true cycle must still terminate."""
    _write(tmp_path, "a.tex", "A-START\n\\input{b}\n")
    _write(tmp_path, "b.tex", "B-START\n\\input{a}\n")
    out = resolve_latex_includes(tmp_path / "a.tex", tmp_path)
    assert "A-START" in out and "B-START" in out


def test_orphan_end_replacement_hits_exact_token() -> None:
    r"""With two identical \end tokens, only the orphan gets commented."""
    tex = "\\begin{figure}\n\\end{figure}\\end{figure}\n"
    assert _fix_orphan_ends(tex) == "\\begin{figure}\n\\end{figure}% \\end{figure}\n"


def test_orphan_end_scan_continues_on_same_line() -> None:
    r"""After an orphan, later begin/end tokens still update the stack."""
    tex = "\\end{foo}\\begin{itemize}\nx\n\\end{itemize}\n"
    out = _fix_orphan_ends(tex)
    lines = out.split("\n")
    assert lines[0] == "% \\end{foo}\\begin{itemize}"
    assert lines[2] == "\\end{itemize}"  # not commented


class TestIncludePathContainment:
    r"""audit5 G3-4: `\input{../..}` must not read outside the archive.

    The zip layer has zip-slip protection; the include resolver needed
    the same containment.
    """

    def test_input_traversal_is_ignored(self, tmp_path: Path) -> None:
        base = tmp_path / "extracted"
        base.mkdir()
        secret = tmp_path / "outside.tex"
        secret.write_text("SECRET", encoding="utf-8")
        main = base / "main.tex"
        main.write_text("before\n\\input{../outside}\nafter\n", encoding="utf-8")
        out = resolve_latex_includes(main, base)
        assert "SECRET" not in out

    def test_lstinputlisting_traversal_is_ignored(self, tmp_path: Path) -> None:
        base = tmp_path / "extracted"
        base.mkdir()
        secret = tmp_path / "outside.py"
        secret.write_text("SECRET", encoding="utf-8")
        main = base / "main.tex"
        main.write_text("\\lstinputlisting{../outside.py}\n", encoding="utf-8")
        out = resolve_latex_includes(main, base)
        assert "SECRET" not in out

    def test_internal_include_still_resolves(self, tmp_path: Path) -> None:
        base = tmp_path / "extracted"
        base.mkdir()
        (base / "sub").mkdir()
        (base / "sub" / "part.tex").write_text("PART", encoding="utf-8")
        main = base / "main.tex"
        main.write_text("\\input{sub/part.tex}\n", encoding="utf-8")
        assert "PART" in resolve_latex_includes(main, base)

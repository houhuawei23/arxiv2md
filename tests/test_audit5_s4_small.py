"""audit5 S4 small-item regression tests (R-2, R-4, R-7, R-8, R-9)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from arxiv2md_beta.latex.includes import (
    _after_unescaped_comment,
    resolve_latex_includes,
)
from arxiv2md_beta.latex.tex_source import _file_is_pdf


class TestFileIsPdfSniff:
    """audit5 R-2: the PDF magic check must not read the whole file."""

    def test_pdf_magic_detected(self, tmp_path: Path):
        p = tmp_path / "x.tar.gz"
        p.write_bytes(b"%PDF-1.7" + b"x" * 100)
        assert _file_is_pdf(p) is True

    def test_non_pdf_rejected(self, tmp_path: Path):
        p = tmp_path / "x.tar.gz"
        p.write_bytes(b"PK\x03\x04" + b"x" * 100)
        assert _file_is_pdf(p) is False

    def test_only_five_bytes_read(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "x.tar.gz"
        p.write_bytes(b"PK\x03\x04")
        seen: list[int] = []
        real_open = open

        def counting_open(*args, **kwargs):
            fh = real_open(*args, **kwargs)
            real_read = fh.read

            def read(n=-1, *a, **k):
                seen.append(n)
                return real_read(n, *a, **k)

            fh.read = read
            return fh

        monkeypatch.setattr("builtins.open", counting_open)
        _file_is_pdf(p)
        assert seen and max(seen) <= 5


class TestMidLineCommentIncludes:
    r"""audit5 R-4: ``foo % \\input{x}`` must not inline the include."""

    def test_after_unescaped_comment(self):
        assert _after_unescaped_comment("foo % \\input{x}", len("foo % \\input{x}"))
        assert _after_unescaped_comment("100\\% done \\input{x}", len("100\\% done \\input{x}")) is False
        assert _after_unescaped_comment("clean line", 5) is False

    def test_include_behind_midline_comment_is_skipped(self, tmp_path: Path):
        base = tmp_path / "src"
        base.mkdir()
        (base / "part.tex").write_text("PART", encoding="utf-8")
        main = base / "main.tex"
        main.write_text("keep % \\input{part.tex}\n", encoding="utf-8")
        out = resolve_latex_includes(main, base)
        assert "PART" not in out
        assert "keep %" in out

    def test_escaped_percent_does_not_hide_include(self, tmp_path: Path):
        base = tmp_path / "src"
        base.mkdir()
        (base / "part.tex").write_text("PART", encoding="utf-8")
        main = base / "main.tex"
        main.write_text("100\\% \\input{part.tex}\n", encoding="utf-8")
        assert "PART" in resolve_latex_includes(main, base)


class TestSemaphoreRegistryWeakref:
    """audit5 R-7: stale semaphores must not leak across loops.

    Closed loops leave nothing behind, and a new loop does not inherit the
    previous loop's exhausted semaphores via id reuse.
    """

    def test_registry_cleared_after_loop_gone(self):
        import gc

        from arxiv2md_beta.utils import concurrency

        async def hold():
            async with concurrency.concurrency_slot("images", 1):
                pass

        loop = asyncio.new_event_loop()
        loop.run_until_complete(hold())
        assert concurrency._semaphores  # populated while the loop lives
        loop.close()
        del loop
        gc.collect()
        assert not dict(concurrency._semaphores)

    def test_new_loop_gets_fresh_semaphores(self):
        from arxiv2md_beta.utils import concurrency

        async def hold():
            async with concurrency.concurrency_slot("images", 1):
                sem = concurrency._semaphore("images", 1)
                sem.acquire()  # exhaust it on purpose

        loop1 = asyncio.new_event_loop()
        loop1.run_until_complete(hold())
        loop1.close()

        async def probe():
            async with concurrency.concurrency_slot("images", 1):
                return "acquired"

        loop2 = asyncio.new_event_loop()
        try:
            assert loop2.run_until_complete(probe()) == "acquired"
        finally:
            loop2.close()


class TestConfigValidateRestoresSettings:
    """audit5 R-8: a failing candidate config must not become the global."""

    def test_failed_validate_leaves_process_settings_intact(self, tmp_path: Path, monkeypatch):

        sentinel = object()
        monkeypatch.setattr("arxiv2md_beta.cli.config_cmd.get_settings", lambda: sentinel)

        def broken_load(*, config_path, force_reload):
            raise ValueError("bad config")

        applied: dict = {}
        monkeypatch.setattr("arxiv2md_beta.cli.config_cmd.load_settings", broken_load)
        # _validate_config_file imports set_settings from settings at call time
        monkeypatch.setattr(
            "arxiv2md_beta.settings.set_settings",
            lambda s: applied.setdefault("restored", s),
        )

        from arxiv2md_beta.cli.config_cmd import _validate_config_file

        with pytest.raises(ValueError, match="bad config"):
            _validate_config_file(tmp_path / "nope.yml")
        assert applied.get("restored") is sentinel


class TestEmptyOutputDirWarns:
    """audit5 R-9: ``-o ""`` must warn, not silently use the default."""

    def test_empty_string_returns_default_and_warns(self, tmp_path, monkeypatch):
        from arxiv2md_beta.output import layout

        warnings: list[str] = []

        class FakeSettings:
            # attribute name mirrors AppSettings.cli_defaults
            cli_defaults = type("CliDefaults", (), {"output_dir": str(tmp_path)})

        monkeypatch.setattr(layout, "get_settings", lambda: FakeSettings())
        monkeypatch.setattr(layout.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
        out = layout.determine_output_dir("")
        assert out == tmp_path
        assert warnings, "empty -o must warn"
        # None (option absent) stays silent
        warnings.clear()
        layout.determine_output_dir(None)
        assert not warnings


def test_orphan_fix_never_comments_end_document(tmp_path: Path) -> None:
    r"""audit5 (found by the S7 adversarial corpus): \\end{document} stays live.

    An unbalanced \\begin{enumerate} inside a statically false \\if0 chunk
    poisoned the env stack (the false-conditional strip runs later, in the
    builder), and _fix_orphan_ends then commented the document terminator —
    killing the whole pandoc parse with "expecting \\end{document}".
    """
    main = tmp_path / "main.tex"
    tex = (
        "\\begin{document}\n"
        "\\if0\n\\begin{enumerate}\n\\else\nreal\n\\fi\n"
        "\\end{document}\n"
    )
    main.write_text(tex, encoding="utf-8")
    out = resolve_latex_includes(main, tmp_path)
    assert any(line.strip() == r"\end{document}" for line in out.splitlines()), out
    assert "% \\end{document}" not in out

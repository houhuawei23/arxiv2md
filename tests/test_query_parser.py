"""Comprehensive tests for query parser module."""

from __future__ import annotations

from pathlib import Path

import pytest

from arxiv2md_beta.exceptions import UserInputError
from arxiv2md_beta.query.parser import (
    parse_arxiv_input,
    parse_local_archive,
    parse_local_html,
)


class TestArxivQueryParsing:
    """Tests for parsing arXiv IDs and URLs."""

    def test_parse_plain_arxiv_id(self):
        """Parse plain arXiv ID."""
        result = parse_arxiv_input("2501.12345")
        assert result.arxiv_id == "2501.12345"
        assert result.version is None
        assert "2501.12345" in result.html_url

    def test_parse_arxiv_id_with_version(self):
        """Parse arXiv ID with version."""
        result = parse_arxiv_input("2501.12345v2")
        assert result.arxiv_id == "2501.12345v2"
        assert result.version == "v2"

    def test_parse_old_arxiv_id_format(self):
        """Parse old arXiv ID format (pre-2007)."""
        result = parse_arxiv_input("cs/0112017")
        assert result.arxiv_id == "cs/0112017"
        assert "cs/0112017" in result.html_url

    def test_parse_arxiv_abs_url(self):
        """Parse arXiv abs URL."""
        result = parse_arxiv_input("https://arxiv.org/abs/2501.12345")
        assert result.arxiv_id == "2501.12345"

    def test_parse_arxiv_html_url(self):
        """Parse arXiv HTML URL."""
        result = parse_arxiv_input("https://arxiv.org/html/2501.12345")
        assert result.arxiv_id == "2501.12345"

    def test_parse_arxiv_pdf_url(self):
        """Parse arXiv PDF URL."""
        result = parse_arxiv_input("https://arxiv.org/pdf/2501.12345.pdf")
        assert result.arxiv_id == "2501.12345"

    def test_parse_arxiv_id_with_whitespace(self):
        """Parse arXiv ID with surrounding whitespace."""
        result = parse_arxiv_input("  2501.12345  ")
        assert result.arxiv_id == "2501.12345"

    def test_parse_invalid_arxiv_id(self):
        """Parse invalid arXiv ID raises UserInputError."""
        with pytest.raises(UserInputError):
            parse_arxiv_input("not-an-arxiv-id")

    def test_parse_empty_string(self):
        """Parse empty string raises UserInputError."""
        with pytest.raises(UserInputError):
            parse_arxiv_input("")


class TestLocalHtmlParsing:
    """Tests for parsing local HTML file paths."""

    def test_is_local_html_path(self, tmp_path: Path):
        """The predicate covers real files, bare extensions, and non-paths."""
        from arxiv2md_beta.query.parser import is_local_html_path

        html_file = tmp_path / "paper.html"
        html_file.write_text("<html></html>")
        assert is_local_html_path(str(html_file)) is True

        # .htm works; non-HTML suffixes and IDs/URLs do not.
        htm_file = tmp_path / "paper.htm"
        htm_file.write_text("<html></html>")
        assert is_local_html_path(str(htm_file)) is True
        assert is_local_html_path(str(tmp_path / "paper.txt")) is False
        assert is_local_html_path("2501.11120") is False
        assert is_local_html_path("https://arxiv.org/abs/2501.11120") is False
        assert is_local_html_path("") is False

    def test_parse_local_html(self, tmp_path: Path):
        """Parse valid local HTML file."""
        html_file = tmp_path / "paper.html"
        html_file.write_text("<html><body>Paper</body></html>")

        result = parse_local_html(str(html_file))
        assert result.html_path == html_file

    def test_parse_local_html_not_found(self):
        """Parse non-existent HTML file raises error."""
        with pytest.raises(FileNotFoundError):
            parse_local_html("/nonexistent/paper.html")


class TestLocalArchiveParsing:
    """Tests for parsing local archive file paths."""

    def test_is_local_archive_path_tar_gz(self, tmp_path: Path):
        """The predicate recognizes tar.gz/tgz and rejects other paths."""
        from arxiv2md_beta.query.parser import is_local_archive_path

        archive = tmp_path / "paper.tar.gz"
        archive.write_bytes(b"fake tar.gz content")
        assert is_local_archive_path(str(archive)) is True

        tgz = tmp_path / "paper.tgz"
        tgz.write_bytes(b"fake tgz content")
        assert is_local_archive_path(str(tgz)) is True
        assert is_local_archive_path(str(tmp_path / "paper.zip")) is True
        assert is_local_archive_path(str(tmp_path / "paper.txt")) is False
        assert is_local_archive_path("2501.11120") is False
        assert is_local_archive_path("") is False

    def test_is_local_archive_path_zip(self, tmp_path: Path):
        """The predicate recognizes zip archives."""
        from arxiv2md_beta.query.parser import is_local_archive_path

        archive = tmp_path / "paper.zip"
        archive.write_bytes(b"fake zip content")
        assert is_local_archive_path(str(archive)) is True

    def test_parse_local_archive_tar_gz(self, tmp_path: Path):
        """Parse valid tar.gz archive."""
        archive = tmp_path / "paper.tar.gz"
        archive.write_bytes(b"fake content")

        result = parse_local_archive(str(archive))
        assert result.archive_path == archive
        assert result.archive_type in ("tar.gz", "tgz")

    def test_parse_local_archive_zip(self, tmp_path: Path):
        """Parse valid zip archive."""
        archive = tmp_path / "paper.zip"
        archive.write_bytes(b"fake content")

        result = parse_local_archive(str(archive))
        assert result.archive_type == "zip"

    def test_parse_local_archive_not_found(self):
        """Parse non-existent archive raises error."""
        with pytest.raises(FileNotFoundError):
            parse_local_archive("/nonexistent/paper.tar.gz")

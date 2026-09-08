from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from arxiv2md_beta.latex.tex_source import ArchiveExtractionError, _extract_tar_archive, _extract_zip_archive


def test_zip_rejects_parent_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escaped.txt", "bad")

    with pytest.raises(ArchiveExtractionError, match="suspicious path"):
        _extract_zip_archive(archive, tmp_path / "out")

    assert not (tmp_path / "escaped.txt").exists()


def test_tar_rejects_parent_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    payload = b"bad"
    with tarfile.open(archive, "w:gz") as handle:
        member = tarfile.TarInfo("../escaped.txt")
        member.size = len(payload)
        handle.addfile(member, io.BytesIO(payload))

    with pytest.raises(ArchiveExtractionError, match="suspicious path"):
        _extract_tar_archive(archive, tmp_path / "out")

    assert not (tmp_path / "escaped.txt").exists()


def test_tar_rejects_symbolic_links(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = "outside"
        handle.addfile(member)

    with pytest.raises(ArchiveExtractionError, match="contains link"):
        _extract_tar_archive(archive, tmp_path / "out")

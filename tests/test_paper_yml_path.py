"""Tests for paper.yml output path resolution."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.output.metadata import (
    arxiv_id_from_paper_yml,
    arxiv_id_from_paper_yml_dict,
    load_paper_yml,
    merge_paper_yml_preserve_user_fields,
)
from arxiv2md_beta.output.paper_yml_path import resolve_paper_yml_output_path


def test_resolve_new_file(tmp_path: Path) -> None:
    p = tmp_path / "paper.yml"
    assert resolve_paper_yml_output_path(p) == p.resolve()


def test_resolve_increment_when_exists(tmp_path: Path) -> None:
    (tmp_path / "paper.yml").write_text("paper: {}\n", encoding="utf-8")
    out = resolve_paper_yml_output_path(tmp_path / "paper.yml")
    assert out == tmp_path / "paper.1.yml"


def test_resolve_second_increment(tmp_path: Path) -> None:
    (tmp_path / "paper.yml").write_text("x: 1\n", encoding="utf-8")
    (tmp_path / "paper.1.yml").write_text("x: 2\n", encoding="utf-8")
    out = resolve_paper_yml_output_path(tmp_path / "paper.yml")
    assert out == tmp_path / "paper.2.yml"


def test_resolve_force_overwrite(tmp_path: Path) -> None:
    (tmp_path / "paper.yml").write_text("x: 1\n", encoding="utf-8")
    out = resolve_paper_yml_output_path(tmp_path / "paper.yml", force=True)
    assert out == tmp_path / "paper.yml"


def test_resolve_directory_defaults_to_paper_yml(tmp_path: Path) -> None:
    d = tmp_path / "out"
    d.mkdir()
    assert resolve_paper_yml_output_path(d) == (d / "paper.yml").resolve()


def test_arxiv_id_from_paper_yml_identifiers(tmp_path: Path) -> None:
    p = tmp_path / "p.yml"
    p.write_text(
        "paper:\n  identifiers:\n    arxiv: 1706.03762v7\n",
        encoding="utf-8",
    )
    assert arxiv_id_from_paper_yml(p) == "1706.03762v7"


def test_arxiv_id_from_paper_id_only(tmp_path: Path) -> None:
    p = tmp_path / "p.yml"
    p.write_text(
        "paper:\n  id: arxiv:1706.03762v7\n",
        encoding="utf-8",
    )
    assert arxiv_id_from_paper_yml(p) == "1706.03762v7"


def test_load_paper_yml_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "x.yml"
    p.write_text("paper:\n  title: T\n", encoding="utf-8")
    d = load_paper_yml(p)
    assert d["paper"]["title"] == "T"


def test_merge_paper_yml_preserves_user_only_urls_and_top_level() -> None:
    existing = {
        "paper": {
            "urls": {
                "pdf": "https://arxiv.org/pdf/old",
                "website": "https://example.org",
                "github": "https://github.com/o/r",
            },
            "title": "Old title",
        },
        "notes": "manual",
    }
    fresh = {
        "paper": {
            "urls": {
                "pdf": "https://arxiv.org/pdf/new",
                "abstract": "https://arxiv.org/abs/new",
            },
            "title": "New title",
        }
    }
    merged = merge_paper_yml_preserve_user_fields(existing, fresh)
    assert merged["notes"] == "manual"
    assert merged["paper"]["title"] == "New title"
    u = merged["paper"]["urls"]
    assert u["pdf"] == "https://arxiv.org/pdf/new"
    assert u["abstract"] == "https://arxiv.org/abs/new"
    assert u["website"] == "https://example.org"
    assert u["github"] == "https://github.com/o/r"


def test_arxiv_id_from_paper_yml_dict_matches_path(tmp_path: Path) -> None:
    p = tmp_path / "p.yml"
    p.write_text(
        "paper:\n  identifiers:\n    arxiv: 1706.03762v7\n",
        encoding="utf-8",
    )
    d = load_paper_yml(p)
    assert arxiv_id_from_paper_yml_dict(d) == arxiv_id_from_paper_yml(p)

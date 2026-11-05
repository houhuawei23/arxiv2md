"""Schema v1 → v2 migration for paper.yml.

v1 scattered identity and classification: ``paper.id`` duplicated
``identifiers.arxiv``, arXiv categories lived in both ``content.keywords``
and ``relations.categories``, ``content.citation`` was a truncated derived
string, and bibtex was embedded as escaped multi-line YAML. v2 moves
bibtex to a sibling ``paper.bib`` and stamps ``paper.schema: 2``.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from arxiv2md_beta.output.metadata import (
    PAPER_YML_SCHEMA_VERSION,
    arxiv_id_from_paper_yml_dict,
    bib_sidecar_path,
    load_paper_yml,
    migrate_paper_yml_v1_to_v2,
    write_paper_yml_file,
)

V1_FIXTURE: dict = {
    "paper": {
        "id": "arxiv:1706.03762",
        "title": "Attention Is All You Need",
        "authors": [
            {"name": "A", "affiliation": "Google Brain"},
            {"name": "B", "affiliations": ["University of Toronto"]},
        ],
        "publication": {"type": "preprint", "venue": "arXiv", "date_published": "2017-06-12", "year": 2017},
        "identifiers": {"arxiv": "1706.03762", "openalex_work": "https://openalex.org/W1"},
        "urls": {"pdf": "https://arxiv.org/pdf/1706.03762", "abstract": "https://arxiv.org/abs/1706.03762"},
        "content": {
            "abstract": "x",
            "keywords": ["cs.CL", "transformers", "attention"],
            "language": "en",
            "citation": "Vaswani et al.. (2017). Attention Is All You Nee...",
        },
        "workflow": {"status": "read", "priority": "high", "date_added": "2020-01-01"},
        "relations": {
            "tags": ["llm"],
            "categories": ["cs.CL", "cs.LG"],
            "primary_category": "cs.CL",
            "related": [],
        },
        "bibtex": "@misc{vaswani2017, title = {Attention Is All You Need}}",
    }
}


def test_migrate_moves_classification_and_drops_v1_keys() -> None:
    data = copy.deepcopy(V1_FIXTURE)
    migrated, bibtex = migrate_paper_yml_v1_to_v2(data)
    p = migrated["paper"]

    assert bibtex == "@misc{vaswani2017, title = {Attention Is All You Need}}"
    assert p["schema"] == PAPER_YML_SCHEMA_VERSION
    assert p["classification"] == {"categories": ["cs.CL", "cs.LG"], "primary_category": "cs.CL"}
    # categories are classification, not keywords
    assert p["content"]["keywords"] == ["transformers", "attention"]
    assert "citation" not in p["content"]
    assert "id" not in p and "bibtex" not in p
    assert "categories" not in p["relations"] and "primary_category" not in p["relations"]
    # legacy scalar affiliation → list
    assert p["authors"][0]["affiliations"] == ["Google Brain"] and "affiliation" not in p["authors"][0]
    # user-owned fields untouched
    assert p["workflow"]["status"] == "read" and p["relations"]["tags"] == ["llm"]
    # reader still resolves the id after migration
    assert arxiv_id_from_paper_yml_dict(migrated) == "1706.03762"


def test_migrate_is_idempotent_and_stamps_unversioned_v2() -> None:
    data = copy.deepcopy(V1_FIXTURE)
    migrated, _ = migrate_paper_yml_v1_to_v2(data)
    again, bibtex = migrate_paper_yml_v1_to_v2(migrated)
    assert again is migrated
    assert bibtex is None

    # structurally v2 but written before the stamp existed
    unversioned = {"paper": {"title": "T", "identifiers": {"arxiv": "1234.5678"}}}
    out, bibtex = migrate_paper_yml_v1_to_v2(unversioned)
    assert out["paper"]["schema"] == PAPER_YML_SCHEMA_VERSION
    assert bibtex is None


def test_write_paper_yml_file_writes_bib_sidecar_and_v2(tmp_path: Path) -> None:
    target = tmp_path / "paper.yml"
    metadata = {
        "arxiv_id": "1706.03762",
        "title": "Attention Is All You Need",
        "year": 2017,
        "published": "2017-06-12T00:00:00Z",
        "categories": ["cs.CL"],
        "bibtex": "@misc{fresh2017}",
        "authors": [{"name": "A"}],
    }
    write_paper_yml_file(metadata, target, merge_existing=copy.deepcopy(V1_FIXTURE))

    data = load_paper_yml(target)
    p = data["paper"]
    assert p["schema"] == PAPER_YML_SCHEMA_VERSION
    assert "bibtex" not in p and "id" not in p
    # user-owned v1 bibtex survives into the sidecar (fresh value loses)
    bib = bib_sidecar_path(target).read_text(encoding="utf-8")
    assert "vaswani2017" in bib and "fresh2017" not in bib
    # migrated user fields preserved through the merge
    assert p["workflow"]["status"] == "read"
    # fresh API categories win over the migrated v1 ones (same-path merge rule)
    assert p["classification"]["categories"] == ["cs.CL"]


def test_migrate_cli_flow_noop_for_v2(tmp_path: Path) -> None:
    from arxiv2md_beta.cli.runner.paper_yml import _run_paper_yml_migrate

    target = tmp_path / "paper.yml"
    metadata = {
        "arxiv_id": "1706.03762",
        "title": "Attention",
        "year": 2017,
        "published": "2017-06-12T00:00:00Z",
    }
    write_paper_yml_file(metadata, target)
    before = target.read_text(encoding="utf-8")

    out = _run_paper_yml_migrate(target)
    assert out == target.resolve()
    assert target.read_text(encoding="utf-8") == before  # untouched


def test_migrate_cli_flow_upgrades_v1_in_place(tmp_path: Path) -> None:
    from arxiv2md_beta.cli.runner.paper_yml import _run_paper_yml_migrate

    target = tmp_path / "paper.yml"
    target.write_text(yaml.dump(V1_FIXTURE, allow_unicode=True), encoding="utf-8")

    _run_paper_yml_migrate(target)
    data = load_paper_yml(target)
    p = data["paper"]
    assert p["schema"] == PAPER_YML_SCHEMA_VERSION
    assert arxiv_id_from_paper_yml_dict(data) == "1706.03762"
    assert p["workflow"]["status"] == "read"  # user field survived
    assert "vaswani2017" in bib_sidecar_path(target).read_text(encoding="utf-8")
    # original preserved in the .bak rotation
    assert "arxiv:1706.03762" in target.with_suffix(".yml.bak").read_text(encoding="utf-8")

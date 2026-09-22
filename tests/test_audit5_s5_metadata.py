"""audit5 S5 metadata regression tests (R-11, T-11)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from arxiv2md_beta.output.metadata import write_paper_yml_file


def _meta(arxiv_id: str = "2401.00001", title: str = "T") -> dict:
    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "summary": "S",
        "authors": [{"name": "A B"}],
        "categories": ["cs.LG"],
        "primary_category": "cs.LG",
    }


class TestBackupRotation:
    """audit5 R-11: ``.bak`` was one generation deep.

    Two consecutive bad writes destroyed the only good copy; the previous
    ``.bak`` now rotates to ``.bak2`` before a new one is taken.
    """

    def test_two_writes_keep_two_generations(self, tmp_path: Path) -> None:
        target = tmp_path / "paper.yml"
        write_paper_yml_file(_meta(title="V0"), target)
        assert not target.with_suffix(".yml.bak").exists()

        write_paper_yml_file(_meta(title="V1"), target)
        write_paper_yml_file(_meta(title="V2"), target)

        bak = yaml.safe_load(target.with_suffix(".yml.bak").read_text(encoding="utf-8"))
        bak2 = yaml.safe_load(target.with_suffix(".yml.bak2").read_text(encoding="utf-8"))
        assert bak["paper"]["title"] == "V1"
        assert bak2["paper"]["title"] == "V0"

    def test_current_file_always_freshest(self, tmp_path: Path) -> None:
        target = tmp_path / "paper.yml"
        write_paper_yml_file(_meta(title="V0"), target)
        write_paper_yml_file(_meta(title="V1"), target)
        cur = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert cur["paper"]["title"] == "V1"


class TestDateAddedUtc:
    """audit5 T-11: ``date_added`` used local time.

    Around midnight the same paper got different entry dates depending on
    the machine's timezone.
    """

    def test_date_added_is_utc_date(self, tmp_path: Path) -> None:
        target = tmp_path / "paper.yml"
        write_paper_yml_file(_meta(), target)
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        recorded = data["paper"]["workflow"]["date_added"]
        assert recorded == datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", recorded)

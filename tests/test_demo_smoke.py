"""Smoke guard for the demo script: it must track the current public API.

The demo rotted twice before (called ``ingest_paper`` with parameters that no
longer exist, and with ``parser="html"`` which that function now rejects), so
this locks the demo to the same entry point the CLI uses.
"""

from __future__ import annotations

from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "demo" / "demo_arxiv2md_beta.py"


def test_demo_uses_public_convert_flow() -> None:
    source = DEMO.read_text(encoding="utf-8")
    assert "run_convert_flow" in source
    assert "ConvertParams" in source


def test_demo_has_no_removed_parameters() -> None:
    source = DEMO.read_text(encoding="utf-8")
    # Signatures/kwargs that no longer exist anywhere in the package.
    for removed in ("remove_toc", "images_dir_name=", "output_dir=", "ingest_paper("):
        assert removed not in source, f"demo references removed API: {removed}"


def test_demo_imports_resolve() -> None:
    import ast

    tree = ast.parse(DEMO.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("arxiv2md_beta"):
            imported.update(alias.name for alias in node.names)
    assert {"run_convert_flow", "run_async", "ConvertParams", "read_paper_manifest"} <= imported

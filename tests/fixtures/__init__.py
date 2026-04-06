"""Test fixtures for arxiv2md-beta."""

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent


def load_sample_html() -> str:
    """Load sample arXiv HTML fixture."""
    return (FIXTURES_DIR / "sample_arxiv.html").read_text(encoding="utf-8")


def get_fixture_path(name: str) -> Path:
    """Get path to a fixture file."""
    return FIXTURES_DIR / name

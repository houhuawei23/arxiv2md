"""High-level ingestion orchestration (LaTeX entry; HTML uses the orchestrator).

The import used to be lazy (PEP 562) because ``pipeline`` pulled in ``cli``
via the orchestrator's ``cli.helpers`` dependency; that edge is gone, so a
plain eager import is safe.
"""

from __future__ import annotations

from arxiv2md_beta.ingestion.pipeline import ingest_paper

__all__ = ["ingest_paper"]

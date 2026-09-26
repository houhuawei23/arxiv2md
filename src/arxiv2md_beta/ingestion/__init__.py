"""High-level ingestion orchestration (LaTeX entry; HTML uses the orchestrator).

``ingest_paper`` is the remote-LaTeX entry point with the unified signature
``(params, query, sections, base_output_dir)``; the remote-HTML path is
:class:`~arxiv2md_beta.ingestion.orchestrator.IngestionOrchestrator`,
routed by the CLI layer.
"""

from __future__ import annotations

from arxiv2md_beta.ingestion.latex import ingest_paper

__all__ = ["ingest_paper"]

"""High-level ingestion orchestration (HTML / LaTeX / local archive).

``ingest_paper`` is resolved lazily (PEP 562) so that importing a submodule
such as ``ingestion.ir_finalize`` does not eagerly load ``ingestion.pipeline``
-- which would pull in ``cli`` and form an import cycle when ``cli`` is not
yet initialized (e.g. when a test imports an ingestion submodule directly).
"""

from __future__ import annotations

__all__ = ["ingest_paper"]


def __getattr__(name: str):
    if name == "ingest_paper":
        from arxiv2md_beta.ingestion.pipeline import ingest_paper

        return ingest_paper
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""TeX-based author affiliation enrichment shared by ``convert`` and ``paper-yml``.

``fetch_arxiv_metadata`` already applies abs-page + OpenAlex enrichment. This module
adds parsing of ``\\icmlauthor`` / ``\\author`` / IEEE blocks from the arXiv TeX source
when :confval:`ingestion.enrich_affiliations_from_tex` is true.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from arxiv2md_beta.latex.tex_source import TexSourceInfo, TexSourceNotFoundError, fetch_and_extract_tex_source
from arxiv2md_beta.settings import get_settings


def merge_tex_affiliations_if_configured(
    metadata: dict[str, Any],
    tex_source_info: TexSourceInfo | None,
) -> int:
    """Merge TeX-parsed affiliations when settings allow and ``tex_source_info`` is available.

    Used by the HTML/LaTeX pipelines after TeX has already been downloaded for images
    (or for HTML ``--no-images`` when a separate fetch was performed).

    Returns
    -------
    int
        Number of authors matched (same as ``merge_tex_affiliations_into_metadata``).
    """
    if not get_settings().ingestion.enrich_affiliations_from_tex:
        return 0
    if tex_source_info is None:
        return 0
    try:
        from arxiv2md_beta.latex.author_affiliations import merge_tex_affiliations_into_metadata

        return merge_tex_affiliations_into_metadata(metadata, tex_source_info)
    except Exception as e:
        logger.warning(f"TeX affiliation merge failed: {e}")
        return 0


async def fetch_and_merge_tex_affiliations_for_metadata(
    metadata: dict[str, Any],
    arxiv_id: str,
    version: str | None = None,
) -> int:
    """Download arXiv TeX source and merge affiliations into ``metadata`` (in place).

    Used by ``arxiv2md-beta paper-yml`` (new file or ``--update``) where no prior TeX
    extraction ran. Uses the same merge logic as :func:`merge_tex_affiliations_if_configured`.

    Parameters
    ----------
    metadata
        Dict from :func:`~arxiv2md_beta.network.arxiv_api.fetch_arxiv_metadata` (already
        Atom + OpenAlex enriched).
    arxiv_id
        Normalized base id (e.g. ``2602.05842``), as from :func:`~arxiv2md_beta.query.parser.parse_arxiv_input`.
    version
        Optional version suffix (e.g. ``v2``), or ``None`` for latest.

    Returns
    -------
    int
        Match count from merge, or ``0`` if skipped or failed.
    """
    if not get_settings().ingestion.enrich_affiliations_from_tex:
        return 0
    try:
        tex = await fetch_and_extract_tex_source(arxiv_id, version=version)
    except TexSourceNotFoundError:
        logger.debug(f"No TeX source for {arxiv_id}; skipping TeX affiliation merge")
        return 0
    except Exception as e:
        logger.warning(f"TeX fetch for affiliations failed: {e}")
        return 0
    try:
        from arxiv2md_beta.latex.author_affiliations import merge_tex_affiliations_into_metadata

        return merge_tex_affiliations_into_metadata(metadata, tex)
    except Exception as e:
        logger.warning(f"TeX affiliation merge failed: {e}")
        return 0

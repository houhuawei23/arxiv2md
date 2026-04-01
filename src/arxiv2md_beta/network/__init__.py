"""HTTP fetch and external APIs (arXiv, CrossRef)."""

from arxiv2md_beta.network.arxiv_api import fetch_arxiv_metadata
from arxiv2md_beta.network.author_enrichment import enrich_authors_with_abs_html_and_openalex
from arxiv2md_beta.network.crossref_api import fetch_crossref_metadata, is_arxiv_doi
from arxiv2md_beta.network.fetch import fetch_arxiv_html, fetch_arxiv_pdf

__all__ = [
    "enrich_authors_with_abs_html_and_openalex",
    "fetch_arxiv_html",
    "fetch_arxiv_pdf",
    "fetch_arxiv_metadata",
    "fetch_crossref_metadata",
    "is_arxiv_doi",
]

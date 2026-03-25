"""HTTP fetch and external APIs (arXiv, CrossRef)."""

from arxiv2md_beta.network.arxiv_api import fetch_arxiv_metadata
from arxiv2md_beta.network.crossref_api import fetch_crossref_metadata, is_arxiv_doi
from arxiv2md_beta.network.fetch import fetch_arxiv_html, fetch_arxiv_pdf

__all__ = [
    "fetch_arxiv_html",
    "fetch_arxiv_pdf",
    "fetch_arxiv_metadata",
    "fetch_crossref_metadata",
    "is_arxiv_doi",
]

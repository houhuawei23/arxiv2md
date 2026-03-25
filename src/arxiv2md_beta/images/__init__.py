"""Figure extraction and PDF/EPS to PNG processing."""

from arxiv2md_beta.images.extract import extract_arxiv_images
from arxiv2md_beta.images.resolver import ProcessedImages, process_images

__all__ = ["ProcessedImages", "extract_arxiv_images", "process_images"]

"""Output paths, Markdown utilities, metadata sidecars.

The legacy format_paper (output/formatter.py) was removed; Markdown formatting
lives in output/markdown_utils.py and all conversion goes through the IR
pipeline (MarkdownEmitter).
"""

from arxiv2md_beta.output.layout import (
    build_output_basename,
    create_paper_output_dir,
    determine_images_dir,
    determine_output_dir,
    sanitize_title_for_filesystem,
)
from arxiv2md_beta.output.metadata import save_paper_metadata

__all__ = [
    "build_output_basename",
    "create_paper_output_dir",
    "determine_images_dir",
    "determine_output_dir",
    "sanitize_title_for_filesystem",
    "save_paper_metadata",
]

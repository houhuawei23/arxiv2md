"""Shared helpers for CLI runner subpackage."""

from __future__ import annotations

from dataclasses import replace

from arxiv2md_beta.cli.params import ConvertParams


def merge_convert_params(template: ConvertParams, input_text: str) -> ConvertParams:
    """Build params for one batch line from a template.

    Uses :func:`dataclasses.replace` so every field of ``ConvertParams`` is
    carried over — hand-listing fields previously dropped ``no_cache``,
    ``naming_scheme``, ``download_pdf``, ``linked_citations`` and ``use_legacy``
    in batch mode (silent feature loss).
    """
    return replace(template, input_text=input_text)

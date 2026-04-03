"""Shared helpers for CLI runner subpackage."""

from __future__ import annotations

from arxiv2md_beta.cli.params import ConvertParams


def merge_convert_params(template: ConvertParams, input_text: str) -> ConvertParams:
    """Build params for one batch line from a template."""
    p = template
    return ConvertParams(
        input_text=input_text,
        parser=p.parser,
        output=p.output,
        source=p.source,
        short=p.short,
        no_images=p.no_images,
        remove_refs=p.remove_refs,
        remove_toc=p.remove_toc,
        remove_inline_citations=p.remove_inline_citations,
        section_filter_mode=p.section_filter_mode,
        sections=p.sections,
        section=p.section,
        include_tree=p.include_tree,
        emit_result_json=p.emit_result_json,
        structured_output=p.structured_output,
        emit_graph_csv=p.emit_graph_csv,
    )

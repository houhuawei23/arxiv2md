"""Shared IR-document finalization steps used by every ingestion path.

The four input paths (remote HTML, remote LaTeX, local HTML file, local
archive) each acquire their source differently and have different metadata
richness, but they share the same *tail*: emit a ``DocumentIR`` to Markdown
(split into main / references / appendix sidecars) and optionally write the
structured JSON bundle. Before this module existed that tail was copy-pasted
across five call sites with subtle drift (the three local paths forgot to
null ``doc.abstract`` for sidecars, so the abstract leaked into the
references/appendix output). Both steps live here once now.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.ir.emitters.markdown import MarkdownEmitter
from arxiv2md_beta.ir.transforms.section_filter import split_ir_sections


def emit_split_markdown(
    doc: DocumentIR,
    *,
    reference_section_titles: list[str],
    linked_citations: bool = False,
    remove_inline_citations: bool = False,
    include_anchors: bool | None = None,
) -> tuple[str, str | None, str | None]:
    """Emit *doc* into main / references / appendix Markdown sidecars.

    The references and appendix sidecars are emitted with ``doc.abstract``
    emptied so the abstract is not repeated in every sidecar. The doc is
    restored to its original state before returning.

    Each sidecar is finalized in a single pass (format + clean, including
    optional anchor stripping per ``settings.output.include_anchors``) so the
    returned strings are the final Markdown -- no further postprocessing is
    needed at the CLI layer.
    """
    # Lazy imports: markdown_postprocess pulls in settings, which would create
    # an import cycle if loaded at module init (ingestion package -> cli).
    from arxiv2md_beta.output.markdown_postprocess import finalize_markdown
    from arxiv2md_beta.settings import get_settings

    if include_anchors is None:
        include_anchors = get_settings().output.include_anchors
    emitter = MarkdownEmitter(
        linked_citations=linked_citations,
        remove_inline_citations=remove_inline_citations,
    )
    main_irs, ref_irs, app_irs = split_ir_sections(doc.sections, reference_section_titles)

    original_sections = doc.sections
    original_abstract = doc.abstract

    doc.sections = main_irs
    content = finalize_markdown(emitter.emit(doc), include_anchors=include_anchors)

    doc.abstract = []
    doc.sections = ref_irs
    ref_raw = emitter.emit(doc) if ref_irs else ""
    content_references = finalize_markdown(ref_raw, include_anchors=include_anchors) if ref_raw.strip() else None

    doc.sections = app_irs
    app_raw = emitter.emit(doc) if app_irs else ""
    content_appendix = finalize_markdown(app_raw, include_anchors=include_anchors) if app_raw.strip() else None

    doc.sections = original_sections
    doc.abstract = original_abstract
    return content, content_references, content_appendix


def run_structured_export(
    doc: DocumentIR,
    output_dir: Path,
    *,
    mode: str,
    emit_graph_csv: bool,
    images_subdir: str,
) -> dict[str, Any]:
    """Write the ``paper.*.json`` bundle for *doc* if *mode* is not ``none``.

    Returns the emitter's path mapping (empty dict when disabled). Errors are
    logged and swallowed at this layer so a structured-export failure never
    aborts an otherwise-successful conversion; callers that want fail-fast
    behavior should validate *mode* first.
    """
    from loguru import logger

    from arxiv2md_beta.ir.emitters.json_emitter import JsonEmitter, normalize_structured_mode

    sm = normalize_structured_mode(mode)
    if sm == "none":
        return {}
    try:
        return JsonEmitter(mode=sm).write_bundle(
            doc,
            output_dir,
            images_subdir=images_subdir,
            emit_graph_csv=emit_graph_csv,
        )
    except (OSError, ValueError, TypeError, RuntimeError) as e:
        logger.warning(f"Structured JSON export failed: {e}")
        return {}

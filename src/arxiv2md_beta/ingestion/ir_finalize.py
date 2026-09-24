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

import re
from pathlib import Path
from typing import Any

from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.ir.emitters.markdown import MarkdownEmitter
from arxiv2md_beta.ir.transforms.section_filter import split_ir_sections

# Top-level bullet ("- " / "* " at column 0) marks a reference entry start.
# Continuation / wrapped lines never begin with a bullet at column 0, so this
# reliably segments the list even when wrap lines are un-indented.
_REF_ENTRY_RE = re.compile(r"^(?P<marker>- |\* )(?P<rest>\S)")


def _number_reference_entries(markdown: str, *, include_anchors: bool) -> str:
    r"""Number reference entries ``[1]``, ``[2]``, ... and add ``ref-N`` anchors.

    ar5iv numbers bibitems in bibliography order, which equals the order entries
    appear here, so the Nth entry corresponds to inline ``[N]`` citations and
    the ``#ref-N`` anchors that ``_fix_citation_links`` points at.

    The ``<a id="ref-N"></a>`` anchors are only emitted when *include_anchors*
    is set; otherwise the reference list carries no HTML tags at all.
    """
    lines = markdown.split("\n")
    out: list[str] = []
    n = 0
    for line in lines:
        m = _REF_ENTRY_RE.match(line)
        if m:
            n += 1
            marker = m.group("marker")
            if include_anchors:
                # Own paragraph, blank-line separated on both sides — glued
                # anchors read as list text in strict parsers (audit5 I-2)
                if out and out[-1].strip():
                    out.append("")
                out.append(f'<a id="ref-{n}"></a>')
                out.append("")
            out.append(f"{marker}[{n}] " + line[len(marker) :])
        else:
            out.append(line)
    return "\n".join(out)


def emit_split_markdown(
    doc: DocumentIR,
    *,
    reference_section_titles: list[str],
    linked_citations: bool = False,
    remove_inline_citations: bool = False,
    include_anchors: bool | None = None,
) -> tuple[str, str | None, str | None]:
    """Emit *doc* into main / references / appendix Markdown sidecars.

    The references and appendix sidecars are emitted with ``abstract`` and
    ``front_matter`` emptied so neither is repeated in every sidecar (audit4
    B3: front_matter used to leak into the sidecars). Emission works on
    shallow ``model_copy`` views — the input document is never mutated.

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

    # Sidecar views drop abstract + front matter; the emitter only reads
    # those three fields, so a shallow copy per view is enough.
    content = finalize_markdown(
        emitter.emit(doc.model_copy(update={"sections": main_irs})),
        include_anchors=include_anchors,
    )

    content_references = None
    if ref_irs:
        ref_view = doc.model_copy(update={"sections": ref_irs, "abstract": [], "front_matter": []})
        ref_raw = emitter.emit(ref_view)
        if ref_raw.strip():
            ref_final = finalize_markdown(ref_raw, include_anchors=include_anchors)
            content_references = _number_reference_entries(ref_final, include_anchors=include_anchors)

    content_appendix = None
    if app_irs:
        app_view = doc.model_copy(update={"sections": app_irs, "abstract": [], "front_matter": []})
        app_raw = emitter.emit(app_view)
        content_appendix = finalize_markdown(app_raw, include_anchors=include_anchors) if app_raw.strip() else None

    return content, content_references, content_appendix


def persist_inline_svgs(doc: DocumentIR, output_dir: Path) -> int:
    """Write inline ``<svg>`` figures collected by the HTML builder to disk.

    The builder carries raw SVG markup in :class:`SvgAsset` nodes (``content``)
    instead of doing file I/O; this is the single persistence point. Each SVG
    is rasterized to PNG first (cairosvg, 2x scale) and the document's image
    references repointed — many Markdown viewers cannot render standalone
    ``.svg`` files. Falls back to writing the raw ``.svg`` when conversion is
    unavailable. Returns the number of assets persisted (PNG or SVG).
    """
    from arxiv2md_beta.ir.assets import SvgAsset

    written = 0
    for asset in doc.assets:
        if not isinstance(asset, SvgAsset) or not asset.content:
            continue
        path = output_dir / asset.path
        path.parent.mkdir(parents=True, exist_ok=True)
        png_rel = str(Path(asset.path).with_suffix(".png"))
        png_path = output_dir / png_rel
        if _rasterize_svg(asset.content, png_path):
            _repoint_svg_srcs(doc, asset.path, png_rel)
            written += 1
            continue
        path.write_text(asset.content, encoding="utf-8")
        written += 1
    if written:
        from loguru import logger

        logger.info(f"Persisted {written} inline SVG figure(s) under {output_dir}")
    return written


def _rasterize_svg(svg_content: str, png_path: Path, scale: float = 2.0) -> bool:
    """Render *svg_content* to *png_path*; return True on success.

    LaTeXML panel SVGs embed their labels as HTML/foreignObject (often with
    MathML), which cairosvg cannot draw — it would rasterize only the
    background shapes. Headless Chrome renders the full picture, so it is
    tried first; cairosvg remains the fallback for plain-SVG input and for
    environments without Chrome.
    """
    if _rasterize_svg_chrome(svg_content, png_path, scale):
        return True
    return _rasterize_svg_cairo(svg_content, png_path, scale)


def _rasterize_svg_chrome(svg_content: str, png_path: Path, scale: float) -> bool:
    """Render via headless Chrome/Chromium screenshot; True on success."""
    import re
    import shutil as _shutil
    import subprocess
    import tempfile

    chrome = _shutil.which("google-chrome") or _shutil.which("chromium") or _shutil.which("chromium-browser")
    if not chrome:
        return False
    svg_tag = re.search(r"<svg[^>]*>", svg_content)
    if not svg_tag:
        return False
    # width/height appear in either order (LaTeXML emits height first)
    w = re.search(r'\bwidth="([\d.]+)"', svg_tag.group(0))
    h = re.search(r'\bheight="([\d.]+)"', svg_tag.group(0))
    if not (w and h):
        return False
    width, height = float(w.group(1)), float(h.group(1))
    view_w, view_h = max(1, round(width * scale)), max(1, round(height * scale))
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:#ffffff;}"
        f"svg{{width:{view_w}px !important;height:{view_h}px !important;}}"
        "</style></head><body>" + svg_content + "</body></html>"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="arxiv2md_svg_") as tmp:
            page = Path(tmp) / "page.html"
            page.write_text(html, encoding="utf-8")
            shot = Path(tmp) / "shot.png"
            subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--hide-scrollbars",
                    "--default-background-color=FFFFFFFF",
                    f"--screenshot={shot}",
                    f"--window-size={view_w},{view_h}",
                    page.as_uri(),
                ],
                check=True,
                timeout=60,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if not shot.is_file() or shot.stat().st_size == 0:
                return False
            png_path.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copyfile(shot, png_path)
            return True
    except (subprocess.SubprocessError, OSError) as e:
        from loguru import logger

        logger.debug(f"Chrome SVG rasterization unavailable for {png_path.name}: {e}")
        return False


def _rasterize_svg_cairo(svg_content: str, png_path: Path, scale: float) -> bool:
    """Render via cairosvg; True on success (plain-SVG input only)."""
    if "foreignObject" in svg_content:
        # cairosvg cannot draw foreignObject content (HTML/MathML labels) —
        # it would emit a background-only PNG. Writing the raw SVG (which
        # browsers render fine) is the better fallback.
        return False
    try:
        import cairosvg
    except ImportError:
        from loguru import logger

        logger.debug("cairosvg not installed; keeping inline SVG as-is (pip install cairosvg)")
        return False
    try:
        png_path.parent.mkdir(parents=True, exist_ok=True)
        cairosvg.svg2png(bytestring=svg_content.encode("utf-8"), write_to=str(png_path), scale=scale)
        return True
    except Exception as e:  # noqa: BLE001 — a broken SVG must not abort the conversion
        from loguru import logger

        logger.warning(f"SVG→PNG conversion failed for {png_path.name}: {e}; keeping SVG")
        return False


def _repoint_svg_srcs(doc: DocumentIR, old_src: str, new_src: str) -> None:
    """Rewrite every ``ImageRefIR`` pointing at *old_src* to *new_src*."""
    from arxiv2md_beta.ir.blocks import FigureIR
    from arxiv2md_beta.ir.inlines import ImageRefIR
    from arxiv2md_beta.ir.visitor import iter_block_descendants, iter_inline_lists

    def walk_sections(sections):
        for sec in sections:
            yield from sec.blocks
            yield from iter_block_descendants(sec)
            yield from walk_sections(sec.children or [])

    for blk in walk_sections(doc.sections):
        if not isinstance(blk, FigureIR):
            continue
        for inline_list in iter_inline_lists(blk):
            for il in inline_list:
                if isinstance(il, ImageRefIR) and il.src == old_src:
                    il.src = new_src


def finalize_ingestion_output(
    doc: DocumentIR,
    *,
    arxiv_id: str,
    paper_output_dir: Path,
    paper_yml_data: dict[str, Any],
    version: str | None = None,
    linked_citations: bool = False,
    remove_inline_citations: bool = False,
    structured_output: str = "none",
    emit_graph_csv: bool = False,
    images_subdir: str,
    extra_metadata: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Shared ingestion tail: emit Markdown, build result, write paper.yml + structured JSON.

    This is the single implementation of the finalize steps that the LaTeX,
    local-archive (LaTeX and HTML) and local-HTML ingestion paths share. It
    replaces four copy-pasted variants that had drifted (e.g. the LaTeX paths
    forgot to forward ``remove_inline_citations`` / ``linked_citations``).

    Returns ``(IngestionResult, metadata_dict)``. paper.yml and structured
    export failures are logged and swallowed so they never abort an otherwise
    successful conversion (same policy the call sites previously had).
    """
    from typing import cast

    from arxiv2md_beta.output.markdown_utils import (
        count_sections,
        create_sections_tree,
        format_token_count,
    )
    from arxiv2md_beta.schemas import IngestionResult
    from arxiv2md_beta.settings import get_settings

    reference_section_titles = get_settings().ingestion.reference_section_titles
    content, content_references, content_appendix = emit_split_markdown(
        doc,
        reference_section_titles=reference_section_titles,
        linked_citations=linked_citations,
        remove_inline_citations=remove_inline_citations,
    )

    m = doc.metadata
    result_title = m.title or arxiv_id
    author_names = [a.name for a in m.authors]
    summary_lines: list[str] = []
    if result_title:
        summary_lines.append(f"# Title: {result_title}")
    summary_lines.append(f"- ArXiv: {arxiv_id}")
    if version:
        summary_lines.append(f"- Version: {version}")
    if author_names:
        summary_lines.append(f"- Authors: {', '.join(author_names)}")
    summary_lines.append(f"- Sections: {count_sections(cast('list[Any]', doc.sections))}")
    tree_lines = ["Sections:"]
    if m.abstract_text:
        tree_lines.append("Abstract")
    tree_lines.append(create_sections_tree(cast("list[Any]", doc.sections)))
    sections_tree = "\n".join(tree_lines)
    token_body = "\n".join(x for x in (content, content_references, content_appendix or "") if x)
    token_estimate = format_token_count(sections_tree + "\n" + token_body)
    if token_estimate:
        summary_lines.append(f"- Estimated tokens: {token_estimate}")
    result = IngestionResult(
        summary="\n".join(summary_lines),
        sections_tree=sections_tree,
        content=content,
        content_references=content_references,
        content_appendix=content_appendix,
    )

    # save_paper_metadata is best-effort and swallows+warns internally
    # (output/metadata.py) — no wrapper needed here.
    from arxiv2md_beta.output.metadata import save_paper_metadata

    save_paper_metadata(paper_yml_data, paper_output_dir)

    structured_export = run_structured_export(
        doc,
        paper_output_dir,
        mode=structured_output,
        emit_graph_csv=emit_graph_csv,
        images_subdir=images_subdir,
    )

    metadata: dict[str, Any] = {
        "title": result_title,
        "authors": author_names,
        "abstract": m.abstract_text,
        "paper_output_dir": paper_output_dir,
        "arxiv_id": arxiv_id,
        "structured_export": structured_export,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return result, metadata


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

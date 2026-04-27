"""Convert command runner."""

from __future__ import annotations

import asyncio
from pathlib import Path

from arxiv2md_beta.cli.helpers import collect_sections
from arxiv2md_beta.cli.output_finalize import finalize_convert_output
from arxiv2md_beta.cli.params import ConvertParams
from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.ingestion.local import ingest_local_archive
from arxiv2md_beta.ingestion.local_html import ingest_local_html
from arxiv2md_beta.output.layout import determine_output_dir
from arxiv2md_beta.query.parser import (
    is_local_archive_path,
    is_local_html_path,
    parse_arxiv_input,
    parse_local_archive,
    parse_local_html,
)
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.metrics import async_timed_operation

logger = get_logger()


async def run_convert_flow(params: ConvertParams) -> Path:
    """Route to local HTML, local archive, or arXiv ingestion; returns paper output directory."""
    async with async_timed_operation("run_convert_flow"):
        input_text = params.input_text.strip()
        if not input_text:
            raise ValueError("INPUT cannot be empty")
        if is_local_html_path(input_text):  # Check HTML first (more specific)
            return await _process_local_html(params)
        if is_local_archive_path(input_text):
            return await _process_local_archive(params)
        if params.use_legacy:
            return await _process_arxiv_paper(params)
        return await _process_arxiv_paper_ir(params)


def run_convert_sync(params: ConvertParams) -> None:
    """Run convert flow in a fresh event loop (Typer entry)."""
    asyncio.run(run_convert_flow(params))


async def _process_arxiv_paper(params: ConvertParams) -> Path:
    """Process an arXiv paper (HTML or LaTeX parser)."""
    query = parse_arxiv_input(params.input_text.strip())

    sections = collect_sections(params.sections, params.section)

    base_output_dir = determine_output_dir(params.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing arXiv paper: {query.arxiv_id}")
    logger.info(f"Parser mode: {params.parser}")

    result, metadata = await ingest_paper(
        arxiv_id=query.arxiv_id,
        version=query.version,
        html_url=query.html_url,
        ar5iv_url=query.ar5iv_url,
        parser=params.parser,
        remove_refs=params.remove_refs,
        remove_toc=params.remove_toc,
        remove_inline_citations=params.remove_inline_citations,
        section_filter_mode=params.section_filter_mode,
        sections=sections,
        base_output_dir=base_output_dir,
        no_images=params.no_images,
        source=params.source,
        short=params.short,
        structured_output=params.structured_output,
        emit_graph_csv=params.emit_graph_csv,
        use_cache=not params.no_cache,
    )

    base_id = query.arxiv_id.split("v")[0] if "v" in query.arxiv_id else query.arxiv_id
    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        result_key=query.arxiv_id,
        arxiv_id_for_sidecar=str(metadata.get("arxiv_id") or query.arxiv_id),
        fallback_md_stem=base_id,
        pdf_fetch=(query.arxiv_id, query.version),
        log_local_success=False,
    )


async def _process_arxiv_paper_ir(params: ConvertParams) -> Path:
    """Process an arXiv paper using the IR pipeline with full feature parity."""
    from arxiv2md_beta.html.parser import parse_arxiv_html
    from arxiv2md_beta.html.sections import filter_sections, split_sections_at_reference
    from arxiv2md_beta.images.resolver import process_images
    from arxiv2md_beta.ir import (
        AnchorPass,
        FigureReorderPass,
        HTMLBuilder,
        MarkdownEmitter,
        NumberingPass,
        PassPipeline,
        SectionFilterPass,
    )
    from arxiv2md_beta.latex.tex_source import (
        TexSourceNotFoundError,
        fetch_and_extract_tex_source,
    )
    from arxiv2md_beta.network.arxiv_api import (
        author_display_names_from_metadata,
        fetch_arxiv_metadata,
        fill_arxiv_metadata_defaults,
    )
    from arxiv2md_beta.network.fetch import fetch_arxiv_html
    from arxiv2md_beta.output.formatter import (
        _create_sections_tree,
        _format_markdown_output,
        _format_token_count,
        count_sections,
    )
    from arxiv2md_beta.output.layout import create_paper_output_dir
    from arxiv2md_beta.output.metadata import save_paper_metadata
    from arxiv2md_beta.output.metadata_tex import merge_tex_affiliations_if_configured
    from arxiv2md_beta.settings import get_settings

    query = parse_arxiv_input(params.input_text.strip())
    sections = collect_sections(params.sections, params.section)
    s = get_settings()
    ing = s.ingestion

    base_output_dir = determine_output_dir(params.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing arXiv paper (IR pipeline): {query.arxiv_id}")
    logger.info(f"Parser mode: {params.parser}")

    # 1. Fetch HTML
    html = await fetch_arxiv_html(
        query.html_url,
        arxiv_id=query.arxiv_id,
        version=query.version,
        ar5iv_url=query.ar5iv_url,
        use_cache=not params.no_cache,
    )

    # 2. Parse HTML for metadata and section structure
    parsed = parse_arxiv_html(html)

    # 3. Fetch arXiv API metadata (submission date, author ordering, DOI, etc.)
    api_metadata = await fetch_arxiv_metadata(query.arxiv_id)
    display_author_names = author_display_names_from_metadata(api_metadata) or list(parsed.authors)
    submission_date = api_metadata.get("submission_date") or parsed.submission_date
    if not parsed.title and api_metadata.get("title"):
        parsed.title = api_metadata["title"]

    # 4. Filter sections (apply --section / --sections / --remove-refs)
    filtered_sections = filter_sections(
        parsed.sections, mode=params.section_filter_mode, selected=sections
    )
    if params.remove_refs:
        filtered_sections = filter_sections(
            filtered_sections, mode="exclude", selected=ing.reference_section_titles
        )

    # Check if abstract should be included based on section filter
    abstract_key = ing.abstract_section_title.lower()
    selected_lower = [s.lower() for s in sections]
    if params.section_filter_mode == "exclude":
        include_abstract = abstract_key not in selected_lower
    else:  # include mode
        include_abstract = not sections or abstract_key in selected_lower

    # 5. Create paper-specific output directory
    paper_output_dir = create_paper_output_dir(
        base_output_dir, submission_date, parsed.title,
        source=params.source, short=params.short,
    )
    images_dir_name = s.cli_defaults.images_subdir
    images_dir = paper_output_dir / images_dir_name
    images_dir.mkdir(parents=True, exist_ok=True)

    # 6. Process images from TeX source
    image_map: dict[int, Path] = {}
    image_stem_map: dict[str, Path] = {}
    tex_source_info = None
    if not params.no_images:
        try:
            tex_source_info = await fetch_and_extract_tex_source(
                query.arxiv_id, version=query.version,
                use_cache=not params.no_cache,
            )
            processed_images = process_images(
                tex_source_info, paper_output_dir, images_dir_name,
            )
            image_map = processed_images.image_map
            image_stem_map = processed_images.stem_to_image_path
        except TexSourceNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Failed to process images: {e}")

    # 7. Enrich affiliations from TeX when configured
    if (
        ing.enrich_affiliations_from_tex
        and tex_source_info is None
        and params.no_images
        and ing.fetch_tex_for_affiliations_when_no_images
    ):
        try:
            tex_source_info = await fetch_and_extract_tex_source(
                query.arxiv_id, version=query.version,
                use_cache=not params.no_cache,
            )
        except TexSourceNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"TeX fetch for affiliations failed: {e}")

    # 8. Build IR from HTML (with image maps for local paths)
    builder = HTMLBuilder(image_map=image_map, image_stem_map=image_stem_map)
    doc = builder.build(html, arxiv_id=query.arxiv_id)

    # 8a. Populate doc.assets from image maps for structured export
    if image_map or image_stem_map:
        from arxiv2md_beta.ir.assets import ImageAsset, SvgAsset

        seen_paths: set[str] = set()
        for stem, path in image_stem_map.items():
            try:
                rel = str(path.relative_to(paper_output_dir))
            except ValueError:
                rel = path.as_posix()
            if rel not in seen_paths:
                seen_paths.add(rel)
                ext = path.suffix.lower()
                asset_cls = SvgAsset if ext == ".svg" else ImageAsset
                doc.assets.append(asset_cls(path=rel, tex_stem=stem))
        for idx, path in sorted(image_map.items()):
            try:
                rel = str(path.relative_to(paper_output_dir))
            except ValueError:
                rel = path.as_posix()
            if rel not in seen_paths:
                seen_paths.add(rel)
                ext = path.suffix.lower()
                asset_cls = SvgAsset if ext == ".svg" else ImageAsset
                doc.assets.append(asset_cls(path=rel, figure_index=idx))

    # 8b. Enrich doc.metadata with API data not available to the builder
    if submission_date:
        doc.metadata.submission_date = submission_date
    if display_author_names:
        doc.metadata.authors = list(display_author_names)
    if not doc.metadata.title and parsed.title:
        doc.metadata.title = parsed.title

    # 9. Run transform passes
    pipeline = PassPipeline()
    pipeline.add(NumberingPass())
    pipeline.add(FigureReorderPass())
    if sections:
        pipeline.add(SectionFilterPass(
            mode=params.section_filter_mode, selected=sections,
        ))
    pipeline.add(AnchorPass())
    doc = pipeline.run(doc)

    # 10. Normalize abstract: strip redundant heading, honour include/exclude
    _strip_abstract_heading(doc)
    if not include_abstract:
        doc.abstract = []

    # 11. Split IR sections for reference/appendix sidecars
    main_irs, ref_irs, app_irs = _split_ir_sections(
        doc.sections, ing.reference_section_titles,
    )

    # 12. Emit markdown for each part
    emitter = MarkdownEmitter()
    original_sections = doc.sections
    original_abstract = doc.abstract

    doc.sections = main_irs
    content = _format_markdown_output(emitter.emit(doc))

    # References and appendix sidecars should not repeat the abstract
    doc.abstract = []
    doc.sections = ref_irs
    ref_raw = emitter.emit(doc) if ref_irs else ""
    content_references = _format_markdown_output(ref_raw) if ref_raw.strip() else None

    doc.sections = app_irs
    app_raw = emitter.emit(doc) if app_irs else ""
    content_appendix = _format_markdown_output(app_raw) if app_raw.strip() else None

    doc.sections = original_sections
    doc.abstract = original_abstract

    # 13. Build summary (matching format_paper output style)
    m = doc.metadata
    title = m.title or parsed.title
    summary_lines = []
    if title:
        summary_lines.append(f"# Title: {title}")
    summary_lines.append(f"- ArXiv: {query.arxiv_id}")
    if query.version:
        summary_lines.append(f"- Version: {query.version}")
    if display_author_names:
        summary_lines.append(f"- Authors: {', '.join(display_author_names)}")
    summary_lines.append(f"- Sections: {count_sections(filtered_sections)}")
    token_body = "\n".join(
        x for x in (content, content_references, content_appendix or "") if x
    )
    token_estimate = _format_token_count(
        _create_sections_tree(filtered_sections) + "\n" + token_body
    )
    if token_estimate:
        summary_lines.append(f"- Estimated tokens: {token_estimate}")
    summary = "\n".join(summary_lines)

    # 14. Build recursive sections tree
    tree_lines = ["Sections:"]
    if include_abstract and parsed.abstract:
        tree_lines.append("Abstract")
    tree_lines.append(_create_sections_tree(filtered_sections))
    sections_tree = "\n".join(tree_lines)

    result = IngestionResult(
        summary=summary,
        sections_tree=sections_tree,
        content=content,
        content_references=content_references,
        content_appendix=content_appendix,
    )

    # 15. Save paper.yml metadata
    try:
        base_id = query.arxiv_id.split("v")[0] if "v" in query.arxiv_id else query.arxiv_id
        paper_meta = dict(api_metadata)
        if not paper_meta.get("title") and parsed.title:
            paper_meta["title"] = parsed.title
        if not paper_meta.get("summary") and parsed.abstract:
            paper_meta["summary"] = parsed.abstract
        if not paper_meta.get("authors") and parsed.authors:
            paper_meta["authors"] = [{"name": a} for a in parsed.authors if a]
        paper_meta = fill_arxiv_metadata_defaults(paper_meta, base_id)
        merge_tex_affiliations_if_configured(paper_meta, tex_source_info)
        save_paper_metadata(paper_meta, paper_output_dir)
    except Exception as e:
        logger.warning(f"Failed to save paper.yml: {e}")

    # 16. Structured JSON export via JsonEmitter (IR-native)
    structured_export: dict = {}
    try:
        from arxiv2md_beta.ir.emitters.json_emitter import (
            JsonEmitter,
            normalize_structured_mode,
        )
        sm = normalize_structured_mode(params.structured_output)
        if sm != "none":
            json_emitter = JsonEmitter(mode=sm)
            structured_export = json_emitter.write_bundle(
                doc,
                paper_output_dir,
                images_subdir=images_dir_name,
                emit_graph_csv=params.emit_graph_csv,
            )
    except Exception as e:
        logger.warning(f"Structured JSON export failed: {e}")

    metadata: dict = {
        "title": title,
        "authors": display_author_names,
        "abstract": parsed.abstract,
        "submission_date": submission_date,
        "paper_output_dir": paper_output_dir,
        "arxiv_id": query.arxiv_id,
        "structured_export": structured_export,
    }

    base_id = query.arxiv_id.split("v")[0] if "v" in query.arxiv_id else query.arxiv_id
    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        result_key=query.arxiv_id,
        arxiv_id_for_sidecar=str(metadata.get("arxiv_id") or query.arxiv_id),
        fallback_md_stem=base_id,
        pdf_fetch=(query.arxiv_id, query.version),
        log_local_success=False,
    )


def _split_ir_sections(
    sections: list,
    reference_titles: list[str],
) -> tuple[list, list, list]:
    """Split IR sections into (main, references, appendix) for sidecar output."""
    ref_set = {t.strip().lower() for t in reference_titles if t and t.strip()}
    if not ref_set:
        return list(sections), [], []

    first_ref_idx: int | None = None
    first_app_idx: int | None = None
    for i, sec in enumerate(sections):
        n = (sec.title or "").strip().lower()
        if first_ref_idx is None and n in ref_set:
            first_ref_idx = i
        if first_app_idx is None and n.startswith("appendix"):
            first_app_idx = i

    if first_ref_idx is not None:
        return (
            sections[:first_ref_idx],
            [sections[first_ref_idx]],
            sections[first_ref_idx + 1:],
        )
    if first_app_idx is not None:
        return sections[:first_app_idx], [], sections[first_app_idx:]

    return list(sections), [], []


def _strip_abstract_heading(doc) -> None:
    """Remove the redundant ``Abstract`` heading block from ``doc.abstract``.

    arXiv HTML abstracts often contain ``<h6>Abstract</h6>`` which the HTML
    builder converts to a ``HeadingIR``. The emitter already renders its own
    ``## Abstract`` heading, so this duplicate is removed in-place.
    """
    from arxiv2md_beta.ir.blocks import HeadingIR

    if not doc.abstract:
        return
    keep = []
    for blk in doc.abstract:
        if hasattr(blk, "type") and blk.type == "heading":
            text = " ".join(
                il.text for il in (getattr(blk, "inlines", []) or [])
                if hasattr(il, "text")
            ).strip().lower()
            if text in ("abstract",):
                continue
        keep.append(blk)
    doc.abstract = keep


async def _process_local_html(params: ConvertParams) -> Path:
    """Process a local HTML file."""
    query = parse_local_html(params.input_text.strip())

    sections = collect_sections(params.sections, params.section)

    base_output_dir = determine_output_dir(params.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing local HTML file: {query.html_path}")

    result, metadata = await ingest_local_html(
        query=query,
        base_output_dir=base_output_dir,
        source=params.source,
        short=params.short,
        no_images=params.no_images,
        remove_refs=params.remove_refs,
        remove_toc=params.remove_toc,
        remove_inline_citations=params.remove_inline_citations,
        section_filter_mode=params.section_filter_mode,
        sections=sections,
        structured_output=params.structured_output,
        emit_graph_csv=params.emit_graph_csv,
    )

    rk = str(metadata.get("arxiv_id") or query.html_path.stem)
    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        result_key=query.html_path.stem,
        arxiv_id_for_sidecar=rk,
        fallback_md_stem=query.html_path.stem,
        pdf_fetch=None,
        log_local_success=True,
    )


async def _process_local_archive(params: ConvertParams) -> Path:
    """Process a local archive file (tar.gz, tgz, or zip)."""
    query = parse_local_archive(params.input_text.strip())

    sections = collect_sections(params.sections, params.section)

    base_output_dir = determine_output_dir(params.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing local archive: {query.archive_path}")
    logger.info(f"Archive type: {query.archive_type}")

    result, metadata = await ingest_local_archive(
        query=query,
        base_output_dir=base_output_dir,
        source=params.source,
        short=params.short,
        no_images=params.no_images,
        remove_refs=params.remove_refs,
        remove_toc=params.remove_toc,
        remove_inline_citations=params.remove_inline_citations,
        section_filter_mode=params.section_filter_mode,
        sections=sections,
        structured_output=params.structured_output,
        emit_graph_csv=params.emit_graph_csv,
    )

    rk = str(metadata.get("arxiv_id") or query.archive_path.stem)
    return await finalize_convert_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        result_key=query.archive_path.stem,
        arxiv_id_for_sidecar=rk,
        fallback_md_stem=query.archive_path.stem,
        pdf_fetch=None,
        log_local_success=True,
    )

"""LaTeX ingestion pipeline for arXiv LaTeX -> Markdown with image support."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from arxiv2md_beta.exceptions import ParseError, ParserNotAvailableError
from arxiv2md_beta.images.processor import process_images_async
from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.latex.tex_source import TexSourceNotFoundError, fetch_and_extract_tex_source
from arxiv2md_beta.network.arxiv_api import author_display_names_from_metadata, fetch_arxiv_metadata
from arxiv2md_beta.output.metadata_tex import merge_tex_affiliations_if_configured
from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.timing import async_timed_operation

logger = get_logger()


async def ingest_paper(
    params: ConvertParams,
    query: Any,
    sections: list[str],
    base_output_dir: Path,
) -> tuple[IngestionResult, dict[str, Any]]:
    """Fetch, parse, and serialize an arXiv paper from LaTeX source into Markdown.

    Unified entry for the remote-LaTeX path (the CLI ``--parser latex`` mode).
    All flags travel on *params*; nothing is read back from mutated global
    settings.
    """
    arxiv_id = query.arxiv_id
    version = query.version
    async with async_timed_operation(f"ingest_paper_latex({arxiv_id})"):
        # Fetch metadata from API (optional; disabled by default to avoid slow
        # retry chains when arXiv API / OpenAlex / Crossref are unreachable)
        api_metadata: dict[str, Any] = {}
        if params.fetch_metadata or get_settings().ingestion.fetch_arxiv_metadata:
            try:
                api_metadata = await fetch_arxiv_metadata(arxiv_id)
            except Exception as exc:  # noqa: BLE001 - metadata is enrichment only
                logger.warning(f"arXiv API metadata fetch failed for {arxiv_id}; using fallbacks: {exc}")
        fallback_title = get_settings().ingestion.latex_fallback_title
        title = api_metadata.get("title") or fallback_title
        submission_date = api_metadata.get("submission_date")

        # Create paper-specific output directory
        from arxiv2md_beta.output.layout import create_paper_output_dir

        paper_output_dir = create_paper_output_dir(
            base_output_dir,
            cast("str | None", submission_date),
            cast("str | None", title),
            source=params.source,
            short=params.short,
            identity=arxiv_id,
        )
        images_dir_name = get_settings().cli_defaults.images_subdir

        # Fetch and extract TeX source
        tex_source_info = await fetch_and_extract_tex_source(arxiv_id, version=version, use_cache=not params.no_cache)

        if not tex_source_info.main_tex_file:
            raise TexSourceNotFoundError(f"No main LaTeX file found for {arxiv_id}")

        # Process images if enabled
        processed_images = None
        if not params.no_images:
            processed_images = await process_images_async(
                tex_source_info,
                paper_output_dir,
                images_dir_name,
                disable_tqdm=params.no_progress or None,
                max_concurrency=params.concurrency,
            )

        # Build image map from LaTeX labels/paths to local paths
        from arxiv2md_beta.images.processor import build_latex_image_label_map

        latex_image_map = build_latex_image_label_map(tex_source_info, processed_images)

        # Build IR from LaTeX via Pandoc AST (offload blocking pandoc call to thread).
        # Replaces the legacy parse_latex_to_markdown + format_paper + latex/structured
        # chain with the unified IR pipeline (LaTeXBuilder → PassPipeline → MarkdownEmitter
        # + JsonEmitter), matching the HTML IR orchestrator.
        display_author_names = author_display_names_from_metadata(api_metadata)
        abstract_text = cast("str | None", api_metadata.get("summary"))

        def _build_latex_ir() -> DocumentIR:
            from arxiv2md_beta.ingestion._builders import build_latex_document
            from arxiv2md_beta.latex.includes import resolve_latex_includes

            main_tex = tex_source_info.main_tex_file
            assert main_tex is not None  # checked above; narrow for type-checker
            tex_content = resolve_latex_includes(
                main_tex,
                tex_source_info.extracted_dir,
            )
            return build_latex_document(
                tex_content,
                arxiv_id=arxiv_id,
                image_path_map=latex_image_map,
                base_dir=tex_source_info.extracted_dir,
                title=title,
                authors=display_author_names or None,
                abstract=abstract_text,
                images_subdir=images_dir_name,
                section_filter_mode=params.section_filter_mode,
                sections=sections,
                remove_refs=params.remove_refs,
            )

        try:
            doc = await asyncio.to_thread(_build_latex_ir)
        except ParserNotAvailableError:
            raise
        except Exception as e:
            raise ParseError(f"Failed to parse LaTeX: {e}") from e

        # Shared finalize tail: split Markdown emission + paper.yml + structured export.
        from arxiv2md_beta.ingestion.ir_finalize import finalize_ingestion_output

        merge_tex_affiliations_if_configured(api_metadata, tex_source_info)
        # Same off-loop treatment as the local-archive paths (markdown emission,
        # YAML dump and JSON bundle are CPU/IO-bound).
        result, metadata = await asyncio.to_thread(
            finalize_ingestion_output,
            doc,
            arxiv_id=arxiv_id,
            version=version,
            paper_output_dir=paper_output_dir,
            paper_yml_data=dict(api_metadata),
            linked_citations=params.linked_citations,
            remove_inline_citations=params.remove_inline_citations,
            structured_output=params.structured_output,
            emit_graph_csv=params.emit_graph_csv,
            images_subdir=images_dir_name,
            extra_metadata={"submission_date": submission_date},
            include_anchors=params.include_anchors,
        )
        return result, metadata

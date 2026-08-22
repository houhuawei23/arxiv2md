"""Ingestion orchestrator for the IR pipeline.

Extracts the monolithic ``_process_arxiv_paper_ir()`` flow into a stateful
class with discrete, testable steps.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from arxiv2md_beta.exceptions import NetworkError
from arxiv2md_beta.html.parser import ParsedArxivHtml, parse_arxiv_html
from arxiv2md_beta.html.sections import filter_sections
from arxiv2md_beta.images.processor import process_images_async
from arxiv2md_beta.ingestion.ir_finalize import emit_split_markdown, run_structured_export
from arxiv2md_beta.ir import HTMLBuilder
from arxiv2md_beta.ir.document import AuthorIR, DocumentIR
from arxiv2md_beta.ir.resolvers import ImageResolver
from arxiv2md_beta.ir.transforms import build_default_pipeline
from arxiv2md_beta.latex.tex_source import (
    TexSourceInfo,
    TexSourceNotFoundError,
    fetch_and_extract_tex_source,
)
from arxiv2md_beta.network.arxiv_api import (
    author_display_names_from_metadata,
    fetch_arxiv_metadata,
    fill_arxiv_metadata_defaults,
)
from arxiv2md_beta.network.fetch import fetch_arxiv_html
from arxiv2md_beta.output.layout import create_paper_output_dir, determine_output_dir
from arxiv2md_beta.output.markdown_utils import (
    count_sections,
    create_sections_tree,
    format_token_count,
)
from arxiv2md_beta.output.metadata import save_paper_metadata
from arxiv2md_beta.output.metadata_tex import merge_tex_affiliations_if_configured
from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.settings.schema import AppSettings
from arxiv2md_beta.utils.arxiv_ids import strip_version
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()


class IngestionOrchestrator:
    """Orchestrate the full IR-pipeline ingestion flow for an arXiv paper.

    Usage::

        orch = IngestionOrchestrator(params)
        result, metadata = await orch.run()
    """

    def __init__(self, params: ConvertParams, settings: AppSettings | None = None) -> None:
        """Optionally inject *settings* for tests; defaults to the process global."""
        self.params = params
        self._settings = settings or get_settings()
        self._ingestion_cfg = self._settings.ingestion

        # Mutable pipeline state
        self._html: str = ""
        self._html_error: str | None = None
        self._parsed: ParsedArxivHtml | None = None
        self._api_metadata: dict[str, Any] = {}
        self._display_author_names: list[str] = []
        self._submission_date: str | None = None
        self._tex_source_info: TexSourceInfo | None = None
        self._image_resolver: ImageResolver | None = None
        self._doc: DocumentIR | None = None
        self._paper_output_dir: Path | None = None
        self._images_dir_name: str = self._settings.cli_defaults.images_subdir
        self._images_dir: Path | None = None

        # Section-filter state
        self._selected_sections: list[str] = []
        self._filtered_sections: list[Any] = []
        self._include_abstract: bool = True

        # Markdown emission results
        self._content: str = ""
        self._content_references: str | None = None
        self._content_appendix: str | None = None

    # ── Public entry point ─────────────────────────────────────────────

    async def run(self) -> tuple[IngestionResult, dict[str, Any]]:
        """Execute the full pipeline and return (result, metadata)."""
        self._parse_query()
        # HTML 与 API 元数据相互独立，并行获取以减少网络等待
        await self._fetch_html_and_metadata()
        if self._parsed is None:
            # PDF-only paper (no HTML rendering anywhere): still produce the
            # output directory, paper.yml, a stub paper.md, and let finalize
            # download the PDF — a minimal record beats aborting with nothing.
            return self._run_pdf_only_fallback()
        self._filter_sections()
        self._setup_output_dir()
        await self._fetch_tex_and_images()
        # CPU-bound steps (BS4 parse, IR build, transform pipeline, emission) are
        # offloaded so the event loop can advance other papers in batch mode.
        await asyncio.to_thread(self._build_ir)
        await asyncio.to_thread(self._enrich_metadata)
        await asyncio.to_thread(self._run_transforms)
        await asyncio.to_thread(self._normalize_abstract)
        await asyncio.to_thread(self._emit_markdown)
        result = self._build_result()
        await self._save_paper_yml()
        structured_export = await self._structured_export()
        metadata = self._build_metadata(structured_export)
        return result, metadata

    # ── Step 0: Parse query ────────────────────────────────────────────

    def _run_pdf_only_fallback(self) -> tuple[IngestionResult, dict[str, Any]]:
        """Minimal-output path for papers without HTML rendering.

        Creates the output directory (named from API metadata), saves
        ``paper.yml``, and returns a stub result so ``finalize_convert_output``
        writes ``paper.md`` and downloads the PDF. Content conversion is
        impossible — there is nothing to parse.
        """
        self._submission_date = self._api_metadata.get("submission_date")
        self._display_author_names = author_display_names_from_metadata(self._api_metadata)
        title = self._api_metadata.get("title") or strip_version(self._query.arxiv_id)

        base_output_dir = determine_output_dir(self.params.output)
        base_output_dir.mkdir(parents=True, exist_ok=True)
        self._paper_output_dir = create_paper_output_dir(
            base_output_dir,
            self._submission_date,
            title,
            source=self.params.source,
            short=self.params.short,
        )

        summary_lines = [f"# Title: {title}", f"- ArXiv: {self._query.arxiv_id}"]
        if self._query.version:
            summary_lines.append(f"- Version: {self._query.version}")
        if self._display_author_names:
            summary_lines.append("- Authors:")
            summary_lines.extend(f"  - {name}" for name in self._display_author_names)
        summary_lines.append("- Sections: 0")
        summary_lines.append("- Estimated tokens: 1")
        summary = "\n".join(summary_lines)

        note = (
            "No HTML rendering or TeX source available for this paper "
            "(likely a PDF-only submission). Only metadata and the PDF were saved.\n"
        )
        result = IngestionResult(
            summary=summary,
            sections_tree="Sections:",
            content=note,
        )

        paper_meta = dict(self._api_metadata)
        paper_meta.setdefault("title", title)
        paper_meta = fill_arxiv_metadata_defaults(paper_meta, strip_version(self._query.arxiv_id))
        try:
            save_paper_metadata(paper_meta, self._paper_output_dir)
        except (OSError, ValueError, TypeError) as e:
            logger.warning(f"Failed to save paper.yml: {e}")

        metadata: dict[str, Any] = {
            "title": title,
            "authors": self._display_author_names,
            "abstract": self._api_metadata.get("summary"),
            "submission_date": self._submission_date,
            "paper_output_dir": self._paper_output_dir,
            "arxiv_id": self._query.arxiv_id,
            "structured_export": {},
        }
        return result, metadata

    def _parse_query(self) -> None:
        from arxiv2md_beta.query.parser import parse_arxiv_input

        self._query = parse_arxiv_input(self.params.input_text.strip())

    # ── Step 1: Fetch HTML ─────────────────────────────────────────────

    async def _fetch_html(self) -> None:
        self._html = await fetch_arxiv_html(
            self._query.html_url,
            arxiv_id=self._query.arxiv_id,
            version=self._query.version,
            ar5iv_url=self._query.ar5iv_url,
            use_cache=not self.params.no_cache,
        )

    # ── Step 2: Parse HTML ─────────────────────────────────────────────

    def _parse_html(self) -> None:
        self._parsed = parse_arxiv_html(self._html)

    # ── Step 3: Fetch API metadata ─────────────────────────────────────

    async def _fetch_api_metadata(self) -> None:
        self._api_metadata = await fetch_arxiv_metadata(self._query.arxiv_id)

    async def _fetch_html_and_metadata(self) -> None:
        """并行下载 HTML 与获取 API 元数据；HTML 解析后合并作者/日期信息。

        HTML 拉取失败（无 HTML 渲染的 PDF-only 论文）不终止流程：
        ``self._parsed`` 留空，后续走 ``_run_pdf_only_fallback`` 最小产物路径。
        """  # noqa: D415
        html_task = asyncio.create_task(self._fetch_html())
        metadata_task = asyncio.create_task(self._fetch_api_metadata())
        try:
            await metadata_task
        except asyncio.CancelledError:
            html_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await html_task
            raise
        except Exception as exc:
            # Metadata is an enrichment, not a requirement: degrade to
            # HTML-parsed authors/date instead of failing the whole conversion.
            logger.warning(f"arXiv API metadata fetch failed; falling back to HTML metadata: {exc}")
            self._api_metadata = {}
        try:
            await html_task
        except NetworkError as exc:
            logger.warning(f"No usable HTML rendering for {self._query.arxiv_id}: {exc}")
            logger.warning("Falling back to PDF-only minimal output (paper.yml + stub paper.md + PDF).")
            self._html_error = str(exc)
            return
        await asyncio.to_thread(self._parse_html)

        self._display_author_names = author_display_names_from_metadata(self._api_metadata)
        if not self._display_author_names and self._parsed is not None:
            self._display_author_names = [a.name for a in self._parsed.authors]

        self._submission_date = self._api_metadata.get("submission_date")
        if not self._submission_date and self._parsed is not None:
            self._submission_date = self._parsed.submission_date

        if self._parsed is not None and not self._parsed.title and self._api_metadata.get("title"):
            self._parsed.title = self._api_metadata["title"]

    # ── Step 4: Filter sections ────────────────────────────────────────

    def _filter_sections(self) -> None:
        from arxiv2md_beta.cli.helpers import collect_sections

        assert self._parsed is not None
        self._selected_sections = collect_sections(self.params.sections, self.params.section)
        self._filtered_sections = filter_sections(
            self._parsed.sections,
            mode=self.params.section_filter_mode,
            selected=self._selected_sections,
        )
        if self.params.remove_refs:
            self._filtered_sections = filter_sections(
                self._filtered_sections,
                mode="exclude",
                selected=self._ingestion_cfg.reference_section_titles,
            )

        # Determine whether abstract should be included
        abstract_key = self._ingestion_cfg.abstract_section_title.lower()
        selected_lower = [s.lower() for s in self._selected_sections]
        if self.params.section_filter_mode == "exclude":
            self._include_abstract = abstract_key not in selected_lower
        else:
            self._include_abstract = not self._selected_sections or abstract_key in selected_lower

    # ── Step 5: Setup output directory ─────────────────────────────────

    def _setup_output_dir(self) -> None:
        from arxiv2md_beta.output.layout import determine_output_dir

        assert self._parsed is not None

        base_output_dir = determine_output_dir(self.params.output)
        base_output_dir.mkdir(parents=True, exist_ok=True)
        self._paper_output_dir = create_paper_output_dir(
            base_output_dir,
            self._submission_date,
            self._parsed.title,
            source=self.params.source,
            short=self.params.short,
        )
        self._images_dir = self._paper_output_dir / self._images_dir_name
        self._images_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 6: Fetch TeX source and process images ────────────────────

    async def _fetch_tex_and_images(self) -> None:
        assert self._paper_output_dir is not None
        image_map: dict[int, Path] = {}
        image_stem_map: dict[str, Path] = {}

        if not self.params.no_images:
            try:
                self._tex_source_info = await fetch_and_extract_tex_source(
                    self._query.arxiv_id,
                    version=self._query.version,
                    use_cache=not self.params.no_cache,
                )
                # 使用异步并行图像处理（CPU-bound 任务卸载到进程池）
                processed = await process_images_async(
                    self._tex_source_info,
                    self._paper_output_dir,
                    self._images_dir_name,
                )
                image_map = processed.image_map
                image_stem_map = processed.stem_to_image_path
            except TexSourceNotFoundError:
                pass
            except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired) as e:
                logger.warning(f"Failed to process images: {e}")

        # Affiliation-only TeX fetch
        if (
            self._ingestion_cfg.enrich_affiliations_from_tex
            and self._tex_source_info is None
            and self.params.no_images
            and self._ingestion_cfg.fetch_tex_for_affiliations_when_no_images
        ):
            try:
                self._tex_source_info = await fetch_and_extract_tex_source(
                    self._query.arxiv_id,
                    version=self._query.version,
                    use_cache=not self.params.no_cache,
                )
            except TexSourceNotFoundError:
                pass
            except (OSError, ValueError, TypeError, RuntimeError) as e:
                logger.warning(f"TeX fetch for affiliations failed: {e}")

        self._image_resolver = ImageResolver(
            index_map=image_map,
            stem_map=image_stem_map,
        )

    # ── Step 7: Build IR ───────────────────────────────────────────────

    def _build_ir(self) -> None:
        assert self._parsed is not None
        assert self._paper_output_dir is not None
        builder = HTMLBuilder(
            image_resolver=self._image_resolver,
            images_subdir=self._images_dir_name,
        )
        # 直接复用 parse_arxiv_html 的解析结果，避免 builder 再次解析完整 HTML
        self._doc = builder.build(self._parsed, arxiv_id=self._query.arxiv_id)
        self._populate_assets()
        # Inline <svg> figures collected by the builder are persisted here
        # (the builder itself performs no file I/O).
        from arxiv2md_beta.ingestion.ir_finalize import persist_inline_svgs

        persist_inline_svgs(self._doc, self._paper_output_dir)

    # ── Step 7a: Populate assets ───────────────────────────────────────

    def _populate_assets(self) -> None:
        if self._image_resolver is None:
            return
        assert self._doc is not None
        assert self._paper_output_dir is not None
        from arxiv2md_beta.ir.assets import ImageAsset, SvgAsset

        seen_paths: set[str] = set()
        for key, path in self._image_resolver.iter_assets():
            try:
                rel = str(path.relative_to(self._paper_output_dir))
            except ValueError:
                rel = path.as_posix()
            if rel in seen_paths:
                continue
            seen_paths.add(rel)
            ext = path.suffix.lower()
            asset_cls = SvgAsset if ext == ".svg" else ImageAsset
            if isinstance(key, int):
                self._doc.assets.append(asset_cls(path=rel, figure_index=key))
            else:
                self._doc.assets.append(asset_cls(path=rel, tex_stem=key))

    # ── Step 8: Enrich metadata ────────────────────────────────────────

    def _enrich_metadata(self) -> None:
        assert self._doc is not None
        assert self._parsed is not None
        if self._submission_date:
            self._doc.metadata.submission_date = self._submission_date

        if self._display_author_names:
            self._merge_affiliations()

        if not self._doc.metadata.title and self._parsed.title:
            self._doc.metadata.title = self._parsed.title

    def _merge_affiliations(self) -> None:
        """Merge API + HTML + TeX affiliations into doc.metadata.authors."""
        assert self._doc is not None
        assert self._parsed is not None

        def _norm(s: str) -> str:
            return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower().strip()

        # API affiliations (preferred)
        api_affil_map: dict[str, list[str]] = {}
        for a in self._api_metadata.get("authors", []):
            if isinstance(a, dict) and a.get("name"):
                affils = a.get("affiliations", [])
                if not affils and a.get("affiliation"):
                    affils = [x.strip() for x in a["affiliation"].split(";") if x.strip()]
                api_affil_map[_norm(a["name"])] = affils

        # HTML-parsed affiliations (fallback)
        html_affil_map: dict[str, list[str]] = {}
        for a in self._parsed.authors:
            html_affil_map[_norm(a.name)] = a.affiliations

        # Merge: API first, HTML as supplement
        merged: dict[str, list[str]] = {}
        for key, affils in api_affil_map.items():
            merged[key] = list(affils)
        for key, affils in html_affil_map.items():
            if key not in merged:
                merged[key] = affils

        self._doc.metadata.authors = [
            AuthorIR(
                name=n,
                affiliations=merged.get(_norm(n), []),
            )
            for n in self._display_author_names
        ]

    # ── Step 9: Run transform passes ───────────────────────────────────

    def _run_transforms(self) -> None:
        assert self._doc is not None
        pipeline = build_default_pipeline(
            parser="html",
            section_filter_mode=self.params.section_filter_mode,
            selected_sections=self._selected_sections,
            remove_refs=self.params.remove_refs,
            reference_section_titles=self._ingestion_cfg.reference_section_titles,
        )
        self._doc = pipeline.run(self._doc)

    # ── Step 10: Normalize abstract ────────────────────────────────────

    def _normalize_abstract(self) -> None:
        assert self._doc is not None
        _strip_abstract_heading(self._doc)
        if not self._include_abstract:
            self._doc.abstract = []

    # ── Step 11: Emit markdown ─────────────────────────────────────────

    def _emit_markdown(self) -> None:
        assert self._doc is not None
        self._content, self._content_references, self._content_appendix = emit_split_markdown(
            self._doc,
            reference_section_titles=self._ingestion_cfg.reference_section_titles,
            linked_citations=self.params.linked_citations,
            remove_inline_citations=self.params.remove_inline_citations,
        )

    # ── Step 12: Build result ──────────────────────────────────────────

    def _build_result(self) -> IngestionResult:
        assert self._doc is not None
        assert self._parsed is not None
        m = self._doc.metadata
        title = m.title or self._parsed.title

        # Summary
        summary_lines = []
        if title:
            summary_lines.append(f"# Title: {title}")
        summary_lines.append(f"- ArXiv: {self._query.arxiv_id}")
        if self._query.version:
            summary_lines.append(f"- Version: {self._query.version}")
        if self._display_author_names:
            summary_lines.append("- Authors:")
            for author in self._doc.metadata.authors:
                name = author.name
                affils = ", ".join(author.affiliations) if author.affiliations else ""
                if affils:
                    summary_lines.append(f"  - {name} — {affils}")
                else:
                    summary_lines.append(f"  - {name}")
        summary_lines.append(f"- Sections: {count_sections(self._filtered_sections)}")
        token_body = "\n".join(x for x in (self._content, self._content_references, self._content_appendix or "") if x)
        token_estimate = format_token_count(create_sections_tree(self._filtered_sections) + "\n" + token_body)
        if token_estimate:
            summary_lines.append(f"- Estimated tokens: {token_estimate}")
        summary = "\n".join(summary_lines)

        # Sections tree
        tree_lines = ["Sections:"]
        if self._include_abstract and self._parsed.abstract:
            tree_lines.append("Abstract")
        tree_lines.append(create_sections_tree(self._filtered_sections))
        sections_tree = "\n".join(tree_lines)

        return IngestionResult(
            summary=summary,
            sections_tree=sections_tree,
            content=self._content,
            content_references=self._content_references,
            content_appendix=self._content_appendix,
        )

    # ── Step 13: Save paper.yml ────────────────────────────────────────

    async def _save_paper_yml(self) -> None:
        try:
            assert self._parsed is not None
            assert self._paper_output_dir is not None
            base_id = strip_version(self._query.arxiv_id)
            paper_meta = dict(self._api_metadata)
            if not paper_meta.get("title") and self._parsed.title:
                paper_meta["title"] = self._parsed.title
            if not paper_meta.get("summary") and self._parsed.abstract:
                paper_meta["summary"] = self._parsed.abstract
            if self._parsed.authors:
                html_affil_map: dict[str, list[str]] = {}
                for a in self._parsed.authors:
                    html_affil_map[a.name.lower().strip()] = a.affiliations
                if paper_meta.get("authors"):
                    for pa in paper_meta["authors"]:
                        if isinstance(pa, dict) and "name" in pa and not pa.get("affiliations"):
                            affs = html_affil_map.get(pa["name"].lower().strip(), [])
                            if affs:
                                pa["affiliations"] = affs
                else:
                    paper_meta["authors"] = [
                        {"name": a.name, "affiliations": a.affiliations} for a in self._parsed.authors if a.name
                    ]
            paper_meta = fill_arxiv_metadata_defaults(paper_meta, base_id)
            merge_tex_affiliations_if_configured(paper_meta, self._tex_source_info)
            await asyncio.to_thread(save_paper_metadata, paper_meta, self._paper_output_dir)
        except (OSError, ValueError, TypeError) as e:
            logger.warning(f"Failed to save paper.yml: {e}")

    # ── Step 14: Structured JSON export ────────────────────────────────

    async def _structured_export(self) -> dict:
        assert self._doc is not None
        assert self._paper_output_dir is not None
        return run_structured_export(
            self._doc,
            self._paper_output_dir,
            mode=self.params.structured_output,
            emit_graph_csv=self.params.emit_graph_csv,
            images_subdir=self._images_dir_name,
        )

    # ── Step 15: Build metadata dict ───────────────────────────────────

    def _build_metadata(self, structured_export: dict) -> dict[str, Any]:
        assert self._doc is not None
        assert self._parsed is not None
        title = self._doc.metadata.title or self._parsed.title
        return {
            "title": title,
            "authors": self._display_author_names,
            "abstract": self._parsed.abstract,
            "submission_date": self._submission_date,
            "paper_output_dir": self._paper_output_dir,
            "arxiv_id": self._query.arxiv_id,
            "structured_export": structured_export,
        }


# ── Helper functions (moved from convert.py) ─────────────────────────


def _strip_abstract_heading(doc: DocumentIR) -> None:
    """Remove the redundant ``Abstract`` heading block from ``doc.abstract``.

    arXiv HTML abstracts often contain ``<h6>Abstract</h6>`` which the HTML
    builder converts to a ``HeadingIR``. The emitter already renders its own
    ``## Abstract`` heading, so this duplicate is removed in-place.
    """
    if not doc.abstract:
        return
    keep = []
    for blk in doc.abstract:
        if hasattr(blk, "type") and blk.type == "heading":
            text = (
                " ".join(il.text for il in (getattr(blk, "inlines", []) or []) if hasattr(il, "text")).strip().lower()
            )
            if text in ("abstract",):
                continue
        keep.append(blk)
    doc.abstract = keep

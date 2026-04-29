"""Ingestion orchestrator for the IR pipeline.

Extracts the monolithic ``_process_arxiv_paper_ir()`` flow into a stateful
class with discrete, testable steps.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

from arxiv2md_beta.cli.params import ConvertParams
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
from arxiv2md_beta.ir.document import AuthorIR, DocumentIR
from arxiv2md_beta.ir.resolvers import ImageResolver
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
from arxiv2md_beta.schemas import IngestionResult
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()


class IngestionOrchestrator:
    """Orchestrate the full IR-pipeline ingestion flow for an arXiv paper.

    Usage::

        orch = IngestionOrchestrator(params)
        result, metadata = await orch.run()
    """

    def __init__(self, params: ConvertParams) -> None:
        self.params = params
        self._settings = get_settings()
        self._ingestion_cfg = self._settings.ingestion

        # Mutable pipeline state
        self._html: str = ""
        self._parsed: Any | None = None
        self._api_metadata: dict[str, Any] = {}
        self._display_author_names: list[str] = []
        self._submission_date: str = ""
        self._tex_source_info: Any | None = None
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
        await self._fetch_html()
        self._parse_html()
        await self._fetch_api_metadata()
        self._filter_sections()
        self._setup_output_dir()
        await self._fetch_tex_and_images()
        self._build_ir()
        self._enrich_metadata()
        self._run_transforms()
        self._normalize_abstract()
        self._emit_markdown()
        result = self._build_result()
        await self._save_paper_yml()
        structured_export = await self._structured_export()
        metadata = self._build_metadata(structured_export)
        return result, metadata

    # ── Step 1: Fetch HTML ─────────────────────────────────────────────

    async def _fetch_html(self) -> None:
        from arxiv2md_beta.query.parser import parse_arxiv_input

        query = parse_arxiv_input(self.params.input_text.strip())
        self._query = query  # stored for later steps
        self._html = await fetch_arxiv_html(
            query.html_url,
            arxiv_id=query.arxiv_id,
            version=query.version,
            ar5iv_url=query.ar5iv_url,
            use_cache=not self.params.no_cache,
        )

    # ── Step 2: Parse HTML ─────────────────────────────────────────────

    def _parse_html(self) -> None:
        self._parsed = parse_arxiv_html(self._html)

    # ── Step 3: Fetch API metadata ─────────────────────────────────────

    async def _fetch_api_metadata(self) -> None:
        self._api_metadata = await fetch_arxiv_metadata(self._query.arxiv_id)
        self._display_author_names = (
            author_display_names_from_metadata(self._api_metadata)
            or [a.name for a in self._parsed.authors]
        )
        self._submission_date = (
            self._api_metadata.get("submission_date") or self._parsed.submission_date
        )
        if not self._parsed.title and self._api_metadata.get("title"):
            self._parsed.title = self._api_metadata["title"]

    # ── Step 4: Filter sections ────────────────────────────────────────

    def _filter_sections(self) -> None:
        from arxiv2md_beta.cli.helpers import collect_sections

        self._selected_sections = collect_sections(
            self.params.sections, self.params.section
        )
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
            self._include_abstract = (
                not self._selected_sections or abstract_key in selected_lower
            )

    # ── Step 5: Setup output directory ─────────────────────────────────

    def _setup_output_dir(self) -> None:
        from arxiv2md_beta.output.layout import determine_output_dir

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
        image_map: dict[int, Path] = {}
        image_stem_map: dict[str, Path] = {}

        if not self.params.no_images:
            try:
                self._tex_source_info = await fetch_and_extract_tex_source(
                    self._query.arxiv_id,
                    version=self._query.version,
                    use_cache=not self.params.no_cache,
                )
                processed = process_images(
                    self._tex_source_info,
                    self._paper_output_dir,
                    self._images_dir_name,
                )
                image_map = processed.image_map
                image_stem_map = processed.stem_to_image_path
            except TexSourceNotFoundError:
                pass
            except (OSError, ValueError, TypeError, RuntimeError) as e:
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
        builder = HTMLBuilder(image_resolver=self._image_resolver)
        self._doc = builder.build(self._html, arxiv_id=self._query.arxiv_id)
        self._populate_assets()

    # ── Step 7a: Populate assets ───────────────────────────────────────

    def _populate_assets(self) -> None:
        if self._image_resolver is None:
            return
        from arxiv2md_beta.ir.assets import ImageAsset, SvgAsset

        seen_paths: set[str] = set()
        # Stem map assets
        for stem, path in self._image_resolver._stem_map.items():
            try:
                rel = str(path.relative_to(self._paper_output_dir))
            except ValueError:
                rel = path.as_posix()
            if rel not in seen_paths:
                seen_paths.add(rel)
                ext = path.suffix.lower()
                asset_cls = SvgAsset if ext == ".svg" else ImageAsset
                self._doc.assets.append(asset_cls(path=rel, tex_stem=stem))
        # Index map assets
        for idx, path in sorted(self._image_resolver._index_map.items()):
            try:
                rel = str(path.relative_to(self._paper_output_dir))
            except ValueError:
                rel = path.as_posix()
            if rel not in seen_paths:
                seen_paths.add(rel)
                ext = path.suffix.lower()
                asset_cls = SvgAsset if ext == ".svg" else ImageAsset
                self._doc.assets.append(asset_cls(path=rel, figure_index=idx))

    # ── Step 8: Enrich metadata ────────────────────────────────────────

    def _enrich_metadata(self) -> None:
        if self._submission_date:
            self._doc.metadata.submission_date = self._submission_date

        if self._display_author_names:
            self._merge_affiliations()

        if not self._doc.metadata.title and self._parsed.title:
            self._doc.metadata.title = self._parsed.title

    def _merge_affiliations(self) -> None:
        """Merge API + HTML + TeX affiliations into doc.metadata.authors."""

        def _norm(s: str) -> str:
            return (
                unicodedata.normalize("NFKD", s)
                .encode("ascii", "ignore")
                .decode("ascii")
                .lower()
                .strip()
            )

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
        pipeline = PassPipeline()
        # Phase 1: Filter first to reduce work for downstream passes
        if self._selected_sections:
            pipeline.add(
                SectionFilterPass(
                    mode=self.params.section_filter_mode,
                    selected=self._selected_sections,
                )
            )
        # Phase 2: Numbering (required by FigureReorder)
        pipeline.add(NumberingPass())
        # Phase 3: Reorder figures (depends on numbering)
        pipeline.add(FigureReorderPass())
        # Phase 4: Anchor generation (last, needs final structure)
        pipeline.add(AnchorPass())
        self._doc = pipeline.run(self._doc)

    # ── Step 10: Normalize abstract ────────────────────────────────────

    def _normalize_abstract(self) -> None:
        _strip_abstract_heading(self._doc)
        if not self._include_abstract:
            self._doc.abstract = []

    # ── Step 11: Emit markdown ─────────────────────────────────────────

    def _emit_markdown(self) -> None:
        emitter = MarkdownEmitter()
        main_irs, ref_irs, app_irs = _split_ir_sections(
            self._doc.sections,
            self._ingestion_cfg.reference_section_titles,
        )

        original_sections = self._doc.sections
        original_abstract = self._doc.abstract

        # Main content
        self._doc.sections = main_irs
        self._content = _format_markdown_output(emitter.emit(self._doc))

        # References sidecar (no abstract)
        self._doc.abstract = []
        self._doc.sections = ref_irs
        ref_raw = emitter.emit(self._doc) if ref_irs else ""
        self._content_references = (
            _format_markdown_output(ref_raw) if ref_raw.strip() else None
        )

        # Appendix sidecar (no abstract)
        self._doc.sections = app_irs
        app_raw = emitter.emit(self._doc) if app_irs else ""
        self._content_appendix = (
            _format_markdown_output(app_raw) if app_raw.strip() else None
        )

        # Restore
        self._doc.sections = original_sections
        self._doc.abstract = original_abstract

    # ── Step 12: Build result ──────────────────────────────────────────

    def _build_result(self) -> IngestionResult:
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
        token_body = "\n".join(
            x for x in (self._content, self._content_references, self._content_appendix or "")
            if x
        )
        token_estimate = _format_token_count(
            _create_sections_tree(self._filtered_sections) + "\n" + token_body
        )
        if token_estimate:
            summary_lines.append(f"- Estimated tokens: {token_estimate}")
        summary = "\n".join(summary_lines)

        # Sections tree
        tree_lines = ["Sections:"]
        if self._include_abstract and self._parsed.abstract:
            tree_lines.append("Abstract")
        tree_lines.append(_create_sections_tree(self._filtered_sections))
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
            base_id = (
                self._query.arxiv_id.split("v")[0]
                if "v" in self._query.arxiv_id
                else self._query.arxiv_id
            )
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
                        if (
                            isinstance(pa, dict)
                            and "name" in pa
                            and not pa.get("affiliations")
                        ):
                            affs = html_affil_map.get(pa["name"].lower().strip(), [])
                            if affs:
                                pa["affiliations"] = affs
                else:
                    paper_meta["authors"] = [
                        {"name": a.name, "affiliations": a.affiliations}
                        for a in self._parsed.authors
                        if a.name
                    ]
            paper_meta = fill_arxiv_metadata_defaults(paper_meta, base_id)
            merge_tex_affiliations_if_configured(paper_meta, self._tex_source_info)
            save_paper_metadata(paper_meta, self._paper_output_dir)
        except (OSError, ValueError, TypeError) as e:
            logger.warning(f"Failed to save paper.yml: {e}")

    # ── Step 14: Structured JSON export ────────────────────────────────

    async def _structured_export(self) -> dict:
        try:
            from arxiv2md_beta.ir.emitters.json_emitter import (
                JsonEmitter,
                normalize_structured_mode,
            )

            sm = normalize_structured_mode(self.params.structured_output)
            if sm == "none":
                return {}
            json_emitter = JsonEmitter(mode=sm)
            return json_emitter.write_bundle(
                self._doc,
                self._paper_output_dir,
                images_subdir=self._images_dir_name,
                emit_graph_csv=self.params.emit_graph_csv,
            )
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"Structured JSON export failed: {e}")
            return {}

    # ── Step 15: Build metadata dict ───────────────────────────────────

    def _build_metadata(self, structured_export: dict) -> dict[str, Any]:
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
            sections[first_ref_idx + 1 :],
        )
    if first_app_idx is not None:
        return sections[:first_app_idx], [], sections[first_app_idx:]

    return list(sections), [], []


def _strip_abstract_heading(doc: DocumentIR) -> None:
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
                il.text for il in (getattr(blk, "inlines", []) or []) if hasattr(il, "text")
            ).strip().lower()
            if text in ("abstract",):
                continue
        keep.append(blk)
    doc.abstract = keep

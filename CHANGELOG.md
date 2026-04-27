# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.0] - 2026-04-27

### Added

- **IR (Intermediate Representation) System**: Three-tier compiler architecture for paper parsing
  - **Frontend (Builders)**: `HTMLBuilder` (BeautifulSoup → `DocumentIR`) and `LaTeXBuilder` (Pandoc JSON AST → `DocumentIR`) convert raw sources to structured IR
  - **Middle-end (Transforms)**: Composable `PassPipeline` with 5 passes — `NumberingPass` (figure/table/equation/algorithms), `AnchorPass` (stable anchors), `SectionFilterPass` (include/exclude), `FigureReorderPass` (move to first citation), and `PassPipeline` for ordering
  - **Backend (Emitters)**: `MarkdownEmitter` (→ Markdown), `JsonEmitter` (→ structured JSON), `PlainTextEmitter` (→ plain text) serialize `DocumentIR` to target formats
  - **Data Model**: 9 Inline types + 11 Block types + 3 Asset types via Pydantic v2 discriminated unions (`type: Literal[...]`)
  - **Visitor Pattern**: `IRVisitor` + `walk()` for depth-first traversal, with built-in `NodeCounter`, `TextCollector`
  - **RawBlockIR / RawInlineIR**: Fallback nodes preserve original format (HTML/LaTeX) for unrecognized content
- **`--ir` CLI Flag**: Opt-in IR pipeline via `--ir` on `convert` and `batch` commands
- **Python API**: Full programmatic access to `HTMLBuilder`, `LaTeXBuilder`, `PassPipeline`, `MarkdownEmitter`, `JsonEmitter`, `PlainTextEmitter`
- **137 IR Unit Tests**: Comprehensive coverage of builders, emitters, transforms, models, and visitors

### Changed

- **Project Structure**: New `ir/` package with 20+ files organized as `builders/`, `transforms/`, `emitters/`
- **Version**: 0.7.1 → 0.8.0

Wrapped up by Claude Opus 4.6 (claude-code) on 2026-04-27

## [0.7.1] - 2026-04-14

### Added

- **Figure Reordering**: Images are now moved to immediately after the paragraph where they are first cited, improving readability when figures appear far from their first reference in the source HTML/LaTeX
  - Works for both HTML and LaTeX parsers via a unified Markdown post-processing step in `format_paper`
  - Multi-panel figures (e.g. `<div align="center">` with multiple `<img>` tags) move as a single block
  - Unreferenced figures remain at their original position

### Fixed

- **Table Misplacement**: Tables (`Table N`) are no longer incorrectly treated as figures during reordering
- **Figure Citation Matching**: Added support for `Figure [N](#figure-N)` style markdown links when locating the first citation

Wrapped up by Kimi (kimi-for-coding via kimi-cli) on 2026-04-14

## [0.7.0] - 2026-04-14

### Added

- **LaTeX Parser Enhancement**: Full feature parity with HTML parser
  - **File Splitting**: LaTeX mode now generates separate files (`xx.md`, `xx-References.md`, `xx-Appendix.md`)
  - **Table of Contents (TOC)**: Auto-generated TOC with section links for LaTeX output
  - **Section Structure Parsing**: Full hierarchy extraction from `\section`, `\subsection`, `\subsubsection`
  - **Citation Links**: `\cite{key}` converted to `[N](#ref-N)` format with working anchors
  - **Figure/Table/Equation Anchors**: `\label{fig:X}` generates `<a id="fig:X"></a>` for cross-referencing
  - **Section Filtering**: `--sections` and `--section-filter-mode` now work with LaTeX parser
  - **Bibliography Recognition**: Automatic detection of References/Bibliography sections
  - **Appendix Detection**: Recognizes `\appendix` command and `Appendix X` sections
  - **Markdown Beautification**: Enhanced table formatting, figure captions, code blocks, math display
  - **Structured Export**: Full support for `paper.*.json` exports in LaTeX mode

### Changed

- **LaTeX Ingestion**: Now uses `split_for_reference=True` and `include_toc=True` by default
- **CLI Parameters**: `--remove-refs`, `--remove-toc`, `--sections` now work with `--parser latex`
- **CLI Cache Flag**: Renamed `--no-use-cache` to `--no-cache` for simplicity

### Fixed

- **Result Cache Removed**: Eliminated result-level caching that incorrectly bound output directory paths; now only download-level caches (TeX source, HTML, PDF) are kept
- **Cache Propagation**: `--no-cache` now properly disables caching for TeX source, HTML, and PDF downloads across both parsers
- **SectionNode Shadowing Bug**: Fixed a variable name collision in `ingest_paper_latex` that caused `'SectionNode' object has no attribute 'strip'` when section filtering was active

## [0.6.3] - 2026-04-08

### Added

- **Citations**: Inline citation links now generate clickable anchors to specific reference entries.
  - Citations like `[7]` are converted to `[[7](#ref-7)]` format.
  - References in the bibliography get `<a id="ref-N"></a>` anchors.
  - This enables navigation from inline citations to their corresponding bibliography entries.

### Changed

- **Citations**: Changed citation output from plain text `[N]` to linked format `[[N](#ref-N)]`.

## [0.6.2] - 2026-04-08

### Fixed

- **Cache**: Fixed validation error when loading cached results after `IngestionResult` schema added `summary` and `sections_tree` fields. Bumped `CACHE_VERSION` from `"1.0"` to `"1.1"` to invalidate old cache entries automatically.
- **Cache**: Added `default=str` to `json.dumps()` in `async_write_json()` so `pathlib.Path` values in metadata (e.g., `paper_output_dir`) serialize correctly instead of raising `TypeError`.

## [0.6.1] - 2026-04-06

### Fixed

- **Tests**: `tests/benchmarks/test_performance.py` no longer passes `timer="time.perf_counter"` as a string to `pytest.mark.benchmark` (pytest-benchmark requires a callable; a string broke timer calibration).
- **Tests**: Mocked `fetch_arxiv_html` integration tests now call `use_cache=False` so results do not come from `~/.cache/arxiv2md-beta` and bypass the mocked HTTP layer.

### Changed

- **`__version__`**: Aligned `arxiv2md_beta.__version__` with `pyproject.toml` (was stale).

## [0.6.0] - 2026-04-04

### Added

- **Async image parallel processing**: `images/resolver.py` gained `process_images_async()`.
  - PDF-to-PNG conversions run in a `ProcessPoolExecutor` (CPU-bound).
  - Raster copies run via `asyncio.gather` with a thread pool and semaphore-controlled concurrency.
- **HTTP connection pool reuse**: `network/http.py` now exposes `get_http_client()` for a shared module-level `httpx.AsyncClient`, while `async_http_client()` is kept for scoped custom timeouts.
- **Async file I/O**: New `utils/aiofiles_compat.py` wraps `aiofiles` for non-blocking reads/writes; integrated into `network/fetch.py` and `cli/output_finalize.py`.
- **Performance monitoring**: New `utils/metrics.py` provides `timed_operation` / `async_timed_operation` context managers; wired into `run_convert_flow`, `run_batch_flow`, `run_images_flow`, `run_paper_yml_flow`, and `ingest_paper_latex`.
- **Compiled regex patterns**: Module-level pre-compiled regexes in `output/formatter.py`, `latex/parser.py`, and `html/markdown.py` to reduce CPU overhead during parsing.

### Changed

- **CLI runner split**: `cli/runner.py` (369 lines) refactored into `cli/runner/` subpackage:
  - `base.py` – shared helpers (`merge_convert_params`)
  - `convert.py` – convert flow
  - `images.py` – images flow
  - `batch.py` – batch flow
  - `paper_yml.py` – paper-yml flow
- **Exception handling refined**: Replaced overly broad `except Exception` with specific types (`Arxiv2mdError`, `httpx.*`, `OSError`, `ValueError`, etc.) in `cli/runner/`, `network/fetch.py`, `images/resolver.py`, and `latex/parser.py`.
- Added `aiofiles>=24.0.0` to core dependencies.

### Fixed

- Tests updated to `await` the now-async `write_split_markdown_sidecars` and `write_result_json_sidecar` helpers.

## [0.5.0] - 2026-04-03

### Added

- **Local HTML file ingestion**: Support for processing locally saved HTML papers (e.g., from Science.org, IEEE, ACM).
  - New `LocalHtmlQuery` schema and `local_html.py` ingestion pipeline.
  - Auto-detects HTML files by extension (.html, .htm) or path pattern.
  - Extracts title, authors, abstract, and sections from HTML structure.
  - Copies associated files (images) from `_files/` or `.files/` directories.
  - CLI help text updated to mention local HTML file support.

### Changed

- **Citation format**: Changed from `*N*` to `[N]` for better readability and standard academic formatting.
- **External citation links**: Removed URL from citation links (e.g., `[1]` instead of `[*1*](https://...)`).
  - Supports arXiv bib links (`#bib.*`), Science.org collateral links (`#core-collateral-R*`), and common citation patterns.
- **Figure rendering**: Improved figure output format with proper Markdown image syntax and blockquote caption.

### Fixed

- **Content extraction for local HTML**: Fixed `_collect_content_until_next_heading` to properly handle headings with nested elements (e.g., `<i>` inside `<h4>`).
- **Duplicate content**: Fixed nested section content being collected twice in parent sections.
- **`div role="paragraph"`**: Added support for Science.org HTML paragraph structure.

## [0.4.1] - 2026-04-03

### Fixed

- **TeX image order**: Strip ``\affiliation[...]{...}`` blocks before enumerating ``\includegraphics``. Fairmeta / NeurIPS-style papers put institution logos (e.g. ``unc_logo``) in affiliations; those are not ar5iv numbered figures. Counting them shifted ``image_map[0]`` so opaque HTML assets (``xN.png``) paired with the wrong file (often the first affiliation logo).

## [0.4.0] - 2026-04-03

### Added

- **TeX author affiliations**: Parse ICML (`\icmlauthor` / `\icmlaffiliation`), IEEE, and common `\author` layouts from expanded TeX; merge into metadata when `ingestion.enrich_affiliations_from_tex` is true (default in `default_config.yml`). Implemented in `latex/author_affiliations.py` and `output/metadata_tex.py`.
- **`paper.yml` merge**: `merge_paper_yml_preserve_user_fields` keeps user-added keys when re-running conversion (fresh API output wins on overlap; missing keys preserved).
- Tests: `test_tex_image_order.py`, `test_author_affiliations.py`, `test_metadata_tex.py`; extra Markdown figure-order coverage in `test_markdown.py`.

### Fixed

- **Figure images vs ar5iv HTML**: ar5iv renames raster assets to `x1.png`, `x2.png`, … which do not match TeX filenames. Positional pairing then depended on TeX `\includegraphics` order; **logos inside `\icmltitle{...}` / `\title{...}`** were counted first while those graphics are often absent from numbered HTML figures, shifting every caption. TeX parsing now **strips title blocks** before enumerating graphics so `image_map` indices align with body figures.
- **HTML → Markdown**: Raster paths prefer matching `<img src>` basename/stem via `stem_to_image_path`; shared `used_image_indices` avoids reusing slots; smallest-unused index only when the URL is opaque.
- **Images resolver**: Register `source_image_path.name` in the stem map when the processed output basename differs.

### Changed

- Settings: `ingestion.enrich_affiliations_from_tex`; CLI runner and HTML ingestion wire TeX enrichment; `metadata.py` save path uses merge when file exists.

## [0.3.0] - 2026-03-25

### Changed (breaking)

- **Package layout**: Public modules are reorganized into subpackages. Update imports, for example:
  - `arxiv2md_beta.query_parser` → `arxiv2md_beta.query` (or `arxiv2md_beta.query.parser`)
  - `arxiv2md_beta.fetch` / `arxiv_api` / `crossref_api` → `arxiv2md_beta.network.*`
  - `arxiv2md_beta.output_layout` / `output_formatter` / `paper_metadata` → `arxiv2md_beta.output.*`
  - `arxiv2md_beta.image_resolver` / `image_extract` → `arxiv2md_beta.images.*`
  - `arxiv2md_beta.html_parser` / `markdown` / `sections` → `arxiv2md_beta.html.*`
  - `arxiv2md_beta.latex_parser` / `tex_source` → `arxiv2md_beta.latex.*`
  - `arxiv2md_beta.ingestion` / `html_ingestion` / `latex_ingestion` / `local_ingestion` → `arxiv2md_beta.ingestion.*`
- **CLI**: Entry point unchanged (`arxiv2md-beta`); implementation lives under `arxiv2md_beta.cli`.

### Added

- `CHANGELOG.md` for release notes.

### Fixed

- Lint: missing `shutil` import in local ingestion; minor unused imports/variables in network/latex/html.
- **Git**: `.gitignore` rule `output/` accidentally ignored the Python package `arxiv2md_beta/output/`; only repository-root `/output/` is ignored now (for local conversion output).

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

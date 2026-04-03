# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

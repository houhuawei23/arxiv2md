"""Pydantic models for arxiv2md-beta configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AppSection(BaseModel):
    environment: str = Field(
        default="development",
        description="Logical env name; selects environments/<name>.yml",
    )
    log_level: str = "INFO"


class HttpSection(BaseModel):
    fetch_timeout_s: float = Field(gt=0)
    fetch_max_retries: int = Field(ge=0)
    fetch_backoff_s: float = Field(ge=0)
    user_agent: str
    # Version single-sourcing: the bundled YAML carries the bare placeholder;
    # the package's own __version__ is injected here so a release cannot
    # drift between pyproject, default_config.yml, and the live UA string.
    _UA_PLACEHOLDER = "arxiv2md-beta"

    @model_validator(mode="after")
    def _inject_versioned_user_agent(self) -> HttpSection:
        if self.user_agent == self._UA_PLACEHOLDER:
            from arxiv2md_beta import __version__

            self.user_agent = f"arxiv2md-beta/{__version__}"
        return self

    retry_status_codes: list[int]
    large_transfer_timeout_multiplier: float = Field(gt=0)
    max_connections: int = Field(default=100, ge=1, description="httpx connection pool size")
    max_keepalive_connections: int = Field(default=20, ge=0, description="httpx keep-alive limit")
    max_concurrent_requests: int = Field(default=16, ge=1, description="Global concurrent HTTP request limit")
    metadata_timeout_s: float = Field(
        default=90.0,
        gt=0,
        description="Overall wall-clock budget for one metadata fetch (retries included), "
        "so a slow enrichment chain cannot hold a batch slot indefinitely.",
    )
    max_requests_per_second: float = Field(
        default=0.0,
        ge=0,
        description="Global client-side rate limit for HTTP requests. 0 disables "
        "client-side throttling (server-side retry/backoff still applies).",
    )
    mirror_on_404: bool = Field(
        default=True,
        description="Retry arxiv.org downloads on export.arxiv.org after a 404.",
    )
    mirror_on_rate_limit: bool = Field(
        default=True,
        description="Retry arxiv.org downloads on export.arxiv.org after exhausting retries on HTTP 429.",
    )


class CacheSection(BaseModel):
    dir: str = Field(
        description="Cache root. Absolute paths (e.g. ~/.cache/arxiv2md-beta) are used as-is. "
        "Relative paths are resolved under $XDG_CACHE_HOME/arxiv2md-beta (or ~/.cache/arxiv2md-beta), never cwd.",
    )
    ttl_seconds: int

    @field_validator("dir")
    @classmethod
    def expand_cache_dir(cls, v: str) -> str:
        return v


class PathsSection(BaseModel):
    user_config_dir: str


class UrlsSection(BaseModel):
    arxiv_host: str
    arxiv_mirror_host: str = Field(
        default="export.arxiv.org",
        description="Mirror host for /pdf/, /src/ and /html/ fallback when arxiv.org "
        "404s or rate-limits. Empty string disables mirror retries.",
    )
    ar5iv_html_base: str
    arxiv_api_query_template: str
    arxiv_api_search_template: str = Field(
        default=(
            "https://export.arxiv.org/api/query?search_query={query}"
            "&start={start}&max_results={max_results}&sortBy={sort}"
        ),
        description="arXiv Atom API search endpoint used by the ``search`` command.",
    )
    arxiv_pdf_template: str
    arxiv_src_template: str
    crossref_works_template: str


class CliDefaultsSection(BaseModel):
    parser: Literal["html", "latex"] = "html"
    source: str = "Arxiv"
    section_filter_mode: Literal["include", "exclude"] = "exclude"
    output_dir: str = "."
    images_subdir: str = "images"


class OutputNamingSection(BaseModel):
    max_title_length: int = Field(ge=1)
    max_basename_length: int = Field(ge=1)
    max_md_basename_length: int = Field(ge=1)
    default_unknown_title: str
    sanitize_source_max_length: int = Field(ge=1)
    sanitize_short_max_length: int = Field(ge=1)
    naming_scheme: Literal["classic", "paper-pipeline", "arxiv-ym"] = Field(
        default="arxiv-ym",
        description=(
            "Output naming scheme. arxiv-ym (default): {YYYYMM}-{source}-{short}-{title} with"
            " fixed internal filenames (paper.md, Appendix.md, References.md). paper-pipeline:"
            " {source}-{date}-{title} (fixed filenames). classic: legacy {date}-{source}-{title}."
        ),
    )


class IngestionSection(BaseModel):
    fetch_arxiv_metadata: bool = Field(
        default=False,
        description="Fetch optional arXiv API metadata enrichment (disabled by default).",
    )
    reference_section_titles: list[str]
    abstract_section_title: str
    latex_fallback_title: str
    enrich_affiliations_from_tex: bool = Field(
        default=True,
        description="Parse TeX source for author affiliations and merge into paper.yml.",
    )
    fetch_tex_for_affiliations_when_no_images: bool = Field(
        default=True,
        description="When HTML mode skips images (--no-images), still download TeX to enrich affiliations.",
    )


class ParsingSection(BaseModel):
    max_author_part_length: int = Field(ge=1)


class ImagesSection(BaseModel):
    pdf_to_png_dpi: int = Field(gt=0)
    trim_whitespace: bool = Field(
        default=False,
        description="If true, crop PDF/EPS→PNG output to content bbox via _trim_whitespace; "
        "default off to preserve margins.",
    )
    trim_whitespace_tolerance: int = Field(ge=0)
    disable_tqdm: bool = False
    max_concurrency: int = Field(
        default=4,
        ge=1,
        description="Maximum concurrent image tasks per paper (raster + PDF conversions).",
    )
    global_max_concurrency: int = Field(
        default=12,
        ge=1,
        description="Maximum image processing tasks across all papers.",
    )
    global_pdf_concurrency: int = Field(
        default=4,
        ge=1,
        description="Maximum PDF rasterization jobs submitted across all papers.",
    )
    pdf_workers: int = Field(
        default=0,
        ge=0,
        description="Process-pool workers for PDF→PNG conversion, shared across a batch. "
        "0 = auto (min(4, max(1, cpu_count // 2))).",
    )


class MarkdownSvgSection(BaseModel):
    foreignobject_default_width: float = Field(gt=0)
    foreignobject_default_height: float = Field(gt=0)
    font_size_min: float = Field(gt=0)
    font_size_max_ratio: float = Field(gt=0, le=1)


class LoggingSection(BaseModel):
    console_format: str
    file_format: str
    file_rotation: str
    file_retention: str
    file_compression: str
    default_log_file: str


class FeaturesSection(BaseModel):
    enable_file_logging: bool = False


class OutputSection(BaseModel):
    tiktoken_encoding: str = "o200k_base"
    include_anchors: bool = Field(
        default=False,
        description='If True, keep <a id="..."></a> anchor tags in generated Markdown.',
    )
    linked_citations: bool = Field(
        default=False,
        description="If True, render inline citations as linked [N](#ref-N); otherwise render them as plain [N].",
    )
    stub_min_bytes: int = Field(
        default=5000,
        ge=0,
        description="Quality gate: output Markdown below this UTF-8 byte count is treated as a "
        "stub and rejected (exit 5) unless --allow-stub is passed.",
    )
    stub_min_tokens: int = Field(
        default=1000,
        ge=0,
        description="Quality gate: output Markdown below this estimated token count is treated as "
        "a stub. Skipped automatically when tiktoken is unavailable.",
    )
    allow_stub: bool = Field(
        default=False,
        description="Write stub-level output instead of rejecting it (CLI --allow-stub overrides).",
    )


class AppSettings(BaseModel):
    """Full application settings (YAML merged with env in loader; env wins over YAML)."""

    model_config = ConfigDict(extra="ignore")

    app: AppSection
    http: HttpSection
    cache: CacheSection
    paths: PathsSection
    urls: UrlsSection
    cli_defaults: CliDefaultsSection
    output_naming: OutputNamingSection
    ingestion: IngestionSection
    parsing: ParsingSection
    images: ImagesSection
    markdown_svg: MarkdownSvgSection
    logging: LoggingSection
    features: FeaturesSection
    output: OutputSection

    def resolved_cache_path(self) -> Path:
        """Resolve cache directory: never anchor relative paths to cwd."""
        p = Path(self.cache.dir).expanduser()
        if p.is_absolute():
            return p.resolve()
        xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
        base = Path(xdg).expanduser().resolve() if xdg else (Path.home() / ".cache").resolve()
        return (base / "arxiv2md-beta" / p).resolve()

    def resolved_user_config_dir(self) -> Path:
        return Path(self.paths.user_config_dir).expanduser().resolve()

    def resolved_default_log_file(self) -> Path:
        return self.resolved_user_config_dir() / self.logging.default_log_file

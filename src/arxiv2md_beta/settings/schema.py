"""Pydantic models for arxiv2md-beta configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AppSection(BaseModel):
    environment: str = Field(default="development", description="Logical env name; selects environments/<name>.yml")
    log_level: str = "INFO"


class HttpSection(BaseModel):
    fetch_timeout_s: float = Field(gt=0)
    fetch_max_retries: int = Field(ge=0)
    fetch_backoff_s: float = Field(ge=0)
    user_agent: str
    retry_status_codes: list[int]
    large_transfer_timeout_multiplier: float = Field(gt=0)


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
    ar5iv_html_base: str
    arxiv_api_query_template: str
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


class IngestionSection(BaseModel):
    reference_section_titles: list[str]
    abstract_section_title: str
    latex_fallback_title: str


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

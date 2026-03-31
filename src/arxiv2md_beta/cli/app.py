"""Typer CLI: global config callback and ``convert`` / ``images`` commands."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import typer

from arxiv2md_beta.cli.runner import ConvertParams, ImagesParams, run_convert_sync, run_images_sync
from arxiv2md_beta.settings import ConfigurationError, apply_cli_overrides, get_settings, load_settings, set_settings
from arxiv2md_beta.utils.logging_config import configure_logging, get_logger

app = typer.Typer(
    name="arxiv2md-beta",
    help="Convert arXiv papers to Markdown with image support.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def global_callback(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        metavar="PATH",
        help="User YAML configuration file (merged after bundled default and environment profile).",
    ),
    env: Optional[str] = typer.Option(
        None,
        "--env",
        "-E",
        metavar="NAME",
        help="Environment profile: development, production, or test.",
    ),
    force_reload: bool = typer.Option(
        False,
        "--force-reload",
        help="Reload configuration from disk instead of using cached settings.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Print DEBUG-level logs (overrides app.log_level for this run).",
    ),
) -> None:
    """Global options (applied before ``convert`` / ``images``)."""
    if ctx.resilient_parsing:
        return
    try:
        load_settings(
            config_path=config,
            environment=env,
            force_reload=force_reload,
        )
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    configure_logging(
        settings=get_settings(),
        level="DEBUG" if verbose else None,
    )


@app.command("convert")
def convert_cmd(
    input_text: str = typer.Argument(
        ...,
        metavar="INPUT",
        help="arXiv ID, URL, or local archive path.",
    ),
    parser: Optional[str] = typer.Option(
        None,
        "--parser",
        help="Parser mode: html or latex.",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory; a subdirectory may be created inside.",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        help="Article source (conference/journal name).",
    ),
    short: Optional[str] = typer.Option(
        None,
        "--short",
        help="Short name for the article.",
    ),
    no_images: bool = typer.Option(
        False,
        "--no-images",
        help="Skip downloading and inserting images (HTML mode only).",
    ),
    remove_refs: bool = typer.Option(
        False,
        "--remove-refs",
        help="Remove bibliography/references sections from output.",
    ),
    remove_toc: bool = typer.Option(
        False,
        "--remove-toc",
        help="Remove table of contents from output.",
    ),
    remove_inline_citations: bool = typer.Option(
        False,
        "--remove-inline-citations",
        help="Remove inline citation text from output.",
    ),
    section_filter_mode: Optional[str] = typer.Option(
        None,
        "--section-filter-mode",
        help="Section filtering: include or exclude.",
    ),
    sections: Optional[str] = typer.Option(
        None,
        "--sections",
        help='Comma-separated section titles (e.g. "Abstract,Introduction").',
    ),
    section: list[str] = typer.Option(
        [],
        "--section",
        help="Repeatable section title filter.",
    ),
    include_tree: bool = typer.Option(
        False,
        "--include-tree",
        help="Include the section tree before the Markdown content.",
    ),
    no_progress: bool = typer.Option(
        False,
        "--no-progress",
        help="Disable Rich progress bars (downloads, images); logs still show milestones.",
    ),
    emit_result_json: bool = typer.Option(
        False,
        "--emit-result-json",
        help="Print one line ARXIV2MD_RESULT_JSON={...} with paper_output_dir for scripting.",
    ),
) -> None:
    """Convert an arXiv paper or local TeX archive to Markdown."""
    logger = get_logger()
    s = get_settings()
    d = s.cli_defaults
    parser_mode = parser if parser is not None else d.parser
    if parser_mode not in ("html", "latex"):
        typer.echo(f"Invalid --parser {parser_mode!r}; expected html or latex.", err=True)
        raise typer.Exit(code=2)
    source_v = source if source is not None else d.source
    mode = section_filter_mode if section_filter_mode is not None else d.section_filter_mode
    if mode not in ("include", "exclude"):
        typer.echo(f"Invalid --section-filter-mode {mode!r}; expected include or exclude.", err=True)
        raise typer.Exit(code=2)

    merged = apply_cli_overrides(
        s,
        SimpleNamespace(parser=parser_mode, source=source_v, section_filter_mode=mode),
    )
    if no_progress:
        merged = merged.model_copy(
            update={
                "images": merged.images.model_copy(update={"disable_tqdm": True}),
            }
        )
    set_settings(merged)

    sec_list = section if section else None
    params = ConvertParams(
        input_text=input_text.strip(),
        parser=parser_mode,
        output=output,
        source=source_v,
        short=short,
        no_images=no_images,
        remove_refs=remove_refs,
        remove_toc=remove_toc,
        remove_inline_citations=remove_inline_citations,
        section_filter_mode=mode,
        sections=sections,
        section=sec_list,
        include_tree=include_tree,
        emit_result_json=emit_result_json,
    )
    try:
        run_convert_sync(params)
    except Exception as exc:
        logger.error(f"Error: {exc}")
        raise typer.Exit(code=1) from exc


@app.command("images")
def images_cmd(
    arxiv_input: str = typer.Argument(
        ...,
        metavar="ARXIV",
        help="arXiv ID or URL.",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        metavar="DIR",
        help="Output directory; images go to DIR/<images-subdir>.",
    ),
    images_subdir: Optional[str] = typer.Option(
        None,
        "--images-subdir",
        metavar="NAME",
        help="Subdirectory under --output for processed images.",
    ),
    no_tex_cache: bool = typer.Option(
        False,
        "--no-tex-cache",
        help="Ignore cached TeX extract and re-download source from arXiv.",
    ),
) -> None:
    """Extract and process figures from arXiv TeX source only (no Markdown)."""
    logger = get_logger()
    s = get_settings()
    d = s.cli_defaults
    subdir = images_subdir if images_subdir is not None else d.images_subdir
    params = ImagesParams(
        arxiv_input=arxiv_input.strip(),
        output=output,
        images_subdir=subdir,
        no_tex_cache=no_tex_cache,
    )
    try:
        run_images_sync(params)
    except Exception as exc:
        logger.error(f"Error: {exc}")
        raise typer.Exit(code=1) from exc


def main() -> None:
    """Console script entry (see ``pyproject.toml`` ``project.scripts``)."""
    try:
        app()
    except KeyboardInterrupt:
        get_logger().info("Interrupted by user")
        sys.exit(130)

"""Typer CLI: global config callback and ``convert`` / ``images`` commands."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from arxiv2md_beta.cli.config_cmd import app as config_app
from arxiv2md_beta.cli.convert_cli import (
    apply_convert_cli_settings,
    make_convert_params,
)
from arxiv2md_beta.cli.runner import (
    ImagesParams,
    PaperYmlParams,
    run_batch_sync,
    run_convert_sync,
    run_images_sync,
    run_paper_yml_sync,
)
from arxiv2md_beta.exceptions import Arxiv2mdError
from arxiv2md_beta.settings import ConfigurationError, get_settings, load_settings
from arxiv2md_beta.utils.logging_config import configure_logging, get_logger

app = typer.Typer(
    name="arxiv2md-beta",
    help="Convert arXiv papers to Markdown with image support.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _handle_command_error(logger: object, exc: BaseException) -> None:
    """Map typed errors to exit codes; generic exceptions exit with 1."""
    if isinstance(exc, Arxiv2mdError):
        logger.error(f"Error: {exc}")
        raise typer.Exit(code=exc.exit_code) from exc
    logger.error(f"Error: {exc}")
    raise typer.Exit(code=1) from exc


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
        help="arXiv ID, URL, local archive path, or local HTML file path.",
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
    structured_output: str = typer.Option(
        "none",
        "--structured-output",
        help="Emit versioned JSON next to Markdown: none | meta | document | full | all.",
    ),
    emit_graph_csv: bool = typer.Option(
        False,
        "--emit-graph-csv",
        help="With --structured-output all, also write paper.graph.nodes.csv and paper.graph.edges.csv.",
    ),
    no_use_cache: bool = typer.Option(
        False,
        "--no-use-cache",
        help="Disable result-level caching (re-convert even if cached result exists).",
    ),
) -> None:
    """Convert an arXiv paper or local TeX archive to Markdown."""
    logger = get_logger()
    parser_mode, source_v, mode, so = apply_convert_cli_settings(
        parser=parser,
        source=source,
        section_filter_mode=section_filter_mode,
        structured_output=structured_output,
        no_progress=no_progress,
    )
    params = make_convert_params(
        input_text.strip(),
        parser_mode=parser_mode,
        output=output,
        source_v=source_v,
        short=short,
        no_images=no_images,
        remove_refs=remove_refs,
        remove_toc=remove_toc,
        remove_inline_citations=remove_inline_citations,
        mode=mode,
        sections=sections,
        section=section,
        include_tree=include_tree,
        emit_result_json=emit_result_json,
        so=so,
        emit_graph_csv=emit_graph_csv,
        use_cache=not no_use_cache,
    )
    try:
        run_convert_sync(params)
    except BaseException as exc:
        if isinstance(exc, (typer.Exit, KeyboardInterrupt)):
            raise
        _handle_command_error(logger, exc)


@app.command("batch")
def batch_cmd(
    input_file: Path = typer.Argument(
        ...,
        metavar="INPUT_FILE",
        exists=True,
        readable=True,
        help="Text file: one INPUT per line (arXiv ID, URL, local archive, or local HTML file). Lines starting with # are ignored.",
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
    structured_output: str = typer.Option(
        "none",
        "--structured-output",
        help="Emit versioned JSON next to Markdown: none | meta | document | full | all.",
    ),
    emit_graph_csv: bool = typer.Option(
        False,
        "--emit-graph-csv",
        help="With --structured-output all, also write paper.graph.nodes.csv and paper.graph.edges.csv.",
    ),
    max_concurrency: int = typer.Option(
        3,
        "--max-concurrency",
        "-j",
        help="Maximum concurrent conversions.",
    ),
    delay_seconds: float = typer.Option(
        0.0,
        "--delay-seconds",
        help="Seconds to sleep before each task after the first (rate limiting).",
    ),
    fail_fast: bool = typer.Option(
        False,
        "--fail-fast",
        help="Stop on first error (default: process all lines and report failures).",
    ),
    no_use_cache: bool = typer.Option(
        False,
        "--no-use-cache",
        help="Disable result-level caching for batch conversions.",
    ),
) -> None:
    """Convert multiple papers listed in INPUT_FILE (same options as ``convert``)."""
    logger = get_logger()
    parser_mode, source_v, mode, so = apply_convert_cli_settings(
        parser=parser,
        source=source,
        section_filter_mode=section_filter_mode,
        structured_output=structured_output,
        no_progress=no_progress,
    )
    template = make_convert_params(
        "",
        parser_mode=parser_mode,
        output=output,
        source_v=source_v,
        short=short,
        no_images=no_images,
        remove_refs=remove_refs,
        remove_toc=remove_toc,
        remove_inline_citations=remove_inline_citations,
        mode=mode,
        sections=sections,
        section=section,
        include_tree=include_tree,
        emit_result_json=emit_result_json,
        so=so,
        emit_graph_csv=emit_graph_csv,
        use_cache=not no_use_cache,
    )
    lines = input_file.read_text(encoding="utf-8").splitlines()
    try:
        results = run_batch_sync(
            lines,
            params_template=template,
            max_concurrency=max_concurrency,
            continue_on_error=not fail_fast,
            delay_seconds=delay_seconds,
        )
    except BaseException as exc:
        if isinstance(exc, (typer.Exit, KeyboardInterrupt)):
            raise
        _handle_command_error(logger, exc)

    table = Table(title="batch results", show_lines=True)
    table.add_column("input", overflow="fold")
    table.add_column("status")
    table.add_column("detail", overflow="fold")

    any_err = False
    for inp, err, pdir in results:
        if err:
            any_err = True
            table.add_row(inp, "error", err)
        elif pdir is None:
            table.add_row(inp, "skip", "")
        else:
            table.add_row(inp, "ok", pdir)

    Console(stderr=False).print(table)
    if any_err:
        raise typer.Exit(code=1)


@app.command("paper-yml")
def paper_yml_cmd(
    arxiv: Optional[str] = typer.Argument(
        None,
        metavar="ARXIV",
        help="arXiv ID or URL (required when not using --update).",
    ),
    update: Optional[Path] = typer.Option(
        None,
        "--update",
        "-u",
        metavar="PATH",
        help="Existing paper.yml to refresh in place (reads identifiers.arxiv / paper.id). Merges with fetched metadata: keys you added (e.g. urls.website) are kept; API fields overwrite.",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        metavar="PATH",
        help="Output paper.yml path (file or directory ending in paper.yml). If the file exists, writes paper.1.yml, paper.2.yml, … unless --force.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite --output path when it already exists (no numeric suffix).",
    ),
) -> None:
    """Fetch arXiv metadata only and write or refresh ``paper.yml`` (no Markdown conversion)."""
    logger = get_logger()
    if update is not None:
        if arxiv:
            logger.warning("Ignoring ARXIV argument because --update is set.")
        params = PaperYmlParams(
            update_path=update,
            arxiv_input=None,
            output=None,
            force=False,
        )
    else:
        if not arxiv:
            typer.echo(
                "Error: provide ARXIV id/URL or use --update PATH/to/paper.yml",
                err=True,
            )
            raise typer.Exit(code=2)
        arxiv_stripped = arxiv.strip()
        if not arxiv_stripped:
            typer.echo(
                "Error: provide ARXIV id/URL or use --update PATH/to/paper.yml",
                err=True,
            )
            raise typer.Exit(code=2)
        if not output or not output.strip():
            typer.echo("Error: --output is required when not using --update.", err=True)
            raise typer.Exit(code=2)
        params = PaperYmlParams(
            update_path=None,
            arxiv_input=arxiv_stripped,
            output=output.strip(),
            force=force,
        )
    try:
        run_paper_yml_sync(params)
    except BaseException as exc:
        if isinstance(exc, (typer.Exit, KeyboardInterrupt)):
            raise
        _handle_command_error(logger, exc)


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
    except BaseException as exc:
        if isinstance(exc, (typer.Exit, KeyboardInterrupt)):
            raise
        _handle_command_error(logger, exc)


app.add_typer(config_app, name="config")


@app.command("bibtex")
def bibtex_cmd(
    arxiv_input: str = typer.Argument(
        ...,
        metavar="ARXIV",
        help="arXiv ID, URL, or path to local HTML/Markdown file containing bibliography.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        metavar="FILE",
        help="Output BibTeX file path (default: stdout).",
    ),
    resolve: bool = typer.Option(
        True,
        "--resolve/--no-resolve",
        help="Resolve DOIs to full metadata via Crossref.",
    ),
) -> None:
    """Extract bibliography and export as BibTeX."""
    import asyncio

    from arxiv2md_beta.citations import (
        export_bibtex,
        parse_citations_from_html,
        parse_citations_from_text,
    )
    from arxiv2md_beta.network.fetch import fetch_arxiv_html
    from arxiv2md_beta.query.parser import parse_arxiv_input

    logger = get_logger()

    async def _run():
        # Check if input is a local file
        input_path = Path(arxiv_input)
        if input_path.exists():
            content = input_path.read_text(encoding="utf-8")
            if input_path.suffix.lower() in (".html", ".htm"):
                citations = parse_citations_from_html(content)
            else:
                # Assume plain text/markdown
                citations = parse_citations_from_text(content)
        else:
            # Fetch from arXiv
            query = parse_arxiv_input(arxiv_input)
            html = await fetch_arxiv_html(
                query.html_url,
                arxiv_id=query.arxiv_id,
                version=query.version,
                ar5iv_url=query.ar5iv_url,
            )
            citations = parse_citations_from_html(html)

        if not citations:
            typer.echo("No citations found.", err=True)
            raise typer.Exit(code=1)

        if resolve:
            bibtex = await export_bibtex(citations)
        else:
            # Export without resolving
            from arxiv2md_beta.citations.formatter import format_bibtex_database
            from arxiv2md_beta.citations.models import CitationEntry

            entries = [
                CitationEntry(
                    key=c.key,
                    raw_text=c.text,
                    entry_type="misc",
                )
                for c in citations
            ]
            bibtex = format_bibtex_database(entries)

        if output:
            output.write_text(bibtex, encoding="utf-8")
            typer.echo(f"BibTeX exported to {output}")
        else:
            typer.echo(bibtex)

    try:
        asyncio.run(_run())
    except BaseException as exc:
        if isinstance(exc, (typer.Exit, KeyboardInterrupt)):
            raise
        _handle_command_error(logger, exc)


def main() -> None:
    """Console script entry (see ``pyproject.toml`` ``project.scripts``)."""
    try:
        app()
    except KeyboardInterrupt:
        get_logger().info("Interrupted by user")
        sys.exit(130)

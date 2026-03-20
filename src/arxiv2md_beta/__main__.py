"""Command-line interface (CLI) entry point for arxiv2md-beta."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from arxiv2md_beta.cli import (
    build_output_basename,
    collect_sections,
    create_paper_output_dir,
    determine_images_dir,
    determine_output_dir,
    parse_args,
)
from arxiv2md_beta.fetch import fetch_arxiv_pdf
from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.local_ingestion import ingest_local_archive
from arxiv2md_beta.query_parser import is_local_archive_path, parse_arxiv_input, parse_local_archive
from arxiv2md_beta.utils.logging_config import configure_logging, get_logger

logger = get_logger()


def main() -> None:
    """Run the CLI entry point for arXiv ingestion."""
    configure_logging(level="INFO")
    args = parse_args()

    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as exc:
        logger.error(f"Error: {exc}")
        sys.exit(1)


async def _async_main(args) -> None:
    """Async main function."""
    input_text = args.input_text

    # Determine if this is a local archive path
    if is_local_archive_path(input_text):
        await _process_local_archive(args)
    else:
        await _process_arxiv_paper(args)


async def _process_arxiv_paper(args) -> None:
    """Process an arXiv paper (original workflow)."""
    # Parse arXiv input
    query = parse_arxiv_input(args.input_text)

    # Collect section filters
    sections = collect_sections(args.sections, args.section)

    # Determine base output directory
    base_output_dir = determine_output_dir(args.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing arXiv paper: {query.arxiv_id}")
    logger.info(f"Parser mode: {args.parser}")

    # Ingest paper (this will return metadata including title and date)
    result, metadata = await ingest_paper(
        arxiv_id=query.arxiv_id,
        version=query.version,
        html_url=query.html_url,
        ar5iv_url=query.ar5iv_url,
        parser=args.parser,
        remove_refs=args.remove_refs,
        remove_toc=args.remove_toc,
        remove_inline_citations=args.remove_inline_citations,
        section_filter_mode=args.section_filter_mode,
        sections=sections,
        base_output_dir=base_output_dir,
        no_images=args.no_images,
        source=args.source,
        short=args.short,
    )

    # Get submission date and title from metadata
    submission_date = metadata.get("submission_date")
    title = metadata.get("title")

    # Use the paper_output_dir from ingestion (already created)
    paper_output_dir = metadata.get("paper_output_dir")
    if paper_output_dir is None:
        # Fallback: create directory if not provided (for backward compatibility)
        paper_output_dir = create_paper_output_dir(
            base_output_dir, submission_date, title,
            source=args.source, short=args.short,
        )
    else:
        # Ensure it's a Path object
        if isinstance(paper_output_dir, str):
            paper_output_dir = Path(paper_output_dir)
    logger.info(f"Output directory: {paper_output_dir}")

    # Format output
    output_text = _format_output(
        result.summary,
        result.sections_tree,
        result.content,
        include_tree=args.include_tree,
    )

    # Determine output filename (use same format as directory: [Date]-[Source]-[Short]-[Paper Name])
    if submission_date and title:
        # Reserve 3 chars for .md extension, so max basename = 252
        basename = build_output_basename(
            submission_date, title,
            source=args.source, short=args.short,
            max_title_length=220,
            max_basename_length=252,
        )
        output_filename = f"{basename}.md"
    else:
        base_id = query.arxiv_id.split("v")[0] if "v" in query.arxiv_id else query.arxiv_id
        output_filename = f"{base_id}.md"

    output_path = paper_output_dir / output_filename

    # Write to file
    output_path.write_text(output_text, encoding="utf-8")
    logger.info(f"Output written to: {output_path}")

    # Download PDF (use same filename as markdown, but with .pdf extension)
    try:
        # PDF filename should match the markdown filename format
        pdf_filename = output_filename.replace(".md", ".pdf")
        pdf_path = paper_output_dir / pdf_filename
        await fetch_arxiv_pdf(query.arxiv_id, pdf_path, query.version)
        logger.info(f"PDF downloaded to: {pdf_path}")
    except Exception as e:
        logger.warning(f"Failed to download PDF: {e}")

    # Print summary
    print("\nSummary:")
    try:
        print(result.summary)
    except UnicodeEncodeError:
        print(result.summary.encode("utf-8", errors="replace").decode("utf-8"))


async def _process_local_archive(args) -> None:
    """Process a local archive file (tar.gz, tgz, or zip)."""
    # Parse local archive input
    query = parse_local_archive(args.input_text)

    # Collect section filters
    sections = collect_sections(args.sections, args.section)

    # Determine base output directory
    base_output_dir = determine_output_dir(args.output)
    base_output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Processing local archive: {query.archive_path}")
    logger.info(f"Archive type: {query.archive_type}")

    # Ingest local archive
    result, metadata = await ingest_local_archive(
        query=query,
        base_output_dir=base_output_dir,
        source=args.source,
        short=args.short,
        no_images=args.no_images,
        remove_refs=args.remove_refs,
        remove_toc=args.remove_toc,
        remove_inline_citations=args.remove_inline_citations,
        section_filter_mode=args.section_filter_mode,
        sections=sections,
    )

    # Get submission date and title from metadata
    submission_date = metadata.get("submission_date")
    title = metadata.get("title")

    # Use the paper_output_dir from ingestion (already created)
    paper_output_dir = metadata.get("paper_output_dir")
    if paper_output_dir is None:
        # Fallback: create directory if not provided
        paper_output_dir = create_paper_output_dir(
            base_output_dir, submission_date, title,
            source=args.source, short=args.short,
        )
    else:
        # Ensure it's a Path object
        if isinstance(paper_output_dir, str):
            paper_output_dir = Path(paper_output_dir)
    logger.info(f"Output directory: {paper_output_dir}")

    # Format output
    output_text = _format_output(
        result.summary,
        result.sections_tree,
        result.content,
        include_tree=args.include_tree,
    )

    # Determine output filename
    if submission_date and title:
        basename = build_output_basename(
            submission_date, title,
            source=args.source, short=args.short,
            max_title_length=220,
            max_basename_length=252,
        )
        output_filename = f"{basename}.md"
    else:
        # Use archive name as fallback
        output_filename = f"{query.archive_path.stem}.md"

    output_path = paper_output_dir / output_filename

    # Write to file
    output_path.write_text(output_text, encoding="utf-8")
    logger.info(f"Output written to: {output_path}")

    # Note: PDF is not downloaded for local archives (they don't have arXiv PDFs)
    # But we can copy the archive itself if needed
    logger.info("Local archive processed successfully (no PDF download for local archives)")

    # Print summary
    print("\nSummary:")
    try:
        print(result.summary)
    except UnicodeEncodeError:
        print(result.summary.encode("utf-8", errors="replace").decode("utf-8"))


def _format_output(summary: str, tree: str, content: str, *, include_tree: bool) -> str:
    """Format final output."""
    if include_tree:
        return f"{summary}\n\n{tree}\n\n{content}".strip()
    return f"{summary}\n\n{content}".strip()


if __name__ == "__main__":
    main()

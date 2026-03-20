"""Demo script for arxiv2md-beta."""

from __future__ import annotations

import asyncio
from pathlib import Path

from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.query_parser import parse_arxiv_input


async def main():
    """Demo main function."""
    # Example arXiv ID
    arxiv_id = "2501.11120"  # Replace with a real arXiv ID for testing

    print(f"Demo: Converting arXiv paper {arxiv_id} to Markdown")
    print("=" * 60)

    # Parse input
    query = parse_arxiv_input(arxiv_id)
    print(f"Parsed arXiv ID: {query.arxiv_id}")
    print(f"Version: {query.version}")

    # Create output directory
    output_dir = Path("demo_output")
    output_dir.mkdir(exist_ok=True)

    # Ingest paper (HTML mode)
    print("\nIngesting paper in HTML mode...")
    try:
        result, metadata = await ingest_paper(
            arxiv_id=query.arxiv_id,
            version=query.version,
            html_url=query.html_url,
            ar5iv_url=query.ar5iv_url,
            parser="html",
            remove_refs=False,
            remove_toc=False,
            remove_inline_citations=False,
            section_filter_mode="exclude",
            sections=[],
            output_dir=output_dir,
            images_dir_name="images",
            no_images=False,  # Set to True to skip images for faster demo
        )

        # Write output
        output_file = output_dir / f"{query.arxiv_id}.md"
        output_text = f"{result.summary}\n\n{result.content}"
        output_file.write_text(output_text, encoding="utf-8")

        print(f"\n✓ Success! Output written to: {output_file}")
        print(f"\nSummary:\n{result.summary}")

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())

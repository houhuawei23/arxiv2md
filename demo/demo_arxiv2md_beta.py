"""Demo: convert an arXiv paper to Markdown via the public CLI flow.

Uses the same entry point as the ``arxiv2md-beta convert`` command
(``run_convert_flow``), so the demo exercises the real pipeline —
output layout, quality gate, images, sidecars, and the per-paper manifest.

Run from the repo root::

    python demo/demo_arxiv2md_beta.py            # HTML mode (default)
    python demo/demo_arxiv2md_beta.py --latex    # LaTeX (pandoc) mode
"""

from __future__ import annotations

import argparse
from pathlib import Path

from arxiv2md_beta.cli.runner.convert import run_convert_flow
from arxiv2md_beta.network.http import run_async
from arxiv2md_beta.output.manifest import read_paper_manifest
from arxiv2md_beta.params import ConvertParams

DEMO_ARXIV_ID = "1706.03762"  # "Attention Is All You Need"


def build_params(output_dir: Path, *, parser: str) -> ConvertParams:
    return ConvertParams(
        input_text=DEMO_ARXIV_ID,
        parser=parser,
        output=str(output_dir),
        source="Arxiv",
        short=None,
        no_images=True,  # keep the demo fast; set False to fetch TeX images
        remove_refs=False,
        remove_inline_citations=False,
        section_filter_mode="exclude",
        sections=None,
        section=None,
        include_tree=True,
        structured_output="none",
        emit_graph_csv=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="arxiv2md-beta demo")
    parser.add_argument("--latex", action="store_true", help="use the LaTeX (pandoc) parser")
    parser.add_argument("--output", default="demo_output", help="base output directory")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    params = build_params(output_dir, parser="latex" if args.latex else "html")

    print(f"Demo: converting arXiv paper {DEMO_ARXIV_ID} to Markdown ({params.parser} mode)")
    print("=" * 60)

    # run_async tears the shared HTTP client down cleanly afterwards.
    paper_dir = run_async(run_convert_flow(params))

    print("\n✓ Success!")
    print(f"  Output directory: {paper_dir}")
    for generated in sorted(paper_dir.iterdir()):
        print(f"  - {generated.name}")
    manifest = read_paper_manifest(paper_dir)
    if manifest:
        print(f"  Status: {manifest.get('status')} ({manifest.get('content_bytes', 0)} bytes)")


if __name__ == "__main__":
    main()

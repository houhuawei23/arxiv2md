"""Shared Typer option annotations for ``convert`` and ``batch``.

Both commands expose the same option set; defining the ``typer.Option`` info
objects once here prevents the two signatures from drifting (``batch``
historically dropped fields like ``--no-cache``).
"""

from __future__ import annotations

import typer

PARSER_OPT = typer.Option(None, "--parser", help="Parser mode: html or latex.")
OUTPUT_OPT = typer.Option(None, "--output", "-o", help="Output directory; a subdirectory may be created inside.")
SOURCE_OPT = typer.Option(None, "--source", help="Article source (conference/journal name).")
SHORT_OPT = typer.Option(None, "--short", help="Short name for the article.")
NO_IMAGES_OPT = typer.Option(False, "--no-images", help="Skip downloading and inserting images (HTML mode only).")
REMOVE_REFS_OPT = typer.Option(False, "--remove-refs", help="Remove bibliography/references sections from output.")
REMOVE_INLINE_CITATIONS_OPT = typer.Option(
    False, "--remove-inline-citations", help="Remove inline citation text from output."
)
SECTION_FILTER_MODE_OPT = typer.Option(None, "--section-filter-mode", help="Section filtering: include or exclude.")
SECTIONS_OPT = typer.Option(None, "--sections", help='Comma-separated section titles (e.g. "Abstract,Introduction").')
SECTION_OPT = typer.Option([], "--section", help="Repeatable section title filter.")
INCLUDE_TREE_OPT = typer.Option(False, "--include-tree", help="Include the section tree before the Markdown content.")
NO_PROGRESS_OPT = typer.Option(
    False,
    "--no-progress",
    help="Disable Rich progress bars (downloads, images); logs still show milestones.",
)
EMIT_RESULT_JSON_OPT = typer.Option(
    False,
    "--emit-result-json",
    help="Print one line ARXIV2MD_RESULT_JSON={...} with paper_output_dir for scripting.",
)
STRUCTURED_OUTPUT_OPT = typer.Option(
    "none",
    "--structured-output",
    help="Emit versioned JSON next to Markdown: none | meta | document | full | all.",
)
EMIT_GRAPH_CSV_OPT = typer.Option(
    False,
    "--emit-graph-csv",
    help="With --structured-output all, also write paper.graph.nodes.csv and paper.graph.edges.csv.",
)
NO_CACHE_OPT = typer.Option(False, "--no-cache", help="Disable download caching for TeX source, HTML, and PDF.")
INCLUDE_ANCHORS_OPT: bool | None = typer.Option(
    None,
    "--include-anchors/--no-include-anchors",
    help='Emit <a id="..."></a> anchor tags in the generated Markdown (default: settings).',
)
LINKED_CITATIONS_OPT: bool | None = typer.Option(
    None,
    "--linked-citations/--no-linked-citations",
    help="Render inline citations as linked [N](#ref-N) instead of plain [N] (default: settings).",
)
NAMING_SCHEME_OPT = typer.Option(
    None,
    "--naming-scheme",
    help="Output naming scheme: arxiv-ym (default), paper-pipeline, or classic.",
)
DOWNLOAD_PDF_OPT = typer.Option(
    True,
    "--download-pdf/--skip-pdf-download",
    help="Download the arXiv PDF into the output directory (default: True).",
)

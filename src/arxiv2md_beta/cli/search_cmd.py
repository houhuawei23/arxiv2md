"""``search`` command: arXiv title-phrase lookup to verify IDs before converting.

Registered on the main Typer app (single command, no sub-group) by
``cli/app.py``.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from arxiv2md_beta.network.arxiv_search import search_arxiv


def search_cmd(
    query: str = typer.Argument(
        ...,
        metavar="QUERY",
        help='Title phrase or arXiv field expression (e.g. ti:"fourier neural operator").',
    ),
    author: list[str] = typer.Option(
        [],
        "--author",
        help='Author filter; repeatable. Appends AND au:"<name>" to the query.',
    ),
    field: str = typer.Option(
        "all",
        "--field",
        help="Field for plain phrases: ti (title), all, or abs (abstract).",
    ),
    sort: str = typer.Option(
        "relevance",
        "--sort",
        help="Result ordering: relevance or submitted.",
    ),
    start: int = typer.Option(0, "--start", help="Result offset for pagination."),
    max_results: int = typer.Option(
        10,
        "--max-results",
        "-n",
        help="Maximum number of results (arXiv API page size).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print a JSON array instead of a table (for scripting).",
    ),
) -> None:
    """Search arXiv and print id / date / title / first author / category.

    Used to confirm the arXiv ID of a paper before converting it: never trust
    a remembered ID — check the title here, then convert the confirmed id.
    """
    try:
        from arxiv2md_beta.network.http import run_async

        results: list[dict[str, Any]] = run_async(
            search_arxiv(
                query,
                authors=list(author),
                field=field,
                sort=sort,
                start=start,
                max_results=max_results,
            )
        )
    except Exception as exc:
        from arxiv2md_beta.cli.app import _handle_command_error
        from arxiv2md_beta.utils.logging_config import get_logger

        _handle_command_error(get_logger(), exc)
        return  # unreachable; _handle_command_error always raises

    if json_output:
        print(
            json.dumps(
                [
                    {
                        "arxiv_id": r.get("arxiv_id"),
                        "title": r.get("title"),
                        "submission_date": r.get("submission_date"),
                        "authors": [a.get("name") for a in (r.get("authors") or []) if a.get("name")],
                        "primary_category": r.get("primary_category"),
                        "abstract_url": r.get("abstract_url"),
                    }
                    for r in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        if not results:
            typer.echo(f"No results for: {query}", err=True)
            raise typer.Exit(code=1)

    if not results:
        # Distinct from success so scripts can tell "nothing found" apart
        # from a valid empty answer (audit4 P3).
        typer.echo(f"No results for: {query}", err=True)
        raise typer.Exit(code=1)

    table = Table(title=f'arXiv search: "{query}"', show_lines=False)
    table.add_column("arxiv_id", style="cyan", no_wrap=True)
    table.add_column("date", no_wrap=True)
    table.add_column("title", overflow="fold")
    table.add_column("first author", overflow="fold")
    table.add_column("category", no_wrap=True)
    for r in results:
        authors = [a.get("name") for a in (r.get("authors") or []) if a.get("name")]
        first = f"{authors[0]} et al." if len(authors) > 1 else (authors[0] if authors else "-")
        date = r.get("submission_date") or ""
        table.add_row(
            r.get("arxiv_id") or "-",
            date[:6],
            r.get("title") or "-",
            first,
            r.get("primary_category") or "-",
        )
    Console(stderr=False).print(table)

"""Paper-yml command runner."""

from __future__ import annotations

import asyncio
from pathlib import Path

from arxiv2md_beta.cli.params import PaperYmlParams
from arxiv2md_beta.network.arxiv_api import fetch_arxiv_metadata
from arxiv2md_beta.output.metadata import (
    arxiv_id_from_paper_yml_dict,
    load_paper_yml,
    write_paper_yml_file,
)
from arxiv2md_beta.output.metadata_tex import fetch_and_merge_tex_affiliations_for_metadata
from arxiv2md_beta.output.paper_yml_path import resolve_paper_yml_output_path
from arxiv2md_beta.query.parser import parse_arxiv_input
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.metrics import async_timed_operation

logger = get_logger()


async def run_paper_yml_flow(params: PaperYmlParams) -> Path:
    """Fetch arXiv metadata and write ``paper.yml`` (refresh existing or new path)."""
    async with async_timed_operation("run_paper_yml_flow"):
        if params.update_path is not None:
            path = Path(params.update_path).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"paper.yml not found: {path}")
            existing_yml = load_paper_yml(path)
            aid = arxiv_id_from_paper_yml_dict(existing_yml)
            logger.info(f"paper-yml --update: read arXiv id {aid!r} from {path}")
            meta = await fetch_arxiv_metadata(aid)
            query = parse_arxiv_input(aid)
            await fetch_and_merge_tex_affiliations_for_metadata(
                meta, query.arxiv_id, query.version
            )
            write_paper_yml_file(meta, path, merge_existing=existing_yml)
            print(str(path.resolve()))
            return path

        raw = (params.arxiv_input or "").strip()
        if not raw:
            raise ValueError("Provide ARXIV (id or URL) or use --update PATH")
        out = (params.output or "").strip()
        if not out:
            raise ValueError("Provide --output /path/to/paper.yml when not using --update")

        query = parse_arxiv_input(raw)
        logger.info(f"paper-yml: fetching metadata for {query.arxiv_id}")
        meta = await fetch_arxiv_metadata(query.arxiv_id)
        await fetch_and_merge_tex_affiliations_for_metadata(
            meta, query.arxiv_id, query.version
        )
        out_path = Path(out).expanduser()
        primary = out_path
        if primary.is_dir():
            primary = primary / "paper.yml"
        elif primary.suffix.lower() not in (".yml", ".yaml"):
            primary = primary / "paper.yml"
        primary = primary.resolve()
        dest = resolve_paper_yml_output_path(out_path, force=params.force)
        if not params.force and primary.exists() and dest.resolve() != primary:
            logger.info(
                f"Primary output {primary} exists; writing to {dest} (use --force to overwrite)"
            )
        write_paper_yml_file(meta, dest)
        print(str(dest.resolve()))
        return dest


def run_paper_yml_sync(params: PaperYmlParams) -> Path:
    """Run paper-yml flow in a fresh event loop."""
    return asyncio.run(run_paper_yml_flow(params))

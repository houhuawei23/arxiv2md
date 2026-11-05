"""Paper-yml command runner."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.cli.params import PaperYmlParams
from arxiv2md_beta.exceptions import UserInputError
from arxiv2md_beta.network.arxiv_api import fetch_arxiv_metadata
from arxiv2md_beta.output.metadata import (
    PAPER_YML_SCHEMA_VERSION,
    arxiv_id_from_paper_yml_dict,
    load_paper_yml,
    migrate_paper_yml_v1_to_v2,
    write_paper_yml_file,
)
from arxiv2md_beta.output.metadata_tex import fetch_and_merge_tex_affiliations_for_metadata
from arxiv2md_beta.output.paper_yml_path import resolve_paper_yml_output_path
from arxiv2md_beta.query.parser import parse_arxiv_input
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.timing import async_timed_operation

logger = get_logger()


async def run_paper_yml_flow(params: PaperYmlParams) -> Path:
    """Fetch arXiv metadata and write ``paper.yml`` (refresh existing or new path)."""
    async with async_timed_operation("run_paper_yml_flow"):
        if params.migrate_path is not None:
            return _run_paper_yml_migrate(params.migrate_path)
        if params.update_path is not None:
            path = Path(params.update_path).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"paper.yml not found: {path}")
            existing_yml = load_paper_yml(path)
            aid = arxiv_id_from_paper_yml_dict(existing_yml)
            logger.info(f"paper-yml --update: read arXiv id {aid!r} from {path}")
            meta = await fetch_arxiv_metadata(aid)
            if not params.force and not str(meta.get("title") or "").strip():
                # Degraded API response (timeout / transient failure resolves
                # to a skeleton with title=None): overwriting the user's file
                # with a placeholder would destroy real data (audit4 A4).
                raise UserInputError(
                    f"arXiv metadata for {aid!r} is unavailable (degraded response); "
                    f"refusing to overwrite {path.name}. Retry later or pass --force."
                )
            query = parse_arxiv_input(aid)
            await fetch_and_merge_tex_affiliations_for_metadata(meta, query.arxiv_id, query.version)
            from arxiv2md_beta.network.author_enrichment import apply_openalex_affiliation_gap_fill

            apply_openalex_affiliation_gap_fill(meta)
            write_paper_yml_file(meta, path, merge_existing=existing_yml, refresh=params.refresh)
            print(str(path.resolve()))
            return path

        raw = (params.arxiv_input or "").strip()
        if not raw:
            raise UserInputError("Provide ARXIV (id or URL) or use --update PATH")
        out = (params.output or "").strip()
        if not out:
            raise UserInputError("Provide --output /path/to/paper.yml when not using --update")

        query = parse_arxiv_input(raw)
        logger.info(f"paper-yml: fetching metadata for {query.arxiv_id}")
        meta = await fetch_arxiv_metadata(query.arxiv_id)
        await fetch_and_merge_tex_affiliations_for_metadata(meta, query.arxiv_id, query.version)
        from arxiv2md_beta.network.author_enrichment import apply_openalex_affiliation_gap_fill

        apply_openalex_affiliation_gap_fill(meta)
        out_path = Path(out).expanduser()
        primary = out_path
        if primary.is_dir() or primary.suffix.lower() not in (".yml", ".yaml"):
            primary = primary / "paper.yml"
        primary = primary.resolve()
        dest = resolve_paper_yml_output_path(out_path, force=params.force)
        if not params.force and primary.exists() and dest.resolve() != primary:
            logger.info(f"Primary output {primary} exists; writing to {dest} (use --force to overwrite)")
        write_paper_yml_file(meta, dest)
        print(str(dest.resolve()))
        return dest


def _run_paper_yml_migrate(path: Path) -> Path:
    """Upgrade a paper.yml from schema v1 to v2 in place (offline, idempotent).

    Extracts ``paper.bibtex`` to the sibling ``paper.bib`` and rewrites the
    YAML via :func:`write_paper_yml_file`'s backup protocol (merge_existing
    migration), so a crash can never lose the original file. A file already
    at v2 passes through untouched.

    The actual migration runs inside ``write_paper_yml_file`` — running it
    here first would consume the extracted bibtex before the sidecar writer
    sees it; a probe on a deep copy only decides no-op vs rewrite.
    """
    import copy

    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"paper.yml not found: {path}")
    existing = load_paper_yml(path)
    _, extracted_bibtex = migrate_paper_yml_v1_to_v2(copy.deepcopy(existing))
    if extracted_bibtex is None and existing.get("paper", {}).get("schema") == PAPER_YML_SCHEMA_VERSION:
        print(str(path))
        return path
    aid = arxiv_id_from_paper_yml_dict(existing)
    write_paper_yml_file(
        {"arxiv_id": aid, "title": existing.get("paper", {}).get("title") or ""},
        path,
        merge_existing=existing,
    )
    print(str(path))
    return path


def run_paper_yml_sync(params: PaperYmlParams) -> Path:
    """Run paper-yml flow in a fresh event loop."""
    from arxiv2md_beta.network.http import run_async

    return run_async(run_paper_yml_flow(params))

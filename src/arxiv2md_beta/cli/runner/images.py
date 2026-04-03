"""Images command runner."""

from __future__ import annotations

import asyncio

from arxiv2md_beta.cli.params import ImagesParams
from arxiv2md_beta.images.extract import extract_arxiv_images
from arxiv2md_beta.output.layout import determine_output_dir
from arxiv2md_beta.query.parser import parse_arxiv_input
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.utils.logging_config import get_logger
from arxiv2md_beta.utils.metrics import async_timed_operation

logger = get_logger()


async def run_images_flow(params: ImagesParams) -> None:
    """Download TeX source and write processed images only."""
    async with async_timed_operation("run_images_flow"):
        s = get_settings()
        raw = params.arxiv_input.strip()
        if not raw:
            raise ValueError("arxiv input cannot be empty")
        query = parse_arxiv_input(raw)
        base_output_dir = determine_output_dir(params.output, settings=s)
        base_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Images-only mode: arXiv {query.arxiv_id}")
        logger.info(f"Output root: {base_output_dir}")
        logger.info(f"Images subdirectory: {params.images_subdir}")

        processed = await extract_arxiv_images(
            arxiv_id=query.arxiv_id,
            version=query.version,
            output_dir=base_output_dir,
            images_subdir=params.images_subdir,
            use_tex_cache=not params.no_tex_cache,
        )

        n = len(processed.image_map)
        logger.info(f"Processed {n} image(s) -> {processed.images_dir}")
        print(f"Images directory: {processed.images_dir}")
        print(f"Image count: {n}")
        if n:
            for i, rel in sorted(processed.image_map.items()):
                print(f"  [{i}] {rel}")


def run_images_sync(params: ImagesParams) -> None:
    """Run images-only flow in a fresh event loop (Typer entry)."""
    asyncio.run(run_images_flow(params))

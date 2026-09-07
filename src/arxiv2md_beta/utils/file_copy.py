"""Shared file-copy helper used by the local-archive and local-HTML paths."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from loguru import logger


def copy_images_flat(source_files: Iterable[Path], images_dir: Path) -> int:
    """Copy *source_files* into *images_dir*, flattened.

    Same-named files get ``_1``/``_2`` suffixes instead of silently
    overwriting each other. Returns the number of files copied.
    """
    copied = 0
    for img_file in source_files:
        try:
            dest_path = images_dir / img_file.name
            counter = 1
            original_dest = dest_path
            while dest_path.exists():
                dest_path = images_dir / f"{original_dest.stem}_{counter}{original_dest.suffix}"
                counter += 1
            shutil.copy2(img_file, dest_path)
            copied += 1
            logger.debug(f"Copied image: {img_file} -> {dest_path}")
        except OSError as e:
            logger.warning(f"Failed to copy image {img_file}: {e}")
    return copied

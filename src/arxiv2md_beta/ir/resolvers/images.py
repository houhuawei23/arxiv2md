"""Unified image path resolution for HTML and LaTeX builders.

Supports multi-strategy fallback:
    exact path match → stem match → index match → original src
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ImageResolver:
    r"""Resolve image ``src`` attributes to local processed paths.

    Parameters
    ----------
    index_map :
        HTML builder: mapping from 0-based figure index to local path.
    stem_map :
        HTML builder: mapping from TeX stem to local path.
    path_map :
        LaTeX builder: mapping from original ``\includegraphics`` path to
        local path.

    All value types accept :class:`~pathlib.Path` or ``str``.
    """

    def __init__(
        self,
        index_map: dict[int, Any] | None = None,
        stem_map: dict[str, Any] | None = None,
        path_map: dict[str, Any] | None = None,
    ) -> None:
        self._index_map: dict[int, Path] = {
            k: Path(v) for k, v in (index_map or {}).items()
        }
        self._stem_map: dict[str, Path] = {
            k: Path(v) for k, v in (stem_map or {}).items()
        }
        self._path_map: dict[str, Path] = {
            k: Path(v) for k, v in (path_map or {}).items()
        }
        self._used_indices: set[int] = set()
        self._cache: dict[str, str] = {}

    # ── Public API ─────────────────────────────────────────────────────

    def resolve(self, src: str, *, figure_index: int | None = None) -> str:
        """Return a local path for *src* if known, otherwise *src* unchanged."""
        if src in self._cache:
            return self._cache[src]

        resolved = (
            self._try_exact(src)
            or self._try_stem(src)
            or self._try_index(figure_index)
            or self._try_path_map(src)
        )
        result = str(resolved) if resolved else src
        self._cache[src] = result
        return result

    # ── Internal strategies ────────────────────────────────────────────

    def _try_exact(self, src: str) -> Path | None:
        """Exact match in *path_map*."""
        if src in self._path_map:
            return self._path_map[src]
        return None

    def _try_stem(self, src: str) -> Path | None:
        """Match by filename stem (HTML *stem_map* + LaTeX *path_map* stems)."""
        src_basename = src.rsplit("/", 1)[-1] if "/" in src else src
        src_stem = (
            src_basename.rsplit(".", 1)[0]
            if "." in src_basename
            else src_basename
        )

        # HTML stem_map: case-insensitive exact stem match first
        for stem_key, local_path in self._stem_map.items():
            key_stem = (
                stem_key.rsplit(".", 1)[0]
                if "." in stem_key
                else stem_key
            )
            if key_stem.lower() == src_stem.lower():
                return local_path

        # Fallback: substring match in basename only (not full path)
        for stem_key, local_path in self._stem_map.items():
            key_stem = (
                stem_key.rsplit(".", 1)[0]
                if "." in stem_key
                else stem_key
            )
            if key_stem.lower() in src_basename.lower():
                return local_path

        # LaTeX path_map: stem / name match
        path_obj = Path(src)
        for key, val in self._path_map.items():
            if Path(key).stem == path_obj.stem or Path(key).name == path_obj.name:
                return val

        return None

    def _try_index(self, figure_index: int | None) -> Path | None:
        """Match by 1-based figure index (HTML *index_map*)."""
        if figure_index is None:
            return None

        # 1-based lookup
        if figure_index in self._index_map and figure_index not in self._used_indices:
            self._used_indices.add(figure_index)
            return self._index_map[figure_index]

        # 0-based fallback
        if figure_index - 1 in self._index_map and (figure_index - 1) not in self._used_indices:
            self._used_indices.add(figure_index - 1)
            return self._index_map[figure_index - 1]

        return None

    def _try_path_map(self, src: str) -> Path | None:
        """Name / stem lookup in *path_map* (LaTeX builder fallback)."""
        path_obj = Path(src)
        if path_obj.name in self._path_map:
            return self._path_map[path_obj.name]
        if path_obj.stem in self._path_map:
            return self._path_map[path_obj.stem]
        return None

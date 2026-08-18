"""Unified image path resolution for HTML and LaTeX builders.

Strategy chain (first hit wins):
    exact path_map key → exact stem (case-insensitive) → path_map stem/name
    → ar5iv xN.png name → figure index → path_map name/stem → loose stem
    (word-boundary substring) → original src

The loose substring match runs *last* so a short stem key ("fig") can never
shadow a precise strategy for a sibling file ("fig2.png").
"""

from __future__ import annotations

import re
from collections.abc import Iterator
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

    # ar5iv renames rasterized float figures: x1.png, x2.png, ... in figure order.
    _XNAME_RE = re.compile(r"^x(\d+)\.png$", re.IGNORECASE)

    def __init__(
        self,
        index_map: dict[int, Any] | None = None,
        stem_map: dict[str, Any] | None = None,
        path_map: dict[str, Any] | None = None,
    ) -> None:
        self._index_map: dict[int, Path] = {k: Path(v) for k, v in (index_map or {}).items()}
        self._stem_map: dict[str, Path] = {k: Path(v) for k, v in (stem_map or {}).items()}
        self._path_map: dict[str, Path] = {k: Path(v) for k, v in (path_map or {}).items()}
        self._used_indices: set[int] = set()
        # figure_index → next 0-based index to hand out (subfigure continuation)
        self._figure_next: dict[int, int] = {}
        self._cache: dict[str, str] = {}

    # ── Public API ─────────────────────────────────────────────────────

    def resolve(self, src: str, *, figure_index: int | None = None) -> str:
        """Return a local path for *src* if known, otherwise *src* unchanged."""
        cache_key = f"{src}@{figure_index}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        resolved = (
            self._try_exact(src)
            or self._try_stem(src)
            or self._try_xname(src)
            or self._try_index(figure_index)
            or self._try_path_map(src)
            or self._try_stem_loose(src)
        )
        result = str(resolved) if resolved else src
        self._cache[cache_key] = result
        return result

    def iter_assets(self) -> Iterator[tuple[str | int, Path]]:
        """Yield ``(key, path)`` for every resolved asset.

        Keys are the ``str`` stems from the stem map (yielded first) and the
        ``int`` indices from the index map (yielded in ascending order). Exposes
        the resolver's asset inventory so callers do not reach into the private
        ``_stem_map`` / ``_index_map`` attributes.
        """
        yield from self._stem_map.items()
        for idx in sorted(self._index_map):
            yield idx, self._index_map[idx]

    # ── Internal strategies ────────────────────────────────────────────

    def _try_exact(self, src: str) -> Path | None:
        """Exact match in *path_map*."""
        if src in self._path_map:
            return self._path_map[src]
        return None

    def _try_stem(self, src: str) -> Path | None:
        """Precise stem matching: exact (case-insensitive) stem + path_map stem/name."""
        src_basename = src.rsplit("/", 1)[-1] if "/" in src else src
        src_stem = src_basename.rsplit(".", 1)[0] if "." in src_basename else src_basename

        # HTML stem_map: case-insensitive exact stem match
        for stem_key, local_path in self._stem_map.items():
            key_stem = stem_key.rsplit(".", 1)[0] if "." in stem_key else stem_key
            if key_stem.lower() == src_stem.lower():
                return local_path

        # LaTeX path_map: stem / name match
        path_obj = Path(src)
        for key, val in self._path_map.items():
            if Path(key).stem == path_obj.stem or Path(key).name == path_obj.name:
                return val

        return None

    def _try_stem_loose(self, src: str) -> Path | None:
        """Last-resort stem match by word-boundary substring.

        Only tried after every precise strategy failed. The boundary guards
        (``(?<![A-Za-z0-9])key(?![A-Za-z0-9])``) keep a short key like ``fig``
        from matching a sibling file like ``fig2.png`` while still matching
        wrapped names like ``prefix_fig_suffix.png``.
        """
        src_basename = src.rsplit("/", 1)[-1] if "/" in src else src
        lowered = src_basename.lower()
        for stem_key, local_path in self._stem_map.items():
            key_stem = stem_key.rsplit(".", 1)[0] if "." in stem_key else stem_key
            pattern = rf"(?<![A-Za-z0-9]){re.escape(key_stem.lower())}(?![A-Za-z0-9])"
            if re.search(pattern, lowered):
                return local_path
        return None

    def _try_xname(self, src: str) -> Path | None:
        """Resolve ar5iv opaque float names ``x1.png``, ``x2.png``, ...

        ar5iv renames rasterized float figures to ``xN.png`` in figure order.
        ``N`` is 1-based and matches the TeX float-figure order, so it maps
        directly to ``index_map[N-1]`` (0-based). This is more precise than
        positional ``figure_index`` fallback when an HTML page mixes float
        and inline images.
        """
        basename = src.rsplit("/", 1)[-1] if "/" in src else src
        m = self._XNAME_RE.match(basename)
        if not m:
            return None
        idx = int(m.group(1)) - 1
        if idx in self._index_map and idx not in self._used_indices:
            self._used_indices.add(idx)
            return self._index_map[idx]
        return None

    def _try_index(self, figure_index: int | None) -> Path | None:
        """Match by figure index (HTML *index_map*).

        *index_map* is 0-based (the convention used by image processing), while
        *figure_index* is 1-based (the HTML figure counter). Repeated calls with
        the same *figure_index* (a multi-subfigure figure) hand out consecutive
        indices starting at ``figure_index - 1``. If that base index is missing
        or already consumed by another figure, we return ``None`` and let the
        later strategies decide — consuming a farther index would steal an
        image that belongs to another figure.
        """
        if figure_index is None:
            return None

        # Subfigure continuation: same figure asking for its next image.
        if figure_index in self._figure_next:
            nxt = self._figure_next[figure_index]
            if nxt in self._index_map and nxt not in self._used_indices:
                self._used_indices.add(nxt)
                self._figure_next[figure_index] = nxt + 1
                return self._index_map[nxt]
            return None

        # First request for this figure: 0-based base index.
        base = figure_index - 1
        if base in self._index_map and base not in self._used_indices:
            self._used_indices.add(base)
            self._figure_next[figure_index] = base + 1
            return self._index_map[base]

        # Backward-compatible 1-based lookup — only for maps that actually
        # look 1-based (no 0 key). Otherwise a dense 0-based map would let a
        # failed base lookup steal the next figure's image.
        if 0 not in self._index_map and figure_index in self._index_map and figure_index not in self._used_indices:
            self._used_indices.add(figure_index)
            self._figure_next[figure_index] = figure_index + 1
            return self._index_map[figure_index]

        return None

    def _try_path_map(self, src: str) -> Path | None:
        """Name / stem lookup in *path_map* (LaTeX builder fallback)."""
        path_obj = Path(src)
        if path_obj.name in self._path_map:
            return self._path_map[path_obj.name]
        if path_obj.stem in self._path_map:
            return self._path_map[path_obj.stem]
        return None

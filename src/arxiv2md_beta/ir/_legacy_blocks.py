"""Extract coarse block-level IR from ar5iv HTML fragments."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from arxiv2md_beta.schemas.structured import BlockJson

try:
    from bs4 import BeautifulSoup
    from bs4.element import NavigableString, Tag
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("BeautifulSoup4 is required.") from exc


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _plain_from_tag(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()


def _make_id(section_id: str, idx: int, suffix: str) -> str:
    return f"{section_id}:b{idx}:{suffix}"


def extract_blocks_from_html(html: str | None, section_id: str) -> list[BlockJson]:
    """Parse a section (or abstract) HTML fragment into ordered blocks."""
    if not html or not html.strip():
        return []

    soup = BeautifulSoup(f"<div class='arxiv2md-root'>{html}</div>", "html.parser")
    root = soup.find("div", class_="arxiv2md-root")
    if root is None:
        return []

    blocks: list[BlockJson] = []
    counter = 0

    def emit(
        block_type: str,
        extra: dict[str, Any],
        plain: str | None,
        md_hint: str | None = None,
    ) -> None:
        nonlocal counter
        bid = _make_id(section_id, counter, block_type)
        blocks.append(
            BlockJson(
                id=bid,
                type=block_type,
                section_id=section_id,
                order_index=counter,
                text_plain=plain[:20000] if plain else None,
                text_md=md_hint[:20000] if md_hint else None,
                extra=extra,
            )
        )
        counter += 1

    for child in root.children:
        if isinstance(child, NavigableString):
            t = str(child).strip()
            if t:
                emit("other", {"raw_text": True}, t)
            continue
        if not isinstance(child, Tag):
            continue
        _emit_tag_block(child, emit)

    return blocks


def _emit_tag_block(tag: Tag, emit) -> None:
    """Classify a top-level tag into a block."""
    name = tag.name
    cls = " ".join(tag.get("class", []))

    if name == "p":
        emit("paragraph", {"tag": "p"}, _plain_from_tag(tag))
        return
    if name == "figure":
        cap = tag.find("figcaption") or tag.find("span", class_=re.compile(r"ltx_caption"))
        cap_text = _plain_from_tag(cap) if cap else None
        img = tag.find("img")
        src = img.get("src") if img else None
        emit(
            "figure",
            {"tag": "figure", "img_src": src, "caption": cap_text},
            cap_text or _plain_from_tag(tag),
        )
        return
    if name == "table":
        emit("table", {"tag": "table"}, _plain_from_tag(tag)[:5000])
        return
    if name in {"ul", "ol"}:
        emit("list", {"tag": name}, _plain_from_tag(tag))
        return
    if name == "blockquote":
        emit("blockquote", {"tag": "blockquote"}, _plain_from_tag(tag))
        return
    if name == "pre":
        emit("code", {"tag": "pre"}, _plain_from_tag(tag))
        return
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        emit("heading", {"tag": name}, _plain_from_tag(tag))
        return

    if name == "div" and "ltx_listing" in cls and "ltx_listingline" not in cls:
        emit("code", {"tag": "div", "class": "ltx_listing"}, _plain_from_tag(tag)[:8000])
        return

    # Equation / equation group
    if name in {"math"} or (name == "div" and re.search(r"ltx_equation|ltx_eqn", cls)):
        emit("equation", {"tag": name}, _plain_from_tag(tag)[:8000])
        return

    if name == "div":
        inner = tag.find_all(
            ["p", "figure", "table", "ul", "ol", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"],
            recursive=False,
        )
        if inner:
            for sub in inner:
                _emit_tag_block(sub, emit)
            return
        for sub in tag.children:
            if isinstance(sub, Tag):
                _emit_tag_block(sub, emit)
        return

    if name == "span":
        # Inline-only spans at top level: treat as paragraph fragment
        t = _plain_from_tag(tag)
        if t:
            emit("other", {"tag": "span"}, t)
        return

    emit("other", {"tag": name}, _plain_from_tag(tag))


def hash_markdown(s: str | None) -> str | None:
    if not s:
        return None
    return _sha256_text(s.strip())


def hash_html(s: str | None) -> str | None:
    if not s:
        return None
    return _sha256_text(s.strip())

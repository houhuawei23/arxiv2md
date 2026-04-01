"""Write versioned JSON (and optional CSV) structured exports next to Markdown."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger

from arxiv2md_beta.ir.blocks import extract_blocks_from_html, hash_html, hash_markdown
from arxiv2md_beta.schemas.sections import SectionNode
from arxiv2md_beta.schemas.structured import (
    SCHEMA_VERSION,
    AssetJson,
    BibEntryJson,
    BlockJson,
    GraphEdgeJson,
    GraphNodeJson,
    PaperAssetsJson,
    PaperBibJson,
    PaperDocumentJson,
    PaperGraphJson,
    PaperMetaJson,
    SectionTreeNodeJson,
)


def normalize_structured_mode(mode: str | None) -> str:
    """Map CLI / config values to ``none`` | ``meta`` | ``document`` | ``full`` | ``all``."""
    if mode is None or not str(mode).strip():
        return "none"
    m = str(mode).strip().lower()
    if m in ("", "none", "off", "false", "0"):
        return "none"
    if m in ("meta", "document", "full", "all"):
        return m
    return "none"


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("arxiv2md-beta")
    except Exception:
        return "0.0.0"


def _sha256_parts(parts: list[str]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def assign_struct_ids(sections: list[SectionNode], prefix: str = "sec") -> None:
    """Mutate sections with stable ``struct_id`` (e.g. sec_0, sec_0_1)."""

    def walk(nodes: list[SectionNode], path: tuple[int, ...]) -> None:
        for i, node in enumerate(nodes):
            sid = prefix + "".join(f"_{p}" for p in (*path, i))
            node.struct_id = sid
            walk(node.children, (*path, i))

    walk(sections, ())


def _section_to_tree_json(node: SectionNode) -> SectionTreeNodeJson:
    return SectionTreeNodeJson(
        struct_id=node.struct_id or "sec_unknown",
        title=node.title,
        level=node.level,
        anchor=node.anchor,
        html_sha256=hash_html(node.html),
        markdown_sha256=hash_markdown(node.markdown),
        children=[_section_to_tree_json(c) for c in node.children],
    )


def _collect_blocks_for_sections(sections: list[SectionNode], out: list[BlockJson]) -> None:
    for node in sections:
        sid = node.struct_id or "sec_unknown"
        if node.html:
            out.extend(extract_blocks_from_html(node.html, sid))
        _collect_blocks_for_sections(node.children, out)


def _collect_asset_ids(
    stem_to_image_path: dict[str, Path] | None,
    image_map: dict[int, Path] | None,
) -> list[str]:
    """Stable ordered list of ``asset:<relative_path>`` ids (deduplicated)."""
    seen: set[str] = set()
    out: list[str] = []
    if stem_to_image_path:
        for _stem, path in stem_to_image_path.items():
            rel = path.as_posix() if isinstance(path, Path) else str(path)
            aid = f"asset:{rel}"
            if aid not in seen:
                seen.add(aid)
                out.append(aid)
    if image_map:
        for _idx, path in sorted(image_map.items()):
            rel = path.as_posix() if isinstance(path, Path) else str(path)
            aid = f"asset:{rel}"
            if aid not in seen:
                seen.add(aid)
                out.append(aid)
    return out


def _content_fingerprint(abstract_md: str | None, sections: list[SectionNode]) -> str:
    parts: list[str] = []
    if abstract_md:
        parts.append(abstract_md)

    def walk(nodes: list[SectionNode]) -> None:
        for n in nodes:
            if n.markdown:
                parts.append(n.markdown)
            walk(n.children)

    walk(sections)
    return _sha256_parts(parts)


def build_graph(
    *,
    arxiv_id: str,
    section_nodes: list[SectionNode],
    blocks: list[BlockJson],
    asset_ids: list[str],
) -> PaperGraphJson:
    """Build a heterogeneous graph: paper — section — block; block — next; block — asset."""
    nodes: list[GraphNodeJson] = []
    edges: list[GraphEdgeJson] = []

    nodes.append(GraphNodeJson(id="paper", type="paper", properties={"arxiv_id": arxiv_id}))

    def walk_sections(parent_id: str, secs: list[SectionNode], is_root: bool) -> None:
        prev_sid: str | None = None
        for sec in secs:
            sid = sec.struct_id or "sec_unknown"
            nodes.append(
                GraphNodeJson(
                    id=sid,
                    type="section",
                    properties={"title": sec.title, "level": sec.level},
                )
            )
            rel = "child_section" if not is_root else "contains"
            edges.append(GraphEdgeJson(src=parent_id, dst=sid, type=rel))
            if prev_sid is not None:
                edges.append(GraphEdgeJson(src=prev_sid, dst=sid, type="next", properties={"scope": "section"}))
            prev_sid = sid
            walk_sections(sid, sec.children, False)

    walk_sections("paper", section_nodes, True)

    by_section: dict[str, list[BlockJson]] = {}
    for b in blocks:
        by_section.setdefault(b.section_id, []).append(b)

    for sid, blist in by_section.items():
        blist.sort(key=lambda x: x.order_index)
        prev_bid: str | None = None
        for b in blist:
            nodes.append(GraphNodeJson(id=b.id, type="block", properties={"block_type": b.type}))
            edges.append(GraphEdgeJson(src=sid, dst=b.id, type="contains"))
            if prev_bid is not None:
                edges.append(GraphEdgeJson(src=prev_bid, dst=b.id, type="next", properties={"scope": "block"}))
            prev_bid = b.id

    for aid in asset_ids:
        nodes.append(GraphNodeJson(id=aid, type="asset", properties={}))
        edges.append(GraphEdgeJson(src="paper", dst=aid, type="contains"))

    return PaperGraphJson(schema_version=SCHEMA_VERSION, arxiv_id=arxiv_id, nodes=nodes, edges=edges)


def write_structured_bundle(
    *,
    paper_output_dir: Path,
    mode: str,
    emit_graph_csv: bool,
    arxiv_id: str,
    arxiv_version: str | None,
    title: str | None,
    authors: list[str],
    submission_date: str | None,
    html_url: str | None,
    ar5iv_url: str | None,
    parser: str,
    sections: list[SectionNode],
    abstract_md: str | None,
    abstract_html: str | None,
    front_matter_html: str | None,
    include_abstract_parts: bool,
    image_map: dict[int, Path] | None,
    stem_to_image_path: dict[str, Path] | None,
    images_subdir: str,
) -> dict[str, Any]:
    """Write ``paper.*.json`` (and optional CSV) according to ``mode``.

    ``mode``: ``none`` | ``meta`` | ``document`` | ``full`` | ``all``.
    Returns a dict of relative paths written (for sidecar / CLI).
    """
    if mode in ("", "none", "off", "false"):
        return {}

    written: dict[str, str] = {}
    tool_version = _package_version()

    # Always assign ids for tree / blocks when exporting anything beyond none
    if mode not in ("none", ""):
        assign_struct_ids(sections)

    fingerprint = _content_fingerprint(abstract_md if include_abstract_parts else None, sections)

    meta = PaperMetaJson(
        schema_version=SCHEMA_VERSION,
        arxiv_id=arxiv_id,
        arxiv_version=arxiv_version,
        title=title,
        authors=authors,
        submission_date=submission_date,
        html_url=html_url,
        ar5iv_url=ar5iv_url,
        tool_version=tool_version,
        content_sha256=fingerprint,
        parser=parser,  # type: ignore[arg-type]
    )

    if mode in ("meta", "document", "full", "all"):
        p = paper_output_dir / "paper.meta.json"
        p.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        written["paper.meta.json"] = str(p.relative_to(paper_output_dir))
        logger.info(f"Structured metadata written to: {p}")

    abstract_blocks: list[BlockJson] = []
    front_blocks: list[BlockJson] = []
    body_blocks: list[BlockJson] = []
    if mode in ("document", "full", "all"):
        if include_abstract_parts and abstract_html:
            abstract_blocks = extract_blocks_from_html(abstract_html, "abstract")
        if include_abstract_parts and front_matter_html:
            front_blocks = extract_blocks_from_html(front_matter_html, "front_matter")
        _collect_blocks_for_sections(sections, body_blocks)

        doc = PaperDocumentJson(
            schema_version=SCHEMA_VERSION,
            arxiv_id=arxiv_id,
            abstract_blocks=abstract_blocks,
            front_matter_blocks=front_blocks,
            sections=[_section_to_tree_json(s) for s in sections],
            blocks=body_blocks,
        )
        p = paper_output_dir / "paper.document.json"
        p.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        written["paper.document.json"] = str(p.relative_to(paper_output_dir))
        logger.info(f"Structured document written to: {p}")

    if mode in ("full", "all"):
        assets: list[AssetJson] = []
        stem_map: dict[str, str] = {}
        if stem_to_image_path:
            for stem, path in stem_to_image_path.items():
                rel = path.as_posix() if isinstance(path, Path) else str(path)
                aid = f"asset:{rel}"
                kind: Any = "svg" if rel.lower().endswith(".svg") else "image"
                assets.append(AssetJson(id=aid, path=rel, kind=kind, tex_stem=stem))
                stem_map[stem] = rel
        if image_map:
            for idx, path in sorted(image_map.items()):
                rel = path.as_posix() if isinstance(path, Path) else str(path)
                aid = f"asset:{rel}"
                if not any(a.id == aid for a in assets):
                    kind = "svg" if rel.lower().endswith(".svg") else "image"
                    assets.append(AssetJson(id=aid, path=rel, kind=kind, figure_index=idx))

        pa = PaperAssetsJson(
            schema_version=SCHEMA_VERSION,
            arxiv_id=arxiv_id,
            images_subdir=images_subdir,
            assets=assets,
            stem_to_path=stem_map,
        )
        p = paper_output_dir / "paper.assets.json"
        p.write_text(pa.model_dump_json(indent=2), encoding="utf-8")
        written["paper.assets.json"] = str(p.relative_to(paper_output_dir))
        logger.info(f"Structured assets written to: {p}")

        bib = PaperBibJson(schema_version=SCHEMA_VERSION, arxiv_id=arxiv_id, entries=[])
        p = paper_output_dir / "paper.bib.json"
        p.write_text(bib.model_dump_json(indent=2), encoding="utf-8")
        written["paper.bib.json"] = str(p.relative_to(paper_output_dir))

    if mode == "all":
        all_blocks = abstract_blocks + front_blocks + body_blocks
        asset_id_list = _collect_asset_ids(stem_to_image_path, image_map)

        graph = build_graph(
            arxiv_id=arxiv_id,
            section_nodes=sections,
            blocks=all_blocks,
            asset_ids=asset_id_list,
        )
        p = paper_output_dir / "paper.graph.json"
        p.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
        written["paper.graph.json"] = str(p.relative_to(paper_output_dir))
        logger.info(f"Structured graph written to: {p}")

        if emit_graph_csv:
            nodes_path = paper_output_dir / "paper.graph.nodes.csv"
            edges_path = paper_output_dir / "paper.graph.edges.csv"
            with open(nodes_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["id", "type", "properties_json"])
                for n in graph.nodes:
                    w.writerow([n.id, n.type, json.dumps(n.properties, ensure_ascii=False)])
            with open(edges_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["src", "dst", "type", "properties_json"])
                for e in graph.edges:
                    w.writerow([e.src, e.dst, e.type, json.dumps(e.properties, ensure_ascii=False)])
            written["paper.graph.nodes.csv"] = str(nodes_path.relative_to(paper_output_dir))
            written["paper.graph.edges.csv"] = str(edges_path.relative_to(paper_output_dir))
            logger.info(f"Graph CSV written to: {nodes_path}, {edges_path}")

    return {
        "schema_version": SCHEMA_VERSION,
        "arxiv_id": arxiv_id,
        "paths": written,
    }


def write_minimal_structured(
    *,
    paper_output_dir: Path,
    mode: str,
    emit_graph_csv: bool = False,
    arxiv_id: str,
    arxiv_version: str | None,
    title: str | None,
    authors: list[str],
    submission_date: str | None,
    parser: str,
    sections: list[SectionNode],
    abstract_md: str | None,
    stem_to_image_path: dict[str, Path] | None = None,
    image_map: dict[int, Path] | None = None,
    images_subdir: str = "images",
) -> dict[str, Any]:
    """LaTeX / minimal pipeline: meta + single-section document with optional one block per section body."""
    if mode in ("", "none", "off", "false"):
        return {}
    written: dict[str, str] = {}
    tool_version = _package_version()
    assign_struct_ids(sections)

    fingerprint = _content_fingerprint(abstract_md, sections)
    meta = PaperMetaJson(
        schema_version=SCHEMA_VERSION,
        arxiv_id=arxiv_id,
        arxiv_version=arxiv_version,
        title=title,
        authors=authors,
        submission_date=submission_date,
        html_url=None,
        ar5iv_url=None,
        tool_version=tool_version,
        content_sha256=fingerprint,
        parser=parser,  # type: ignore[arg-type]
    )
    if mode in ("meta", "document", "full", "all"):
        p = paper_output_dir / "paper.meta.json"
        p.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        written["paper.meta.json"] = str(p.relative_to(paper_output_dir))

    doc_blocks: list[BlockJson] = []
    if mode in ("document", "full", "all"):
        for i, sec in enumerate(sections):
            sid = sec.struct_id or f"sec_{i}"
            if sec.markdown:
                doc_blocks.append(
                    BlockJson(
                        id=f"{sid}:b0:other",
                        type="other",
                        section_id=sid,
                        order_index=0,
                        text_plain=sec.markdown[:20000],
                        text_md=sec.markdown[:20000],
                        extra={"source": "latex_markdown"},
                    )
                )
        doc = PaperDocumentJson(
            schema_version=SCHEMA_VERSION,
            arxiv_id=arxiv_id,
            abstract_blocks=[],
            front_matter_blocks=[],
            sections=[_section_to_tree_json(s) for s in sections],
            blocks=doc_blocks,
        )
        p = paper_output_dir / "paper.document.json"
        p.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        written["paper.document.json"] = str(p.relative_to(paper_output_dir))

    if mode in ("full", "all"):
        bib = PaperBibJson(schema_version=SCHEMA_VERSION, arxiv_id=arxiv_id, entries=[])
        bp = paper_output_dir / "paper.bib.json"
        bp.write_text(bib.model_dump_json(indent=2), encoding="utf-8")
        written["paper.bib.json"] = str(bp.relative_to(paper_output_dir))

        assets: list[AssetJson] = []
        stem_map: dict[str, str] = {}
        if stem_to_image_path:
            for stem, path in stem_to_image_path.items():
                rel = path.as_posix() if isinstance(path, Path) else str(path)
                aid = f"asset:{rel}"
                kind: Any = "svg" if rel.lower().endswith(".svg") else "image"
                assets.append(AssetJson(id=aid, path=rel, kind=kind, tex_stem=stem))
                stem_map[stem] = rel
        if image_map:
            for idx, path in sorted(image_map.items()):
                rel = path.as_posix() if isinstance(path, Path) else str(path)
                aid = f"asset:{rel}"
                if not any(a.id == aid for a in assets):
                    kind = "svg" if rel.lower().endswith(".svg") else "image"
                    assets.append(AssetJson(id=aid, path=rel, kind=kind, figure_index=idx))

        pa = PaperAssetsJson(
            schema_version=SCHEMA_VERSION,
            arxiv_id=arxiv_id,
            images_subdir=images_subdir,
            assets=assets,
            stem_to_path=stem_map,
        )
        ap = paper_output_dir / "paper.assets.json"
        ap.write_text(pa.model_dump_json(indent=2), encoding="utf-8")
        written["paper.assets.json"] = str(ap.relative_to(paper_output_dir))

    if mode == "all":
        asset_id_list = _collect_asset_ids(stem_to_image_path, image_map)
        graph = build_graph(arxiv_id=arxiv_id, section_nodes=sections, blocks=doc_blocks, asset_ids=asset_id_list)
        gp = paper_output_dir / "paper.graph.json"
        gp.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
        written["paper.graph.json"] = str(gp.relative_to(paper_output_dir))

        if emit_graph_csv:
            nodes_path = paper_output_dir / "paper.graph.nodes.csv"
            edges_path = paper_output_dir / "paper.graph.edges.csv"
            with open(nodes_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["id", "type", "properties_json"])
                for n in graph.nodes:
                    w.writerow([n.id, n.type, json.dumps(n.properties, ensure_ascii=False)])
            with open(edges_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["src", "dst", "type", "properties_json"])
                for e in graph.edges:
                    w.writerow([e.src, e.dst, e.type, json.dumps(e.properties, ensure_ascii=False)])
            written["paper.graph.nodes.csv"] = str(nodes_path.relative_to(paper_output_dir))
            written["paper.graph.edges.csv"] = str(edges_path.relative_to(paper_output_dir))

    return {"schema_version": SCHEMA_VERSION, "arxiv_id": arxiv_id, "paths": written}

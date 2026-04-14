"""Structured export enhancements for LaTeX parser."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from arxiv2md_beta.ir.blocks import BlockJson
from arxiv2md_beta.schemas.sections import SectionNode
from arxiv2md_beta.schemas.structured import (
    SCHEMA_VERSION,
    AssetJson,
    GraphEdgeJson,
    GraphNodeJson,
    PaperAssetsJson,
    PaperBibJson,
    PaperDocumentJson,
    PaperGraphJson,
    PaperMetaJson,
    SectionTreeNodeJson,
)


def extract_blocks_from_markdown(
    markdown: str,
    section_id: str,
    start_index: int = 0,
) -> list[BlockJson]:
    """Extract content blocks from markdown (LaTeX mode alternative to HTML block extraction).
    
    Parameters
    ----------
    markdown : str
        Markdown content
    section_id : str
        Section identifier
    start_index : int
        Starting order index
        
    Returns:
    -------
    list[BlockJson]
        List of content blocks
    """
    blocks: list[BlockJson] = []
    order_idx = start_index

    # Split content into blocks
    # Pattern to match different block types
    patterns = [
        # Display math
        (r'\$\$[\s\S]*?\$\$', 'math_display'),
        # Code blocks
        (r'```[\s\S]*?```', 'code'),
        # Tables
        (r'\|[^\n]+\|\n\|[-:\s|]+\|\n(?:\|[^\n]+\|\n?)+', 'table'),
        # Blockquotes
        (r'^>\s*.+$', 'quote'),
        # Paragraphs (non-empty lines)
        (r'^(?!#|\s*\$\$|\s*```|\s*\||\s*>)[^\n]+(?:\n(?!#|\s*\$\$|\s*```|\s*\||\s*>)[^\n]+)*',
         'paragraph'),
    ]

    remaining = markdown

    while remaining.strip():
        best_match = None
        best_type = None
        best_start = len(remaining)

        for pattern, block_type in patterns:
            match = re.search(pattern, remaining, re.MULTILINE)
            if match and match.start() < best_start:
                best_match = match
                best_type = block_type
                best_start = match.start()

        if best_match:
            # Add any text before the match as plain text if significant
            if best_match.start() > 0:
                prefix = remaining[:best_match.start()].strip()
                if len(prefix) > 20:
                    blocks.append(
                        BlockJson(
                            id=f"{section_id}:b{order_idx}:text",
                            type="text",
                            section_id=section_id,
                            order_index=order_idx,
                            text_plain=prefix[:5000],
                            text_md=prefix[:20000],
                            extra={"source": "latex_markdown"},
                        )
                    )
                    order_idx += 1

            content = best_match.group(0)

            # Create appropriate block
            if best_type == 'math_display':
                blocks.append(
                    BlockJson(
                        id=f"{section_id}:b{order_idx}:equation",
                        type="equation",
                        section_id=section_id,
                        order_index=order_idx,
                        text_plain=content[:5000],
                        text_md=content[:20000],
                        extra={"source": "latex_markdown", "display": True},
                    )
                )
            elif best_type == 'code':
                blocks.append(
                    BlockJson(
                        id=f"{section_id}:b{order_idx}:code",
                        type="code",
                        section_id=section_id,
                        order_index=order_idx,
                        text_plain=content[:5000],
                        text_md=content[:20000],
                        extra={"source": "latex_markdown"},
                    )
                )
            elif best_type == 'table':
                blocks.append(
                    BlockJson(
                        id=f"{section_id}:b{order_idx}:table",
                        type="table",
                        section_id=section_id,
                        order_index=order_idx,
                        text_plain=content[:5000],
                        text_md=content[:20000],
                        extra={"source": "latex_markdown"},
                    )
                )
            elif best_type == 'quote':
                blocks.append(
                    BlockJson(
                        id=f"{section_id}:b{order_idx}:quote",
                        type="quote",
                        section_id=section_id,
                        order_index=order_idx,
                        text_plain=content[:5000],
                        text_md=content[:20000],
                        extra={"source": "latex_markdown"},
                    )
                )
            else:
                blocks.append(
                    BlockJson(
                        id=f"{section_id}:b{order_idx}:text",
                        type="text",
                        section_id=section_id,
                        order_index=order_idx,
                        text_plain=content[:5000],
                        text_md=content[:20000],
                        extra={"source": "latex_markdown"},
                    )
                )

            order_idx += 1
            remaining = remaining[best_match.end():]
        else:
            # No more matches, add remaining as text if significant
            remaining_text = remaining.strip()
            if len(remaining_text) > 20:
                blocks.append(
                    BlockJson(
                        id=f"{section_id}:b{order_idx}:text",
                        type="text",
                        section_id=section_id,
                        order_index=order_idx,
                        text_plain=remaining_text[:5000],
                        text_md=remaining_text[:20000],
                        extra={"source": "latex_markdown"},
                    )
                )
            break

    return blocks


def extract_blocks_from_sections(
    sections: list[SectionNode],
) -> list[BlockJson]:
    """Extract blocks from all sections recursively.
    
    Parameters
    ----------
    sections : list[SectionNode]
        Section tree
        
    Returns:
    -------
    list[BlockJson]
        All blocks from all sections
    """
    all_blocks: list[BlockJson] = []

    def process_section(sec: SectionNode) -> None:
        sid = sec.struct_id or "sec_unknown"
        if sec.markdown:
            blocks = extract_blocks_from_markdown(sec.markdown, sid)
            all_blocks.extend(blocks)

        for child in sec.children:
            process_section(child)

    for sec in sections:
        process_section(sec)

    return all_blocks


def extract_abstract_blocks(
    abstract_md: str | None,
    section_id: str = "abstract",
) -> list[BlockJson]:
    """Extract blocks from abstract.
    
    Parameters
    ----------
    abstract_md : str | None
        Abstract markdown content
    section_id : str
        Section identifier
        
    Returns:
    -------
    list[BlockJson]
        Blocks from abstract
    """
    if not abstract_md:
        return []

    return extract_blocks_from_markdown(abstract_md, section_id)


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


def assign_struct_ids(sections: list[SectionNode], prefix: str = "sec") -> None:
    """Mutate sections with stable struct_id (e.g. sec_0, sec_0_1)."""
    def walk(nodes: list[SectionNode], path: tuple[int, ...]) -> None:
        for i, node in enumerate(nodes):
            sid = prefix + "".join(f"_{p}" for p in (*path, i))
            node.struct_id = sid
            walk(node.children, (*path, i))

    walk(sections, ())


def _section_to_tree_json(node: SectionNode) -> SectionTreeNodeJson:
    from arxiv2md_beta.ir.blocks import hash_html, hash_markdown
    return SectionTreeNodeJson(
        struct_id=node.struct_id or "sec_unknown",
        title=node.title,
        level=node.level,
        anchor=node.anchor,
        html_sha256=hash_html(node.html),
        markdown_sha256=hash_markdown(node.markdown),
        children=[_section_to_tree_json(c) for c in node.children],
    )


def _collect_asset_ids(
    stem_to_image_path: dict[str, Path] | None,
    image_map: dict[int, Path] | None,
) -> list[str]:
    """Stable ordered list of asset:<relative_path> ids (deduplicated)."""
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


def write_structured_bundle_for_latex(
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
    abstract_blocks: list[BlockJson],
    body_blocks: list[BlockJson],
    abstract_md: str | None,
    stem_to_image_path: dict[str, Path] | None = None,
    image_map: dict[int, Path] | None = None,
    images_subdir: str = "images",
) -> dict[str, Any]:
    """Enhanced structured export for LaTeX parser with proper block extraction.
    
    Parameters
    ----------
    paper_output_dir : Path
        Output directory
    mode : str
        Export mode: none, meta, document, full, all
    emit_graph_csv : bool
        Whether to emit CSV files for graph
    arxiv_id : str
        arXiv ID
    arxiv_version : str | None
        Version string
    title : str | None
        Paper title
    authors : list[str]
        Author names
    submission_date : str | None
        Submission date
    parser : str
        Parser type (latex)
    sections : list[SectionNode]
        Section tree
    abstract_blocks : list[BlockJson]
        Blocks from abstract
    body_blocks : list[BlockJson]
        Blocks from body sections
    abstract_md : str | None
        Abstract markdown
    stem_to_image_path : dict[str, Path] | None
        Mapping from stem to image path
    image_map : dict[int, Path] | None
        Mapping from index to image path
    images_subdir : str
        Images subdirectory name
        
    Returns:
    -------
    dict[str, Any]
        Export result with paths
    """
    if mode in ("", "none", "off", "false"):
        return {}

    written: dict[str, str] = {}
    tool_version = _package_version()

    # Assign struct IDs
    if mode not in ("none", ""):
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

    if mode in ("document", "full", "all"):
        doc = PaperDocumentJson(
            schema_version=SCHEMA_VERSION,
            arxiv_id=arxiv_id,
            abstract_blocks=abstract_blocks,
            front_matter_blocks=[],
            sections=[_section_to_tree_json(s) for s in sections],
            blocks=body_blocks,
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
        graph = build_graph(
            arxiv_id=arxiv_id,
            section_nodes=sections,
            blocks=body_blocks,
            asset_ids=asset_id_list,
        )
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

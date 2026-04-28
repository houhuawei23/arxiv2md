"""JSON emitter: DocumentIR → structured JSON files.

Replaces the legacy ``output/structured_export.py`` for the IR pipeline.
Produces ``paper.meta.json``, ``paper.document.json``, ``paper.assets.json``,
``paper.bib.json``, and ``paper.graph.json`` with full IR block structures.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.emitters.base import IREmitter
from arxiv2md_beta.ir.emitters.markdown import MarkdownEmitter
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()

SCHEMA_VERSION = "2.0"


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


def _assign_struct_ids(sections: list[SectionIR], prefix: str = "sec") -> None:
    """Mutate *sections* with stable ``struct_id`` values in-place."""

    def walk(secs: list[SectionIR], path: tuple[int, ...]) -> None:
        for i, sec in enumerate(secs):
            sec.struct_id = prefix + "".join(f"_{p}" for p in (*path, i))
            walk(sec.children, (*path, i))

    walk(sections, ())


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


# ---------------------------------------------------------------------------
# Helpers: flatten IR sections into blocks with stable IDs
# ---------------------------------------------------------------------------


def _flatten_section_blocks(
    sections: list[SectionIR],
) -> list[dict[str, Any]]:
    """Return a flat list of block dicts with assigned ``id`` and ``section_id``."""

    out: list[dict[str, Any]] = []

    def walk(secs: list[SectionIR]) -> None:
        for sec in secs:
            sid = sec.struct_id or "sec_unknown"
            for bi, blk in enumerate(sec.blocks):
                d = blk.model_dump(exclude_none=True)
                d["id"] = f"{sid}:b{bi}:{blk.type}"
                d["section_id"] = sid
                d["order_index"] = bi
                out.append(d)
            walk(sec.children)

    walk(sections)
    return out


def _section_to_dict(sec: SectionIR) -> dict[str, Any]:
    """Recursively convert a :class:`SectionIR` to a dict for JSON."""
    sid = sec.struct_id or "sec_unknown"
    blocks = [
        {
            **b.model_dump(exclude_none=True),
            "id": f"{sid}:b{i}:{b.type}",
            "section_id": sid,
            "order_index": i,
        }
        for i, b in enumerate(sec.blocks)
    ]
    return {
        "title": sec.title,
        "level": sec.level,
        "anchor": sec.anchor,
        "struct_id": sec.struct_id,
        "blocks": blocks,
        "children": [_section_to_dict(c) for c in sec.children],
    }


def _build_asset_list(
    doc: DocumentIR,
    images_subdir: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Build asset list and stem→path map from *doc*."""
    assets: list[dict[str, Any]] = []
    stem_map: dict[str, str] = {}
    seen: set[str] = set()

    for a in doc.assets:
        d = a.model_dump(exclude_none=True)
        path = d.get("path", "")
        aid = f"asset:{path}"
        if aid in seen:
            continue
        seen.add(aid)
        d["id"] = aid
        assets.append(d)
        stem = d.get("tex_stem")
        if stem and path:
            stem_map[stem] = path

    return assets, stem_map


def _content_fingerprint(doc: DocumentIR) -> str:
    """SHA-256 of abstract + section markdown content."""
    emitter = MarkdownEmitter()
    parts: list[str] = []

    if doc.abstract:
        parts.append(emitter._emit_blocks(doc.abstract))

    def walk(secs: list[SectionIR]) -> None:
        for sec in secs:
            if sec.blocks:
                parts.append(emitter._emit_blocks(sec.blocks))
            walk(sec.children)

    walk(doc.sections)
    return _sha256_parts(parts)


# ---------------------------------------------------------------------------
# Graph builder (IR-native, replaces legacy build_graph)
# ---------------------------------------------------------------------------


def build_graph(doc: DocumentIR) -> dict[str, Any]:
    """Build a heterogeneous graph from *doc*.

    Returns a dict with ``schema_version``, ``arxiv_id``, ``nodes``, ``edges``.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    arxiv_id = doc.metadata.arxiv_id
    nodes.append({
        "id": "paper",
        "type": "paper",
        "properties": {"arxiv_id": arxiv_id},
    })

    # Walk sections
    def walk_sections(
        parent_id: str,
        secs: list[SectionIR],
        is_root: bool,
    ) -> None:
        prev_sid: str | None = None
        for sec in secs:
            sid = sec.struct_id or "sec_unknown"
            nodes.append({
                "id": sid,
                "type": "section",
                "properties": {"title": sec.title, "level": sec.level},
            })
            rel = "child_section" if not is_root else "contains"
            edges.append({"src": parent_id, "dst": sid, "type": rel})
            if prev_sid is not None:
                edges.append({
                    "src": prev_sid,
                    "dst": sid,
                    "type": "next",
                    "properties": {"scope": "section"},
                })
            prev_sid = sid

            # Add block nodes for this section
            prev_bid: str | None = None
            for bi, blk in enumerate(sec.blocks):
                bid = f"{sid}:b{bi}:{blk.type}"
                nodes.append({
                    "id": bid,
                    "type": "block",
                    "properties": {"block_type": blk.type},
                })
                edges.append({"src": sid, "dst": bid, "type": "contains"})
                if prev_bid is not None:
                    edges.append({
                        "src": prev_bid,
                        "dst": bid,
                        "type": "next",
                        "properties": {"scope": "block"},
                    })
                prev_bid = bid

            walk_sections(sid, sec.children, False)

    walk_sections("paper", doc.sections, True)

    # Asset nodes
    for a in doc.assets:
        d = a.model_dump(exclude_none=True)
        path = d.get("path", "")
        aid = f"asset:{path}"
        nodes.append({"id": aid, "type": "asset", "properties": {}})
        edges.append({"src": "paper", "dst": aid, "type": "contains"})

    return {
        "schema_version": SCHEMA_VERSION,
        "arxiv_id": arxiv_id,
        "nodes": nodes,
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# JsonEmitter
# ---------------------------------------------------------------------------


class JsonEmitter(IREmitter):
    """Emit :class:`DocumentIR` as structured JSON.

    Parameters
    ----------
    mode : str
        One of ``"meta"``, ``"document"``, ``"full"``, ``"all"``.
    indent : int | None
        JSON indentation (default 2).
    """

    format_name = "json"

    def __init__(self, mode: str = "full", indent: int | None = 2) -> None:
        self.mode = mode
        self.indent = indent

    # ------------------------------------------------------------------
    # emit() — single JSON string for the configured mode
    # ------------------------------------------------------------------

    def emit(self, doc: DocumentIR) -> str:
        """Serialize *doc* to JSON according to *mode*."""
        data = self._build_data(doc, self.mode)
        return json.dumps(data, indent=self.indent, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # write_bundle() — write individual files to disk
    # ------------------------------------------------------------------

    def write_bundle(
        self,
        doc: DocumentIR,
        paper_output_dir: Path,
        *,
        images_subdir: str = "images",
        emit_graph_csv: bool = False,
    ) -> dict[str, Any]:
        """Write ``paper.*.json`` files to *paper_output_dir*.

        Returns a dict with ``schema_version``, ``arxiv_id``, and ``paths``
        (relative paths keyed by filename), compatible with the result sidecar.
        """
        mode = self.mode
        if mode in ("", "none", "off", "false"):
            return {}

        _assign_struct_ids(doc.sections)

        written: dict[str, str] = {}
        arxiv_id = doc.metadata.arxiv_id

        # --- paper.meta.json ---
        if mode in ("meta", "document", "full", "all"):
            written.update(self._write_meta(doc, paper_output_dir))

        # --- paper.document.json ---
        if mode in ("document", "full", "all"):
            written.update(self._write_document(doc, paper_output_dir))

        # --- paper.assets.json + paper.bib.json ---
        if mode in ("full", "all"):
            written.update(self._write_assets(doc, paper_output_dir, images_subdir))
            written.update(self._write_bib(doc, paper_output_dir))

        # --- paper.graph.json (+ optional CSV) ---
        if mode == "all":
            written.update(
                self._write_graph(doc, paper_output_dir, emit_graph_csv)
            )

        return {
            "schema_version": SCHEMA_VERSION,
            "arxiv_id": arxiv_id,
            "paths": written,
        }

    # ------------------------------------------------------------------
    # Internal: data builders
    # ------------------------------------------------------------------

    def _build_data(self, doc: DocumentIR, mode: str) -> dict[str, Any]:
        if mode == "meta":
            return self._build_meta(doc)
        if mode == "document":
            return self._build_document(doc)
        if mode in ("full", "all"):
            return self._build_full(doc)
        return self._build_meta(doc)

    def _build_meta(self, doc: DocumentIR) -> dict[str, Any]:
        m = doc.metadata
        return {
            "schema_version": SCHEMA_VERSION,
            "arxiv_id": m.arxiv_id,
            "arxiv_version": m.arxiv_version,
            "title": m.title,
            "authors": [a.model_dump(exclude_none=True) for a in m.authors],
            "submission_date": m.submission_date,
            "abstract_text": m.abstract_text,
            "source_url": m.source_url,
            "parser": m.parser,
            "tool_name": m.tool_name,
            "tool_version": _package_version() if m.tool_version == "0.0.0" else m.tool_version,
            "content_sha256": _content_fingerprint(doc),
        }

    def _build_document(self, doc: DocumentIR) -> dict[str, Any]:
        return {
            **self._build_meta(doc),
            "abstract": [b.model_dump(exclude_none=True) for b in doc.abstract],
            "front_matter": [b.model_dump(exclude_none=True) for b in doc.front_matter],
            "sections": [_section_to_dict(s) for s in doc.sections],
        }

    def _build_full(self, doc: DocumentIR) -> dict[str, Any]:
        result = self._build_document(doc)
        result["bibliography"] = [b.model_dump(exclude_none=True) for b in doc.bibliography]
        assets, stem_map = _build_asset_list(doc, "")
        result["assets"] = assets
        result["stem_to_path"] = stem_map
        return result

    # ------------------------------------------------------------------
    # Internal: file writers
    # ------------------------------------------------------------------

    def _write_meta(
        self, doc: DocumentIR, out_dir: Path
    ) -> dict[str, str]:
        data = self._build_meta(doc)
        path = out_dir / "paper.meta.json"
        path.write_text(
            json.dumps(data, indent=self.indent, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info(f"Structured metadata written to: {path}")
        return {"paper.meta.json": str(path.relative_to(out_dir))}

    def _write_document(
        self, doc: DocumentIR, out_dir: Path
    ) -> dict[str, str]:
        data = self._build_document(doc)
        path = out_dir / "paper.document.json"
        path.write_text(
            json.dumps(data, indent=self.indent, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info(f"Structured document written to: {path}")
        return {"paper.document.json": str(path.relative_to(out_dir))}

    def _write_assets(
        self, doc: DocumentIR, out_dir: Path, images_subdir: str
    ) -> dict[str, str]:
        assets, stem_map = _build_asset_list(doc, images_subdir)
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "arxiv_id": doc.metadata.arxiv_id,
            "images_subdir": images_subdir,
            "assets": assets,
            "stem_to_path": stem_map,
        }
        path = out_dir / "paper.assets.json"
        path.write_text(
            json.dumps(data, indent=self.indent, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info(f"Structured assets written to: {path}")
        return {"paper.assets.json": str(path.relative_to(out_dir))}

    def _write_bib(
        self, doc: DocumentIR, out_dir: Path
    ) -> dict[str, str]:
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "arxiv_id": doc.metadata.arxiv_id,
            "entries": [],
        }
        path = out_dir / "paper.bib.json"
        path.write_text(
            json.dumps(data, indent=self.indent, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return {"paper.bib.json": str(path.relative_to(out_dir))}

    def _write_graph(
        self,
        doc: DocumentIR,
        out_dir: Path,
        emit_graph_csv: bool,
    ) -> dict[str, str]:
        written: dict[str, str] = {}
        graph = build_graph(doc)

        gp = out_dir / "paper.graph.json"
        gp.write_text(
            json.dumps(graph, indent=self.indent, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        written["paper.graph.json"] = str(gp.relative_to(out_dir))
        logger.info(f"Structured graph written to: {gp}")

        if emit_graph_csv:
            nodes_path = out_dir / "paper.graph.nodes.csv"
            edges_path = out_dir / "paper.graph.edges.csv"
            with open(nodes_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["id", "type", "properties_json"])
                for n in graph["nodes"]:
                    w.writerow([
                        n["id"], n["type"],
                        json.dumps(n.get("properties", {}), ensure_ascii=False),
                    ])
            with open(edges_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["src", "dst", "type", "properties_json"])
                for e in graph["edges"]:
                    w.writerow([
                        e["src"], e["dst"], e["type"],
                        json.dumps(e.get("properties", {}), ensure_ascii=False),
                    ])
            written["paper.graph.nodes.csv"] = str(nodes_path.relative_to(out_dir))
            written["paper.graph.edges.csv"] = str(edges_path.relative_to(out_dir))
            logger.info(f"Graph CSV written to: {nodes_path}, {edges_path}")

        return written

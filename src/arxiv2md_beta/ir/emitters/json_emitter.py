"""JSON emitter: DocumentIR → structured JSON (replaces structured_export.py)."""

from __future__ import annotations

import json
from typing import Any

from arxiv2md_beta.ir.document import DocumentIR
from arxiv2md_beta.ir.emitters.base import IREmitter


class JsonEmitter(IREmitter):
    """Emit :class:`DocumentIR` as structured JSON.

    Parameters
    ----------
    mode : str
        One of ``"meta"``, ``"document"``, ``"full"``, ``"all"``.
        See :meth:`emit` for details.
    indent : int | None
        JSON indentation (default 2).
    """

    format_name = "json"

    def __init__(self, mode: str = "full", indent: int | None = 2) -> None:
        self.mode = mode
        self.indent = indent

    def emit(self, doc: DocumentIR) -> str:
        """Serialize *doc* to JSON according to *mode*.

        Modes
        -----
        meta
            Only metadata (arXiv ID, title, authors, date, tool info).
        document
            Metadata + abstract blocks + section tree with block-level IR.
        full
            Metadata + document + assets (images).
        all
            Full + extra graphable lists (nodes, edges).
        """
        if self.mode == "meta":
            data = self._build_meta(doc)
        elif self.mode == "document":
            data = self._build_document(doc)
        elif self.mode in ("full", "all"):
            data = self._build_full(doc)
        else:
            data = self._build_meta(doc)

        return json.dumps(data, indent=self.indent, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # Builders for each mode
    # ------------------------------------------------------------------

    def _build_meta(self, doc: DocumentIR) -> dict[str, Any]:
        m = doc.metadata
        return {
            "schema_version": doc.schema_version,
            "arxiv_id": m.arxiv_id,
            "arxiv_version": m.arxiv_version,
            "title": m.title,
            "authors": m.authors,
            "submission_date": m.submission_date,
            "abstract_text": m.abstract_text,
            "source_url": m.source_url,
            "parser": m.parser,
            "tool_name": m.tool_name,
            "tool_version": m.tool_version,
        }

    def _build_document(self, doc: DocumentIR) -> dict[str, Any]:
        return {
            **self._build_meta(doc),
            "abstract": [b.model_dump() for b in doc.abstract],
            "sections": [self._section_to_dict(s) for s in doc.sections],
        }

    def _build_full(self, doc: DocumentIR) -> dict[str, Any]:
        result = self._build_document(doc)
        result["bibliography"] = [b.model_dump() for b in doc.bibliography]
        result["assets"] = [a.model_dump() for a in doc.assets]
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _section_to_dict(section) -> dict[str, Any]:
        return {
            "title": section.title,
            "level": section.level,
            "anchor": section.anchor,
            "struct_id": section.struct_id,
            "blocks": [b.model_dump() for b in section.blocks],
            "children": [
                JsonEmitter._section_to_dict(c) for c in section.children
            ],
        }

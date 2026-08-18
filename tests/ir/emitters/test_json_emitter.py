"""Tests for JsonEmitter id/order semantics (R2.2)."""

from __future__ import annotations

from arxiv2md_beta.ir import DocumentIR, FigureIR, PaperMetadata, SectionIR
from arxiv2md_beta.ir.emitters.json_emitter import JsonEmitter, _assign_struct_ids


def _doc() -> DocumentIR:
    fig = FigureIR(figure_id="figure-1", anchor="figure-1", images=[], order_index=7)
    sec = SectionIR(title="1 Intro", level=1, struct_id="sec_1", blocks=[fig])
    child = SectionIR(title="1.1 Sub", level=2, struct_id="sec_1_1", blocks=[])
    sec.children = [child]
    return DocumentIR(metadata=PaperMetadata(arxiv_id="t", parser="latex"), sections=[sec])


def test_assign_struct_ids_preserves_existing_ids() -> None:
    doc = _doc()
    _assign_struct_ids(doc.sections)
    assert doc.sections[0].struct_id == "sec_1"
    assert doc.sections[0].children[0].struct_id == "sec_1_1"


def test_assign_struct_ids_fills_only_missing() -> None:
    doc = _doc()
    doc.sections[0].struct_id = None
    _assign_struct_ids(doc.sections)
    assert doc.sections[0].struct_id == "sec_0"  # backfilled positionally
    assert doc.sections[0].children[0].struct_id == "sec_1_1"  # untouched


def test_write_bundle_does_not_mutate_doc() -> None:
    import json

    doc = _doc()
    before = json.dumps(doc.model_dump(), default=str, sort_keys=True)
    data = json.loads(JsonEmitter(mode="document").emit(doc))
    after = json.dumps(doc.model_dump(), default=str, sort_keys=True)
    assert before == after, "write path mutated the input doc"
    sec = data["sections"][0]
    assert sec["struct_id"] == "sec_1"
    assert sec["blocks"][0]["order_index"] == 7, "builder order_index was overwritten"

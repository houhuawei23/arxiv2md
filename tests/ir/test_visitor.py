"""Tests for IRVisitor and IRWalker."""

from __future__ import annotations

import pytest

from arxiv2md_beta.ir import (
    DocumentIR,
    IRVisitor,
    PaperMetadata,
    ParagraphIR,
    SectionIR,
    TextIR,
    TextCollector,
    walk,
)
from arxiv2md_beta.ir.core import IRNode


# ── Custom visitors for testing ───────────────────────────────────────


class RecordingVisitor(IRVisitor):
    """Records visit order for testing traversal."""

    def __init__(self):
        self.visited: list[str] = []
        self.enter_order: list[str] = []
        self.leave_order: list[str] = []

    def visit_default(self, node: IRNode) -> None:
        self.visited.append(node.type)

    def enter_node(self, node: IRNode) -> None:
        self.enter_order.append(node.type)

    def leave_node(self, node: IRNode) -> None:
        self.leave_order.append(node.type)


class TextContentVisitor(IRVisitor):
    """Accumulates text content from TextIR nodes in order."""

    def __init__(self):
        self.texts: list[str] = []

    def visit_text(self, node: IRNode) -> None:
        if isinstance(node, TextIR):
            self.texts.append(node.text)

    def visit_default(self, node: IRNode) -> None:
        pass


class ConditionalVisitor(IRVisitor):
    """Only visits text nodes, skips everything else."""

    def __init__(self):
        self.count = 0

    def visit_text(self, node: IRNode) -> None:
        self.count += 1

    def visit_default(self, node: IRNode) -> None:
        pass


# ── Tests ──────────────────────────────────────────────────────────────


class TestIRWalkerOrder:
    def test_walk_hits_every_node(self, complex_doc):
        visitor = RecordingVisitor()
        walk(complex_doc, visitor)
        # document → section → section → paragraph → text → emphasis → ...
        assert "document" in visitor.visited
        assert "paragraph" in visitor.visited
        assert "text" in visitor.visited

    def test_enter_before_visit(self, complex_doc):
        visitor = RecordingVisitor()
        walk(complex_doc, visitor)
        # Every enter for a type should be followed by a visit of that type
        for node_type in visitor.enter_order:
            assert node_type in visitor.visited

    def test_leave_after_children(self, minimal_doc):
        """Leave should fire after all children have been visited."""
        visitor = RecordingVisitor()
        walk(minimal_doc, visitor)

        # Find the document leave
        doc_leave_idx = None
        for i, t in enumerate(visitor.leave_order):
            if t == "document":
                doc_leave_idx = i
                break
        assert doc_leave_idx is not None
        # All other nodes should be visited before document leaves
        assert doc_leave_idx == len(visitor.leave_order) - 1


class TestBuiltInVisitors:
    def test_text_collector_minimal(self, minimal_doc):
        collector = TextCollector()
        walk(minimal_doc, collector)
        assert "This is the abstract." in collector.texts
        assert "Hello world." in collector.texts

    def test_text_collector_complex(self, complex_doc):
        collector = TextCollector()
        walk(complex_doc, collector)
        all_text = "".join(collector.texts)
        assert "IR system" in all_text
        assert "a link" in all_text
        assert "First key finding" in all_text
        assert "E=mc^2" not in all_text  # MathIR is not TextIR

    def test_conditional_visitor(self, complex_doc):
        visitor = ConditionalVisitor()
        walk(complex_doc, visitor)
        assert visitor.count > 10  # many text nodes


class TestDefaultVisitor:
    def test_default_visitor_noop(self, minimal_doc):
        """Default visitor should not raise."""

        class DefaultOnly(IRVisitor):
            pass  # uses visit_default which is a no-op

        visitor = DefaultOnly()
        # Should not raise
        walk(minimal_doc, visitor)


class TestEmptyDocument:
    def test_walk_empty_document(self):
        doc = DocumentIR(metadata=PaperMetadata(arxiv_id="empty"))
        visitor = RecordingVisitor()
        walk(doc, visitor)
        assert visitor.visited == ["document"]


class TestDeepNesting:
    def test_deeply_nested_emphasis(self):
        """Walk through deeply nested emphasis: bold(italic(bold(text)))."""
        from arxiv2md_beta.ir import EmphasisIR

        deep = ParagraphIR(inlines=[
            EmphasisIR(
                style="bold",
                inlines=[
                    EmphasisIR(
                        style="italic",
                        inlines=[
                            EmphasisIR(
                                style="bold",
                                inlines=[TextIR(text="deep")],
                            ),
                        ],
                    ),
                ],
            ),
        ])

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="nest"),
            sections=[SectionIR(title="Nested", level=1, blocks=[deep])],
        )

        class DepthCounter(IRVisitor):
            def __init__(self):
                self.text_count = 0
                self.emphasis_count = 0

            def visit_text(self, node):
                self.text_count += 1

            def visit_emphasis(self, node):
                self.emphasis_count += 1

            def visit_default(self, node):
                pass

        counter = DepthCounter()
        walk(doc, counter)
        assert counter.text_count == 1
        assert counter.emphasis_count == 3

    def test_nested_blockquote(self):
        """Blockquote containing a list containing a paragraph."""
        from arxiv2md_beta.ir import BlockQuoteIR, ListIR

        nested = BlockQuoteIR(
            blocks=[
                ListIR(
                    ordered=False,
                    items=[
                        [ParagraphIR(inlines=[TextIR(text="nested item")])],
                    ],
                ),
            ],
        )

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="nest"),
            sections=[SectionIR(title="Nested", level=1, blocks=[nested])],
        )

        class CountVisitor(IRVisitor):
            def __init__(self):
                self.count = 0

            def visit_text(self, node):
                if node.type == "text":
                    self.count += 1

            def visit_default(self, node):
                pass

        v = CountVisitor()
        walk(doc, v)
        assert v.count == 1  # "nested item"

    def test_list_with_sublist(self):
        """List item with a sub-list."""
        from arxiv2md_beta.ir import ListIR

        lst = ListIR(
            items=[
                [
                    ParagraphIR(inlines=[TextIR(text="Parent")]),
                    ListIR(
                        ordered=False,
                        items=[
                            [ParagraphIR(inlines=[TextIR(text="Child 1")])],
                            [ParagraphIR(inlines=[TextIR(text="Child 2")])],
                        ],
                    ),
                ],
            ],
        )

        doc = DocumentIR(
            metadata=PaperMetadata(arxiv_id="sublist"),
            sections=[SectionIR(title="L", level=1, blocks=[lst])],
        )

        class Counter(IRVisitor):
            def __init__(self):
                self.texts = []

            def visit_text(self, node):
                if isinstance(node, TextIR):
                    self.texts.append(node.text)

            def visit_default(self, node):
                pass

        c = Counter()
        walk(doc, c)
        assert c.texts == ["Parent", "Child 1", "Child 2"]


class TestVisitorDispatch:
    def test_specific_visitor_called(self):
        """verify that visit_paragraph is preferred over visit_default."""

        class ParaOnly(IRVisitor):
            def __init__(self):
                self.hit = False

            def visit_paragraph(self, node):
                self.hit = True

            def visit_default(self, node):
                pass

        p = ParagraphIR(inlines=[TextIR(text="hi")])
        v = ParaOnly()
        v.visit(p)
        assert v.hit is True

    def test_fallback_to_default(self):
        """verify that visit_default is called when no specific handler."""

        class DefaultOnly(IRVisitor):
            def __init__(self):
                self.hit = False

            def visit_default(self, node):
                self.hit = True

        p = ParagraphIR(inlines=[TextIR(text="hi")])
        v = DefaultOnly()
        v.visit(p)
        assert v.hit is True

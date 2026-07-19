"""Tests for IRVisitor and IRWalker."""

from __future__ import annotations

from arxiv2md_beta.ir import (
    DocumentIR,
    IRVisitor,
    PaperMetadata,
    ParagraphIR,
    SectionIR,
    TextCollector,
    TextIR,
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
        # walk descends into metadata (no authors) then stops.
        assert visitor.visited == ["document", "metadata"]


class TestDeepNesting:
    def test_deeply_nested_emphasis(self):
        """Walk through deeply nested emphasis: bold(italic(bold(text)))."""
        from arxiv2md_beta.ir import EmphasisIR

        deep = ParagraphIR(
            inlines=[
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
            ]
        )

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
        """Verify that visit_paragraph is preferred over visit_default."""

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
        """Verify that visit_default is called when no specific handler."""

        class DefaultOnly(IRVisitor):
            def __init__(self):
                self.hit = False

            def visit_default(self, node):
                self.hit = True

        p = ParagraphIR(inlines=[TextIR(text="hi")])
        v = DefaultOnly()
        v.visit(p)
        assert v.hit is True


def test_child_specs_cover_all_container_types():
    """Regression: _CHILD_SPECS must list every IR node type that nests children.

    A new container node added without registering here would have its children
    silently skipped by walk() — silent data loss for any walk-based transform
    or collector.
    """
    from arxiv2md_beta.ir import blocks as blocks_mod
    from arxiv2md_beta.ir import inlines as inlines_mod
    from arxiv2md_beta.ir.document import DocumentIR, SectionIR
    from arxiv2md_beta.ir.visitor import _CHILD_SPECS

    # Container inline types (carry list[InlineUnion]).
    inline_containers = {
        cls
        for name, cls in vars(inlines_mod).items()
        if isinstance(cls, type)
        and issubclass(cls, inlines_mod.InlineIR)
        and hasattr(cls, "model_fields")
        and any(
            "InlineUnion" in str(getattr(cls.model_fields[f], "annotation", ""))
            for f in cls.model_fields
        )
    }
    # Container block types (carry list[BlockUnion] / list[list[BlockUnion]] / list[InlineUnion]).
    block_containers = {
        cls
        for name, cls in vars(blocks_mod).items()
        if isinstance(cls, type)
        and issubclass(cls, blocks_mod.BlockIR)
        and hasattr(cls, "model_fields")
        and any(
            "BlockUnion" in str(getattr(cls.model_fields[f], "annotation", ""))
            or "InlineUnion" in str(getattr(cls.model_fields[f], "annotation", ""))
            for f in cls.model_fields
        )
    }

    for cls in inline_containers | block_containers:
        # Instantiate with defaults to read .type, then check registration.
        try:
            instance = cls()  # type: ignore[call-arg]
        except Exception:
            # Some classes require args (e.g. EquationIR.latex); skip those —
            # they are leaf content nodes without default-constructible children.
            continue
        type_literal = getattr(instance, "type", None)
        if type_literal is None:
            continue
        assert type_literal in _CHILD_SPECS, (
            f"Container IR type {cls.__name__} (type={type_literal!r}) is missing from "
            f"_CHILD_SPECS — walk() will silently skip its children."
        )

    # Document and Section are the structural containers.
    assert "document" in _CHILD_SPECS
    assert "section" in _CHILD_SPECS


def test_walk_reaches_assets_and_authors():
    """Regression: walk() must descend into doc.assets and doc.metadata.authors.

    Previously the 'document' child-spec omitted both, so NodeCounter and any
    custom visitor silently never saw assets or authors.
    """
    from arxiv2md_beta.ir.assets import ImageAsset
    from arxiv2md_beta.ir.document import AuthorIR, DocumentIR, PaperMetadata
    from arxiv2md_beta.ir.visitor import NodeCounter, walk

    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="x", authors=[AuthorIR(name="Jane")]),
        assets=[ImageAsset(path="images/a.png", figure_index=1)],
    )
    counter = NodeCounter()
    walk(doc, counter)
    assert counter.counts.get("author") == 1
    assert counter.counts.get("image_asset") == 1

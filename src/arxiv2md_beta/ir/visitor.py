"""Visitor pattern for traversing IR trees.

``IRVisitor``
    Double-dispatch visitor. Subclass and override ``visit_<node_type>`` methods.
    Falls back to ``visit_default`` for node types without a dedicated method.

``IRWalker``
    Depth-first walker that calls ``before_visit`` / ``visit`` / ``after_visit``
    on every node in the tree. Useful for collecting statistics, validating refs, etc.

Usage::

    from arxiv2md_beta.ir.visitor import IRVisitor, walk

    class TextCollector(IRVisitor):
        def __init__(self):
            self.texts: list[str] = []

        def visit_text(self, node):
            self.texts.append(node.text)

        def visit_default(self, node):
            pass  # ignore non-text nodes

    doc: DocumentIR = ...
    collector = TextCollector()
    walk(doc, collector)
    print(collector.texts)
"""

from __future__ import annotations

from abc import ABC
from typing import Any

from arxiv2md_beta.ir.blocks import BlockUnion
from arxiv2md_beta.ir.core import IRNode
from arxiv2md_beta.ir.document import DocumentIR, SectionIR
from arxiv2md_beta.ir.inlines import InlineUnion

# ── Node kinds that wrap nested children ──
# Each entry: (type literal, attribute name that holds the children, child-list type)
# We use the type literal string so we don't need to import every concrete class.
_CHILD_SPECS: dict[str, list[tuple[str, str]]] = {
    # Inlines with children
    "emphasis":     [("inlines", "inline")],
    "link":         [("inlines", "inline")],
    "superscript":  [("inlines", "inline")],
    "subscript":    [("inlines", "inline")],
    # Blocks with children
    "paragraph":    [("inlines", "inline")],
    "heading":      [("inlines", "inline")],
    "blockquote":   [("blocks", "block")],
    "list":         [("items", "block_list")],
    "figure":       [("caption", "inline")],
    "table":        [("headers", "inline_list"), ("rows", "inline_list_list"), ("caption", "inline")],
    "algorithm":    [("steps", "block"), ("caption", "inline")],
    "code":         [("caption", "inline")],
    # Document level
    "section":      [("blocks", "block"), ("children", "section")],
    "document":     [("abstract", "block"), ("front_matter", "block"), ("sections", "section"), ("bibliography", "block")],
}


class IRVisitor(ABC):
    """Double-dispatch visitor for IR nodes.

    Override ``visit_<node_type>(self, node)`` for specific types.
    Falls back to ``visit_default(self, node)`` when no override exists.
    """

    def visit(self, node: IRNode) -> Any:
        """Entry point — dispatches on ``node.type``."""
        method_name = f"visit_{node.type}"
        handler = getattr(self, method_name, None)
        if handler is not None:
            return handler(node)
        return self.visit_default(node)

    def visit_default(self, node: IRNode) -> Any:
        """Called when no type-specific visitor exists."""
        return None

    # Optional lifecycle hooks (used by IRWalker)

    def enter_node(self, node: IRNode) -> None:
        """Called before visiting a node (for state push)."""
        pass

    def leave_node(self, node: IRNode) -> None:
        """Called after visiting a node's children (for state pop)."""
        pass


def walk(node: IRNode, visitor: IRVisitor) -> None:
    """Depth-first walk through the entire IR tree, calling the visitor.

    For each node, the walker calls::

        visitor.enter_node(node)
        visitor.visit(node)
        _walk_children(node, visitor)
        visitor.leave_node(node)
    """
    visitor.enter_node(node)
    visitor.visit(node)
    _walk_children(node, visitor)
    visitor.leave_node(node)


def _walk_children(node: IRNode, visitor: IRVisitor) -> None:
    """Recursively walk child nodes based on the child-spec table."""
    specs = _CHILD_SPECS.get(node.type, [])
    for attr, kind in specs:
        children = getattr(node, attr, None)
        if children is None:
            continue
        if kind == "inline":
            # list[InlineUnion]
            for child in children:
                walk(child, visitor)
        elif kind == "block":
            # list[BlockUnion]
            for child in children:
                walk(child, visitor)
        elif kind == "section":
            # list[SectionIR]
            for child in children:
                walk(child, visitor)
        elif kind == "block_list":
            # list[list[BlockUnion]] — used for list items
            for item in children:
                for child in item:
                    walk(child, visitor)
        elif kind == "inline_list":
            # list[list[InlineUnion]] — used for table headers
            for row in children:
                for child in row:
                    walk(child, visitor)
        elif kind == "inline_list_list":
            # list[list[list[InlineUnion]]] — table rows
            for row in children:
                for cell in row:
                    for child in cell:
                        walk(child, visitor)


# ─────────────────────────────────────────────────────────────────────
# Built-in visitors
# ─────────────────────────────────────────────────────────────────────


class TextCollector(IRVisitor):
    """Collect all plain text from TextIR nodes (for token counting, search, etc.)."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def visit_text(self, node: IRNode) -> None:
        from arxiv2md_beta.ir.inlines import TextIR
        if isinstance(node, TextIR):
            self.texts.append(node.text)

    def visit_default(self, node: IRNode) -> None:
        pass


class FigureCollector(IRVisitor):
    """Collect all FigureIR nodes with their figure_ids."""

    def __init__(self) -> None:
        self.figures: list[IRNode] = []

    def visit_figure(self, node: IRNode) -> None:
        self.figures.append(node)

    def visit_default(self, node: IRNode) -> None:
        pass


class NodeCounter(IRVisitor):
    """Count occurrences of each node type."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def visit_default(self, node: IRNode) -> None:
        self.counts[node.type] = self.counts.get(node.type, 0) + 1

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

from typing import Any

from arxiv2md_beta.ir.core import IRNode

# ── Node kinds that wrap nested children ──
# Each entry: (type literal, attribute name that holds the children, child-list type)
# We use the type literal string so we don't need to import every concrete class.
_CHILD_SPECS: dict[str, list[tuple[str, str]]] = {
    # Inlines with children
    "emphasis": [("inlines", "inline")],
    "link": [("inlines", "inline")],
    "superscript": [("inlines", "inline")],
    "subscript": [("inlines", "inline")],
    # Blocks with children
    "paragraph": [("inlines", "inline")],
    "heading": [("inlines", "inline")],
    "blockquote": [("blocks", "block")],
    "list": [("items", "block_list")],
    # figure.images (ImageRefIR list) must be walked too: TextCollector /
    # NodeCounter / content fingerprints otherwise miss every figure image.
    "figure": [("caption", "inline"), ("images", "inline"), ("grid", "inline_list_list")],
    "table": [("headers", "inline_list"), ("rows", "inline_list_list"), ("caption", "inline")],
    "algorithm": [("steps", "block"), ("caption", "inline")],
    "code": [("caption", "inline")],
    # Document level
    "section": [("blocks", "block"), ("children", "section")],
    "metadata": [("authors", "author")],
    "document": [
        ("abstract", "block"),
        ("front_matter", "block"),
        ("sections", "section"),
        ("assets", "asset"),
        ("metadata", "object"),
    ],
}


def child_block_lists(block: Any) -> list[list[Any]]:
    """Nested block lists of *block* per ``_CHILD_SPECS`` (may be empty).

    Single source of descent for block containers (``blockquote.blocks``,
    ``list.items``, ``algorithm.steps``): transforms previously kept their
    own copies, and a new container field (audit5 I-5: ``algorithm.steps``)
    got missed by every walker but one. ``block``-kind specs hold nodes
    directly; ``block_list``-kind specs (``list.items``) hold lists of them.
    """
    specs = _CHILD_SPECS.get(getattr(block, "type", ""), [])
    lists: list[list[Any]] = []
    for attr, kind in specs:
        if kind not in ("block", "block_list"):
            continue
        children = getattr(block, attr, None)
        if not children:
            continue
        if kind == "block":
            lists.append(children)
        else:  # ``block_list``: the value already is a list of block lists
            lists.extend(children)
    return lists


def iter_block_descendants(node: Any) -> Any:
    """Yield every block strictly below *node*, depth-first in document order.

    Covers front-matter/abstract/section callers uniformly; order matches the
    hand-written section+block recursions it replaces (prescans feed
    collision-sensitive sets, so order must not change).
    """
    for sub in child_block_lists(node):
        for child in sub:
            yield child
            yield from iter_block_descendants(child)


def iter_inline_lists(node: Any, _top: bool = True) -> Any:
    """Yield every inline list reachable from *node* per ``_CHILD_SPECS``.

    Includes nested emphasis/link inlines (their ``inlines`` lists are
    yielded as separate lists). Consumers sweep each list flat; the
    per-type dispatch this replaces previously drifted (figure grid cells
    were missed by one of the two link sweeps, audit5 I-5).
    """
    specs = _CHILD_SPECS.get(getattr(node, "type", ""), [])
    for attr, kind in specs:
        children = getattr(node, attr, None)
        if not children:
            continue
        if kind == "inline":
            yield children
            for il in children:
                yield from iter_inline_lists(il, _top=False)
        elif kind == "inline_list":
            for row in children:
                yield row
                for il in row:
                    yield from iter_inline_lists(il, _top=False)
        elif kind == "inline_list_list":
            for row in children:
                for cell in row:
                    yield cell
                    for il in cell:
                        yield from iter_inline_lists(il, _top=False)


class IRVisitor:
    """Double-dispatch visitor for IR nodes.

    Override ``visit_<node_type>(self, node)`` for specific types.
    Falls back to ``visit_default(self, node)`` when no override exists.

    ``enter_node`` and ``leave_node`` are optional lifecycle hooks used by IRWalker.
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
        if kind == "object":
            # Single nested IRNode (e.g. doc.metadata).
            walk(children, visitor)
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
        else:
            # Flat list kinds: inline / block / section / asset / author.
            for child in children:
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


class NodeCounter(IRVisitor):
    """Count nodes by ``type`` discriminator (stats, test coverage of child specs)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def visit_default(self, node: IRNode) -> None:
        t = getattr(node, "type", None)
        if isinstance(t, str):
            self.counts[t] = self.counts.get(t, 0) + 1

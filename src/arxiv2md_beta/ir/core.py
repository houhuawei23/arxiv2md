"""IR node base classes and core types.

All IR nodes inherit from ``IRNode``. The hierarchy is:

    IRNode
    ├── InlineIR   — inline elements within a paragraph
    ├── BlockIR    — block-level elements within a section
    ├── AssetIR    — static resources (images, SVGs, etc.)
    ├── SectionIR  — hierarchical section node
    └── DocumentIR — top-level document container
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class IRNode(BaseModel):
    """Base class for all IR nodes.

    Every node carries an optional ``label`` filled in by builders
    and used by transforms for numbering, cross-reference resolution, etc.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    # -- node type discriminator (overridden by concrete subclasses) --
    type: str = ""

    # -- structural identifier (set by builder, used by transforms) --
    label: str | None = None  # e.g. \label{fig:overview} or HTML @id


class InlineIR(IRNode):
    """Base class for inline (text-level) elements."""


class BlockIR(IRNode):
    """Base class for block-level elements.

    Blocks carry an optional ``anchor`` used as a cross-reference target
    (e.g. ``<a id="figure-3"></a>`` in HTML/markdown).
    """

    anchor: str | None = None


class AssetIR(IRNode):
    """Base class for static assets (images, SVGs, etc.)."""

"""Deprecated alias: anchor assignment is now part of ``NumberingPass``.

Kept as a thin subclass so existing pipelines/tests that add
``AnchorPass`` keep working; re-running the merged pass is idempotent.
"""

from __future__ import annotations

from arxiv2md_beta.ir.transforms.numbering import NumberingPass


class AnchorPass(NumberingPass):
    """Deprecated: use :class:`NumberingPass`, which now assigns anchors.

    Section/block anchoring (with uniqueness) and section-fragment
    repointing used to be a separate pass that had to run last; both are
    absorbed into :class:`NumberingPass`.
    """

    name = "anchor"
    description = "Deprecated alias of NumberingPass (anchor assignment merged into it)."

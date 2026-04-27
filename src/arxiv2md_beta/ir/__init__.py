"""arxiv2md-beta Intermediate Representation (IR).

The IR is a three-tier compiler-style architecture:

    Builders (Frontend)
        Convert raw sources (HTML, LaTeX) → :class:`DocumentIR`.

    Transforms (Middle-end)
        Ordered passes that analyse / mutate a :class:`DocumentIR`.

    Emitters (Backend)
        Serialise a :class:`DocumentIR` to target formats (Markdown, JSON, plain text).

Quick start::

    from arxiv2md_beta.ir import DocumentIR, PaperMetadata, SectionIR
    from arxiv2md_beta.ir import ParagraphIR, TextIR

    doc = DocumentIR(
        metadata=PaperMetadata(arxiv_id="2501.12345", title="A Paper"),
        sections=[
            SectionIR(
                title="Introduction",
                level=1,
                blocks=[ParagraphIR(inlines=[TextIR(text="Hello world.")])],
            )
        ],
    )
    print(doc.model_dump_json(indent=2))
"""

from arxiv2md_beta.ir.blocks import (  # noqa: F401
    AlgorithmIR,
    BlockQuoteIR,
    BlockUnion,
    CodeIR,
    EquationIR,
    FigureIR,
    HeadingIR,
    ListIR,
    ParagraphIR,
    RawBlockIR,
    RuleIR,
    TableIR,
)
from arxiv2md_beta.ir.core import (  # noqa: F401
    AssetIR,
    BlockIR,
    IRNode,
    InlineIR,
    SourceLoc,
)
from arxiv2md_beta.ir.document import DocumentIR, PaperMetadata, SectionIR  # noqa: F401
from arxiv2md_beta.ir.inlines import (  # noqa: F401
    BreakIR,
    EmphasisIR,
    ImageRefIR,
    InlineUnion,
    LinkIR,
    MathIR,
    RawInlineIR,
    SubscriptIR,
    SuperscriptIR,
    TextIR,
)
from arxiv2md_beta.ir.visitor import (  # noqa: F401
    IRVisitor,
    NodeCounter,
    TextCollector,
    walk,
)
from arxiv2md_beta.ir.assets import (  # noqa: F401
    AssetUnion,
    ImageAsset,
    OtherAsset,
    SvgAsset,
)
from arxiv2md_beta.ir.builders import IRBuilder, HTMLBuilder, LaTeXBuilder  # noqa: F401
from arxiv2md_beta.ir.emitters import IREmitter, JsonEmitter, MarkdownEmitter, PlainTextEmitter  # noqa: F401
from arxiv2md_beta.ir.transforms import (  # noqa: F401
    AnchorPass,
    FigureReorderPass,
    IRPass,
    NumberingPass,
    PassPipeline,
    SectionFilterPass,
)

__all__ = [
    # Core
    "IRNode",
    "InlineIR",
    "BlockIR",
    "AssetIR",
    "SourceLoc",
    # Inlines
    "TextIR",
    "MathIR",
    "ImageRefIR",
    "BreakIR",
    "RawInlineIR",
    "EmphasisIR",
    "LinkIR",
    "SuperscriptIR",
    "SubscriptIR",
    "InlineUnion",
    # Blocks
    "ParagraphIR",
    "HeadingIR",
    "BlockQuoteIR",
    "ListIR",
    "CodeIR",
    "RuleIR",
    "EquationIR",
    "FigureIR",
    "TableIR",
    "AlgorithmIR",
    "RawBlockIR",
    "BlockUnion",
    # Document
    "SectionIR",
    "PaperMetadata",
    "DocumentIR",
    # Assets
    "ImageAsset",
    "SvgAsset",
    "OtherAsset",
    "AssetUnion",
    # Builders
    "IRBuilder",
    "HTMLBuilder",
    "LaTeXBuilder",
    # Emitters
    "IREmitter",
    "JsonEmitter",
    "MarkdownEmitter",
    "PlainTextEmitter",
    # Transforms
    "IRPass",
    "PassPipeline",
    "NumberingPass",
    "AnchorPass",
    "SectionFilterPass",
    "FigureReorderPass",
    # Visitor
    "IRVisitor",
    "TextCollector",
    "NodeCounter",
    "walk",
]

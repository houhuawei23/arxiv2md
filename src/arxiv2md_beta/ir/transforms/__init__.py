"""IR transforms — passes that analyse and modify a :class:`DocumentIR`."""

from arxiv2md_beta.ir.transforms.anchor import AnchorPass  # noqa: F401
from arxiv2md_beta.ir.transforms.base import IRPass, PassPipeline  # noqa: F401
from arxiv2md_beta.ir.transforms.figure_reorder import FigureReorderPass  # noqa: F401
from arxiv2md_beta.ir.transforms.numbering import NumberingPass, SectionNumberingPass  # noqa: F401
from arxiv2md_beta.ir.transforms.pipeline import build_default_pipeline  # noqa: F401
from arxiv2md_beta.ir.transforms.section_filter import SectionFilterPass, split_ir_sections  # noqa: F401

__all__ = [
    "IRPass",
    "PassPipeline",
    "NumberingPass",
    "AnchorPass",
    "SectionFilterPass",
    "FigureReorderPass",
    "SectionNumberingPass",
    "build_default_pipeline",
    "split_ir_sections",
]

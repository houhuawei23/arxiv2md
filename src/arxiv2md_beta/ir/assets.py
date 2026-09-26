"""Asset IR types — static resources like images and SVGs."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from arxiv2md_beta.ir.core import AssetIR


class ImageAsset(AssetIR):
    """A raster image asset (PNG, JPG, etc.)."""

    type: Literal["image_asset"] = "image_asset"
    path: str
    tex_stem: str | None = None  # original LaTeX filename without extension
    figure_index: int | None = None
    width: int | None = None
    height: int | None = None


class SvgAsset(AssetIR):
    """An SVG asset, optionally with inline content."""

    type: Literal["svg_asset"] = "svg_asset"
    path: str
    tex_stem: str | None = None
    figure_index: int | None = None
    content: str | None = None  # inline SVG source (optional)


AssetUnion = Annotated[
    ImageAsset | SvgAsset,
    Field(discriminator="type"),
]

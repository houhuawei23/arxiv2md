r"""Regression tests for the LaTeX \includegraphics label→image mapping."""

from __future__ import annotations

from pathlib import Path

from arxiv2md_beta.images.processor import ProcessedImages, build_latex_image_label_map
from arxiv2md_beta.latex.tex_source import TexSourceInfo


def _processed(
    image_map: dict[int, Path],
    source_paths: dict[Path, Path],
    images_dir: Path,
) -> ProcessedImages:
    return ProcessedImages(
        image_map=image_map,
        images_dir=images_dir,
        filename_map={},
        stem_to_image_path={},
        source_paths=source_paths,
    )


def test_label_map_uses_source_identity_not_position(tmp_path: Path) -> None:
    """A label must resolve via its own source file, not a renumbered index.

    ``image_map`` is rebuilt in float-figure order; when an inline image
    precedes the floats (or any image fails), positional lookups would hand
    the wrong file to the label.
    """
    extracted = tmp_path / "tex"
    inline_img = extracted / "logo.png"
    float_a = extracted / "fig_a.pdf"
    float_b = extracted / "fig_b.pdf"
    extracted.mkdir()

    tex_info = TexSourceInfo(
        extracted_dir=extracted,
        main_tex_file=None,
        # Document order: an inline graphic first, then two float figures.
        image_files={"fig:a": float_a, "fig:b": float_b},
        all_images=[inline_img, float_a, float_b],
        figure_image_files=[float_a, float_b],
    )
    # Float-order renumbering: float_a -> 0, float_b -> 1 (inline dropped).
    images_dir = tmp_path / "images"
    processed = _processed(
        image_map={0: Path("images/fig_a.png"), 1: Path("images/fig_b.png")},
        source_paths={float_a: Path("images/fig_a.png"), float_b: Path("images/fig_b.png")},
        images_dir=images_dir,
    )

    label_map = build_latex_image_label_map(tex_info, processed)
    assert label_map["fig:a"] == Path("images/fig_a.png")
    assert label_map["fig:b"] == Path("images/fig_b.png")
    # Source filename and extraction-relative aliases are registered too.
    assert label_map["fig_a.pdf"] == Path("images/fig_a.png")
    assert label_map["fig_b.pdf"] == Path("images/fig_b.png")


def test_label_map_skips_failed_images(tmp_path: Path) -> None:
    """A failed image is absent from source_paths and must not be mapped.

    The old positional code would hand the *next* float's file to its label.
    """
    extracted = tmp_path / "tex"
    float_a = extracted / "broken.pdf"
    float_b = extracted / "good.png"
    extracted.mkdir()

    tex_info = TexSourceInfo(
        extracted_dir=extracted,
        main_tex_file=None,
        image_files={"fig:x": float_a, "fig:y": float_b},
        all_images=[float_a, float_b],
        figure_image_files=[float_a, float_b],
    )
    images_dir = tmp_path / "images"
    processed = _processed(
        # After the failure the float map renumbers: good.png takes slot 0.
        image_map={0: Path("images/good.png")},
        source_paths={float_b: Path("images/good.png")},
        images_dir=images_dir,
    )

    label_map = build_latex_image_label_map(tex_info, processed)
    assert "fig:x" not in label_map
    assert label_map["fig:y"] == Path("images/good.png")


def test_label_map_empty_when_nothing_processed(tmp_path: Path) -> None:
    extracted = tmp_path / "tex"
    tex_info = TexSourceInfo(
        extracted_dir=extracted,
        main_tex_file=None,
        image_files={"fig:a": extracted / "a.png"},
        all_images=[extracted / "a.png"],
    )
    assert build_latex_image_label_map(tex_info, None) == {}

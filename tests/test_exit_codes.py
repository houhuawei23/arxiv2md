"""Exit-code semantics: every failure class maps to its documented code.

Reference table (docs/playbook improvement #3):
0 success | 1 unclassified | 2 user input | 3 network/404 | 4 parse/convert
5 empty/stub | 6 storage/images | 7 PDF fallback | 130 KeyboardInterrupt
"""

from __future__ import annotations

import pytest

from arxiv2md_beta.exceptions import (
    Arxiv2mdError,
    BuilderError,
    EmitterError,
    EmptyContentError,
    ImageProcessingError,
    IngestionError,
    NetworkError,
    NonRetryableNetworkError,
    ParseError,
    ParserNotAvailableError,
    PDFConversionError,
    PdfFallbackCompleted,
    StorageError,
    TransformError,
    UserInputError,
)


@pytest.mark.parametrize(
    ("exc_cls", "expected"),
    [
        (Arxiv2mdError, 1),
        (UserInputError, 2),
        (NetworkError, 3),
        (NonRetryableNetworkError, 3),
        (ParseError, 4),
        (ParserNotAvailableError, 4),
        (IngestionError, 4),
        (BuilderError, 4),
        (TransformError, 4),
        (EmitterError, 4),
        (EmptyContentError, 5),
        (StorageError, 6),
        (ImageProcessingError, 6),
        (PDFConversionError, 6),
        (PdfFallbackCompleted, 7),
    ],
)
def test_exception_exit_codes(exc_cls: type[Arxiv2mdError], expected: int) -> None:
    assert exc_cls("boom").exit_code == expected


def test_instance_exit_code_overrides_class_default() -> None:
    assert NetworkError("boom", exit_code=9).exit_code == 9


def test_parse_error_keeps_source_snippet() -> None:
    exc = ParseError("bad", source_snippet="<x>")
    assert exc.source_snippet == "<x>"
    assert exc.exit_code == 4

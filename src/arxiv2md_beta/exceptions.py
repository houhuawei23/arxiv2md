"""Application-specific exceptions with stable CLI exit codes."""

from __future__ import annotations


class Arxiv2mdError(Exception):
    """Base error; ``exit_code`` is used by the Typer CLI.

    ``exit_code`` is a class attribute so subclasses can declare their own
    default with a single line; an instance-level ``exit_code=`` keyword
    still overrides it for one-off cases.
    """

    exit_code: int = 1

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class UserInputError(Arxiv2mdError):
    """Invalid CLI arguments or user input (exit code 2)."""

    exit_code = 2


class NetworkError(Arxiv2mdError):
    """HTTP or remote fetch failures, including missing IDs (404) — exit 3.

    ``status_code`` carries the HTTP status when one is known, so callers
    (e.g. the export-mirror fallback) can react without parsing messages.
    """

    exit_code = 3

    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, exit_code=exit_code)
        self.status_code = status_code


class NonRetryableNetworkError(NetworkError):
    """Deterministic network failure that must not be retried.

    Raised for outcomes that cannot change on a second attempt (e.g. HTTP 404).
    Retry loops must re-raise this immediately instead of backing off.
    """

    pass


class IngestionError(Arxiv2mdError):
    """Paper parsing or conversion pipeline failures (exit code 4)."""

    exit_code = 4


class ImageProcessingError(Arxiv2mdError):
    """Image processing failure (exit code 6)."""

    exit_code = 6


class PDFConversionError(ImageProcessingError):
    """PDF to PNG conversion failed."""

    pass


class StorageError(Arxiv2mdError):
    """Local file or cache operation failures (exit code 6)."""

    exit_code = 6


class ParseError(Arxiv2mdError):
    """HTML or LaTeX parsing failure (exit code 4)."""

    exit_code = 4

    def __init__(
        self,
        message: str,
        *,
        source_snippet: str | None = None,
    ) -> None:
        super().__init__(message)
        self.source_snippet = source_snippet


class ParserNotAvailableError(ParseError):
    """A required parser backend (e.g. pypandoc/Pandoc) is not installed."""

    pass


class BuilderError(Arxiv2mdError):
    """IR builder failure (HTMLBuilder or LaTeXBuilder) — exit code 4."""

    exit_code = 4


class TransformError(Arxiv2mdError):
    """IR transform pass failure (exit code 4)."""

    exit_code = 4


class EmitterError(Arxiv2mdError):
    """Markdown or JSON emitter failure (exit code 4)."""

    exit_code = 4


class EmptyContentError(Arxiv2mdError):
    """Conversion succeeded but the content is below stub thresholds (exit code 5).

    Raised by the output quality gate instead of silently writing a near-empty
    ``paper.md``. Use ``--allow-stub`` to bypass.
    """

    exit_code = 5


class PdfFallbackCompleted(Arxiv2mdError):  # noqa: N818 — completion signal, not a failure
    """TeX conversion failed and the PDF fallback was performed (exit code 7).

    Partial success: no Markdown was produced, but the arXiv PDF was
    downloaded to the output directory for external parsing (e.g. mineru).
    """

    exit_code = 7

    def __init__(self, message: str, *, paper_output_dir: str | None = None) -> None:
        super().__init__(message)
        self.paper_output_dir = paper_output_dir

"""Application-specific exceptions with stable CLI exit codes."""

from __future__ import annotations


class Arxiv2mdError(Exception):
    """Base error; ``exit_code`` is used by the Typer CLI (default 1)."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class UserInputError(Arxiv2mdError):
    """Invalid CLI arguments or user input (exit code 2)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class NetworkError(Arxiv2mdError):
    """HTTP or remote fetch failures."""

    pass


class IngestionError(Arxiv2mdError):
    """Paper parsing or conversion pipeline failures."""

    pass


class ImageProcessingError(Arxiv2mdError):
    """Image processing failure."""

    pass


class PDFConversionError(ImageProcessingError):
    """PDF to PNG conversion failed."""

    pass


class StorageError(Arxiv2mdError):
    """Local file or cache operation failures."""

    pass

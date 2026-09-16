"""Output quality gate: reject stub-level Markdown instead of writing it silently.

Batch-download post-mortems (see the paper-download playbook) showed the worst
failure mode is a "successful" conversion that wrote a near-empty ``paper.md``
— the exit code was 0 and nothing surfaced until a manual audit. This gate
runs right before the Markdown hits disk and raises
:class:`~arxiv2md_beta.exceptions.EmptyContentError` (exit code 5) when the
content is below both a byte and a token threshold.
"""

from __future__ import annotations

from arxiv2md_beta.exceptions import EmptyContentError
from arxiv2md_beta.output.markdown_utils import count_tokens
from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.settings.schema import AppSettings


def is_stub(output_text: str, *, settings: AppSettings | None = None) -> bool:
    """True when ``output_text`` is below either stub threshold (OR semantics)."""
    s = settings or get_settings()
    content_bytes = len(output_text.encode("utf-8"))
    tokens = count_tokens(output_text)
    too_few_bytes = content_bytes < s.output.stub_min_bytes
    too_few_tokens = tokens is not None and tokens < s.output.stub_min_tokens
    return too_few_bytes or too_few_tokens


def ensure_not_stub(
    output_text: str,
    *,
    settings: AppSettings | None = None,
    allow_stub: bool = False,
) -> None:
    """Raise :class:`EmptyContentError` when ``output_text`` looks like a stub.

    Two independent thresholds, rejected when **either** is under (stub pages
    sit far below both; real short papers pass at least one):

    - UTF-8 byte count < ``output.stub_min_bytes``
    - tiktoken token estimate < ``output.stub_min_tokens`` (skipped when
      tiktoken is unavailable)

    ``allow_stub`` (CLI ``--allow-stub`` or the ``output.allow_stub`` setting)
    bypasses the gate.
    """
    s = settings or get_settings()
    if allow_stub or s.output.allow_stub:
        return

    if not is_stub(output_text, settings=s):
        return

    content_bytes = len(output_text.encode("utf-8"))
    tokens = count_tokens(output_text)
    details: list[str] = [f"{content_bytes} bytes (min {s.output.stub_min_bytes})"]
    if tokens is not None:
        details.append(f"{tokens} tokens (min {s.output.stub_min_tokens})")
    raise EmptyContentError(
        "Converted content looks like a stub and was not written ("
        + "; ".join(details)
        + "). This usually means the paper has no usable HTML/TeX source; "
        "try a specific version (e.g. <id>v1), or re-run with --allow-stub to write it anyway."
    )

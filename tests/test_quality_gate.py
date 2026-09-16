"""Stub quality gate: reject near-empty output with exit code 5 unless allowed."""

from __future__ import annotations

import pytest

from arxiv2md_beta.exceptions import EmptyContentError
from arxiv2md_beta.output.markdown_utils import count_tokens, format_token_count
from arxiv2md_beta.output.quality_gate import ensure_not_stub


def test_short_content_rejected() -> None:
    with pytest.raises(EmptyContentError) as excinfo:
        ensure_not_stub("tiny")
    assert "stub" in str(excinfo.value)
    assert "--allow-stub" in str(excinfo.value)


def test_byte_gate_alone_rejects() -> None:
    # Above the token threshold is impossible at these sizes; bytes alone must
    # trip the gate (OR semantics).
    with pytest.raises(EmptyContentError):
        ensure_not_stub("a" * 400)


def test_realistic_content_passes() -> None:
    text = "Introduction. This paper studies a real problem. " * 300
    ensure_not_stub(text)


def test_allow_stub_bypasses_gate() -> None:
    ensure_not_stub("tiny", allow_stub=True)


def test_error_message_contains_measurements() -> None:
    with pytest.raises(EmptyContentError) as excinfo:
        ensure_not_stub("hello world")
    assert "bytes (min" in str(excinfo.value)


def test_count_tokens_matches_format_token_count() -> None:
    text = "some text for token counting " * 50
    raw = count_tokens(text)
    formatted = format_token_count(text)
    if raw is None:  # tiktoken unavailable in this env
        assert formatted is None
        return
    assert formatted == str(raw) if raw < 1000 else formatted.endswith("k")


def test_count_tokens_none_or_positive() -> None:
    raw = count_tokens("hello")
    assert raw is None or raw > 0

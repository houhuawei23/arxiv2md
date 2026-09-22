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


def test_ensure_not_stub_counts_tokens_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """audit5 X3: the stub path must not encode the full text twice."""
    calls = {"n": 0}
    real = count_tokens

    def counting(text: str) -> int | None:
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr("arxiv2md_beta.output.quality_gate.count_tokens", counting)
    with pytest.raises(EmptyContentError):
        ensure_not_stub("hello world")
    assert calls["n"] == 1


def test_precomputed_token_count_skips_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    """audit5 X3: finalize encodes once and passes the count through."""

    def boom(text: str) -> int | None:
        raise AssertionError("count_tokens must not be called when token_count is given")

    monkeypatch.setattr("arxiv2md_beta.output.quality_gate.count_tokens", boom)
    with pytest.raises(EmptyContentError):
        ensure_not_stub("hello world", token_count=3)
    # Over both thresholds (bytes AND tokens) → passes without any encoding.
    ensure_not_stub("a" * 6000, token_count=100000)

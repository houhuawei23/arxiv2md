"""CLI-facing finalization: result JSON line, summary and performance printing.

The business half (quality gate, naming, writes, PDF download, manifest)
lives in :mod:`arxiv2md_beta.ingestion.persist` — this module only talks to
the user and to the parent process.
"""

from __future__ import annotations

import json
from pathlib import Path

from arxiv2md_beta.ingestion.persist import persist_ingestion_output
from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.schemas import IngestionMetadata, IngestionResult
from arxiv2md_beta.utils.logging_config import get_logger

logger = get_logger()


def emit_result_json_line(
    paper_output_dir: Path,
    *,
    params: ConvertParams,
    structured: dict[str, object] | None = None,
) -> None:
    """单行机器可读结果，供父进程脚本解析（``ARXIV2MD_RESULT_JSON=...``）。."""
    if not params.emit_result_json:
        return
    payload: dict[str, object] = {"paper_output_dir": str(paper_output_dir.resolve())}
    if structured and structured.get("paths"):
        payload["schema_version"] = structured.get("schema_version")
        payload["structured_paths"] = structured.get("paths")
    line = f"ARXIV2MD_RESULT_JSON={json.dumps(payload, ensure_ascii=False)}"
    print(line, flush=True)


def _print_summary(result: IngestionResult) -> None:
    print("\nSummary:")
    try:
        print(result.summary)
    except UnicodeEncodeError:
        print(result.summary.encode("utf-8", errors="replace").decode("utf-8"))

    if result.performance:
        print("\nPerformance:")
        print(f"Total: {result.performance.get('total_seconds', 0):.3f}s")
        for name, metric in result.performance.get("stages", {}).items():
            print(f"  {name}: {metric.get('seconds', 0):.3f}s ({metric.get('share', 0) * 100:.1f}%)")


async def finalize_convert_output(
    *,
    result: IngestionResult,
    metadata: IngestionMetadata,
    params: ConvertParams,
    base_output_dir: Path,
    fallback_md_stem: str,
    pdf_fetch: tuple[str, str | None] | None = None,
    log_local_success: bool = False,
) -> Path:
    """Persist the ingestion result, then report to the user / parent process.

    Returns the resolved paper output directory.
    """
    paper_output_dir = await persist_ingestion_output(
        result=result,
        metadata=metadata,
        params=params,
        base_output_dir=base_output_dir,
        fallback_md_stem=fallback_md_stem,
        pdf_fetch=pdf_fetch,
        log_local_success=log_local_success,
    )

    structured = metadata.structured_export or None

    # Machine-readable result line: emitted only after the quality gate has
    # passed and main markdown + sidecars + manifest are on disk, so a parent
    # process reading the line never races a torn or rejected output
    # (audit4 S5.2 — it used to print before the gate). The pdf_only branch
    # returns from persist after the PDF + manifest have landed, so the same
    # "record exists first" contract holds there.
    emit_result_json_line(paper_output_dir, params=params, structured=structured)

    _print_summary(result)
    return paper_output_dir

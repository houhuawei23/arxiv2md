"""Settings injection for library use (audit5 S8 F7).

``run_convert_flow(ConvertParams)`` is importable, but settings come from a
process-global singleton (``loader._SETTINGS``), so two conversions with
different configurations could not coexist in one process — embedding
applications had to mutate global state (or serialize runs).
``ConvertParams.settings`` + a ContextVar-based override fixes that:

- the injected object wins for everything reading ``get_settings()`` inside
  the flow, including code that runs in worker threads/tasks (both propagate
  the current context);
- the process-global settings are untouched during and after the run;
- concurrent flows with different injected settings are isolated.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from arxiv2md_beta.cli.runner.convert import run_convert_flow
from arxiv2md_beta.params import ConvertParams
from arxiv2md_beta.settings import get_settings, load_settings, reset_settings_cache


def _params_with_output_dir(output_dir: str, tmp_path: Path) -> ConvertParams:
    reset_settings_cache()
    injected = load_settings(environment="test", force_reload=True).model_copy(deep=True)
    injected = injected.model_copy(
        update={"cli_defaults": injected.cli_defaults.model_copy(update={"output_dir": output_dir})}
    )
    return ConvertParams(
        input_text="2401.12345",
        parser="html",
        output=None,  # resolved from (injected) settings
        source="Arxiv",
        short=None,
        no_images=False,
        remove_refs=False,
        remove_inline_citations=False,
        section_filter_mode="include",
        sections=None,
        section=None,
        include_tree=False,
        dry_run=True,
        settings=injected,
    )


class TestSettingsContext:
    def test_injected_settings_visible_inside_context_only(self, tmp_path: Path) -> None:
        from arxiv2md_beta.settings import settings_context

        reset_settings_cache()
        injected = load_settings(environment="test", force_reload=True).model_copy(deep=True)
        global_before = get_settings()
        with settings_context(injected):
            assert get_settings() is injected
        assert get_settings() is global_before

    def test_context_propagates_into_threads_and_tasks(self, tmp_path: Path) -> None:
        from arxiv2md_beta.settings import settings_context

        injected = get_settings().model_copy(deep=True)

        async def _check_thread() -> bool:
            def _read() -> bool:
                return get_settings() is injected

            return await asyncio.to_thread(_read)

        async def _check_task() -> bool:
            async def _read() -> bool:
                return get_settings() is injected

            task = asyncio.create_task(_read())
            return await task

        with settings_context(injected):
            assert asyncio.run(_check_thread())
            assert asyncio.run(_check_task())


class TestRunConvertFlowInjection:
    async def test_flow_resolves_output_dir_from_injected_settings(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        injected_out = str(tmp_path / "injected-out")
        params = _params_with_output_dir(injected_out, tmp_path)
        out = await run_convert_flow(params)
        assert out == Path(injected_out)

    async def test_flow_leaves_global_settings_untouched(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        params = _params_with_output_dir(str(tmp_path / "injected-out"), tmp_path)
        before = get_settings()
        await run_convert_flow(params)
        assert get_settings() is before
        assert get_settings().cli_defaults.output_dir == before.cli_defaults.output_dir

    async def test_concurrent_flows_isolated(self, tmp_path: Path) -> None:
        out_a = str(tmp_path / "flow-a")
        out_b = str(tmp_path / "flow-b")
        results = await asyncio.gather(
            run_convert_flow(_params_with_output_dir(out_a, tmp_path)),
            run_convert_flow(_params_with_output_dir(out_b, tmp_path)),
        )
        assert [str(p) for p in results] == [out_a, out_b]

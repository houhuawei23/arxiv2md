# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.15.0] - 2026-08-18

图片链路正确性（R1）+ 编号单一事实来源（R2）。含两处输出格式变更（见 Changed）。

### Fixed（R1 — 图片链路）

- **ImageResolver 错配收紧**：stem 子串匹配降为最后一级 fallback 并加词边界约束（"fig" 不再错配 "fig2.png"，下划线包裹名仍可命中）；索引 fallback 不再"向前顺延"偷取其他 figure 的图片——同一 figure 的重复解析（多子图）按 base/base+1/base+2 连续分配，基索引被占则返回 None；1 基向后兼容分支仅在 map 无 0 键时生效。
- **本地路径 SVG 不再丢失**：`ingestion/local.py` 与 `local_html.py` 此前不传 SVG 输出目录，内联 `<svg>` figure 整图被静默丢弃。
- **LaTeX 无扩展名探测**不再把临时目录绝对路径写进 Markdown，改为 `<images_subdir>/<name>` 相对路径（`LaTeXBuilder` 新增 `images_subdir` build 参数）。
- **图片失败可见化**：`ProcessedImages` 新增 `failed` 列表，处理失败的图片汇总 warning（此前逐条静默消失）。
- **TikZ 快速失败**：pdflatex 不可用（FileNotFoundError）只探测一次并跳过全部 snippet，不再每个 snippet 等待 45s 超时。

### Changed（R2 — 编号单一来源；⚠️ 输出格式变更）

- **NumberingPass 成为唯一编号源**：HTMLBuilder 停止为无 caption figure 预写 `figure-N`；pass 预扫描全部 caption 派生 id，自动编号跳过已占用号；anchor 全局唯一——重复 caption id（附录 "Figure 1" 重现）时 `figure_id` 保留 caption 语义、anchor 去重为 `figure-1-2` 等；equation anchor 由公式自身编号驱动（`eq-3`，去括号），不再用位置计数。
- **JSON bundle 不再改写语义**：`_assign_struct_ids` 只补缺失的 struct_id（不再把 `sec_1_2` 覆盖为 0 基 `sec_0_1`）；block 的 `order_index` 保留 builder 的文档级值（缺失才补）；`write_bundle` 深拷贝后工作，不再 mutate 输入 doc。
- **图内链锚点表化**：figure/algorithm 携带 arXiv 元素 id（如 `S1.F1`）为 label，NumberingPass 构建 label→anchor 映射并把内部链接重定向到真实 anchor（替代 `S1.F1 → figure-全局计数` 的猜测）。
- **Markdown emitter 转义**（⚠️ 输出格式变更）：链接文本 `[`/`]` 转义、URL 空格/括号百分号编码；表格单元格内换行转 `<br>`（不再撕开表格）；figure/table caption 多行时逐行加 `>` 前缀；上/下标由 `^1`/`_i` 改为 `<sup>1</sup>`/`<sub>i</sub>`（裸前缀与 Markdown 强调/数学符号冲突）。
- SVG 持久化从 HTMLBuilder（文件 I/O 副作用）移至 ingestion 层 `persist_inline_svgs()`，文件名使用配置的 `images_subdir`（消灭硬编码 `images/`）。
- `config init` 模板与 `default_config.yml` 的 user_agent 版本串统一为当前版本（此前分别为 0.6.1 / 0.1）。


## [0.14.0] - 2026-08-18

全项目架构评审与重构：修复 11 个确定性 bug、收敛 4 份复制的收尾代码、消除 ingestion→cli 依赖倒置。评审由三位并行探索代理完成全面扫描后按 P0-P3 分阶段实施。

### Fixed（P0 确定性 bug）

- **配置合并链**：`--include-anchors` / `--linked-citations` 改为三态 flag（`--flag/--no-flag`），仅显式传参时覆盖 YAML/env（此前 CLI 默认 `False` 无条件覆盖用户 YAML）；`apply_cli_overrides` 补上缺失的 `linked_citations` 分支。
- **LaTeX 路径功能漂移**：`--remove-inline-citations` / `--linked-citations` 现已接入远程 LaTeX、本地归档、本地 HTML 全部 ingestion 路径（此前被静默忽略）。
- **图编号 0/1 基混用**：HTML builder 无 caption 图的 `figure_id` 与 `figure_index` 统一为 1 基。
- **元数据抓取失败降级**：orchestrator 中 arXiv API 失败改为回退 HTML 解析的作者/日期，而非取消整个转换。
- **batch 三个语义 bug**：`--delay-seconds` 移入信号量内（真正限速）；未预期异常不再从 `gather` 冒出炸掉整个 batch。
- **arXiv ID 去 version**：新增 `utils/arxiv_ids.strip_version()`（正则 `v\d+$`）替换全部 7 处脆弱的 `split("v")[0]`。
- **下载缓存原子写**：PDF/TeX 缓存改为 temp+rename，并发转换同一论文不再写坏缓存。
- **arXiv tarball 路径穿越防护**：`_extract_archive` 合并到已加固的 `_extract_tar_archive`（本地路径版本本有检查，远程版本缺失）。
- **Crossref DOI 误判**：`is_arxiv_doi` 由子串匹配收窄为 `10.48550/arxiv` 前缀匹配。
- **图像子进程超时**：`subprocess.TimeoutExpired` 纳入 orchestrator 降级捕获，不再中断转换。

### Fixed（P3 边界 bug）

- `FigureReorderPass` 改为按对象身份定位 figure 与引用段落（修复多图交错移动时的错位/IndexError）。
- unnumbered section 子树编号延续同级序列（消除重复 `struct_id`/anchor）；equation anchor 不再产出非法的 `eq-(3)`。
- LaTeX 多引用（`target_id="35,2,5"`）正确渲染并支持逐个 `#ref-N` 链接；内联图片不再把 width/height 拼进 URL（破坏 GFM 链接目标）。
- 本地归档 `_copy_local_images` 同名图加 `_1/_2` 后缀防静默覆盖（与 local_html 行为一致）。
- Section 过滤统一为规范化精确匹配（`utils/section_titles.py` 单一来源），消除 sections tree 与实际输出内容的语义分叉（子串匹配导致的矛盾）。

### Changed（架构收敛）

- **收尾代码收敛**：新增 `ingestion/ir_finalize.finalize_ingestion_output()`，替换 4 份复制的 summary/tree/paper.yml/structured-export 尾巴（漂移曾导致 LaTeX 路径丢失 flags）。
- **网络重试统一**：新增 `network/retry.request_with_retries()` 收敛 Crossref/OpenAlex/abs-page 三个 best-effort 重试循环；不可重试 4xx 不再浪费重试。
- **convert/batch 选项共享**：新增 `cli/options.py`，21 个选项注解单一定义，杜绝两份签名漂移。
- **依赖倒置消除**：`ConvertParams` 下沉至包根 `params.py`，ingestion 层不再 import `cli/`。
- **naming scheme 单一来源**：`FIXED_INTERNAL_SCHEMES` 移入 `output/layout.py`；删除失效的 `ConvertParams.naming_scheme`（实际读取走 settings）。
- **paper.yml 单一序列化路径**：`save_paper_metadata` 委托 `write_paper_yml_file`。
- `config show` 改为全量 model_dump（手工枚举已落后 schema）；`config validate` 不再把被验证文件写入全局 settings。

### Removed

- `--remove-toc`（全链路 no-op 死参数）、`split_sections_at_reference`、`_fix_citation_links`、僵尸结果侧车 `write_result_json_sidecar`、`PlainTextEmitter`、`FigureCollector`、`_used_image_indices`、`cache/` 与 `html/serializers/` 残档。


## [0.13.1] - 2026-07-13

### Fixed

- **LaTeX parser: 修复 figure* 环境中多图片丢失问题**：`_build_figure_from_pandoc` 现在通过 `_inlines_from_pandoc` 处理 figure body 中的 Para 块，正确提取嵌套在 Span 节点中的所有 Image。之前的实现只检查 Para 的直接子节点，导致 Pandoc 将多个 `{\includegraphics{...}}` 转换为 `Para → Span → Image` 结构时，只有第一个图片被提取，其余图片丢失。修复后，Appendix 中的 5 个图片（1706.03762 论文）全部正确输出。

## [0.13.0] - 2026-07-13

### LaTeX IR Builder — 引用、编号、侧边栏对齐

LaTeXBuilder（Pandoc JSON AST IR Builder）在远程 arXiv LaTeX 路径（`--parser latex`）上实现与 HTML 路径（ar5iv）一致的输出质量。本次修改完成了 Phase 5（LaTeX 迁移到 IR）的收尾工作，修复了 6 个真实论文 1706.03762（Attention Is All You Need）测试中发现的准确性问题。

### Added

- **`SectionNumberingPass`**（`ir/transforms/numbering.py`）：LaTeX section 的分层编号（`1`, `1.1`, …）。跳过 unnumbered（`\section*{}`）和 paragraph-level（`level>=5`）标题。结果直接写入 `SectionIR.title`，使 MarkdownEmitter 输出 `## 1 Introduction` 而非 `## Introduction`。
- **`SectionIR.unnumbered`** 字段（`ir/document.py`）：标记 starred LaTeX sections，供 `SectionNumberingPass` 和 `split_ir_sections` 使用。

### Fixed

- **引用解析**：`\cite{key}` 和 `\citep{key}` 现在解析为引用编号（如 `[[13]]`），通过从 `\bibitem{key}` 顺序提取的 `_cite_key_to_num` 映射实现。多引用渲染为 `[[35], [2], [5]]`，与 ar5iv HTML 格式一致。
- **标题层级**：Pandoc AST 层级偏移 +1（`\section=1` → IR level=2），现与 ar5iv（`\section` → `h2`）对齐。
- **References 格式**：thebibliography Div 现在渲染为带 `- ` 前缀的无序列表，而非纯段落。参考文献部分标记为 `unnumbered`，避免 `## 8 References` 被主编号方案计数。
- **Sidecar 分割**：`split_ir_sections` 现在在匹配参考文献/附录标题之前，会去除标题中的编号前缀（如 `"8 "`），使分割逻辑与 `SectionNumberingPass` 兼容。
- **杂散 bibitem 标签**：pandoc 生成的 bibitem 标签段落（如 `10`，来自 `\bibitem[10]{key}`）会被过滤，不会在参考文献列表中渲染为杂散条目。
- **杂散 `\cite` RawInline**：未被 pandoc 解析为 Cite 节点的原始 LaTeX 引用命令（如图注中）现在会被丢弃，而非泄漏到输出中。
- **无扩展名图片**：`_resolve_image_src` 现在通过探测 `base_dir` 中的常见扩展名（`.png`、`.jpg` 等），为没有扩展名的 LaTeX `\includegraphics` 路径执行回退。
- **插图前块丢弃**：在第一个 section 标题之前积累的块（权限声明、maketitle 产物）会被丢弃，避免产生空的 `## ` 标题。
- **无标题文档**：没有 section 标题的极简文档（如 `\begin{document} Hello. \end{document}`）会获得一个包罗万象的无名 section，而非静默丢弃所有内容。
- **Sidecar 中的摘要重复**：在发出 References/Appendix sidecar 之前临时清除 `doc.abstract`，防止摘要在每个 sidecar 文件中重复出现。

### Changed

- **LaTeXBuilder**：`_build_sections` 完全重写——基于栈的层次结构替换为扁平列表 + `_build_section_hierarchy` 后处理。参考文献提取从 `doc.bibliography` 移除；参考文献现在作为普通 `SectionIR` 包含在 `doc.sections` 中。
- **_hooks.py**：导出列表包含 `SectionNumberingPass`。
- **Ingestion pipeline**：`SectionNumberingPass` 在 LaTeX 路径中添加到 `PassPipeline`（位于 `NumberingPass` 之后，`FigureReorderPass` 之前）。

## [0.12.0] - 2026-06-24

### Performance

- **HTTP client 全面复用**：PDF 下载、TeX source 下载、OpenAlex API 统一使用共享 `httpx.AsyncClient`（`network.http.get_http_client()`），配合请求级超时，避免每次网络请求新建连接池。
- **LaTeX 解析异步化**：HTML 旧路径、LaTeX 路径、本地归档路径的 `parse_latex_to_markdown()` 均通过 `asyncio.to_thread()`  offload 到线程池，避免阻塞 pandoc 调用阻塞事件循环。
- **图片处理全异步**：旧 HTML/本地归档路径中的图片处理改为 `await process_images_async()`，与 IR 管线保持一致，使用 `ProcessPoolExecutor` + 线程池并发。
- **IR builder 与 visitor 类型基础加固**：`IRNode` 基类补全 `type` 字段，消除 visitor 中的类型盲区。

### Engineering

- **静态检查清零**：`ruff check`、`ruff format`、`mypy src/arxiv2md_beta` 均通过。
- **CI/CD**：新增 `.github/workflows/ci.yml`（lint/type/test 矩阵）和 `.github/workflows/release.yml`（tag 触发 PyPI 发布 + 版本校验）。
- **Pre-commit 统一**：`.githooks/pre-commit` 末尾追加 `exec pre-commit run --show-diff-on-failure`。
- **入口统一**：`ingest_paper()` 的 HTML 模式默认委托给 `IngestionOrchestrator`，旧路径保留用于 LaTeX/本地归档。
- **死代码清理**：删除 `html/markdown.py::convert_html_to_markdown_v2` 及 `latex/parser.py` 中 7 个未调用函数。
- **文档更新**：重写 `AGENTS.md`，更新架构图、CLI 示例与配置说明。

### Changed

- **Version**: 0.11.0 → 0.12.0

Wrapped up by Kimi (kimi-k2.7 via kimi-code) on 2026-06-24

## [0.11.0] - 2026-06-19

### Performance

- **IR HTML pipeline image processing is now asynchronous and parallel**: `process_images_async()` uses a `ProcessPoolExecutor` for PDF→PNG conversion and a thread pool for raster copies, significantly speeding up papers with many figures.
- **HTML and API metadata are fetched concurrently** in `IngestionOrchestrator.run()`, reducing network wait time.
- **Shared `httpx.AsyncClient` is reused** across arXiv API, Crossref, and author-enrichment requests to preserve connections and avoid repeated TLS handshakes.
- **IR builder reuses `ParsedArxivHtml`**: `HTMLBuilder.build()` now accepts a `ParsedArxivHtml` instance, avoiding a second full-HTML BeautifulSoup parse.
- **tiktoken encoding is cached** in `_format_token_count()`, removing repeated `tiktoken.get_encoding()` calls.

### Added

- New CLI flag `--download-pdf / --skip-pdf-download` to control whether the arXiv PDF is downloaded into the output directory. Default remains `True` for backward compatibility; downstream `paper-pipeline` defaults to skipping it for speed.
- `ParsedArxivHtml` now carries `document_root` so downstream builders can reuse the already-parsed DOM.

### Changed

- **Version**: 0.10.6 → 0.11.0

Wrapped up by Kimi (kimi-k2.7 via kimi-code) on 2026-06-19

## [0.10.7] - 2026-06-19

### Fixed

- **IR pipeline HTML→Markdown formatting issues**:
  - Display math inside list items is now rendered with proper `$$` block delimiters and indentation, instead of leaking as raw LaTeX or breaking list structure.
  - Paragraphs containing display math (e.g. `<span class="ltx_p">` with an inline `<table class="ltx_equation">`) are now split into separate paragraph/equation blocks so display math is emitted as a block-level element.
  - The Markdown post-processor now preserves indentation on multi-line display math blocks, keeping list-item equations correctly nested.
  - The `_format_markdown_output` display-math regex now matches indented `$$` fences, preventing it from accidentally consuming equations inside list items.
  - List item block-level content (equations, figures, code, etc.) is indented at least 4 spaces per nesting level so standard Markdown parsers recognise it as part of the list item.

### Changed

- **Version**: 0.10.6 → 0.10.7

Wrapped up by Kimi (kimi-k2.7 via kimi-code) on 2026-06-19

## [0.10.6] - 2026-06-18

### Added

- New `--include-anchors` flag (and `output.include_anchors` setting) to optionally emit `<a id="..."></a>` anchor tags in generated Markdown. Default is `false`, so anchors are stripped from the final output.
- Final Markdown post-processing now cleans math formulas by removing trailing LaTeX spacing commands such as `\,`, `\ `, `\;`, etc.
- Inline math formulas are now padded with spaces around the `$` delimiters when adjacent to words.

### Fixed

- **IR pipeline HTML→Markdown formatting issues**:
  - `<br class="ltx_break"/>` tags at block level no longer leak as raw HTML; they are now ignored so paragraph/section spacing stays clean.
  - Code listings mis-labelled as Python (e.g. `pip install causal-learn`) are reclassified to `bash` when the content is an obvious shell command, or `text` when it is not valid Python syntax. Both base64 payload and line-by-line listing paths are covered.
  - Pipe characters `|` inside Markdown table cells are now escaped as `\|` so they do not corrupt the table layout.
  - `<math display="block">` elements are now emitted as display math (`$$...$$`) instead of inline math.
- **Legacy pipeline HTML→Markdown formatting issues**:
  - Ordered lists (`<ol>`) are now rendered with `1.`, `2.` numbering instead of always using `- ` bullets.
  - Tables without `<tbody>/<thead>/<tfoot>` no longer duplicate cells into every row due to an incorrectly nested `rows.append(values)`.
  - Code listings mis-labelled as Python are reclassified to `bash`/`text` using the same content-based heuristic as the IR pipeline.
- **v2 serializer registry**:
  - Inline tags (`em`, `i`, `strong`, `b`, `code`, `a`, `sup`, `sub`, `br`, `math`, `cite`, `span`) are now registered to their concrete serializer classes instead of the base `InlineSerializer`, so formatting and links are preserved when the v2 serializers are used.

### Changed

- **Version**: 0.10.5 → 0.10.6
- Updated `.gitignore` to also ignore the runtime `outputs/` directory.

Wrapped up by Kimi (kimi-k2.7 via kimi-code) on 2026-06-18

## [0.10.5] - 2026-05-29

### Fixed

- **Exception specificity across core modules**: Replaced 8 instances of bare `except Exception` with concrete exception tuples, improving error transparency and preventing unintended swallowing of programming errors.
  - `html/serializers/block.py`: `(UnicodeDecodeError, base64.binascii.Error)` for base64 decoding of `<pre>` code blocks.
  - `ingestion/local.py`: `(OSError, UnicodeDecodeError)` for LaTeX metadata extraction; `(OSError, ValueError, RuntimeError)` for HTML parsing; `OSError` for image file copy failures.
  - `ingestion/local_html.py`: `OSError` for HTML file read; `(ValueError, RuntimeError, OSError)` for HTML parse failures.
  - `ir/builders/latex.py`: Fixed a precedence bug in the section-stack pop condition—`while stack and stack[-1][0] >= current_level if current_level is not None else True` was parsed as `(while stack and stack[-1][0] >= current_level) if current_level is not None else True`, which could skip the pop loop entirely when `current_level` was `None`. Now correctly written as `while stack and (current_level is None or stack[-1][0] >= current_level)`.
  - `latex/structured.py`: Replaced a local `from importlib.metadata import version` with a top-level `import importlib.metadata` and catches `PackageNotFoundError` specifically.
  - `output/metadata_tex.py`: `(RuntimeError, ValueError, OSError)` for TeX affiliation merge failures; `(NetworkError, Arxiv2mdError, OSError)` for TeX fetch failures.
  - `settings/loader.py`: `(yaml.YAMLError, OSError, IOError)` for default config load failures.

### Changed

- **Project hygiene**: Moved `OPTIMIZATION_PLAN.md` to `docs/old_OPTIMIZATION_PLAN.md` and removed stale `.prompts.md` from the repository root.
- **Version**: 0.10.4 → 0.10.5

Wrapped up by Kimi (kimi-k2.6 via kimi-cli) on 2026-05-29

## [0.10.4] - 2026-05-13

### Fixed

- **LaTeX figure numbering and positioning bugs**:
  - `_remove_pandoc_divs` no longer strips blank lines inside `::: wrapfigure` divs, preventing anchor/image/caption blocks from collapsing into a single block.
  - `_renumber_figures_by_position` now renumbers all `> Figure N:` captions sequentially by document order, eliminating duplicate numbers caused by wrapfigure/figure/markdown-image handlers running in type-order rather than document-order.
  - `reorder_figures_to_first_reference` in `output/formatter.py` now detects merged figure blocks and skips caption blocks when searching for figure references, preventing figures from being relocated to the wrong section.

- **LaTeX reference "公式" false positive**: `_fix_references` only prepends the "公式" prefix when the label actually starts with `eq:`, so `\autoref{fig:xxx}` is rendered as `[fig:xxx](#fig:xxx)` instead of `[公式 fig:xxx](#fig:xxx)`.

### Added

- **Test coverage**: New `tests/test_latex_parser.py` with 7 tests for `_beautify_math_display` covering inline display-math blockification, trailing text, multiple blocks, boundary conditions, and internal newline collapsing.

### Changed

- **Version**: 0.10.3 → 0.10.4

Wrapped up by Kimi (kimi-k2.6 via kimi-cli) on 2026-05-13

## [0.10.3] - 2026-05-13

### Fixed

- **LaTeX Parser Ignored by IR Pipeline**: The default IR pipeline (`IngestionOrchestrator`) only supports HTML content parsing and silently ignored the `--parser latex` flag, producing empty "Untitled Document" output with 0 sections.
  - Fix: `run_convert_flow()` in `cli/runner/convert.py` now routes `--parser latex` requests to the legacy LaTeX pipeline, which correctly resolves TeX includes, extracts titles/sections, and emits full Markdown.
  - Affected: arXiv papers with multi-file TeX sources (e.g., `\input{sec/...}`) where HTML parsing yields no meaningful content.

### Changed

- **Version**: 0.10.2 → 0.10.3

Wrapped up by Kimi (kimi-k2.6 via kimi-cli) on 2026-05-13

## [0.10.2] - 2026-04-29

### Fixed

- **ImageResolver Stem Matching Too Permissive**: `_try_stem()` previously used `stem.lower() in src.lower()`, which caused false matches when a directory name in the HTML img `src` path happened to contain a valid stem. For example, `/html/.../Modality_Consistency/Modality_Consistency_Challenge.png` was incorrectly resolved to `Modality_Consistency.png` because the directory `Modality_Consistency` matched as a substring.
  - Fix: Stem matching now restricts substring checks to the **basename only** (`src_basename.lower()`), preventing directory-name collisions. Exact stem matches are still tried first.
  - `_try_index()` now guards against reusing already-consumed indices, eliminating duplicate image assignments.

- **JSON Schema Drift**: Regenerated `paper.document.schema.json` and `paper.meta.schema.json` to match the current Pydantic 2.9.2 output.

### Changed

- **Version**: 0.10.1 → 0.10.2

Wrapped up by Kimi (kimi-k2.6 via claude-code) on 2026-04-29

## [0.10.1] - 2026-04-28

### Fixed

- **BeautifulSoup Duplicate Parsing Elimination**: `HTMLBuilder._tag_to_blocks()` no longer re-serializes container children via `"".join(str(c) for c in tag.children)` and re-parses with `BeautifulSoup(...)`. Instead, the new `_children_to_blocks()` helper traverses children directly, avoiding O(n²) DOM operations.
  - Affects: `section`, `article`, `div`, `span` containers and `blockquote` blocks.

- **HTMLBuilder Footnote Queue**: `list.pop(0)` replaced with `deque.popleft()`, eliminating O(n²) list shifts when flushing pending footnotes.

- **Exception Specificity**: Replaced 5 instances of bare `except Exception` in core paths with concrete exception tuples.
  - `ir/emitters/json_emitter.py`: `(ImportError, ModuleNotFoundError)` for `importlib.metadata` fallback.
  - `images/resolver.py`: `(OSError, ValueError, TypeError, RuntimeError)` for image processing failures.
  - `network/arxiv_api.py`: `(ValueError)` for `datetime.fromisoformat`, `(AttributeError, ValueError, TypeError, KeyError)` for XML metadata extraction, `(httpx.RequestError, httpx.HTTPStatusError, ValueError, TypeError)` for Crossref fetch.

### Changed

- **Legacy Pipeline Deprecation**: `--legacy` flag now emits a `DeprecationWarning` on use. Help text updated to indicate deprecation and planned removal in v1.0.0.

- **Version**: 0.10.0 → 0.10.1

Wrapped up by Kimi (kimi-k2.6 via claude-code) on 2026-04-28

## [0.10.0] - 2026-04-28

### Added

- **IngestionOrchestrator**: Extracted the monolithic 340-line `_process_arxiv_paper_ir()` into a stateful 15-step orchestrator (`ingestion/orchestrator.py`).
  - Each step (`_fetch_html`, `_parse_html`, `_build_ir`, `_run_transforms`, etc.) is a discrete, testable method.
  - Pipeline state (HTML, parsed DOM, API metadata, image resolver, DocumentIR) is held as instance attributes.
  - `_merge_affiliations()` implements three-layer merge (API → HTML → TeX) with NFKD Unicode normalization.

- **ImageResolver**: Unified image path resolution layer (`ir/resolvers/images.py`).
  - Supports three resolution strategies: `index_map` (HTML figure index), `stem_map` (TeX filename stem), `path_map` (exact path match).
  - Strategy priority: exact path > stem match > index match > original fallback.
  - Result caching per resolver instance eliminates redundant lookups.
  - Both `HTMLBuilder` and `LaTeXBuilder` now delegate to `ImageResolver`.

- **LaTeXBuilder Footnote & Citation Support**:
  - `\footnote{...}`: Converted to `SuperscriptIR` marker (`¹`) + footnote content blocks (flushed at block boundaries).
  - `\cite{...}`: Extracted citation IDs rendered as superscript markers (e.g., `[smith2020]`).
  - Previously both were silently discarded (`return None`).

- **Exception Hierarchy**: Expanded `exceptions.py` with domain-specific errors.
  - `ParseError` — HTML/LaTeX parsing failures (with optional `source_snippet`).
  - `BuilderError` — IR builder failures.
  - `TransformError` — Transform pass failures.
  - `EmitterError` — Markdown/JSON emitter failures.

- **Tests**:
  - `tests/ir/test_image_resolver.py`: 16 unit tests covering exact/stem/index matching, priority, caching, case insensitivity, combined maps.
  - `tests/ir/builders/test_latex_builder.py`: 2 new tests for footnote and citation conversion.
  - `tests/test_integration_real_papers.py`: 5 LaTeX pipeline integration tests (TeX download → expansion → DocumentIR → Markdown emission).

### Changed

- **Transform Pipeline Order**: `SectionFilterPass` now runs before `NumberingPass`.
  - Reduces work for downstream passes when `--section` filtering is active.
  - Order: `SectionFilter` → `Numbering` → `FigureReorder` → `Anchor`.

- **BeautifulSoup Footnote Queue**: `list.pop(0)` replaced with `deque.popleft()` in `HTMLBuilder`, eliminating O(n²) list shifts.

- **Broad Exception Handling**: Replaced 4 instances of bare `except Exception` in `orchestrator.py` with specific exception tuples `(OSError, ValueError, TypeError, RuntimeError)`.

- **Version**: 0.9.2 → 0.10.0

Wrapped up by Kimi (kimi-k2.6 via claude-code) on 2026-04-28

## [0.9.2] - 2026-04-28

### Fixed

- **Author affiliation extraction for <br>-delimited personname**: Papers like 1706.03762 (Attention Is All You Need) put all authors in a single `ltx_personname` with `<br>` separators. The parser previously returned empty affiliations because the combined text exceeded the 80-char threshold.
  - Fix: Added `_parse_br_delimited_authors()` in `html/parser.py` to split by `<br>`, detect names vs affiliations, and build proper `ParsedAuthor` records.

- **Author affiliation enrichment from API metadata**: IR pipeline now prefers arXiv API author affiliations over HTML-parsed ones.
  - API metadata provides complete affiliations (e.g. "Google Brain; Google (United States), Mountain View, United States").
  - HTML parser catches edge cases when API lacks data.
  - Unicode name normalization (`NFKD` → ASCII) ensures `Łukasz Kaiser` (HTML) matches `Lukasz Kaiser` (API).

- **HTTP proxy support**: `httpx.AsyncClient` now reads `HTTP_PROXY` / `HTTPS_PROXY` environment variables.

### Changed

- **Version**: 0.9.1 → 0.9.2

Wrapped up by Kimi (kimi-k2.6 via claude-code) on 2026-04-28

## [0.9.1] - 2026-04-28

### Fixed

- **IR Pipeline Equation LaTeX extraction**: Fixed duplicated Unicode math symbols in generated markdown equations.
  - Root cause: ar5iv HTML renders equations as both Unicode text (in `<span class="ltx_text">`) and LaTeX (in `<math><annotation encoding="application/x-tex">`); `_get_text()` concatenated both.
  - Fix: Added `_extract_equation_latex()` to prefer `<math>` annotation LaTeX exclusively, falling back to plain text only when no math annotations are present.
  - Affects: `HTMLBuilder._build_table()` for equation tables (`ltx_equationgroup`, `ltx_eqn_table`, `ltx_eqn_align`).

- **IR Pipeline Table formatting**: Fixed broken markdown table output with excessive blank lines in cells.
  - Root cause: `_tag_to_inlines()` converted whitespace-only `NavigableString` nodes (newlines/indentation inside `<td>`) into `TextIR("\n")` entries.
  - Fix: Filter out whitespace-only text nodes in `_tag_to_inlines()` before creating `TextIR`.

- **IR Pipeline Footnote rendering**: Fixed footnote markers and content being merged inline as unreadable text (e.g. `^1^11To illustrate...`).
  - Fix: `_process_footnote()` extracts only the marker as `SuperscriptIR`, queues content as `BlockQuoteIR`, and flushes after each paragraph block.

- **IR Pipeline Ordered list numbering**: Fixed all ordered list items rendering as `1.` instead of sequential numbers.
  - Fix: Pass index through `_emit_list_item()` and use `f"{prefix}{index + 1}. "` for ordered markers.

- **IR Pipeline Author affiliations in summary**: Added author affiliations to markdown header summary output.

### Changed

- **Version**: 0.9.0 → 0.9.1

Wrapped up by Kimi (kimi-k2.6 via claude-code) on 2026-04-28

## [0.9.0] - 2026-04-27

### Added

- **IR Pipeline Full Feature Parity**: The IR pipeline (`_process_arxiv_paper_ir`) now supports all features of the legacy pipeline, enabling direct equivalent replacement:
  - **arXiv API metadata**: Fetch and save submission date, author ordering, DOI, categories via Atom XML API
  - **paper.yml generation**: Complete metadata YAML with authors, affiliations, publication info, identifiers, URLs
  - **Image processing**: Download TeX source, extract images, resolve local paths in markdown output
  - **Affiliation enrichment**: Merge TeX-author affiliations into paper metadata when configured
  - **Reference/Appendix sidecars**: Three-file split (main + References + Appendix) via `_split_ir_sections`
  - **Summary with token count**: Formatted title/authors/sections/tokens header matching legacy output
  - **Recursive sections tree**: Indented section hierarchy in markdown output
  - **Abstract heading normalization**: Strip redundant HTML-generated "Abstract" heading via `_strip_abstract_heading`
  - **full IR-based structured JSON**: Sections now contain nested blocks with full typed IR structures

- **IR-based Structured JSON (schema v2.0)**: Replaced legacy `write_structured_bundle` with `JsonEmitter.write_bundle()`:
  - `paper.meta.json` — Metadata with SHA-256 content fingerprint
  - `paper.document.json` — Section tree with full typed IR blocks (paragraphs with inlines, figures with images/captions, tables with headers/rows, equations, etc.)
  - `paper.assets.json` — Deduplicated asset list with paths, TeX stems, figure indices
  - `paper.graph.json` — Heterogeneous graph (paper → section → block, block → next, paper → asset)
  - CSV exports for graph nodes and edges

- **`--version` CLI Flag**: Check installed version via `arxiv2md-beta --version`

### Changed

- **IR Pipeline is now default**: `arxiv2md-beta convert` uses the IR pipeline by default; use `--legacy` to fall back to the original pipeline
- **`JsonEmitter`**: Complete rewrite with `write_bundle()`, `build_graph()`, CSV export, and support for all export modes (meta/document/full/all)
- **`HTMLBuilder`**: Enhanced with `image_map`/`image_stem_map` for local image path resolution and front matter block processing
- **`convert.py`**: Reorganized with asset population from image maps, API metadata enrichment on `DocumentIR`, and streamlined structured export
- **Version**: 0.8.0 → 0.9.0

Wrapped up by deepseek-v4-pro (deepseek-v4-flash via claude-code) on 2026-04-27

## [0.8.0] - 2026-04-27

### Added

- **IR (Intermediate Representation) System**: Three-tier compiler architecture for paper parsing
  - **Frontend (Builders)**: `HTMLBuilder` (BeautifulSoup → `DocumentIR`) and `LaTeXBuilder` (Pandoc JSON AST → `DocumentIR`) convert raw sources to structured IR
  - **Middle-end (Transforms)**: Composable `PassPipeline` with 5 passes — `NumberingPass` (figure/table/equation/algorithms), `AnchorPass` (stable anchors), `SectionFilterPass` (include/exclude), `FigureReorderPass` (move to first citation), and `PassPipeline` for ordering
  - **Backend (Emitters)**: `MarkdownEmitter` (→ Markdown), `JsonEmitter` (→ structured JSON), `PlainTextEmitter` (→ plain text) serialize `DocumentIR` to target formats
  - **Data Model**: 9 Inline types + 11 Block types + 3 Asset types via Pydantic v2 discriminated unions (`type: Literal[...]`)
  - **Visitor Pattern**: `IRVisitor` + `walk()` for depth-first traversal, with built-in `NodeCounter`, `TextCollector`
  - **RawBlockIR / RawInlineIR**: Fallback nodes preserve original format (HTML/LaTeX) for unrecognized content
- **`--ir` CLI Flag**: Opt-in IR pipeline via `--ir` on `convert` and `batch` commands
- **Python API**: Full programmatic access to `HTMLBuilder`, `LaTeXBuilder`, `PassPipeline`, `MarkdownEmitter`, `JsonEmitter`, `PlainTextEmitter`
- **137 IR Unit Tests**: Comprehensive coverage of builders, emitters, transforms, models, and visitors

### Changed

- **Project Structure**: New `ir/` package with 20+ files organized as `builders/`, `transforms/`, `emitters/`
- **Version**: 0.7.1 → 0.8.0

Wrapped up by Claude Opus 4.6 (claude-code) on 2026-04-27

## [0.7.1] - 2026-04-14

### Added

- **Figure Reordering**: Images are now moved to immediately after the paragraph where they are first cited, improving readability when figures appear far from their first reference in the source HTML/LaTeX
  - Works for both HTML and LaTeX parsers via a unified Markdown post-processing step in `format_paper`
  - Multi-panel figures (e.g. `<div align="center">` with multiple `<img>` tags) move as a single block
  - Unreferenced figures remain at their original position

### Fixed

- **Table Misplacement**: Tables (`Table N`) are no longer incorrectly treated as figures during reordering
- **Figure Citation Matching**: Added support for `Figure [N](#figure-N)` style markdown links when locating the first citation

Wrapped up by Kimi (kimi-for-coding via kimi-cli) on 2026-04-14

## [0.7.0] - 2026-04-14

### Added

- **LaTeX Parser Enhancement**: Full feature parity with HTML parser
  - **File Splitting**: LaTeX mode now generates separate files (`xx.md`, `xx-References.md`, `xx-Appendix.md`)
  - **Table of Contents (TOC)**: Auto-generated TOC with section links for LaTeX output
  - **Section Structure Parsing**: Full hierarchy extraction from `\section`, `\subsection`, `\subsubsection`
  - **Citation Links**: `\cite{key}` converted to `[N](#ref-N)` format with working anchors
  - **Figure/Table/Equation Anchors**: `\label{fig:X}` generates `<a id="fig:X"></a>` for cross-referencing
  - **Section Filtering**: `--sections` and `--section-filter-mode` now work with LaTeX parser
  - **Bibliography Recognition**: Automatic detection of References/Bibliography sections
  - **Appendix Detection**: Recognizes `\appendix` command and `Appendix X` sections
  - **Markdown Beautification**: Enhanced table formatting, figure captions, code blocks, math display
  - **Structured Export**: Full support for `paper.*.json` exports in LaTeX mode

### Changed

- **LaTeX Ingestion**: Now uses `split_for_reference=True` and `include_toc=True` by default
- **CLI Parameters**: `--remove-refs`, `--remove-toc`, `--sections` now work with `--parser latex`
- **CLI Cache Flag**: Renamed `--no-use-cache` to `--no-cache` for simplicity

### Fixed

- **Result Cache Removed**: Eliminated result-level caching that incorrectly bound output directory paths; now only download-level caches (TeX source, HTML, PDF) are kept
- **Cache Propagation**: `--no-cache` now properly disables caching for TeX source, HTML, and PDF downloads across both parsers
- **SectionNode Shadowing Bug**: Fixed a variable name collision in `ingest_paper_latex` that caused `'SectionNode' object has no attribute 'strip'` when section filtering was active

## [0.6.3] - 2026-04-08

### Added

- **Citations**: Inline citation links now generate clickable anchors to specific reference entries.
  - Citations like `[7]` are converted to `[[7](#ref-7)]` format.
  - References in the bibliography get `<a id="ref-N"></a>` anchors.
  - This enables navigation from inline citations to their corresponding bibliography entries.

### Changed

- **Citations**: Changed citation output from plain text `[N]` to linked format `[[N](#ref-N)]`.

## [0.6.2] - 2026-04-08

### Fixed

- **Cache**: Fixed validation error when loading cached results after `IngestionResult` schema added `summary` and `sections_tree` fields. Bumped `CACHE_VERSION` from `"1.0"` to `"1.1"` to invalidate old cache entries automatically.
- **Cache**: Added `default=str` to `json.dumps()` in `async_write_json()` so `pathlib.Path` values in metadata (e.g., `paper_output_dir`) serialize correctly instead of raising `TypeError`.

## [0.6.1] - 2026-04-06

### Fixed

- **Tests**: `tests/benchmarks/test_performance.py` no longer passes `timer="time.perf_counter"` as a string to `pytest.mark.benchmark` (pytest-benchmark requires a callable; a string broke timer calibration).
- **Tests**: Mocked `fetch_arxiv_html` integration tests now call `use_cache=False` so results do not come from `~/.cache/arxiv2md-beta` and bypass the mocked HTTP layer.

### Changed

- **`__version__`**: Aligned `arxiv2md_beta.__version__` with `pyproject.toml` (was stale).

## [0.6.0] - 2026-04-04

### Added

- **Async image parallel processing**: `images/resolver.py` gained `process_images_async()`.
  - PDF-to-PNG conversions run in a `ProcessPoolExecutor` (CPU-bound).
  - Raster copies run via `asyncio.gather` with a thread pool and semaphore-controlled concurrency.
- **HTTP connection pool reuse**: `network/http.py` now exposes `get_http_client()` for a shared module-level `httpx.AsyncClient`, while `async_http_client()` is kept for scoped custom timeouts.
- **Async file I/O**: New `utils/aiofiles_compat.py` wraps `aiofiles` for non-blocking reads/writes; integrated into `network/fetch.py` and `cli/output_finalize.py`.
- **Performance monitoring**: New `utils/metrics.py` provides `timed_operation` / `async_timed_operation` context managers; wired into `run_convert_flow`, `run_batch_flow`, `run_images_flow`, `run_paper_yml_flow`, and `ingest_paper_latex`.
- **Compiled regex patterns**: Module-level pre-compiled regexes in `output/formatter.py`, `latex/parser.py`, and `html/markdown.py` to reduce CPU overhead during parsing.

### Changed

- **CLI runner split**: `cli/runner.py` (369 lines) refactored into `cli/runner/` subpackage:
  - `base.py` – shared helpers (`merge_convert_params`)
  - `convert.py` – convert flow
  - `images.py` – images flow
  - `batch.py` – batch flow
  - `paper_yml.py` – paper-yml flow
- **Exception handling refined**: Replaced overly broad `except Exception` with specific types (`Arxiv2mdError`, `httpx.*`, `OSError`, `ValueError`, etc.) in `cli/runner/`, `network/fetch.py`, `images/resolver.py`, and `latex/parser.py`.
- Added `aiofiles>=24.0.0` to core dependencies.

### Fixed

- Tests updated to `await` the now-async `write_split_markdown_sidecars` and `write_result_json_sidecar` helpers.

## [0.5.0] - 2026-04-03

### Added

- **Local HTML file ingestion**: Support for processing locally saved HTML papers (e.g., from Science.org, IEEE, ACM).
  - New `LocalHtmlQuery` schema and `local_html.py` ingestion pipeline.
  - Auto-detects HTML files by extension (.html, .htm) or path pattern.
  - Extracts title, authors, abstract, and sections from HTML structure.
  - Copies associated files (images) from `_files/` or `.files/` directories.
  - CLI help text updated to mention local HTML file support.

### Changed

- **Citation format**: Changed from `*N*` to `[N]` for better readability and standard academic formatting.
- **External citation links**: Removed URL from citation links (e.g., `[1]` instead of `[*1*](https://...)`).
  - Supports arXiv bib links (`#bib.*`), Science.org collateral links (`#core-collateral-R*`), and common citation patterns.
- **Figure rendering**: Improved figure output format with proper Markdown image syntax and blockquote caption.

### Fixed

- **Content extraction for local HTML**: Fixed `_collect_content_until_next_heading` to properly handle headings with nested elements (e.g., `<i>` inside `<h4>`).
- **Duplicate content**: Fixed nested section content being collected twice in parent sections.
- **`div role="paragraph"`**: Added support for Science.org HTML paragraph structure.

## [0.4.1] - 2026-04-03

### Fixed

- **TeX image order**: Strip ``\affiliation[...]{...}`` blocks before enumerating ``\includegraphics``. Fairmeta / NeurIPS-style papers put institution logos (e.g. ``unc_logo``) in affiliations; those are not ar5iv numbered figures. Counting them shifted ``image_map[0]`` so opaque HTML assets (``xN.png``) paired with the wrong file (often the first affiliation logo).

## [0.4.0] - 2026-04-03

### Added

- **TeX author affiliations**: Parse ICML (`\icmlauthor` / `\icmlaffiliation`), IEEE, and common `\author` layouts from expanded TeX; merge into metadata when `ingestion.enrich_affiliations_from_tex` is true (default in `default_config.yml`). Implemented in `latex/author_affiliations.py` and `output/metadata_tex.py`.
- **`paper.yml` merge**: `merge_paper_yml_preserve_user_fields` keeps user-added keys when re-running conversion (fresh API output wins on overlap; missing keys preserved).
- Tests: `test_tex_image_order.py`, `test_author_affiliations.py`, `test_metadata_tex.py`; extra Markdown figure-order coverage in `test_markdown.py`.

### Fixed

- **Figure images vs ar5iv HTML**: ar5iv renames raster assets to `x1.png`, `x2.png`, … which do not match TeX filenames. Positional pairing then depended on TeX `\includegraphics` order; **logos inside `\icmltitle{...}` / `\title{...}`** were counted first while those graphics are often absent from numbered HTML figures, shifting every caption. TeX parsing now **strips title blocks** before enumerating graphics so `image_map` indices align with body figures.
- **HTML → Markdown**: Raster paths prefer matching `<img src>` basename/stem via `stem_to_image_path`; shared `used_image_indices` avoids reusing slots; smallest-unused index only when the URL is opaque.
- **Images resolver**: Register `source_image_path.name` in the stem map when the processed output basename differs.

### Changed

- Settings: `ingestion.enrich_affiliations_from_tex`; CLI runner and HTML ingestion wire TeX enrichment; `metadata.py` save path uses merge when file exists.

## [0.3.0] - 2026-03-25

### Changed (breaking)

- **Package layout**: Public modules are reorganized into subpackages. Update imports, for example:
  - `arxiv2md_beta.query_parser` → `arxiv2md_beta.query` (or `arxiv2md_beta.query.parser`)
  - `arxiv2md_beta.fetch` / `arxiv_api` / `crossref_api` → `arxiv2md_beta.network.*`
  - `arxiv2md_beta.output_layout` / `output_formatter` / `paper_metadata` → `arxiv2md_beta.output.*`
  - `arxiv2md_beta.image_resolver` / `image_extract` → `arxiv2md_beta.images.*`
  - `arxiv2md_beta.html_parser` / `markdown` / `sections` → `arxiv2md_beta.html.*`
  - `arxiv2md_beta.latex_parser` / `tex_source` → `arxiv2md_beta.latex.*`
  - `arxiv2md_beta.ingestion` / `html_ingestion` / `latex_ingestion` / `local_ingestion` → `arxiv2md_beta.ingestion.*`
- **CLI**: Entry point unchanged (`arxiv2md-beta`); implementation lives under `arxiv2md_beta.cli`.

### Added

- `CHANGELOG.md` for release notes.

### Fixed

- Lint: missing `shutil` import in local ingestion; minor unused imports/variables in network/latex/html.
- **Git**: `.gitignore` rule `output/` accidentally ignored the Python package `arxiv2md_beta/output/`; only repository-root `/output/` is ignored now (for local conversion output).

# arxiv2md-beta 数据流程

> 状态反映 2026-07-19 重构后（见 `docs/REVIEW_2026-07-19.md`）。4 种输入模式统一走 IR 三层架构；编排尾与后处理已收敛。

## 输入模式与路由

`cli/runner/convert.py::run_convert_flow` 按输入类型 4 路分支，全部最终汇聚到 `cli/output_finalize.py::finalize_convert_output`：

| 输入模式 | 入口 | Builder |
|---|---|---|
| 远程 arXiv HTML（默认 `convert <id>`） | `IngestionOrchestrator.run` | HTMLBuilder |
| 远程 arXiv LaTeX（`--parser latex`） | `ingest_paper_latex` | LaTeXBuilder |
| 本地 HTML 文件（`convert path.html`） | `ingest_local_html` | HTMLBuilder |
| 本地归档（`convert paper.tar.gz`） | `ingest_local_archive` → 自动探测 | LaTeX 或 HTML Builder |

公共 API `ingestion.ingest_paper()` 把 LaTeX 请求委托给 `ingest_paper_latex`，HTML 委托给 `IngestionOrchestrator`（PEP 562 懒加载，避免 `ingestion→cli` 循环导入）。

## 共享尾部（4 路径相同）

无论哪条路径，建好 `DocumentIR` 后都走同一序列：

```
Builder.build → DocumentIR
  → build_default_pipeline(parser=…).run(doc)        # 唯一管线工厂
  → emit_split_markdown(doc, …)                       # main / refs / appendix，单次 finalize_markdown
  → save_paper_metadata                                # paper.yml
  → run_structured_export(doc, …)                     # paper.{meta,document,…}.json（可选）
  → finalize_convert_output                            # 写 main.md + sidecars + 可选 PDF
```

`ingestion/ir_finalize.py` 是 4 路径共享尾的唯一来源：
- `emit_split_markdown`：拆 main/references/appendix（sidecar 清空 abstract 防泄漏），每段经 `finalize_markdown` 单次后处理，按 `settings.output.include_anchors` 决定剥锚点。
- `run_structured_export`：`JsonEmitter.write_bundle` 封装，mode=`none` 时 no-op。

## IR 三层（`ir/`）

1. **Builder**（`ir/builders/{html,latex}.py`）：`ParsedArxivHtml` / TeX → `DocumentIR`。封装全部 BS4 / Pandoc，IR 不接触原始格式。
2. **Transform**（`ir/transforms/pipeline.py::build_default_pipeline`）：**唯一**管线来源。顺序：
   `[SectionFilter?] → Numbering → [SectionNumbering 仅 latex] → FigureReorder → Anchor`。
   Pass 就地变异；每文档只跑一次。
3. **Emitter**（`ir/emitters/`）：
   - `MarkdownEmitter`（`linked_citations` / `remove_inline_citations`）：未知类型 `raise EmitterError`。
   - `JsonEmitter`：`paper.{meta,document,assets,bib,graph}.json`，`SCHEMA_VERSION` 单一源。
   - `PlainTextEmitter`：fail-fast。

## 后处理（单次）

`output/markdown_postprocess.py::finalize_markdown` = `format_markdown_output ∘ clean_markdown_output`。在 `emit_split_markdown` 内 emit 后**单次**应用；`cli/output_finalize` 不再二次后处理。

## 远程 HTML 路径（IngestionOrchestrator）细节

`IngestionOrchestrator.run()`（远程 HTML 默认路径）编排：

```
parse_query
  → 并行 fetch HTML + API metadata（TaskGroup 安全；HTML 解析 offload 到线程）
  → filter_sections（BS4 层，用于 summary/tree）
  → setup_output_dir
  → fetch TeX source + process_images_async（进程池 PDF→PNG）
  → build_ir (HTMLBuilder)          [to_thread]
  → enrich_metadata (API + HTML + TeX 机构合并)  [to_thread]
  → run_transforms (build_default_pipeline)      [to_thread]
  → normalize_abstract                              [to_thread]
  → emit_markdown (emit_split_markdown)            [to_thread]
  → build_result
  → save_paper_yml
  → structured_export (run_structured_export)
  → build_metadata
```

CPU 密集步骤经 `asyncio.to_thread` 卸载，batch 模式事件循环可并发推进其它论文。

## IR 数据模型

Pydantic v2 discriminated union，`extra="forbid"`。
- `BlockUnion`（11）：paragraph, heading, figure, table, list, code, equation, blockquote, algorithm, rule, raw_block
- `InlineUnion`（9）：text, emphasis, link, math, image_ref, superscript, subscript, break, raw_inline
- `AssetUnion`（3）：image_asset, svg_asset, other_asset
- `walk()` 覆盖 abstract / front_matter / sections / bibliography / assets / metadata.authors。

## 图片处理

- `images/processor.py::process_images_async`（**唯一**入口；同步版已删）：TeX 源 → `ProcessedImages`（PDF→PNG、EPS→PNG、栅格拷贝、裁白边）。进程池 + 信号量限流 + `finally` shutdown。
- `ir/resolvers/images.py::ImageResolver`：IR 构建时的纯路径查找器。多策略回退：exact → stem → index → path_map。`iter_assets()` 暴露资产清单（调用方不摸私有属性）。

## 网络层

- `network/http.py`：共享 `httpx.AsyncClient` 单例。`get_http_client()` 跨 event loop 重建；`close_http_client()` / `run_async(coro)` 顶层优雅关闭；`async_http_client()` async-with 上下文。
- 重试退避：arXiv HTML、PDF、TeX source、arXiv API、Crossref、abs-HTML 各自在 fetcher 内 `fetch_max_retries` + 指数退避（5 处循环语义各异——404 特例、retry_status 集合、per-API 状态——未抽象统一 helper）。
- 代理：从 `HTTP_PROXY` / `HTTPS_PROXY` 读取。

## 配置

合并优先级：**CLI > 环境变量（`ARXIV2MD_BETA__` 嵌套）> 用户 YAML（`ARXIV2MD_BETA_CONFIG_PATH` / `--config`）> 环境 profile > 默认 YAML**。`settings/loader.py` 手写 `deep_merge`（列表替换不合并）。`IngestionOrchestrator.__init__(params, settings=None)` 支持注入。

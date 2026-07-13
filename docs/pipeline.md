# arxiv2md-beta 数据流程

> 状态反映 v0.13 开发分支（IR 迁移进行中）。HTML 模式已迁移到 IR 三层架构；LaTeX 与本地模式仍走 legacy 管道，计划在后续阶段迁移。

## 输入模式与管道路由

`cli/runner/convert.py::run_convert_flow` 按输入类型路由到四条路径之一：

| 输入模式 | 入口 | 管道 |
|---|---|---|
| 远程 arXiv HTML（默认 `convert <id>`） | `IngestionOrchestrator` | **IR** |
| 远程 arXiv LaTeX（`convert <id> --parser latex`） | `ingestion/latex.py::ingest_paper_latex` | **Legacy**（pandoc + `format_paper`） |
| 本地 HTML 文件（`convert path.html`） | `ingestion/local_html.py::ingest_local_html` | **Legacy**（`convert_fragment_to_markdown` + `format_paper`） |
| 本地归档（`convert paper.tar.gz`） | `ingestion/local.py::ingest_local_archive` | **Legacy**（pandoc/本地 + `format_paper`） |

公共 API `ingestion.ingest_paper()` 把 LaTeX 请求委托给 `ingest_paper_latex`，HTML 委托给 `IngestionOrchestrator`。

## 一、IR 管道（HTML 默认路径）

`ingestion/orchestrator.py::IngestionOrchestrator.run()` 编排：

```
parse_query
  → 并行 fetch HTML + API metadata (TaskGroup-safe; HTML 解析 offload 到线程)
  → filter_sections
  → setup_output_dir
  → fetch TeX source + process_images_async (进程池 PDF→PNG)
  → build_ir          (HTMLBuilder: ParsedArxivHtml → DocumentIR)        [to_thread]
  → enrich_metadata   (API + HTML + TeX 机构信息合并)                    [to_thread]
  → run_transforms    (PassPipeline)                                     [to_thread]
  → normalize_abstract                                                     [to_thread]
  → emit_markdown     (MarkdownEmitter)                                  [to_thread]
  → build_result
  → save_paper_yml    (offloaded disk write)
  → structured_export (JsonEmitter, 可选)
  → build_metadata
```

CPU 密集步骤通过 `asyncio.to_thread` 卸载，事件循环在 batch 模式下可推进其它论文。

### IR 三层架构（`ir/`）

1. **Builder**（`ir/builders/html.py::HTMLBuilder`）：消费 `ParsedArxivHtml`，产出 `DocumentIR`。封装所有 BeautifulSoup 逻辑，IR 层不接触 HTML。
2. **Transform**（`ir/transforms/`）：`PassPipeline` 顺序执行（顺序敏感）：
   `SectionFilterPass` → `NumberingPass` → `FigureReorderPass` → `AnchorPass`
   - Pass **就地变异** `DocumentIR`（非纯函数；同一文档重跑会重复编号）。
   - `AnchorPass` 处理 `doc.abstract` + `front_matter` + `sections`。
   - `FigureReorderPass` 正则 `Fig(?:ure)?\.?\s*(\d+)`，覆盖 "Figure 3" / "Fig. 3" / "Fig 3"。
3. **Emitter**（`ir/emitters/`）：
   - `MarkdownEmitter`：`DocumentIR` → GitHub-flavored Markdown。覆盖全部 11 个 BlockUnion + 9 个 InlineUnion 成员；**未知类型 raise `EmitterError`**（不静默丢弃）。
   - `JsonEmitter`：`paper.{meta,document,assets,bib,graph}.json`。
   - `PlainTextEmitter`：token 计数 / 搜索用。

### IR 数据模型

Pydantic v2 discriminated union（`Annotated[A|B|..., Field(discriminator="type")]`），`extra="forbid"`。
- `ir/document.py`：`DocumentIR` / `SectionIR` / `PaperMetadata` / `AuthorIR`
- `ir/blocks.py`：`BlockUnion`（11 成员）
- `ir/inlines.py`：`InlineUnion`（9 成员）
- `ir/assets.py`：`AssetUnion`

### 图片解析

- `images/processor.py::process_images_async`：TeX 源 → `ProcessedImages`（PDF→PNG、EPS→PNG、栅格拷贝、裁白边）。进程池 + 信号量限流。
- `ir/resolvers/images.py::ImageResolver`：IR 构建时的纯路径查找器，把 `<img src>` 映射到本地路径。多策略回退：exact → stem → index → path_map。`iter_assets()` 暴露资产清单。

### Markdown 后处理

IR 路径的 Markdown 经过 `output/markdown_utils.py::format_markdown_output`（锚点换行、表格标题、display-math 简化、bullet 去重、空行压缩）+ `output/markdown_postprocess.py::clean_markdown_output`（剥 `<a id>`、清数学 LaTeX）。数学简化在 `simplify_display_math`（同模块）。

> 注：后处理仍是多遍（emitter 内 `_post_process` → `format_markdown_output` → `clean_markdown_output`）。收敛为单遍是后续优化项，需 golden-snapshot 校验。

## 二、Legacy 管道（LaTeX + 本地模式）

### LaTeX（`ingestion/latex.py`）

```
fetch_arxiv_metadata → fetch_and_extract_tex_source → process_images_async
  → parse_latex_to_markdown (pypandoc + _postprocess_markdown_enhanced, offloaded to_thread)
  → filter_sections → format_paper
```

`latex/parser.py`（1989 行）封装 pandoc 调用 + 大量后处理（修公式标签、表格、图片、引用、移除 pandoc div）。元数据用 TexSoup 优先、regex 回退。`ParserNotAvailableError`（`ParseError` 子类）在 pandoc 缺失时抛出。

### 本地 HTML（`ingestion/local_html.py`）

`parse_local_html` 自带解析器（与 `html/parser.py::parse_arxiv_html` 分离），走 `convert_fragment_to_markdown` + `format_paper`。**未走 IR**。

### 本地归档（`ingestion/local.py`）

`extract_local_archive` 解压，按内容分派到 LaTeX 子流程或 HTML 子流程。

## 三、共享输出层

- `output/markdown_utils.py`：`format_markdown_output` / `format_token_count` / `create_sections_tree` / `count_sections` / `simplify_display_math`。IR 与 legacy 共用，leaf 模块（仅依赖 schemas + settings + re + tiktoken）。
- `output/formatter.py::format_paper`：legacy 管道用，拼 summary + tree + content，含 `reorder_figures_to_first_reference`。
- `output/layout.py`：输出目录命名（classic / paper-pipeline）。
- `output/metadata.py` + `metadata_tex.py`：`paper.yml` 写入 + TeX 机构信息富化。
- `output/structured_export.py` + `ir/_legacy_blocks.py`：legacy 结构化导出（LaTeX/local 用）；IR 路径用 `JsonEmitter`。两套并存，待统一。

## 四、网络层

- `network/http.py`：共享 `httpx.AsyncClient` 单例。`get_http_client()` 跨 event loop 重建；`close_http_client()` / `run_async(coro)` 在 runner 顶层优雅关闭连接池。
- 重试退避：arXiv HTML、PDF、TeX source、arXiv API、Crossref、OpenAlex、abs-HTML 均走 `fetch_max_retries` + 指数退避（`retry_status_codes`）。
- 代理：从 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量读取。

## 五、配置

合并优先级：**CLI > 环境变量（`ARXIV2MD_BETA__` 嵌套）> 用户 YAML（`ARXIV2MD_BETA_CONFIG_PATH` / `--config`）> 环境 profile > 默认 YAML**。`settings/loader.py` 手写 `deep_merge`（列表替换不合并）。

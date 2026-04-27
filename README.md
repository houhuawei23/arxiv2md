# arxiv2md-beta

将 arXiv 论文转换为 Markdown 文件的 Python 工具，支持图片下载和插入，以及 HTML 和 LaTeX 两种解析模式。

## 功能特性

- **IR 中间表示系统**：三段式编译器架构
  - **前端 (Builder)**：HTML → `DocumentIR`（保底 RawBlock 保留原始格式）
  - **中端 (Transform)**：可组合的 Pass 管线（编号、锚点、图片重排、章节过滤...）
  - **后端 (Emitter)**：`DocumentIR` → Markdown / JSON / 纯文本
- **多源支持**：
  - arXiv 论文（ID 或 URL）
  - 本地 HTML 文件（Science.org、IEEE、ACM 等保存的论文）
  - 本地 arXiv 归档（tar.gz）
- **双解析模式**：
  - HTML 模式（默认）：解析 arXiv HTML 页面或本地 HTML 文件
  - LaTeX 模式：下载 TeX 源码，使用 pandoc 转换为 Markdown
- **图片支持**：
  - 自动下载 arXiv TeX Source
  - 提取图片文件（PNG, JPG, PDF, EPS 等）
  - PDF 图片自动转换为 PNG
  - 将图片插入到 Markdown 文件中
  - **与 ar5iv 对齐**：HTML 中插图常为 `x1.png`、`x2.png` 等匿名文件名；TeX 侧在统计 `\includegraphics` 时会排除 `\icmltitle{…}` / `\title{…}` 以及 **`\affiliation[…]{…}`** 内的机构 logo（fairmeta 等模板），避免与正文 Figure 序号错位；正文图在可用时按 `<img src>` 与 TeX 输出文件名匹配
- **性能优化**：
  - HTTP 连接池复用：减少批量处理时的连接建立开销
  - 图片异步并行处理：PDF 转换使用 `ProcessPoolExecutor`，普通图片使用线程池并发
  - 正则表达式预编译：提升 HTML/LaTeX 解析性能
  - 异步文件 I/O：避免文件操作阻塞事件循环
  - 关键路径性能监控：内置 `timed_operation` 上下文管理器
- **专业鲁棒**：
  - 完善的错误处理和异常处理
  - 使用 loguru 进行日志记录
  - 使用 [Rich](https://github.com/Textualize/rich) 显示下载与批处理进度
  - 清晰的类型注释和代码注释

## 安装

### 基本安装

```bash
pip install -e .
```

### 安装 LaTeX 解析支持（可选）

LaTeX 解析模式需要 `pypandoc`，它依赖于系统安装的 Pandoc：

```bash
# 安装 Pandoc（Ubuntu/Debian）
sudo apt-get install pandoc

# 安装 Python 依赖
pip install -e ".[latex]"
```

或者使用 `pypandoc_binary`（自带 Pandoc）：

```bash
pip install pypandoc_binary
```

## 使用方法

### 基本用法

子命令 **`convert`** 将论文转为 Markdown；**`batch`** 从文件批量执行与 `convert` 相同的转换；**`images`** 仅拉取并处理 TeX 中的图片（用于测试图片管线）。

```bash
# HTML 模式（默认）
arxiv2md-beta convert 2501.11120

# LaTeX 模式
arxiv2md-beta convert 2501.11120 --parser latex

# 指定输出根目录（其下会再建日期-标题子目录）
arxiv2md-beta convert 2501.11120 -o ./out

# 跳过图片下载
arxiv2md-beta convert 2501.11120 --no-images

# 本地 HTML 文件（如 Science.org、IEEE 等保存的论文）
arxiv2md-beta convert ./paper.html -o ./out

# 批量：ids.txt 每行一个 ID/URL/本地路径（# 开头与空行忽略）
arxiv2md-beta batch ids.txt -o ./out --no-images --max-concurrency 2

# 仅提取图片到目录（无 Markdown）
arxiv2md-beta images 2501.11120 -o ./img_test
```

**迁移说明**：旧版省略子命令的写法（如 `arxiv2md-beta 2501.11120`）已改为必须写 `convert`（或 `images` / `batch`）。

### 命令行参数（`convert`）

全局选项（写在子命令前）：`--config`、`--env` / `-E`、`--verbose` / `-v`（输出 DEBUG 日志）、`--force-reload`。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `INPUT` | arXiv ID、URL、本地归档路径或本地 HTML 文件 | - |
| `--parser` | 解析模式：`html` 或 `latex` | 配置中 `cli_defaults.parser` |
| `--output`, `-o` | 输出根目录 | 配置中 `cli_defaults.output_dir` |
| `--no-images` | 不下载/插入图片（仅 HTML 模式） | False |
| `--remove-refs` | 移除参考文献 | False |
| `--remove-toc` | 移除目录 | False |
| `--remove-inline-citations` | 移除内联引用 | False |
| `--section-filter-mode` | 过滤模式：`include` 或 `exclude` | `exclude` |
| `--sections` | 逗号分隔的 section 过滤 | - |
| `--section` | 可重复的 section 标题 | - |
| `--include-tree` | 输出 section 树 | False |
| `--emit-result-json` | 打印一行 `ARXIV2MD_RESULT_JSON=...`（含 `paper_output_dir`） | False |
| `--structured-output` | 在论文目录旁写入版本化 JSON：`none` \| `meta` \| `document` \| `full` \| `all` | `none` |
| `--emit-graph-csv` | 与 `all` 联用，额外输出 `paper.graph.nodes.csv` / `paper.graph.edges.csv` | False |

### 命令行参数（`batch`）

与 `convert` 使用相同的解析与输出相关选项（`--parser`、`--output`、`--no-images`、`--structured-output` 等）。额外参数：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `INPUT_FILE` | 每行一个 INPUT（与 `convert` 的 INPUT 相同）；以 `#` 开头的行视为注释 | - |
| `--max-concurrency`, `-j` | 最大并发转换数 | `3` |
| `--delay-seconds` | 从第二条任务起，每条任务开始前休眠的秒数（礼貌限流） | `0` |
| `--fail-fast` | 遇到首个错误即停止；默认处理完所有行并汇总 | 关闭 |

结束时在终端打印 Rich 汇总表；若有任一项失败，进程退出码为 `1`。请合理设置并发，避免对 arXiv 造成过大压力。

### 结构化 JSON（`paper.*.json`）

与 `paper.yml` 并行，可用 `--structured-output` 导出机器可读、带 `schema_version`（当前为 `1.0`）的接口，便于检索、图模型与下游脚本：

| 文件 | 说明 |
|------|------|
| `paper.meta.json` | arXiv 标识、标题、作者、日期、URL、工具版本、`content_sha256` |
| `paper.document.json` | 章节树摘要哈希 + 摘要/正文块级 IR（`blocks`） |
| `paper.assets.json` | 图片路径与 TeX stem 映射（`full` / `all`） |
| `paper.bib.json` | 参考文献占位（当前为空列表，预留 Phase D） |
| `paper.graph.json` | 异构图节点/边（`all`） |

JSON Schema 随包提供：`arxiv2md_beta/schemas/json/paper.meta.schema.json`、`paper.document.schema.json`。

根目录侧车 `.arxiv2md-result-<id>.json` 在启用结构化导出时会额外包含 `schema_version` 与 `structured_paths`（相对论文目录的路径表）。

示例：

```bash
arxiv2md-beta convert 2501.14622 --structured-output all --emit-graph-csv -o ./out
```

### Python API

**旧 API（仍可用，内部未变）：**

```python
import asyncio
from pathlib import Path

from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.query import parse_arxiv_input

async def main():
    query = parse_arxiv_input("2501.11120")
    result, metadata = await ingest_paper(
        arxiv_id=query.arxiv_id,
        version=query.version,
        html_url=query.html_url,
        ar5iv_url=query.ar5iv_url,
        parser="html",
        base_output_dir=Path("output"),
        structured_output="document",
    )
    print(result.content)

asyncio.run(main())
```

**新 IR API：手工构建、变换、导出**

```python
from arxiv2md_beta.ir import (
    # 数据模型
    DocumentIR, PaperMetadata, SectionIR,
    ParagraphIR, TextIR, EmphasisIR, LinkIR, CodeIR,
    # 前端
    HTMLBuilder,
    # 中端
    PassPipeline, NumberingPass, AnchorPass, FigureReorderPass,
    # 后端
    MarkdownEmitter,
)

# 1. HTML → DocumentIR
with open("paper.html") as f:
    html = f.read()
doc = HTMLBuilder().build(html, arxiv_id="2501.12345")

# 2. 变换管线
doc = (
    PassPipeline()
    .add(NumberingPass())         # figure/table/equation 顺序编号
    .add(FigureReorderPass())     # 图片移到首次引用处
    .add(AnchorPass())            # 稳定锚点
    .run(doc)
)

# 3. DocumentIR → Markdown
md = MarkdownEmitter().emit(doc)
```

**遍历/分析 IR 树：**

```python
from arxiv2md_beta.ir import walk, NodeCounter, IRVisitor

# 内置：统计各类型节点数量
counter = NodeCounter()
walk(doc, counter)
print(counter.counts)  # {"document": 1, "paragraph": 12, "text": 45, ...}

# 自定义 Visitor
class FigureCollector(IRVisitor):
    def __init__(self):
        self.ids = []
    def visit_figure(self, node):
        self.ids.append(node.figure_id)
    def visit_default(self, node):
        pass

fc = FigureCollector()
walk(doc, fc)
print(fc.ids)  # ["figure-1", "figure-2"]
```

**IR 数据模型（Pydantic v2，discriminated union）：**

```python
# JSON 往返无损
json_str = doc.model_dump_json(indent=2)
doc2 = DocumentIR.model_validate_json(json_str)

# 所有节点类型通过 type: Literal[...] 字段自动分发
# 9 种 Inline: text, emphasis, link, math, image_ref, superscript, subscript, break, raw_inline
# 11 种 Block:  paragraph, heading, figure, table, list, code, equation, blockquote, algorithm, rule, raw_block
```

## 项目结构

```
arxiv2md-beta/
├── src/
│   └── arxiv2md_beta/
│       ├── __init__.py
│       ├── __main__.py           # python -m 入口（转调 cli.main）
│       ├── cli/                  # Typer CLI
│       ├── network/              # fetch、http（httpx）、API
│       ├── query/                # arXiv ID / URL 解析
│       ├── output/               # layout、formatter、metadata
│       ├── images/               # 图片下载与处理
│       ├── html/                 # HTML 解析（parser、markdown、sections）
│       ├── latex/                # LaTeX 解析（parser、tex_source）
│       ├── ingestion/            # 编排层（pipeline、html、latex、local）
│       ├── cache/                # 下载缓存
│       ├── citations/            # 参考文献解析与 BibTeX
│       ├── config/               # 默认配置 YAML
│       ├── settings/             # Pydantic 配置加载
│       ├── schemas/              # 数据模型与 JSON Schema
│       ├── ir/                   # ★ IR 中间表示系统
│       │   ├── __init__.py       #   统一公开 API
│       │   ├── core.py           #   IRNode / InlineIR / BlockIR / AssetIR 基类
│       │   ├── inlines.py        #   9 种行内节点 + InlineUnion
│       │   ├── blocks.py         #   11 种群级节点 + BlockUnion
│       │   ├── document.py       #   SectionIR / PaperMetadata / DocumentIR
│       │   ├── assets.py         #   ImageAsset / SvgAsset / OtherAsset
│       │   ├── visitor.py        #   IRVisitor / IRWalker / walk()
│       │   ├── builders/         #   前端: HTML → DocumentIR
│       │   │   ├── base.py       #     IRBuilder 抽象基类
│       │   │   └── html.py       #     HTMLBuilder
│       │   ├── transforms/       #   中端: DocumentIR → DocumentIR
│       │   │   ├── base.py       #     IRPass / PassPipeline
│       │   │   ├── numbering.py  #     图表/公式编号
│       │   │   ├── anchor.py     #     锚点生成
│       │   │   ├── section_filter.py  # 章节过滤
│       │   │   └── figure_reorder.py  # 图片重排
│       │   └── emitters/         #   后端: DocumentIR → 目标格式
│       │       ├── base.py       #     IREmitter 抽象基类
│       │       └── markdown.py   #     MarkdownEmitter
│       └── utils/
│           └── logging_config.py
├── tests/
│   └── ir/                       # IR 系统测试 (118 tests)
├── demo/
├── pyproject.toml
└── README.md
```

## 主要模块

### `ir/` — IR 中间表示系统

采用三段式编译器架构，将论文解析为结构化数据，支持变换与多格式导出：

| 层 | 角色 | 核心类 |
|----|------|--------|
| Builder (前端) | 原始格式 → `DocumentIR` | `HTMLBuilder` |
| Transform (中端) | `DocumentIR` → `DocumentIR` | `IRPass`, `PassPipeline` |
| Emitter (后端) | `DocumentIR` → 目标格式 | `MarkdownEmitter` |

**数据模型**: 9 种 Inline 节点 (Text, Emphasis, Link, Math, ImageRef, Superscript, Subscript, Break, RawInline) + 11 种 Block 节点 (Paragraph, Heading, Figure, Table, List, Code, Equation, BlockQuote, Algorithm, Rule, RawBlock)，全部通过 Pydantic discriminated union 自动分发。

**Visitor**: `walk(doc, visitor)` 深度优先遍历 IR 树，内置 `NodeCounter`、`TextCollector`。

### `latex/tex_source.py`

下载和提取 arXiv TeX Source：
- 下载 TeX Source 压缩包
- 解压并提取图片文件
- 解析 LaTeX 文件中的图片引用（展开 `\input`/`\include`；**不计入** `\icmltitle{…}` / `\title{…}` / `\affiliation{…}` 内的 `\includegraphics`，以便与 ar5iv 正文插图顺序一致）
- 建立图片映射关系

### `images/resolver.py`

处理图片文件（支持异步并行）：
- 将 PDF 图片转换为 PNG（CPU 密集型任务放入 `ProcessPoolExecutor`）
- 复制其他格式的图片（通过线程池并发执行）
- 生成图片映射表（figure_index -> local_path）及 **stem → 路径** 映射（含源文件名与输出文件名别名），供 HTML `<img src>` 解析
- 通过信号量控制最大并发数，避免资源争用

### `html/markdown.py`

HTML 到 Markdown 转换：
- 支持图片路径替换（优先按 URL basename 与 TeX 产物 stem 匹配；否则按「未占用的最小 `image_map` 下标」兜底）
- 处理表格、列表、数学公式等
- 支持图片映射（`image_map`）与 `image_stem_map`

### `latex/parser.py`

LaTeX 到 Markdown 转换：
- 使用 pypandoc 进行转换
- 支持递归解析 `\input` 和 `\include`
- 提取标题、作者、摘要等元数据
- 替换图片引用为本地路径
- 正则表达式预编译，减少解析时的 CPU 开销

## 测试

运行测试：

```bash
# 全部测试
pytest tests/

# 仅 IR 系统测试 (118 tests)
pytest tests/ir/
```

可选：仅运行性能基准（需安装 `pytest-benchmark`，已包含在 `dev` 额外依赖中）：

```bash
pytest tests/benchmarks/ --benchmark-only
```

**说明**：集成测试里若未关闭 `use_cache`，可能命中 `~/.cache/arxiv2md-beta` 下已有缓存，从而绕过 `respx` 的 HTTP mock；仓库内相关用例已对 `fetch_arxiv_html` 使用 `use_cache=False`。

运行 demo：

```bash
python demo/demo_arxiv2md_beta.py
```

## 配置

运行时参数由打包的 `arxiv2md_beta/config/default_config.yml` 提供默认值，可按下面顺序覆盖（后者优先）：

1. 默认 `default_config.yml`
2. `environments/<name>.yml`（由 `app.environment` 或 `ARXIV2MD_BETA_APP__ENVIRONMENT` 选择，默认 `development`）
3. 用户 YAML：`--config /path/to.yml` 或环境变量 `ARXIV2MD_BETA_CONFIG_PATH`
4. 嵌套环境变量：前缀 `ARXIV2MD_BETA_`，子节用 `__` 连接（如 `ARXIV2MD_BETA_CACHE__DIR`、`ARXIV2MD_BETA_HTTP__FETCH_TIMEOUT_S`）；环境变量会覆盖 YAML
5. 命令行：`--config`、`--env` 等与 CLI 相关的覆盖（见 `arxiv2md_beta.cli.app` 中 Typer callback）

**不再支持**旧版扁平环境变量名（如 `ARXIV2MD_BETA_CACHE_PATH`、`ARXIV2MD_BETA_FETCH_TIMEOUT_S`）；请改用上述嵌套形式或 YAML。

用户配置目录默认在 `paths.user_config_dir`（见默认 YAML），日志等相对路径会解析到该目录下。

## 依赖

- `beautifulsoup4`: HTML 解析
- `httpx`: HTTP 客户端
- `loguru`: 日志记录
- `pydantic`: 配置模型与校验（环境变量在 loader 中合并）
- `rich`: 终端进度条与批量结果表格
- `tiktoken`: Token 计数
- `Pillow`: 图片处理
- `pdf2image`: PDF 转 PNG
- `aiofiles`: 异步文件 I/O
- `typer`: 命令行子命令与帮助
- `pypandoc` (可选): LaTeX 解析

## 注意事项

1. **PDF 转 PNG**：需要安装 `poppler-utils`（Ubuntu/Debian: `sudo apt-get install poppler-utils`）
2. **LaTeX 解析**：需要系统安装 Pandoc 或使用 `pypandoc_binary`
3. **网络访问**：需要能够访问 arXiv.org
4. **缓存**：默认在 `~/.cache/arxiv2md-beta`（与当前工作目录无关）；相对路径配置会解析到 `$XDG_CACHE_HOME/arxiv2md-beta/` 下。路径与 TTL 见 `cache` 节或 `ARXIV2MD_BETA_CACHE__*` 环境变量
5. **作者单位（TeX）**：默认尝试从已下载的 TeX 解析并合并到元数据；可在配置中关闭 `ingestion.enrich_affiliations_from_tex`
6. **`paper.yml` 增量更新**：再次运行同一输出目录时，磁盘上已有文件中仅存在于用户侧的字段会保留（与 API 新结果深度合并）

## 更新日志

版本与破坏性变更说明见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

本项目采用 [MIT License](LICENSE) 开源协议。参照 arxiv2md 项目的代码和结构，作为独立项目开发。

## 贡献

欢迎提交 Issue 和 Pull Request！

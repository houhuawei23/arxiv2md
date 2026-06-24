# AGENTS.md - arxiv2md-beta

本文档为 AI 编码助手提供项目背景、架构和开发指南。

## 项目概述

**arxiv2md-beta** 是一个将 arXiv 论文转换为 Markdown 文件的 Python 工具，支持图片下载和插入，以及 HTML 和 LaTeX 两种解析模式。

### 核心功能

- **双解析模式**：
  - HTML 模式（默认）：解析 arXiv HTML 页面，提取内容并转换为 Markdown
  - LaTeX 模式：下载 TeX 源码，使用 pandoc 转换为 Markdown
- **中间表示（IR）管线**：HTML 模式默认走 `ir/` 三层架构（Builder → Transform → Emitter）
- **图片支持**：
  - 自动下载 arXiv TeX Source
  - 提取图片文件（PNG, JPG, PDF, EPS 等）
  - PDF 图片自动转换为 PNG
  - 将图片插入到 Markdown 文件中
- **完善的错误处理**：使用 loguru 进行日志记录，使用 rich 显示进度条与表格

## 技术栈

- **Python**: >= 3.10
- **构建工具**: setuptools (PEP 517)
- **主要依赖**:
  - `beautifulsoup4`: HTML 解析
  - `httpx`: 异步 HTTP 客户端
  - `loguru`: 结构化日志
  - `pydantic`: 数据验证和序列化（IR 模型、配置）
  - `rich`: 进度条、表格、CLI 输出
  - `tiktoken`: Token 计数
  - `Pillow`: 图片处理
  - `pdf2image`: PDF 转 PNG
  - `aiofiles`: 异步文件 I/O
  - `typer`: CLI 子命令与全局 `--config` 等
  - `pypandoc` / `TexSoup` (可选): LaTeX 解析

## 项目结构

```
arxiv2md-beta/
├── src/arxiv2md_beta/          # 主源代码目录
│   ├── __init__.py             # 包初始化，版本号
│   ├── __main__.py             # python -m 入口，转调 cli.main
│   ├── cli/                    # Typer 应用与异步编排
│   │   ├── app.py              # Typer：callback + convert / batch / images / paper-yml / config / bibtex
│   │   ├── convert_cli.py      # convert 命令参数处理
│   │   ├── config_cmd.py       # config 子命令
│   │   ├── params.py           # ConvertParams / ImagesParams / PaperYmlParams
│   │   ├── runner/             # 各命令的 asyncio 业务流
│   │   ├── output_finalize.py  # 输出收尾与侧车 JSON
│   │   └── helpers.py          # collect_sections 等
│   ├── ir/                     # 中间表示（IR）三层架构
│   │   ├── core.py             # IRNode / InlineIR / BlockIR 基类
│   │   ├── document.py         # DocumentIR / SectionIR / PaperMetadata
│   │   ├── blocks.py           # BlockUnion 类型
│   │   ├── inlines.py          # InlineUnion 类型
│   │   ├── assets.py           # 图片 / SVG / 其他资源
│   │   ├── builders/           # HTML / LaTeX Builder
│   │   ├── emitters/           # Markdown / JSON / PlainText Emitter
│   │   ├── transforms/         # Numbering / Anchor / SectionFilter / FigureReorder Pass
│   │   ├── resolvers/          # ImageResolver
│   │   └── visitor.py          # IR 树 Visitor
│   ├── html/                   # HTML 解析与 Markdown 转换（旧路径 + parser）
│   │   ├── parser.py           # BeautifulSoup 解析器
│   │   ├── markdown.py         # 旧版 HTML→Markdown 转换器
│   │   ├── sections.py         # section 过滤工具
│   │   └── serializers/        # 实验性插件式序列化器
│   ├── latex/                  # LaTeX 解析
│   │   ├── parser.py           # Pandoc 包装与 Markdown 后处理
│   │   ├── tex_source.py       # TeX 源下载与解压
│   │   ├── author_affiliations.py
│   │   └── structured.py       # LaTeX 结构化导出
│   ├── ingestion/              # 入口编排
│   │   ├── __init__.py         # 导出 ingest_paper
│   │   ├── pipeline.py         # 旧公共 API（HTML 模式已委托给 IR Orchestrator）
│   │   ├── orchestrator.py     # IR 管道编排器
│   │   ├── ir_pipeline.py      # IR Builder / Transform / Emitter 工具函数
│   │   ├── html.py             # 旧 HTML 流程
│   │   ├── latex.py            # LaTeX 流程
│   │   ├── local.py            # 本地 LaTeX 归档
│   │   └── local_html.py       # 本地 HTML 文件
│   ├── citations/              # 引用解析（实验性，未完全接入主流程）
│   │   ├── models.py
│   │   ├── resolver.py
│   │   ├── formatter.py
│   │   └── html_parser.py
│   ├── images/                 # 图片处理
│   │   ├── resolver.py         # process_images / process_images_async
│   │   └── extract.py          # 仅提取图片的 CLI/API 入口
│   ├── cache/                  # 结果缓存
│   │   └── result_cache.py
│   ├── network/                # HTTP 客户端、arXiv API、Crossref、OpenAlex
│   ├── output/                 # 输出格式化、目录布局、metadata、结构化导出
│   ├── query/                  # 查询解析
│   ├── schemas/                # Pydantic 数据模型
│   ├── settings/               # 配置加载与 schema
│   └── utils/                  # 日志、进度、文件兼容、辅助函数
├── tests/                      # 测试目录
├── demo/                       # 示例脚本
├── docs/                       # 设计文档
├── pyproject.toml              # 项目配置和依赖
├── requirements.txt            # 基础依赖列表
└── README.md                   # 用户文档
```

## 架构设计

### 数据流（IR 模式，HTML 默认）

```
CLI（Typer: arxiv2md-beta convert … / batch …）
    ↓
cli.runner → IngestionOrchestrator
    ↓
_parse_query() → ArxivQuery
    ↓
_fetch_html_and_metadata() 并行下载 HTML + API 元数据
    ↓
parse_arxiv_html() → ParsedArxivHtml
    ↓
HTMLBuilder / LaTeXBuilder → DocumentIR
    ↓
PassPipeline: SectionFilter → Numbering → FigureReorder → Anchor
    ↓
MarkdownEmitter / JsonEmitter → Markdown / JSON
    ↓
写入 Markdown 文件 + paper.yml + 结构化导出
```

### 旧路径

- `ingestion/html.py`、`ingestion/latex.py`、`ingestion/local.py`、`html/markdown.py`、`output/formatter.py` 为旧版实现。
- 公共 API `ingest_paper()` 已让 HTML 模式委托给 `IngestionOrchestrator`，LaTeX/本地归档仍走旧路径。
- 长期目标：完成 IR 迁移后删除旧路径代码。

## 构建和安装

### 开发安装

```bash
# 基础安装
pip install -e .

# 安装 LaTeX 解析支持（需要系统 Pandoc）
pip install -e ".[latex]"

# 开发依赖
pip install -e ".[dev]"
```

### 系统依赖

- **PDF 转 PNG**: 需要 `poppler-utils`
  ```bash
  sudo apt-get install poppler-utils
  ```

- **LaTeX 解析**: 需要 Pandoc
  ```bash
  sudo apt-get install pandoc
  ```

## 测试

```bash
# 运行所有测试
pytest tests/

# 运行特定测试
pytest tests/test_query_parser.py -v

# 查看覆盖率
pytest tests/ --cov=src/arxiv2md_beta --cov-report=html
```

### 静态检查

```bash
ruff check src/arxiv2md_beta tests
ruff format --check src/arxiv2md_beta tests
mypy src/arxiv2md_beta
```

## 配置

见打包的 `src/arxiv2md_beta/config/default_config.yml` 与 `environments/*.yml`。合并优先级：**命令行 > 环境变量（`ARXIV2MD_BETA__` 嵌套）> 用户 YAML（`ARXIV2MD_BETA_CONFIG_PATH` / `--config`）> 环境 profile > 默认 YAML**。

| 用途 | 示例 |
|------|------|
| 用户配置文件 | `ARXIV2MD_BETA_CONFIG_PATH` 或 `--config path.yml` |
| 逻辑环境 | `app.environment` / `ARXIV2MD_BETA_APP__ENVIRONMENT`（`development` / `production` / `test`） |
| 日志级别 | 默认 INFO；`--verbose` / `-v` 单次强制 DEBUG；或 `ARXIV2MD_BETA_APP__LOG_LEVEL` |
| 缓存目录 | `ARXIV2MD_BETA_CACHE__DIR`（默认 `~/.cache/arxiv2md-beta`） |
| HTTP 超时 | `ARXIV2MD_BETA_HTTP__FETCH_TIMEOUT_S` |
| 禁用图片 | `--no-images` 或 `ARXIV2MD_BETA_IMAGES__DISABLE` |
| 保留锚点 | 默认不保留；`--include-anchors` 或 `ARXIV2MD_BETA_OUTPUT__INCLUDE_ANCHORS=true` |

## 代码风格指南

### Python 规范

- 使用 `from __future__ import annotations` 支持类型注解
- 类型注解：使用 `str | None` 而非 `Optional[str]`（Python 3.10+）
- 文档字符串：使用 Google 风格
- 导入顺序：标准库 → 第三方库 → 本地模块

## CLI 使用

```bash
# HTML 模式（默认，走 IR 管道）
arxiv2md-beta convert 2501.11120

# LaTeX 模式
arxiv2md-beta convert 2501.11120 --parser latex

# 批量处理
arxiv2md-beta batch inputs.txt -o ./output

# 指定输出目录和元数据
arxiv2md-beta convert 2501.11120 -o ./output --source "ICML" --short "Dreamer3"

# 跳过图片下载
arxiv2md-beta convert 2501.11120 --no-images

# 移除参考文献和目录
arxiv2md-beta convert 2501.11120 --remove-refs --remove-toc

# Section 过滤
arxiv2md-beta convert 2501.11120 --sections "Abstract,Introduction,Method"
arxiv2md-beta convert 2501.11120 --section-filter-mode exclude --sections "References,Appendix"

# 保留 <a id="..."></a> 锚点（默认不保留）
arxiv2md-beta convert 2501.11120 --include-anchors

# 仅提取图片
arxiv2md-beta images 2501.11120 -o ./img_out

# paper.yml 更新/生成
arxiv2md-beta paper-yml 2501.11120 -o ./paper.yml

# 配置验证/初始化
arxiv2md-beta config validate
arxiv2md-beta config init
```

## Python API 使用

```python
import asyncio
from pathlib import Path

from arxiv2md_beta.ingestion import ingest_paper

async def main():
    result, metadata = await ingest_paper(
        arxiv_id="2501.11120",
        version=None,
        html_url="https://arxiv.org/abs/2501.11120",
        ar5iv_url=None,
        parser="html",
        base_output_dir=Path("output"),
        source="Arxiv",
        short=None,
    )
    print(result.content)

asyncio.run(main())
```

## 输出格式

输出目录命名格式：`[Date]-[Source]-[Short]-[Paper Name]/`

例如：`20250115-Arxiv-Dreamer3-Learning-Predictive-Models/`

目录内包含：
- `[Date]-[Source]-[Short]-[Paper Name].md`: Markdown 文件
- `[Date]-[Source]-[Short]-[Paper Name].pdf`: PDF 文件（若下载成功）
- `images/`: 提取的图片
- `paper.yml`: 论文元数据

## 开发注意事项

- 所有网络请求都使用 `httpx.AsyncClient` 进行异步处理
- 缓存使用文件系统，基于修改时间判断 freshness
- 使用 `loguru` 进行日志记录，支持彩色输出
- Pydantic 模型定义在 `schemas/` 与 `ir/` 目录，用于数据验证和序列化
- 默认 HTML 模式已迁移到 IR 管道；新增功能请优先在 `ir/` 中实现
- 提交前本地 hook 会运行 secret leak 扫描与 `pre-commit run`

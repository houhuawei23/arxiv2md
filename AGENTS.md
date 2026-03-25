# AGENTS.md - arxiv2md-beta

本文档为 AI 编码助手提供项目背景、架构和开发指南。

## 项目概述

**arxiv2md-beta** 是一个将 arXiv 论文转换为 Markdown 文件的 Python 工具，支持图片下载和插入，以及 HTML 和 LaTeX 两种解析模式。

### 核心功能

- **双解析模式**：
  - HTML 模式（默认）：解析 arXiv HTML 页面，提取内容并转换为 Markdown
  - LaTeX 模式：下载 TeX 源码，使用 pandoc 转换为 Markdown
- **图片支持**：
  - 自动下载 arXiv TeX Source
  - 提取图片文件（PNG, JPG, PDF, EPS 等）
  - PDF 图片自动转换为 PNG
  - 将图片插入到 Markdown 文件中
- **完善的错误处理**：使用 loguru 进行日志记录，使用 tqdm 显示进度条

## 技术栈

- **Python**: >= 3.10
- **构建工具**: setuptools (PEP 517)
- **主要依赖**:
  - `beautifulsoup4`: HTML 解析
  - `httpx`: 异步 HTTP 客户端
  - `loguru`: 结构化日志
  - `pydantic`: 数据验证和序列化
  - `tqdm`: 进度条显示
  - `tiktoken`: Token 计数
  - `Pillow`: 图片处理
  - `pdf2image`: PDF 转 PNG
  - `typer`: CLI 子命令（`convert` / `images`）与全局 `--config` 等
  - `pypandoc` (可选): LaTeX 解析

## 项目结构

```
arxiv2md-beta/
├── src/arxiv2md_beta/          # 主源代码目录
│   ├── __init__.py             # 包初始化，版本号
│   ├── __main__.py             # python -m 入口，转调 cli.main
│   ├── cli/                    # Typer 应用与异步编排
│   │   ├── app.py              # Typer：callback + convert / images
│   │   ├── runner.py           # asyncio 业务流
│   │   └── helpers.py          # collect_sections 等
│   ├── network/                # fetch、arxiv_api、crossref_api
│   ├── query/                  # parser：arXiv ID/URL/本地归档
│   ├── output/                 # layout、formatter、metadata
│   ├── images/                 # resolver、extract
│   ├── html/                   # parser、markdown、sections
│   ├── latex/                  # parser、tex_source
│   ├── ingestion/              # pipeline、html、latex、local
│   ├── config/                 # 默认与环境 YAML
│   ├── settings/               # Pydantic 配置加载
│   ├── schemas/                # Pydantic 数据模型
│   │   ├── __init__.py
│   │   ├── query.py            # ArxivQuery 模型
│   │   ├── ingestion.py        # IngestionResult 模型
│   │   └── sections.py         # SectionNode 模型
│   │
│   └── utils/
│       └── logging_config.py   # 日志配置
│
├── tests/                      # 测试目录
│   ├── conftest.py             # pytest 配置和 fixtures
│   ├── test_cli_images.py      # Typer CLI 冒烟测试
│   ├── test_query_parser.py    # 查询解析器测试
│   ├── test_markdown.py        # Markdown 转换测试
│   └── test_integration.py     # 集成测试（需要网络）
│
├── demo/                       # 示例脚本
│   └── demo_arxiv2md_beta.py   # 使用示例
│
├── output/                     # 默认输出目录（运行时创建）
├── pyproject.toml              # 项目配置和依赖
├── requirements.txt            # 基础依赖列表
└── README_ARXIV2MD_BETA.md     # 用户文档
```

## 架构设计

### 数据流

```
CLI（Typer: arxiv2md-beta convert … / images …）
    ↓
cli.runner → query / images.extract / ingestion
    ↓
query.parse_arxiv_input() → ArxivQuery（若输入为 arXiv）
    ↓
ingestion.ingest_paper() → 路由到 HTML 或 LaTeX 解析器
    ↓
[HTML 模式]                    [LaTeX 模式]
network.fetch_arxiv_html()     latex.tex_source.fetch_and_extract_tex_source()
    ↓                                ↓
html.parser.parse_arxiv_html() latex.parser.parse_latex_to_markdown()
    ↓                                ↓
html.sections.filter_sections()  images.resolver.process_images()
    ↓                                ↓
html.markdown.convert…         output.formatter.format_paper()
    ↓
format_paper() → IngestionResult
    ↓
写入 Markdown 文件 + PDF 下载
```

### 关键模块说明

#### `cli/app.py` / `cli/runner.py`
控制台入口：`pyproject.toml` 中 `arxiv2md-beta = "arxiv2md_beta.cli:main"`。全局 `--config` / `--env` / `--force-reload` 在 Typer callback 中调用 `load_settings`；子命令 `convert` 与 `images` 分别进入 `run_convert_sync` 与 `run_images_sync`。

#### `ingestion/pipeline.py`
库入口 `ingest_paper`，根据 `parser` 参数路由到 `ingestion/html.py` 或 `ingestion/latex.py`（由 `cli.runner` 调用）。包 `arxiv2md_beta.ingestion` 在 `__init__.py` 中导出 `ingest_paper`。

#### `ingestion/html.py`
HTML 模式完整流程：
1. 获取 HTML（带缓存）
2. 解析 HTML 提取结构
3. 获取 arXiv API 元数据
4. 下载 TeX Source 提取图片
5. 转换 HTML 为 Markdown（含图片映射）
6. 格式化输出

#### `latex/tex_source.py`
下载和解压 arXiv TeX Source，建立图片映射关系。

#### `images/resolver.py`
处理图片文件：PDF 转 PNG，复制其他格式，生成 figure_index -> local_path 映射。

#### `html/markdown.py`
HTML 到 Markdown 转换，支持：
- 图片路径替换（使用 image_map）
- 表格、列表、数学公式
- 内联引用移除
- SVG 提取保存

## 构建和安装

### 开发安装

```bash
# 基础安装
pip install -e .

# 安装 LaTeX 解析支持（需要系统 Pandoc）
pip install -e ".[latex]"
```

### 系统依赖

- **PDF 转 PNG**: 需要 `poppler-utils`
  ```bash
  # Ubuntu/Debian
  sudo apt-get install poppler-utils
  ```

- **LaTeX 解析**: 需要 Pandoc
  ```bash
  # Ubuntu/Debian
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

### 测试结构

- `test_query_parser.py`: 单元测试，测试 arXiv ID 解析逻辑
- `test_markdown.py`: 单元测试，测试 HTML 到 Markdown 转换
- `test_integration.py`: 集成测试（默认跳过，需要网络访问）

## 配置

见打包的 `src/arxiv2md_beta/config/default_config.yml` 与 `environments/*.yml`。合并优先级：**命令行 > 环境变量（`ARXIV2MD_BETA__` 嵌套）> 用户 YAML（`ARXIV2MD_BETA_CONFIG_PATH` / `--config`）> 环境 profile > 默认 YAML**。

| 用途 | 示例 |
|------|------|
| 用户配置文件 | `ARXIV2MD_BETA_CONFIG_PATH` 或 `--config path.yml` |
| 逻辑环境 | `app.environment` / `ARXIV2MD_BETA_APP__ENVIRONMENT`（`development` / `production` / `test`） |
| 缓存目录 | `ARXIV2MD_BETA__CACHE__DIR` |
| HTTP 超时 | `ARXIV2MD_BETA__HTTP__FETCH_TIMEOUT_S` |
| 禁用 tqdm | `ARXIV2MD_BETA__IMAGES__DISABLE_TQDM=true` |

旧版扁平变量（如 `ARXIV2MD_BETA_CACHE_PATH`）已移除，请改用嵌套键或 YAML。

## 代码风格指南

### Python 规范

- 使用 `from __future__ import annotations` 支持类型注解
- 类型注解：使用 `str | None` 而非 `Optional[str]`（Python 3.10+）
- 文档字符串：使用 Google 风格
- 导入顺序：标准库 → 第三方库 → 本地模块

### 示例

```python
"""Module docstring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

import httpx
from loguru import logger

from arxiv2md_beta.settings import get_settings
from arxiv2md_beta.schemas import ArxivQuery


async def fetch_data(url: str) -> str | None:
    """Fetch data from URL.
    
    Parameters
    ----------
    url : str
        Target URL
        
    Returns
    -------
    str | None
        Response text or None if failed
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            return response.text
    except httpx.RequestError as e:
        logger.error(f"Failed to fetch: {e}")
        return None
```

## CLI 使用

```bash
# HTML 模式（默认）
arxiv2md-beta convert 2501.11120

# LaTeX 模式
arxiv2md-beta convert 2501.11120 --parser latex

# 指定输出目录和元数据
arxiv2md-beta convert 2501.11120 -o ./output --source "ICML" --short "Dreamer3"

# 跳过图片下载
arxiv2md-beta convert 2501.11120 --no-images

# 移除参考文献和目录
arxiv2md-beta convert 2501.11120 --remove-refs --remove-toc

# Section 过滤
arxiv2md-beta convert 2501.11120 --sections "Abstract,Introduction,Method"
arxiv2md-beta convert 2501.11120 --section-filter-mode exclude --sections "References,Appendix"

# 仅提取图片（测试图片管线）
arxiv2md-beta images 2501.11120 -o ./img_out
```

## Python API 使用

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
        parser="html",  # 或 "latex"
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
- `[Date]-[Source]-[Short]-[Paper Name].pdf`: PDF 文件
- `images/`: 提取的图片
- `paper.yml`: 论文元数据

## 注意事项

1. **网络访问**: 需要能够访问 arxiv.org
2. **缓存机制**: 下载的文件会缓存在本地，可通过环境变量配置
3. **ar5iv 回退**: 当 arxiv.org HTML 不可用时，会自动尝试 ar5iv.labs.arxiv.org
4. **图片处理**: TeX Source 可能不存在，图片处理会静默跳过
5. **PDF 下载**: 总是尝试下载 PDF，失败时仅记录警告

## 开发注意事项

- 所有网络请求都使用 `httpx.AsyncClient` 进行异步处理
- 缓存使用文件系统，基于修改时间判断 freshness
- 使用 `loguru` 进行日志记录，支持彩色输出
- Pydantic 模型定义在 `schemas/` 目录，用于数据验证

# arxiv2md-beta

将 arXiv 论文转换为 Markdown 文件的 Python 工具，支持图片下载和插入，以及 HTML 和 LaTeX 两种解析模式。

## 功能特性

- **双解析模式**：
  - HTML 模式（默认）：解析 arXiv HTML 页面，提取内容并转换为 Markdown
  - LaTeX 模式：下载 TeX 源码，使用 pandoc 转换为 Markdown
- **图片支持**：
  - 自动下载 arXiv TeX Source
  - 提取图片文件（PNG, JPG, PDF, EPS 等）
  - PDF 图片自动转换为 PNG
  - 将图片插入到 Markdown 文件中
- **专业鲁棒**：
  - 完善的错误处理和异常处理
  - 使用 loguru 进行日志记录
  - 使用 tqdm 显示进度条
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

```bash
# HTML 模式（默认）
arxiv2md-beta 2501.11120

# LaTeX 模式
arxiv2md-beta 2501.11120 --parser latex

# 指定输出文件
arxiv2md-beta 2501.11120 -o output.md

# 跳过图片下载
arxiv2md-beta 2501.11120 --no-images
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `input` | arXiv ID 或 URL | - |
| `--parser` | 解析模式：`html` 或 `latex` | `html` |
| `--output`, `-o` | 输出文件路径 | `{arxiv_id}.md` |
| `--images-dir` | 图片目录名（相对于输出文件） | `{output_stem}_images` |
| `--no-images` | 不下载/插入图片（仅 HTML 模式） | False |
| `--remove-refs` | 移除参考文献 | False |
| `--remove-toc` | 移除目录 | False |
| `--remove-inline-citations` | 移除内联引用 | False |
| `--section-filter-mode` | 过滤模式：`include` 或 `exclude` | `exclude` |
| `--sections` | 逗号分隔的 section 过滤 | - |
| `--include-tree` | 输出 section 树 | False |

### Python API

```python
import asyncio
from pathlib import Path
from arxiv2md_beta.ingestion import ingest_paper
from arxiv2md_beta.query_parser import parse_arxiv_input

async def main():
    query = parse_arxiv_input("2501.11120")
    result, metadata = await ingest_paper(
        arxiv_id=query.arxiv_id,
        version=query.version,
        html_url=query.html_url,
        parser="html",
        output_dir=Path("output"),
        images_dir_name="images",
    )
    print(result.content)

asyncio.run(main())
```

## 项目结构

```
arxiv2md-beta/
├── src/
│   └── arxiv2md_beta/
│       ├── __init__.py
│       ├── __main__.py           # CLI 入口
│       ├── cli.py                # 命令行参数解析
│       ├── config.py             # 配置
│       ├── ingestion.py          # 主流程编排
│       ├── fetch.py              # HTML/TeX 下载
│       ├── tex_source.py         # TeX 源码下载与解压
│       ├── html_parser.py        # HTML 解析
│       ├── html_ingestion.py     # HTML 解析流程
│       ├── latex_parser.py       # LaTeX 解析
│       ├── latex_ingestion.py    # LaTeX 解析流程
│       ├── markdown.py           # Markdown 转换
│       ├── image_resolver.py     # 图片处理
│       ├── query_parser.py       # arXiv ID 解析
│       ├── sections.py           # Section 过滤
│       ├── output_formatter.py   # 输出格式化
│       ├── schemas/              # 数据模型
│       └── utils/
│           └── logging_config.py # 日志配置
├── tests/                        # 测试
├── demo/                         # Demo 脚本
├── pyproject.toml
└── README.md
```

## 主要模块

### `tex_source.py`

下载和提取 arXiv TeX Source：
- 下载 TeX Source 压缩包
- 解压并提取图片文件
- 解析 LaTeX 文件中的图片引用
- 建立图片映射关系

### `image_resolver.py`

处理图片文件：
- 将 PDF 图片转换为 PNG
- 复制其他格式的图片
- 生成图片映射表（figure_index -> local_path）

### `markdown.py`

HTML 到 Markdown 转换：
- 支持图片路径替换
- 处理表格、列表、数学公式等
- 支持图片映射（image_map）

### `latex_parser.py`

LaTeX 到 Markdown 转换：
- 使用 pypandoc 进行转换
- 支持递归解析 `\input` 和 `\include`
- 提取标题、作者、摘要等元数据
- 替换图片引用为本地路径

## 测试

运行测试：

```bash
pytest tests/
```

运行 demo：

```bash
python demo/demo_arxiv2md_beta.py
```

## 配置

通过环境变量配置：

- `ARXIV2MD_BETA_CACHE_PATH`: 缓存目录路径（默认：`.arxiv2md_beta_cache`）
- `ARXIV2MD_BETA_CACHE_TTL_SECONDS`: 缓存 TTL（默认：86400，24小时）
- `ARXIV2MD_BETA_FETCH_TIMEOUT_S`: 请求超时时间（默认：10秒）
- `ARXIV2MD_BETA_FETCH_MAX_RETRIES`: 最大重试次数（默认：2）

## 依赖

- `beautifulsoup4`: HTML 解析
- `httpx`: HTTP 客户端
- `loguru`: 日志记录
- `pydantic`: 数据验证
- `tqdm`: 进度条
- `tiktoken`: Token 计数
- `Pillow`: 图片处理
- `pdf2image`: PDF 转 PNG
- `pypandoc` (可选): LaTeX 解析

## 注意事项

1. **PDF 转 PNG**：需要安装 `poppler-utils`（Ubuntu/Debian: `sudo apt-get install poppler-utils`）
2. **LaTeX 解析**：需要系统安装 Pandoc 或使用 `pypandoc_binary`
3. **网络访问**：需要能够访问 arXiv.org
4. **缓存**：下载的文件会缓存在本地，可通过环境变量配置缓存路径和 TTL

## 许可证

本项目采用 [MIT License](LICENSE) 开源协议。参照 arxiv2md 项目的代码和结构，作为独立项目开发。

## 贡献

欢迎提交 Issue 和 Pull Request！

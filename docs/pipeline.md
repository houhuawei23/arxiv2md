arxiv2md-beta 目前采用**双管道（Dual Pipeline）**架构，支持 **HTML 模式**（默认）和 **LaTeX 模式**两种转换路径。两者的核心区别是：HTML 模式走「自定义 BeautifulSoup 序列化器」，LaTeX 模式走「Pandoc + 大量后处理」。

以下是完整的转换逻辑和方法：

---

## 一、HTML 模式（arxiv HTML → Markdown）

数据流：

```
fetch HTML → parse_arxiv_html() → filter sections → convert_fragment_to_markdown() → format_paper()
```

### 1. 获取与解析 HTML

- `fetch_arxiv_html()` 先用 `httpx` 抓取 arxiv.org HTML，失败则自动回退到 `ar5iv.labs.arxiv.org`。
- `parse_arxiv_html()`（`html/parser.py`）用 **BeautifulSoup** 提取结构化信息：
  - **Title**：从 `h1.ltx_title` 等选择器提取，过滤 `[cs/0309048]` 和 `Contents` 后缀。
  - **Authors**：从 `div.ltx_authors` 提取，会过滤邮箱、纯数字脚注、贡献声明等噪音。
  - **Abstract**：同时提取纯文本和 `abstract_html`（inner HTML，用于处理摘要里的图片）。
  - **Front Matter**：提取摘要和第一个 `section` 之间的 HTML（如标题块图片）。
  - **Sections**：按 `h1-h6` 切分，构建嵌套的 `SectionNode` 树。
  - **Date**：从 meta tags 或页面时间元素提取 `YYYYMMDD`。

### 2. HTML → Markdown 序列化（`html/markdown.py`）

核心入口：

- `convert_html_to_markdown()`：处理完整文档（标题/作者/摘要/目录）。
- `convert_fragment_to_markdown()`：处理每个 section 和 abstract 的 HTML 片段。

#### 预处理

- `_strip_unwanted_elements()`：移除 `script/style/nav/footer/TOC`。
- `convert_all_mathml_to_latex()`：将 `<math>` 标签中 `annotation(encoding="application/x-tex")` 的内容提取为 `$latex$`。
- `fix_tabular_tables()`：清理 LaTeXML 生成的表格上的冗余属性。

#### 块级序列化 `_serialize_block()`

递归遍历 DOM，按标签类型分发：

- **`<h1-h6>`** → `# heading`
- **`<p>`** → `_serialize_paragraph_maybe_with_figures()`  
  段落中如果内嵌了 `span.ltx_figure`（含 `<img>`），会将其拆出来作为**独立的块级 figure**。
- **`<ul>/<ol>`** → `- item`（支持嵌套）
- **`<figure>`** → `_serialize_figure()`
  - 区分「图片 figure」和「表格/算法 figure」。
  - **多面板图片**：收集 caption 之前的所有 `<img>`，2~N 张时输出 HTML `<div align="center">` + 等宽 `<img>` 并排显示。
  - **单图**：标准 Markdown `![alt](path)`。
  - **图片映射**：通过 `image_map`（按 `\includegraphics` 顺序的索引）和 `image_stem_map`（按文件名 stem 匹配），将 HTML 里的 `<img src="...">` 映射到本地处理后的图片。
- **`<table>` / `span.ltx_tabular`** → `_serialize_table()`，输出 Markdown pipe table。
- **`<div class="ltx_listing">`** → 代码块。优先读取 base64 内嵌的原始代码，否则逐行拼接 `ltx_listingline`。
- **`<blockquote>`** → `> text`

#### 行内序列化 `_serialize_inline()`

- `<em>` → `*text*`；`<strong>` → `**text**`
- `<a>` 智能处理：
  - 引用链接（`#bib.bib7`）→ `[7](#ref-7)`（或根据 `--remove-inline-citations` 直接移除）
  - 内部链接（`arxiv.org/html/...#S1.F1`）→ 映射为本地锚点 `[...](#figure-1)`
  - 普通外链 → `[text](href)`
- `<sup>` → `^text`；`<math>` → `$latex$`
- 空白压缩：多个空白/换行折叠为单个空格。

### 3. 图片处理

即使走 HTML 模式，也会下载 **TeX Source**：

- `fetch_and_extract_tex_source()` 下载并解压源码包。
- `process_images()` 提取其中的 `PNG/JPG/PDF/EPS` 等，PDF 自动转为 PNG。
- 生成 `image_map` 和 `image_stem_map`，供序列化器匹配 HTML 中的图片。

### 4. 最终格式化（`output/formatter.py`）

`format_paper()` 将章节树拼成最终 Markdown：

- YAML frontmatter
- 目录（可选）
- 摘要 + 各 section 递归拼接
- **后处理**：`reorder_figures_to_first_reference()` 会把每个图片块移动到**首次引用它的段落之后**。

---

## 二、LaTeX 模式（TeX Source → Markdown）

数据流：

```
fetch TeX source → resolve includes → pypandoc convert → post-process → section extraction → format_paper()
```

### 1. 获取与预处理

- `fetch_and_extract_tex_source()` 下载 `.tar.gz` 并解压。
- `_resolve_latex_includes()`（`latex/parser.py`）递归内联所有 `\input{}`、`\include{}`。
- `\lstinputlisting{}` 会被直接替换为 "`\n文件内容\n`"。
- `_fix_orphan_ends()`：移除没有对应 `\begin` 的孤立 `\end{env}`。

### 2. 元数据提取

在交给 Pandoc 之前先提取：

- **Title / Authors**：优先用 **TexSoup** 做嵌套大括号解析，失败回退到基于大括号计数的手工 regex。
- **Abstract**：正则匹配 `\begin{abstract}...\end{abstract}`，并做简单的 LaTeX 命令清理。

### 3. Pandoc 核心转换

```python
pypandoc.convert_text(
    full_tex_content, "md", format="latex", extra_args=["--wrap=none"]
)
```

### 4. Markdown 后处理（`_postprocess_markdown()`）

Pandoc 输出需要大量修正，按顺序执行：

| 后处理函数                               | 作用                                                                                                         |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `_fix_equation_labels()`                 | 将 `\label{eq:xxx}` 包裹的公式转换为 `<a id="eq:xxx"></a>` + `$$...$$`                                       |
| `_fix_tables()`                          | 将 Pandoc 的 `::: mytabular` 自定义块转换为标准 Markdown pipe table                                          |
| `_fix_figures()`                         | 将 `<figure id="fig:xxx">` 块转换为 `<a id="fig:xxx"></a>` + `![](./images/xxx.png)` + `> Figure N: caption` |
| `_fix_markdown_images_with_attributes()` | 处理 Pandoc 属性语法 `![caption](path){#fig:xxx width="..."}`                                                |
| `_fix_references()`                      | 简化引用格式，如 `[\[eq:tok\]](#eq:tok){reference-type="ref+label"}` → `[公式 eq:tok](#eq:tok)`              |
| `_remove_pandoc_divs()`                  | 移除 `::: center` 等 Pandoc div 包装                                                                         |
| `_replace_image_references()`            | 将图片路径统一替换为本地处理后的 `./images/xxx.png`                                                          |

### 5. 章节结构化

`_extract_sections_from_latex()`：

- 从原始 LaTeX 中解析 `\section`、`\subsection`、`\subsubsection` 位置和层级。
- 将 Pandoc 生成的 Markdown 按标题切分，与 LaTeX 中的章节一一对应。
- 构建 `SectionNode` 树。

### 6. 格式化输出

同样调用 `format_paper()`，也会经过 `reorder_figures_to_first_reference()` 的图片重排后处理。

---

## 三、两种模式的关键对比

| 维度         | HTML 模式                                                | LaTeX 模式                        |
| ------------ | -------------------------------------------------------- | --------------------------------- |
| **核心引擎** | BeautifulSoup + 自定义序列化器                           | pypandoc (系统 Pandoc)            |
| **数学公式** | MathML → 从 annotation 提取 LaTeX                        | 原生 LaTeX，保留 `$$` 块          |
| **表格**     | 直接解析 HTML `<table>` 或 `span.ltx_tabular`            | Pandoc mytabular → pipe table     |
| **图片映射** | 按 HTML `<img src>` 的 basename/stem 匹配 TeX 提取的图片 | 按 LaTeX 的 `\label`/路径直接映射 |
| **代码块**   | `div.ltx_listing` 逐行或 base64 提取                     | `\lstinputlisting` 内联替换       |
| **后处理量** | 较小（在序列化时即时处理）                               | 较大（修正 Pandoc 输出格式）      |
| **可靠性**   | 对 ar5iv 的 HTML 结构依赖较强                            | 需要系统安装 Pandoc 和 poppler    |

---

## 四、统一的最终后处理

无论哪种模式，最终都会进入 `output/formatter.py` 的 `format_paper()`，其中包含一个统一的 Markdown 级后处理：

**`reorder_figures_to_first_reference()`**

- 将 Markdown 按 `\n\n` 切分为 blocks。
- 识别连续的图片块（`<a id="figure-N"></a>` + `![...](...)` + `> Figure N: ...`）。
- 找到每个图片首次被引用的段落（匹配 `Figure N`、`Fig. N`、`[Figure N](#...)` 等）。
- **将图片块插入到引用段落之后**；未被引用的图片保留在原位置。

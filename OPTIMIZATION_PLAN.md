# arxiv2md-beta 优化重构计划 (v1.0)

> 分析日期: 2026-04-28 | 当前版本: 0.9.2 | 目标版本: 1.0.0

---

## 目录

1. [现状诊断](#1-现状诊断)
2. [优化目标](#2-优化目标)
3. [Phase 1: 架构重构（高优先级）](#phase-1-架构重构高优先级)
4. [Phase 2: 性能优化](#phase-2-性能优化)
5. [Phase 3: 功能增强](#phase-3-功能增强)
6. [Phase 4: 质量与可维护性](#phase-4-质量与可维护性)
7. [实施路线图](#7-实施路线图)
8. [风险与回滚策略](#8-风险与回滚策略)

---

## 1. 现状诊断

### 1.1 架构健康度

| 维度 | 评分 | 说明 |
|------|------|------|
| IR 管线 | 良好 | 三阶架构清晰， Builders/Transforms/Emitters 职责分离 |
| 双管线并存 | 差 | Legacy + IR 双管线并存，代码重复，维护负担重 |
| 数据模型 | 良好 | Pydantic v2 类型安全，Union 类型完整 |
| 配置系统 | 良好 | YAML + 环境变量分层覆盖，热重载支持 |
| 测试覆盖 | 中等 | 325 单元测试 + 8 集成测试，但 legacy 路径覆盖不足 |

### 1.2 关键问题清单

#### A. 架构债务 (Architectural Debt)

1. **Convert Runner 过于庞大**: `cli/runner/convert.py:_process_arxiv_paper_ir()` 长达 340+ 行，混杂了 15+ 个职责：
   - 网络获取 (HTML, API metadata, TeX source)
   - 图像处理
   - IR 构建
   - 机构信息合并（API + HTML + TeX 三层）
   - Transform pipeline 编排
   - Markdown 分片发射（main/refs/appendix）
   - Summary / sections_tree 构建
   - paper.yml 持久化
   - JSON structured export

2. **Legacy Pipeline 僵尸代码**: `ingestion/html.py`, `ingestion/latex.py` 中的 legacy 流程仍被 `--legacy` 路由使用，但 IR 已实现功能对等。维护两套 formatter、两套 metadata 构建、两套 structured export。

3. **Image Map 类型分裂**:
   - `HTMLBuilder`: `image_map: dict[int, Path]` (index-based) + `image_stem_map: dict[str, Path]`
   - `LaTeXBuilder`: `image_map: dict[str, Path]` (path/stem-based)
   - 两个 builder 的 `_resolve_image_src` 逻辑完全不同

#### B. 性能瓶颈 (Performance Bottlenecks)

1. **BeautifulSoup 重复解析**: `HTMLBuilder._html_to_blocks()` 中对 container 元素递归时，使用 `"".join(str(c) for c in tag.children)` 重新序列化后再 `BeautifulSoup(...)` 解析，造成 O(n^2) 的 DOM 操作。
   - 位置: `src/arxiv2md_beta/ir/builders/html.py:191-193`
   - 影响: 每篇论文触发数百次冗余 soup 构造

2. **Markdown 指纹计算过重**: `JsonEmitter._content_fingerprint()` 调用完整 `MarkdownEmitter` 遍历整个文档生成 SHA-256，在 `--structured-output=all` 时增加 30-50% 额外耗时。
   - 位置: `src/arxiv2md_beta/ir/emitters/json_emitter.py:140-155`

3. **Transform Pipeline 串行执行**: `PassPipeline.run()` 顺序执行各 pass，无并行化。`NumberingPass` + `FigureReorderPass` + `SectionFilterPass` + `AnchorPass` 之间无数据依赖，可并行。

4. **Footnote 处理阻塞**: `HTMLBuilder._html_to_blocks()` 中 footnote 队列在 block 间顺序插入，使用 `while + pop(0)` 导致 O(n^2) 列表操作。
   - 位置: `src/arxiv2md_beta/ir/builders/html.py:168-179`

#### C. 代码缺陷 (Bugs & Anti-patterns)

1. **LaTeXBuilder section stack 逻辑错误**:
   ```python
   while stack and stack[-1][0] >= current_level if current_level is not None else True:
   ```
   条件优先级导致 `else True` 绑定到 `current_level is not None` 而非整个 `while` 条件。
   - 位置: `src/arxiv2md_beta/ir/builders/latex.py:263`

2. **LaTeXBuilder footnote/citation 丢失**: `_inline_from_pandoc` 中 `Note` 类型直接返回 `None`，`Cite` 类型仅返回 inner text，学术 paper 中大量脚注和引用被静默丢弃。
   - 位置: `src/arxiv2md_beta/ir/builders/latex.py:603-604`

3. **多处 `except Exception` 过于宽泛**:
   - `convert.py:204-205`, `220-222`, `411-412`, `430-431`
   - 掩盖真正的错误，调试困难

4. **类型安全缺口**:
   - `MarkdownEmitter` 使用 `getattr(block, "type")` 而非类型收窄（Type narrowing）
   - `_split_ir_sections` 参数 `sections: list` 缺失泛型参数
   - `SectionFilterPass` 中 `sec.title or ""` 在 `SectionIR.title` 已是 `str` 时冗余

#### D. 测试与可观测性

1. **集成测试仅限 HTML 模式**: `test_integration_real_papers.py` 仅测试 IR HTML pipeline，缺少 LaTeX pipeline 的真实论文测试。
2. **Benchmark 覆盖不足**: 仅 4 个 benchmark 点，缺少端到端管道性能基准。
3. **无性能回归测试**: 缺乏 CI 中的性能阈值检查。

---

## 2. 优化目标

### 2.1 核心目标

| 目标 | 指标 | 当前 | 目标 |
|------|------|------|------|
| 单论文转换耗时 | median (P50) | ~8-15s | <5s |
| 代码重复率 | 相似代码块 | ~25% | <10% |
| 单文件行数 | max lines/file | 590 (html.py) | <400 |
| 测试覆盖率 | line coverage | ~72% | >85% |
| Legacy 代码占比 | LOC in legacy path | ~40% | 0% (移除) |

### 2.2 设计原则

1. **IR First**: 所有输入统一走 IR pipeline，彻底移除 legacy formatter/serializer
2. **不可变数据流**: IR 节点在 Transform 后应返回新对象而非就地修改（便于并行和回溯）
3. **提前失败 (Fail Fast)**: 用具体异常替代 `except Exception`，在开发阶段暴露问题
4. **流式处理**: 大论文的 block 处理支持生成器模式，降低内存峰值

---

## Phase 1: 架构重构（高优先级）

### 1.1 统一 Image Resolution 层

**问题**: HTMLBuilder 和 LaTeXBuilder 各自维护不同的 image map 类型和解析逻辑。

**方案**: 新建 `src/arxiv2md_beta/ir/resolvers/images.py`

```python
class ImageResolver:
    """统一图像路径解析，支持多策略回退。"""

    def __init__(
        self,
        index_map: dict[int, Path] | None = None,      # HTML: figure index
        stem_map: dict[str, Path] | None = None,        # HTML: tex stem
        path_map: dict[str, Path] | None = None,        # LaTeX: original path
    ):
        self._index_map = index_map or {}
        self._stem_map = stem_map or {}
        self._path_map = path_map or {}
        self._used_indices: set[int] = set()
        self._cache: dict[str, str] = {}  # src -> resolved

    def resolve(self, src: str, *, figure_index: int | None = None) -> str:
        if src in self._cache:
            return self._cache[src]
        # 策略: exact path -> stem match -> index match -> original
        resolved = self._try_exact(src) or self._try_stem(src) or self._try_index(figure_index)
        result = str(resolved) if resolved else src
        self._cache[src] = result
        return result
```

**改动点**:
- `HTMLBuilder._resolve_image_src()` → 委托给 `ImageResolver`
- `LaTeXBuilder._resolve_image_src()` → 委托给 `ImageResolver`
- `HTMLBuilder.__init__` 签名改为 `image_resolver: ImageResolver | None = None`

### 1.2 提取 Ingestion Orchestrator

**问题**: `_process_arxiv_paper_ir()` 340 行，15+ 职责混杂。

**方案**: 提取为 `src/arxiv2md_beta/ingestion/orchestrator.py`

```python
class IngestionOrchestrator:
    """编排 IR pipeline 的完整数据流。"""

    def __init__(self, params: ConvertParams):
        self.params = params
        self._html: str | None = None
        self._parsed: ParsedHtml | None = None
        self._api_metadata: dict = {}
        self._tex_source: TexSourceInfo | None = None
        self._image_resolver: ImageResolver | None = None
        self._doc: DocumentIR | None = None

    async def run(self) -> IngestionResult:
        await self._fetch_html()
        await self._fetch_metadata()
        await self._fetch_tex_and_images()
        self._build_ir()
        self._enrich_metadata()
        self._run_transforms()
        return self._emit_output()
```

**子任务提取**:

| 子任务 | 当前位置 | 提取到 |
|--------|----------|--------|
| 机构信息合并 | convert.py:261-295 | `network/author_enrichment.py:_merge_affiliations()` |
| Asset 构建 | convert.py:228-252 | `ir/assets.py:_build_assets_from_maps()` |
| Summary 构建 | convert.py:342-369 | `output/summary.py:_build_summary()` |
| Sections tree | convert.py:371-376 | `output/summary.py:_build_sections_tree()` |

### 1.3 废弃 Legacy Pipeline

**步骤**:

1. **v0.10.0** (Deprecate): 
   - `--legacy` 标志保留但打印 `DeprecationWarning`
   - 在 CHANGELOG 中公告 legacy pipeline 将在 v1.0.0 移除

2. **v0.11.0** (Redirect):
   - `--legacy` 内部重定向到 IR pipeline，但使用 legacy formatter 风格
   - 移除 `ingestion/html.py`, `ingestion/latex.py` 中的 legacy 路径
   - 保留 `html/markdown.py`, `html/serializers/` 用于 local HTML ingestion

3. **v1.0.0** (Remove):
   - 删除 `output/formatter.py` 中的 legacy `format_paper()`
   - 删除 `output/structured_export.py` 中的 legacy `write_structured_bundle()`
   - 删除 `ir/_legacy_blocks.py`
   - 删除 `--legacy` CLI 标志
   - 统一使用 IR-native 输出

**影响分析**:
- `ir/_legacy_blocks.py` → 删除
- `output/formatter.py:format_paper()` → 仅保留 `_create_sections_tree`, `_format_token_count` 等 IR 也用到的工具函数
- `output/structured_export.py` → 删除，由 `JsonEmitter` 完全替代
- `html/serializers/` → 保留给 local HTML ingestion 使用

---

## Phase 2: 性能优化

### 2.1 BeautifulSoup 重复解析消除

**问题**: `HTMLBuilder._tag_to_blocks()` 中对 container 元素递归时反复创建新的 BeautifulSoup。

**优化方案**:

```python
# 优化前 (O(n^2) DOM 操作)
if tag_name in ("section", "article", "div", "span"):
    return self._html_to_blocks(
        "".join(str(c) for c in tag.children), section_id
    )

# 优化后: 直接遍历 children，无需重新 parse
if tag_name in ("section", "article", "div", "span"):
    blocks: list[BlockUnion] = []
    idx = base_idx
    for child in tag.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                blocks.append(ParagraphIR(...))
                idx += 1
        elif isinstance(child, Tag):
            result = self._tag_to_blocks(child, section_id, idx)
            # ... 同 _html_to_blocks 逻辑
    return blocks
```

**预期收益**: 大论文（100+ sections）转换耗时减少 20-30%。

### 2.2 Footnote 队列优化

**问题**: `pop(0)` 导致 O(n^2) 列表移位。

**优化方案**:

```python
# 优化前
self._pending_footnotes: list[BlockUnion] = []
# ... 使用 pop(0) ...

# 优化后: 使用 deque
from collections import deque
self._pending_footnotes: deque[BlockUnion] = deque()
# ... 使用 popleft() ...
```

### 2.3 Transform Pipeline 并行化

**分析**: `NumberingPass`, `FigureReorderPass`, `SectionFilterPass`, `AnchorPass` 之间无数据依赖（除 FigureReorder 依赖 Numbering 生成的 figure_id）。

**方案**:

```python
class PassPipeline:
    def run(self, doc: DocumentIR) -> DocumentIR:
        # Phase 1: 前置 passes（串行，有依赖关系）
        doc = self._run_pre_passes(doc)
        # Phase 2: 独立 passes（可并行）
        doc = self._run_parallel_passes(doc)
        return doc
```

实际收益有限（Python GIL），但可将 SectionFilterPass（可能大幅缩减文档）提前，减少后续 passes 的工作量。

### 2.4 SHA-256 Fingerprint 增量计算

**问题**: `_content_fingerprint()` 遍历整个文档生成 markdown 再 hash。

**优化方案**: 在 IR 构建阶段增量计算 hash，利用 `hashlib.sha256` 的 update 能力：

```python
class DocumentIR:
    def content_fingerprint(self) -> str:
        h = hashlib.sha256()
        for blk in self.abstract:
            h.update(blk.fingerprint_bytes())
        # ... 递归 sections ...
        return h.hexdigest()
```

各 IR 节点实现 `fingerprint_bytes()` 返回其稳定内容表示。

---

## Phase 3: 功能增强

### 3.1 LaTeXBuilder Footnote & Citation 支持

**问题**: Pandoc `Note` 类型被丢弃，`Cite` 仅返回 inner text。

**实现**:

```python
elif t == "Note":
    # Footnote: Pandoc Note is [Blocks]
    note_blocks = c if isinstance(c, list) else []
    ir_blocks = self._blocks_from_pandoc(note_blocks, section_id)
    # 返回带标记的 SuperscriptIR，同时注册 footnote 内容
    footnote_id = f"fn-{section_id}-{order}"
    self._pending_footnotes.append((footnote_id, ir_blocks))
    return SuperscriptIR(inlines=[LinkIR(
        kind="internal", target_id=footnote_id,
        inlines=[TextIR(text="*")]
    )])

elif t == "Cite":
    c_list = c if isinstance(c, list) else [[], []]
    citations = c_list[0] if len(c_list) > 0 else []
    inner = self._inlines_from_pandoc(c_list[1] if len(c_list) > 1 else [])
    # 生成 [N](#ref-N) 格式的引用链接
    if citations and isinstance(citations, list):
        citation_ids = [c.get("citationId", "") for c in citations]
        # ... 构建 LinkIR ...
    return inner
```

### 3.2 Table colspan/rowspan 支持

**当前**: `TableIR` 的 `headers` 和 `rows` 是 `list[list[list[InlineUnion]]]`，无单元格合并信息。

**增强**:

```python
class TableCellIR(BaseModel):
    """Table cell with merge info."""
    inlines: list[InlineUnion]
    colspan: int = 1
    rowspan: int = 1

class TableIR(BlockIR):
    type: Literal["table"] = "table"
    headers: list[list[TableCellIR]]
    rows: list[list[list[TableCellIR]]]
    # ...
```

**HTMLBuilder 改动**: 从 `<td colspan="3">` / `<td rowspan="2">` 提取属性。

**MarkdownEmitter 改动**: 对 colspan 输出空单元格占位，对 rowspan 在后续行补空单元格。

### 3.3 新增 Transform Passes

| Pass | 用途 | 优先级 |
|------|------|--------|
| `DeduplicateReferencePass` | 移除重复的引用条目 | 中 |
| `AbstractEnrichmentPass` | 从 TeX source 提取更完整的 abstract | 低 |
| `CodeLanguageDetectionPass` | 对无语言标记的 code block 推断语言 | 低 |
| `MathNormalizationPass` | 标准化 LaTeX 公式（移除多余空格、统一命令） | 中 |

### 3.4 Batch 模式流式输出

**当前**: `run_batch_flow()` 等待所有任务完成后统一输出。

**增强**: 支持 `--stream-output` 标志，每完成一个论文立即写入结果，支持长时间运行的 batch 任务中断恢复。

---

## Phase 4: 质量与可维护性

### 4.1 异常体系细化

**当前**: `exceptions.py` 仅有基础异常。

**增强**:

```python
class Arxiv2mdError(Exception): ...

class NetworkError(Arxiv2mdError):
    """HTTP / API 相关错误。"""
    def __init__(self, message: str, *, url: str | None = None, status: int | None = None):
        super().__init__(message)
        self.url = url
        self.status = status

class ParseError(Arxiv2mdError):
    """HTML/LaTeX 解析错误。"""
    def __init__(self, message: str, *, source_snippet: str | None = None):
        super().__init__(message)
        self.source_snippet = source_snippet

class BuilderError(Arxiv2mdError): ...
class TransformError(Arxiv2mdError): ...
class EmitterError(Arxiv2mdError): ...
```

**替换策略**: 逐文件替换 `except Exception` 为具体异常类型，配合 `from e` 保留调用链。

### 4.2 类型安全增强

1. **IR 访问器模式**: 替代 `getattr` 链式调用

```python
# 当前
if hasattr(blk, "type") and blk.type == "heading":
    text = " ".join(il.text for il in getattr(blk, "inlines", []) if hasattr(il, "text"))

# 增强: 使用 match (Python 3.10+)
match blk:
    case HeadingIR(inlines=inlines):
        text = " ".join(il.text for il in inlines if isinstance(il, TextIR))
```

2. **SectionIR 泛型参数补全**:

```python
def _split_ir_sections(
    sections: list[SectionIR],
    reference_titles: list[str],
) -> tuple[list[SectionIR], list[SectionIR], list[SectionIR]]:
```

### 4.3 测试增强

| 测试类型 | 当前 | 目标 | 行动 |
|----------|------|------|------|
| LaTeX 集成测试 | 0 | 4+ | 添加 `TestLaTeXRealPaper` 类，测试 1-2 篇真实论文 |
| 性能回归测试 | 0 | 1 | 添加 `test_performance_regression`，检查 P50 < 5s |
| HTMLBuilder 边界 | 部分 | 完整 | 测试空 table、无 caption figure、嵌套 list 等 |
| Property-based | 0 | 1 | 使用 `hypothesis` 生成随机 IR 树，验证 roundtrip |

### 4.4 文档完善

1. **架构图**: 添加 `docs/architecture.md`，用 Mermaid 描述数据流
2. **IR Schema 文档**: 每个 IR 类型的字段、约束、示例
3. **开发者指南**: 如何添加新的 Transform Pass 和 Emitter
4. **API 文档**: 使用 `mkdocs` + `mkdocstrings` 自动生成

---

## 7. 实施路线图

### 版本规划

```
v0.10.0 (已完成)
├── [DONE] [P0] BeautifulSoup 重复解析消除
├── [DONE] [P0] Footnote deque 优化
├── [DONE] [P1] 统一 ImageResolver
├── [DONE] [P1] 提取 IngestionOrchestrator
├── [DONE] [P2] Transform pipeline 优化（SectionFilterPass 前置）
├── [DONE] [P3] LaTeXBuilder footnote/citation 支持
├── [DONE] [P4] 异常体系细化（前半部分）
└── [DONE] [P4] LaTeX 集成测试

v0.11.0 (3-4 周)
├── [P1] Legacy Pipeline 废弃 (--legacy 重定向)
├── [P3] Table colspan/rowspan 支持
├── [P4] 类型安全增强（match 表达式）
└── [P4] 性能回归测试

v1.0.0 (3-4 周)
├── [P1] 移除 Legacy Pipeline 代码
├── [P2] SHA-256 增量计算
├── [P3] 新增 Transform Passes（MathNormalization, DeduplicateReference）
├── [P3] Batch 流式输出
├── [P4] 文档完善（mkdocs）
└── [P4] 测试覆盖率 > 85%
```

### 优先级矩阵

| 任务 | 影响 |  effort | 优先级 |
|------|------|---------|--------|
| 提取 IngestionOrchestrator | 高 | 中 | P0 |
| BeautifulSoup 优化 | 高 | 低 | P0 |
| Legacy 废弃 | 高 | 中 | P1 |
| 异常体系细化 | 中 | 中 | P1 |
| LaTeX footnote | 中 | 中 | P2 |
| Table colspan | 中 | 高 | P2 |
| 增量 SHA-256 | 低 | 中 | P3 |
| Batch 流式 | 低 | 高 | P3 |

---

## 8. 风险与回滚策略

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Legacy 移除破坏用户工作流 | 中 | 高 | v0.10/0.11 充分 deprecate，保留 `--legacy` 至少 2 个 minor 版本 |
| IngestionOrchestrator 引入 bug | 中 | 高 | 保持原有测试全部通过，添加端到端回归测试 |
| BeautifulSoup 优化改变行为 | 低 | 高 | 在 5+ 真实论文上 diff 验证输出一致性 |
| LaTeX footnote 实现复杂 | 中 | 中 | 分阶段实现：先收集 footnote，再处理引用链接 |

### 回滚检查点

每个 minor 版本发布前必须满足：
1. `pytest tests/ -x` 全部通过
2. `pytest tests/test_integration_real_papers.py -v` 全部通过（需代理）
3. Benchmark 无显著退化（±10% 以内）
4. 在 3 篇不同领域论文上 diff 验证 markdown 输出一致性

---

## 附录: 文件改动索引

### Phase 1 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/arxiv2md_beta/ir/resolvers/images.py` | 新增 | 统一图像解析 |
| `src/arxiv2md_beta/ingestion/orchestrator.py` | 新增 | 编排器提取 |
| `src/arxiv2md_beta/network/author_enrichment.py` | 修改 | 添加 `_merge_affiliations()` |
| `src/arxiv2md_beta/output/summary.py` | 新增 | Summary/sections_tree 构建 |
| `src/arxiv2md_beta/cli/runner/convert.py` | 修改 | 简化 `_process_arxiv_paper_ir` |
| `src/arxiv2md_beta/ir/builders/html.py` | 修改 | 使用 ImageResolver |
| `src/arxiv2md_beta/ir/builders/latex.py` | 修改 | 使用 ImageResolver |

### Phase 2 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/arxiv2md_beta/ir/builders/html.py` | 修改 | 消除 BS 重复解析，deque footnote |
| `src/arxiv2md_beta/ir/emitters/json_emitter.py` | 修改 | 增量 fingerprint |
| `src/arxiv2md_beta/ir/transforms/pipeline.py` | 修改 | 并行化优化 |

### Phase 3 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/arxiv2md_beta/ir/builders/latex.py` | 修改 | footnote/cite 支持 |
| `src/arxiv2md_beta/ir/blocks.py` | 修改 | TableCellIR 新增 |
| `src/arxiv2md_beta/ir/builders/html.py` | 修改 | colspan/rowspan 提取 |
| `src/arxiv2md_beta/ir/emitters/markdown.py` | 修改 | colspan/rowspan 渲染 |
| `src/arxiv2md_beta/ir/transforms/math_normalize.py` | 新增 | MathNormalizationPass |

### Phase 4 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/arxiv2md_beta/exceptions.py` | 修改 | 细化异常体系 |
| 多处 | 修改 | `except Exception` -> 具体异常 |
| `tests/test_integration_real_papers.py` | 修改 | 添加 LaTeX 测试 |
| `tests/benchmarks/` | 新增 | 性能回归测试 |
| `docs/` | 新增 | mkdocs 文档 |

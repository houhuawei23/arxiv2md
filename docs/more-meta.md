
根据 [arXiv API User's Manual](https://info.arxiv.org/help/api/user-manual.html) 和当前 [`arxiv_api.py`](academic/arxiv2md-beta/src/arxiv2md_beta/network/arxiv_api.py) 的实现，可以这样理解：

## Atom 里「单篇论文」能拿到什么（官方说明）

对每条 `<entry>`，规范里写明的字段大致包括：

| 信息 | 说明 |
|------|------|
| `title` | 标题 |
| `id` | 摘要页 URL（可推出 arXiv ID） |
| `published` | **v1** 首次提交并处理的时间 |
| `updated` | **当前检索到的版本** 提交并处理的时间（多版本时与 `published` 不同） |
| `summary` | 摘要 |
| `author` / `name` | 作者 |
| `arxiv:affiliation` | 作者单位（若有） |
| `category`（可多枚） | `term` + `scheme`（arXiv / ACM / MSC 等） |
| `arxiv:primary_category` | 主分类 |
| `link` | `alternate`→HTML 摘要页；`related`+`pdf`→PDF；`related`+`doi`→DOI（若有） |
| `arxiv:comment` | 作者备注（页数、图数等） |
| `arxiv:journal_ref` | 期刊/会议引用信息（若作者填了） |
| `arxiv:doi` | DOI（若有） |

也就是说：**在「单条 `id_list` 查询」下，元数据主要就是这些**，没有引用数、下载量、全文正文等。

## 你们代码里已经覆盖了什么

当前解析里已经用了：**title、published、updated、summary、authors（含 affiliation）、categories、primary_category、comment、journal_ref、doi、abstract/pdf 链接、自生成的 bibtex/citation**；有 DOI 时还会走 **Crossref** 做补充（期刊、页码等）。

因此：**在「同一套 API、同一类 Atom」的前提下，并没有一大块「文档里有、你们完全没解析」的字段**；若要和官方完全一致，顶多再细化例如：每个 `category` 的 **`scheme`**、区分 `published` 与 `updated` 的语义（首版 vs 当前版日期）等。

## 若还要「更多信息」，通常不是这条 API 能给的

- **被引次数、Altmetric、机构主页等**：要用 OpenAlex、Semantic Scholar、Crossref（部分）、期刊方 API 等，**不是** arXiv Query API 的职责。
- **大批量、长期同步**：官方更推荐 **OAI-PMH** 等（见手册里与 [bulk / OAI](https://info.arxiv.org/help/oa/index.html) 的说明），而不是高频打 `export.arxiv.org/api/query`。
- **用法与限制**：请遵守 [Terms of Use](https://info.arxiv.org/help/api/tou.html)，手册还建议对重复查询做缓存、控制频率（例如说明里提到连续调用之间 **约 3 秒** 间隔等实践）。

**结论**：通过 **arXiv API（Atom）** 能拿到的论文元信息，基本上就是上面表格那些；你们实现已覆盖主要字段。若要「更多」，需要 **换数据源**（Crossref 已部分接入）或 **其它学术 API / 开放数据库**，而不是在同一 Atom entry 里再挖字段。若你希望把 **`category` 的 scheme**、**v1 提交日 vs 当前版更新日** 等写进 `paper.yml`，可以在现有 `fetch_arxiv_metadata` 解析层做小幅扩展即可。

## 2026-11 更新：paper.yml Schema v2 与 paper.md front matter

`updated` 的语义（首版 vs 当前版日期）已落地：`publication.updated_date`（与 `date_published` 相同则省略）。同时 `paper.yml` 升级到 **schema v2**（`paper.schema: 2`）：

- 分类独立成 `paper.classification`（`categories` / `primary_category`），不再混入 `content.keywords` 或 `relations`；
- 删除冗余的 `paper.id`、截断的 `content.citation`、内嵌 `paper.bibtex`（bibtex 改写到同目录 `paper.bib`，用户手改的条目在更新时仍优先保留）；
- 新增 `identifiers.doi`（缺失时按 `10.48550/arXiv.{id}` 派生）与可选 `content.license`。

存量 v1 文件用 `arxiv2md-beta paper-yml --migrate PATH` 离线升级（幂等，保留 .bak 备份）；`convert` 重跑与 `paper-yml --update` 会自动随写盘迁移。

另外 `paper.md` 默认以**扁平 YAML front matter** 开头（title/arxiv/doi/authors/publication/meta_status/workflow/urls/categories/sections，数据与 paper.yml 同源派生），可用 `--no-front-matter` 或 `output.md_front_matter: false` 关闭；CLI stdout 的摘要保持人类可读格式不变。

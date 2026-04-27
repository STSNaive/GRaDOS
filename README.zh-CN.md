# GRaDOS

[English](./README.md) | [简体中文](./README.zh-CN.md)

<div align="center">
  <pre style="display:inline-block; margin:0; font-family:'Bitstream Vera Sans Mono', 'SF Mono', Consolas, monospace; font-size:15px; line-height:1.02; font-weight:bold; white-space:pre; text-align:left;">&nbsp;&nbsp;.oooooo.&nbsp;&nbsp;&nbsp;&nbsp;ooooooooo.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;oooooooooo.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;.oooooo.&nbsp;&nbsp;&nbsp;&nbsp;.oooooo..o
&nbsp;d8P'&nbsp;&nbsp;`Y8b&nbsp;&nbsp;&nbsp;`888&nbsp;&nbsp;&nbsp;`Y88.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;`888'&nbsp;&nbsp;&nbsp;`Y8b&nbsp;&nbsp;&nbsp;d8P'&nbsp;&nbsp;`Y8b&nbsp;&nbsp;d8P'&nbsp;&nbsp;&nbsp;&nbsp;`Y8
888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;.d88'&nbsp;&nbsp;.oooo.&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;Y88bo.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888ooo88P'&nbsp;&nbsp;`P&nbsp;&nbsp;)88b&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;`"Y8888o.&nbsp;
888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;ooooo&nbsp;&nbsp;888`88b.&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;.oP"888&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;`"Y88b
`88.&nbsp;&nbsp;&nbsp;&nbsp;.88'&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;`88b.&nbsp;&nbsp;d8(&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;888&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;d88'&nbsp;`88b&nbsp;&nbsp;&nbsp;&nbsp;d88'&nbsp;oo&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;.d8P
&nbsp;`Y8bood8P'&nbsp;&nbsp;&nbsp;o888o&nbsp;&nbsp;o888o&nbsp;`Y888""8o&nbsp;o888bood8P'&nbsp;&nbsp;&nbsp;&nbsp;`Y8bood8P'&nbsp;&nbsp;8""88888P'&nbsp;</pre>
</div>

<p align="center">
  <strong style="font-size:1.75rem;">Graduate Research and Document Operating System</strong>
</p>

面向学术论文工作流的“丰富学习级” MCP 服务器。为了科学。

GRaDOS 为 Claude、Codex、Cursor 等 AI agent 提供单一 stdio MCP 服务，用来检索学术数据库、跨付费墙抓取论文、把 PDF 解析为 canonical Markdown，并在写作时回读已保存论文做引用核验。

## 架构概览 🧭

GRaDOS 设计给 agent 科研工作流直接调用：

1. 先用 `search_saved_papers`、`get_saved_paper_structure` 或 `grados://papers/{safe_doi}` 检查本地论文库
2. 按配置好的优先级检索远程学术数据库
3. 按 `api -> browser -> oa -> scihub` 瀑布抓取全文
4. 按 `Docling -> Marker -> PyMuPDF` 瀑布解析 PDF
5. 把原始 PDF 保存到 `downloads/`，把 canonical Markdown 保存到 `papers/`，把语义检索数据写入 ChromaDB
6. 在正式引用前，先看低 token 结构卡片，再按需深读已保存论文

### MCP 工具 🔧

| 服务 | 工具 | 说明 |
| --- | --- | --- |
| GRaDOS | `search_academic_papers` | 检索远程学术数据库中的论文元数据，支持 DOI 去重与 continuation token 续查。适合先筛选候选 DOI，再进入全文提取。 |
| GRaDOS | `search_saved_papers` | 检索本地已保存论文库，支持语义检索、metadata 过滤与可选词法 reranking。返回的 snippet 只是筛选线索，不是最终引用证据。 |
| GRaDOS | `extract_paper_full_text` | 按 DOI 抓取、解析并保存单篇论文的 canonical 全文。返回的是包含 URI、文件路径、章节和 warning 的紧凑保存回执，而不是全文正文。 |
| GRaDOS | `read_saved_paper` | 从单篇已保存论文中读取段落窗口，用于 canonical 深读与引用核验。可通过 DOI、safe DOI 或 `grados://papers/...` URI 定位论文。 |
| GRaDOS | `get_saved_paper_structure` | 返回单篇论文的低 token 结构卡片，包含预览、章节标题与资产摘要。适合深读前筛选，不应替代最终引用依据。 |
| GRaDOS | `import_local_pdf_library` | 把本地 PDF 文件或目录导入 canonical 论文库与检索索引。返回导入摘要以及前 25 条条目结果。 |
| GRaDOS | `parse_pdf_file` | 把本地 PDF 解析为 markdown。未提供 DOI 时返回截断预览；提供 DOI 时会保存进 canonical 论文库并返回保存回执。 |
| GRaDOS | `save_paper_to_zotero` | 通过 Zotero Web API 把单篇论文保存到当前配置的 Zotero 库，通常用于最终答案里实际引用到的论文。 |
| GRaDOS | `save_research_artifact` | 把 search snapshot、extraction receipt、evidence grid 等可复用中间产物持久化到本地 SQLite 状态库。 |
| GRaDOS | `query_research_artifacts` | 按 id、kind、project id 或关键词查询已保存的 research artifact；`detail=true` 会返回完整内容。 |
| GRaDOS | `manage_failure_cases` | 记录、查询并总结 fetch、parse、search 或 citation 失败案例，也能给出保守的重试建议。 |
| GRaDOS | `get_citation_graph` | 返回本地论文库中的轻量引用关系，包括引用邻居、共同参考文献和反向 citing-paper 查询。 |
| GRaDOS | `get_papers_full_context` | 为少量论文返回结构化全文上下文，可先拿 token 估计，也可直接进入 CAG 风格的深读模式。 |
| GRaDOS | `build_evidence_grid` | 围绕主题或子问题，从本地论文库构建写作前的证据网格。 |
| GRaDOS | `compare_papers` | 跨多篇已保存论文抽取并行对比材料，聚焦 methods、results 或 full text。 |
| GRaDOS | `audit_draft_support` | 审计草稿中的 claim 是否被本地论文库支持，返回 `supported`、`weak`、`unsupported` 或 `misattributed` 状态。当前可解析的拉丁字母或中文 author-year 引文都能较可靠判定 `misattributed`；numeric citation 在建立 bibliography 映射前仍只做 support 检查。 |

### MCP 资源 📚

| 资源 | 说明 |
| --- | --- |
| `grados://papers/index` | 所有已保存论文的低 token 索引。 |
| `grados://papers/{safe_doi}` | 单篇已保存论文的 canonical 概览卡片。 |

### 本地论文库 🗂️

提取或导入之后，GRaDOS 会把论文保存在一套可见的目录结构里：

| 目录 | 内容 | 用途 |
| --- | --- | --- |
| `config.json` | 运行时配置 | 整个安装共用的单一配置文件 |
| `papers/` | 带 YAML front-matter 的 canonical Markdown 论文 | 深读、结构卡片与检索 |
| `downloads/` | 原始 `.pdf` 文件 | 抓取或导入后的归档副本 |
| `database/chroma/` | ChromaDB collections | 内置语义检索存储 |
| `browser/` | 托管 Chromium、profile、extensions | 难处理 publisher 页面的浏览器回退 |
| `models/` | embedding 与 OCR 模型缓存 | setup 预热的运行时资产 |

### 仓库地图 🗺️

- `README.md` / `README.zh-CN.md`：主要安装与使用说明
- `.mcp.json`：仓库内 MCP 配置示例
- `.claude-plugin/`：Claude Code 的原生 plugin manifest
- `.agents/plugins/marketplace.json`：repo-hosted 的 Codex marketplace manifest
- `plugin.mcp.json`：Claude Code 插件使用的根目录插件专用 MCP 配置
- `plugins/grados/.codex-plugin/`：给 Codex marketplace 用的自包含 plugin bundle
- `plugins/grados/plugin.mcp.json`：复制进 Codex plugin bundle 的插件专用 MCP 配置
- `skills/grados/SKILL.md`：构建在 MCP 工具之上的结构化科研工作流
- `grados-python-implementation-plan.md`：实施计划与完成度台账
- `TODO.md`：从实施计划提炼出的简明执行快照

## 安装 🚀

### 方式 A：`uv tool install`（推荐）

```bash
uv tool install grados
grados setup
grados client install all
```

这会创建 `~/GRaDOS/config.json`，准备可见目录结构，安装托管浏览器资产，并预热默认的 Harrier embedding 运行时。由于当前 canonical 解析链已经改为 Docling 优先，默认安装现在也会自带 `docling`。
推荐用 `grados auth set <provider>` 把 API Key 写入系统 keychain。若你临时把明文 key 填进 `config.json`，GRaDOS 会在下次运行时自动导入 keychain，并在迁移成功后清空原文件中的明文值。

### 方式 B：extras、零安装或 pip

```bash
# 默认安装（已包含 Docling）
uv tool install grados

# 安装额外的重型解析器
uv tool install "grados[marker]"
uv tool install "grados[full]"

# 零安装运行
uvx grados version

# 传统 Python 安装
pip install grados
```

当前包的 extras：

- `grados`：核心 MCP 服务、CLI、ChromaDB 存储、Docling-first 默认解析器、PyMuPDF fallback、浏览器自动化，以及内置 Zotero 保存能力
- `grados[marker]`：在核心上加入 Marker PDF 解析器
- `grados[docling]`：为了兼容旧安装说明而保留的空 alias
- `grados[full]`：在核心上额外加入 Marker 解析器

### 方式 C：从源码运行

```bash
git clone https://github.com/STSNaive/GRaDOS.git
cd GRaDOS
uv sync --all-extras
uv run grados setup
uv run grados client install all
uv run grados status
```

### 快速开始 ⚡

1. 用 `uv tool install grados` 安装 GRaDOS（现在默认就会安装 Docling）
2. 运行 `grados setup`
3. 运行 `grados client install all`，一步接入 Claude Code 和 Codex
4. 运行 `grados auth set elsevier`（以及你需要的其他 provider）
5. 运行 `grados status` 检查依赖、浏览器资产、keychain 健康状态和 API Key 来源
6. 如果你已经有 PDF 库，运行 `grados import-pdfs --from /path/to/papers --recursive`
7. 如果你是从旧的 MiniLM 语义索引升级，请先执行一次 `grados reindex`

### 配置客户端 🔌

推荐方式：

```bash
grados client install all
```

当前 `all` 会同时安装到 Claude Code 和 Codex，并自动：

- 通过各自客户端的官方 CLI 注册 `grados` MCP 服务
- 把内置的 `grados` skill 复制到用户 skills 目录

也可以单独安装到某一个客户端：

```bash
grados client install claude
grados client install codex
grados client list
grados client doctor
```

### 手工配置 MCP（fallback）

Claude Code / Claude Desktop：

```json
{
  "mcpServers": {
    "grados": {
      "command": "uvx",
      "args": ["grados"]
    }
  }
}
```

Codex：

```toml
[mcp_servers.grados]
command = "uvx"
args = ["grados"]
```

`uvx` 适合零安装启动 MCP。长期本地使用仍建议 `uv tool install grados` 加 `grados` 可执行命令，而且现在会默认带上 Docling。如果你想指定自定义数据根目录，请在 MCP 客户端环境变量里设置 `GRADOS_HOME`。

### 原生 Plugin 安装 🧩

GRaDOS 现在同时支持 Codex 和 Claude Code 的原生 plugin。

Claude Code：

```text
/plugin marketplace add STSNaive/GRaDOS
/plugin install grados@grados-plugins
/reload-plugins
```

Codex：

```text
codex plugin marketplace add STSNaive/GRaDOS
codex
/plugins
```

然后选择 `GRaDOS Plugins` marketplace，安装 `GRaDOS` 插件，再新开一个线程。你可以直接写 `@grados`，也可以直接描述科研任务。

### 配套 Skill 🤖

GRaDOS 仓库仍然自带配套 skill，位置在 `skills/grados/`。现在更推荐优先使用上面的 `grados client install ...` 本地安装路径；plugin 安装适合你明确想走原生 plugin 包装时使用。

- `skills/grados/SKILL.md` 对应当前 `search -> structure -> deep read -> cite -> verify` 工作流
- `skills/grados/references/tools.md` 记录当前 16 个工具和 2 个资源
- `skills/grados/agents/openai.yaml` 声明了面向 OpenAI / Codex 的 `grados` MCP 依赖

Codex 和 Claude Code 使用的是同一种 skill 目录形状，也就是 `<skills-root>/grados/SKILL.md`，并共享同一套目录下的辅助文件。区别只在 skills 根目录：

- Codex 个人 skills：`~/.agents/skills`
- Claude Code 个人 skills：`~/.claude/skills`
- Claude Code 项目级 skills：`.claude/skills`

安装时请复制**整个** `skills/grados/` 目录到对应的 skills 根目录，而不是只复制 `SKILL.md`：

```bash
mkdir -p "<skills-root>"
cp -R skills/grados "<skills-root>/"
```

- Codex：把 `<skills-root>` 设为 `~/.agents/skills`
- Claude Code 个人 skills：把 `<skills-root>` 设为 `~/.claude/skills`
- Claude Code 项目级 skills：把 `<skills-root>` 设为 `.claude/skills`

这个 fallback 路径默认假设你的客户端已经注册好了 `grados` MCP 服务。仓库里的 `.mcp.json` 提供了最小 repo-local 示例；复制完 skill 之后，重新加载客户端即可让它发现新的 skill 文件。

## 配置 ⚙️

注释齐全的参考配置以 [grados-config.example.json](./grados-config.example.json) 为准；修改后会在下一次 CLI 运行或 MCP 服务重启时生效。

### 超时与重试

- `search`: `connect_timeout`, `read_timeout`
- `extract`: `fetch_connect_timeout`, `fetch_read_timeout`
- `extract.headless_browser`: `deadline_seconds`, `networkidle_timeout`, `poll_min_seconds`, `poll_max_seconds`
- `retry_policy`: `max_attempts`, `max_wait`, `respect_retry_after`

### 命令 🧰

| 命令 | 作用 |
| --- | --- |
| `grados` | 启动 MCP stdio 服务器 |
| `grados setup` | 创建目录、写入 `config.json`、安装浏览器资产并预热模型 |
| `grados client install claude` | 把 GRaDOS 注册到 Claude Code，并把内置 skills 安装到 `~/.claude/skills` |
| `grados client install codex` | 把 GRaDOS 注册到 Codex，并把内置 skills 安装到 `~/.agents/skills` |
| `grados client install all` | 同时安装到 Claude Code 和 Codex |
| `grados client list` | 查看当前哪些受支持客户端已经安装了 GRaDOS |
| `grados client doctor` | 对受支持客户端做轻量健康检查 |
| `grados client remove claude|codex|all` | 从一个或多个客户端移除 GRaDOS 的 MCP 注册和内置 skills |
| `grados auth set/status/migrate/clear` | 在系统 keychain 中管理各 provider 的 API Key |
| `grados import-pdfs --from /path/to/papers --recursive` | 把已有 PDF 文件夹导入 canonical 论文库 |
| `grados status` | 查看配置、依赖、运行时资产和 API Key 状态 |
| `grados paths` | 查看当前解析到的 GRaDOS 文件布局 |
| `grados update-db` | 在当前 indexing 配置不变时，增量刷新 `papers/` 对应的 ChromaDB 索引 |
| `grados reindex` | 在 embedding 模型或分块配置变化后，从头重建语义索引 |
| `grados version` | 查看包版本信息 |

如果你修改了 `config.json` 里的 `indexing.model_id`、`indexing.max_length` 或 section-aware chunking 参数，请使用 `grados reindex`，不要只跑 `grados update-db`。

如果你只改了 `indexing.batch_size`，它只是运行时调优参数，不需要重建索引。

### 索引默认值 🧠

- 默认模型：`microsoft/harrier-oss-v1-270m`
- 更重的可选模型：`microsoft/harrier-oss-v1-0.6b`
- 默认 `indexing.max_length`：`4096`
- 默认 `indexing.batch_size`：`0`（`auto`，在 CPU/MPS 上更保守，在 CUDA 上更放宽）
- 遇到“整段超长”的旧 markdown 时，会先按句子或分句二次切块，再送进 embedding，避免 `grados reindex` 把巨型单块直接喂给 `SentenceTransformer.encode()`

GRaDOS 不假设本地 macOS / CPU 环境一定有 FlashAttention。即使运行时提示能走 SDPA，也不等于当前设备一定有 CUDA fused FlashAttention 内核；更稳妥的默认策略仍然是更小的 chunk、更短的 indexing length 和更保守的 batch。

### 文件布局 🗄️

默认情况下，GRaDOS 会把所有内容放在一个可见目录里：

```text
~/GRaDOS/
├── config.json
├── papers/
├── downloads/
├── browser/
│   ├── chromium/
│   ├── profile/
│   └── extensions/
├── models/
├── database/
│   └── chroma/
├── logs/
└── cache/
```

数据根目录优先级：

1. `GRADOS_HOME`
2. `~/GRaDOS`

### API Keys 🔑

| Key | 来源 | 必需 |
| --- | --- | --- |
| `ELSEVIER_API_KEY` | Elsevier Developer Portal | 否 |
| `PUBMED_API_KEY` | NCBI E-utilities API key | 否 |
| `WOS_API_KEY` | Clarivate Developer Portal | 否 |
| `SPRINGER_meta_API_KEY` | Springer Nature Metadata API | 否 |
| `SPRINGER_OA_API_KEY` | Springer Nature Open Access API | 否 |
| `LLAMAPARSE_API_KEY` | LlamaCloud | 否 |
| `ZOTERO_API_KEY` | Zotero Settings -> Keys | 否 |

Crossref 不需要 API Key。PubMed 也可以在无 key 情况下运行，但 `PUBMED_API_KEY` 可作为 E-utilities 节流上限的可选增强。GRaDOS 会使用你已配置的服务，未配置的会自动跳过；即使没有第三方 Key，本地论文工作流也能使用，远程检索也仍可依赖免费来源运行。

推荐路径是 `grados auth set <provider>`，它会把 secret 存进系统 keychain。若你临时把明文 key 填进 `~/GRaDOS/config.json`，GRaDOS 会在下一次运行时导入 keychain，并在迁移成功后清空文件中的明文字段。

### 运行顺序 🌊

检索优先级：

```json
{
  "search": {
    "order": ["Elsevier", "Springer", "WebOfScience", "Crossref", "PubMed"]
  }
}
```

全文抓取优先级：

```json
{
  "extract": {
    "fetch_strategy": {
      "order": ["api", "browser", "oa", "scihub"]
    }
  }
}
```

旧的抓取策略别名 `TDM`、`OA`、`SciHub`、`Headless` 仍然兼容，便于现有配置逐步迁移。

`browser` 是机构权限访问 publisher 全文的一等路径。若 publisher 人机验证阻断 PDF 捕获，GRaDOS 会在 `remote_metadata` 中记录 `challenge` 与人工恢复信息；用户在托管浏览器 profile 中完成验证后，再次调用 `extract_paper_full_text` 并设置 `resume_browser=true`，即可从保存的浏览器 URL/profile 继续，而不是重新从 `api` 开始整条链路。

PDF 解析优先级：

```json
{
  "extract": {
    "parsing": {
      "order": ["Docling", "Marker", "PyMuPDF"]
    }
  }
}
```

### 导入现有 PDF 库 ♻️

如果你已经有本地 PDF 库，直接用 `grados import-pdfs` 把文件解析并复制进 canonical `papers/` + `downloads/` 布局：

```bash
grados import-pdfs --from /path/to/papers --recursive
grados status
```

## 开发 🛠️

```bash
uv sync --all-extras
uv run grados version
uv run pytest
uv build
```

## 项目文档 📚

- [TODO.md](./TODO.md)
  - 只记录当前未完成事项和优先级。
- [ADR.md](./ADR.md)
  - 记录已经接受的架构决策，以及项目为何这样设计。
- [CHANGELOG.md](./CHANGELOG.md)
  - 记录已经完成、对用户可见的行为变化和版本演进。

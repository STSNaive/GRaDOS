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

GRaDOS 是一个面向学术检索、全文提取、本地论文存储与 ChromaDB 语义检索的 Python MCP 服务器。

GRaDOS 为 Claude、Codex、Cursor 等 AI agent 提供单一 stdio MCP 服务，用来检索学术数据库、跨付费墙抓取论文、把 PDF 解析为 canonical Markdown，并在写作时回读已保存论文做引用核验。

## 架构概览 🧭

GRaDOS 设计给 agent 科研工作流直接调用：

1. 先用 `search_saved_papers`、`get_saved_paper_structure` 或 `grados://papers/{safe_doi}` 检查本地论文库
2. 按配置好的优先级检索远程学术数据库
3. 按 `TDM -> OA -> Sci-Hub -> Headless` 瀑布抓取全文
4. 按 `PyMuPDF -> Marker -> Docling` 瀑布解析 PDF
5. 把原始 PDF 保存到 `downloads/`，把 canonical Markdown 保存到 `papers/`，把语义检索数据写入 ChromaDB
6. 在正式引用前，先看低 token 结构卡片，再按需深读已保存论文

### MCP 工具 🔧

| 服务 | 工具 | 说明 |
| --- | --- | --- |
| GRaDOS | `search_academic_papers` | 检索 Crossref、PubMed、Web of Science、Elsevier 和 Springer，并做 DOI 去重与 continuation token 续查。 |
| GRaDOS | `search_saved_papers` | 检索由 ChromaDB 支持的本地 canonical 论文库。 |
| GRaDOS | `extract_paper_full_text` | 按 DOI 走全文抓取瀑布、解析正文，并保存 canonical Markdown 与原始 PDF 资产。 |
| GRaDOS | `read_saved_paper` | 从已保存论文中读取段落窗口，用于综合写作与引用核验。 |
| GRaDOS | `get_saved_paper_structure` | 返回低 token 的论文结构卡片，包含预览、章节标题与资产摘要。 |
| GRaDOS | `import_local_pdf_library` | 批量导入已有 PDF 文件夹到 canonical 论文库与检索索引。 |
| GRaDOS | `parse_pdf_file` | 把本地 PDF 解析为 canonical Markdown，并可选绑定 DOI。 |
| GRaDOS | `save_paper_to_zotero` | 通过 Zotero Web API 保存实际引用到的论文。 |

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
- `skills/grados/SKILL.md`：构建在 MCP 工具之上的结构化科研工作流
- `grados-python-implementation-plan.md`：实施计划与完成度台账
- `TODO.md`：从实施计划提炼出的简明执行快照

## 安装 🚀

### 方式 A：`uv tool install`（推荐）

```bash
uv tool install grados
grados setup --all
```

这会创建 `~/GRaDOS/config.json`，准备可见目录结构，安装托管浏览器资产，并预热默认 embedding 模型。

### 方式 B：extras、零安装或 pip

```bash
# 核心安装
uv tool install grados

# 安装可选解析器 extras
uv tool install "grados[marker]"
uv tool install "grados[docling]"
uv tool install "grados[full]"

# 零安装运行
uvx grados version

# 传统 Python 安装
pip install grados
```

当前包的 extras：

- `grados`：核心 MCP 服务、CLI、ChromaDB 存储、默认解析器、浏览器自动化，以及内置 Zotero 保存能力
- `grados[marker]`：在核心上加入 Marker PDF 解析器
- `grados[docling]`：在核心上加入 Docling PDF 解析器
- `grados[full]`：同时加入两个较重的解析器

### 方式 C：从源码运行

```bash
git clone https://github.com/STSNaive/GRaDOS.git
cd GRaDOS
uv sync --all-extras
uv run grados setup --all
uv run grados status
```

### 快速开始 ⚡

1. 用 `uv tool install grados` 安装 GRaDOS
2. 运行 `grados setup --all`
3. 编辑 `~/GRaDOS/config.json`
4. 运行 `grados status` 检查依赖、浏览器资产和 API Key
5. 在 MCP 客户端里指向 `grados` 或 `uvx grados`
6. 如果你已经有 PDF 库，运行 `grados import-pdfs --from /path/to/papers --recursive`

### 配置 MCP 客户端 🔌

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

`uvx` 适合零安装启动 MCP。长期本地使用仍建议 `uv tool install grados` 加 `grados` 可执行命令。如果你想指定自定义数据根目录，请在 MCP 客户端环境变量里设置 `GRADOS_HOME`。

## 配置 ⚙️

### 命令 🧰

| 命令 | 作用 |
| --- | --- |
| `grados` | 启动 MCP stdio 服务器 |
| `grados setup --all` | 创建目录、写入 `config.json`、安装浏览器资产并预热模型 |
| `grados setup --with browser` | 只安装浏览器运行时资产 |
| `grados setup --with models` | 只预热 embedding 模型 |
| `grados import-pdfs --from /path/to/papers --recursive` | 把已有 PDF 文件夹导入 canonical 论文库 |
| `grados status` | 查看配置、依赖、运行时资产和 API Key 状态 |
| `grados paths` | 查看当前解析到的 GRaDOS 文件布局 |
| `grados update-db` | 从 `papers/` 构建或刷新 ChromaDB 索引 |
| `grados migrate-config --from /path/to/legacy` | 从旧版 GRaDOS 安装迁移数据 |
| `grados version` | 查看包版本信息 |

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
| `WOS_API_KEY` | Clarivate Developer Portal | 否 |
| `SPRINGER_meta_API_KEY` | Springer Nature Metadata API | 否 |
| `SPRINGER_OA_API_KEY` | Springer Nature Open Access API | 否 |
| `LLAMAPARSE_API_KEY` | LlamaCloud | 否 |
| `ZOTERO_API_KEY` | Zotero Settings -> Keys | 否 |

Crossref 和 PubMed 不需要 API Key。GRaDOS 会使用你已配置的服务，未配置的会自动跳过。即使没有第三方 Key，本地论文工作流也能使用，远程检索也仍可依赖免费来源运行。

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
    "fetchStrategy": {
      "order": ["TDM", "OA", "SciHub", "Headless"]
    }
  }
}
```

PDF 解析优先级：

```json
{
  "extract": {
    "parsing": {
      "order": ["PyMuPDF", "Marker", "Docling"]
    }
  }
}
```

### 从旧安装迁移 ♻️

如果你已经有较旧的 GRaDOS 数据目录，可以用 `grados migrate-config` 把论文、下载归档、浏览器资产、模型缓存和兼容配置迁入当前布局。

推荐迁移流程：

```bash
uv tool install grados
grados migrate-config --from /path/to/legacy
grados status
```

`grados migrate-config` 会带过来的内容：

- 已保存 Markdown 论文到 `papers/`
- PDF 归档到 `downloads/`
- 托管浏览器资产到 `browser/`
- 模型缓存到 `models/`
- 兼容的搜索、提取、Zotero 和 API Key 设置到新的 `config.json`

路径映射：

| 旧布局 | 当前布局 |
| --- | --- |
| `grados-config.json` | `config.json` |
| `markdown/` | `papers/` |
| `downloads/` | `downloads/` |
| `.grados/browser/` | `browser/` |
| `models/` | `models/` |

## 开发 🛠️

```bash
uv sync --all-extras
uv run grados version
uv run pytest
uv build
```

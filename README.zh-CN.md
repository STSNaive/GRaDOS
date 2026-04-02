# GRaDOS

[English](./README.md) | [简体中文](./README.zh-CN.md)

GRaDOS 是一个面向学术检索、全文提取、本地论文存储与 ChromaDB 语义检索的 Python MCP 服务器。

Python 化之后，GRaDOS 不再依赖 `mcp-local-rag` 或 LanceDB。未来安装形态统一为一个 Python 包、一个可见数据根目录、一个本地语义数据库：ChromaDB。

## 文档地图

当前权威文档：

- `README.md` / `README.zh-CN.md`：主要的用户安装与使用说明
- `.mcp.json`：仓库内置的 MCP 服务器配置示例
- `skills/grados/SKILL.md`：构建在 MCP 工具之上的结构化科研工作流
- `grados-python-implementation-plan.md`：权威工程计划与完成度台账
- `TODO.md`：从实施计划提炼出的简明执行快照

保留但降级为本地开发或历史参考：

- `grados-python-migration-plan.md`：更早期的设计草案，现已并入实施计划
- `status.md`：Python 化前 Elsevier / 浏览器工程日志
- `docs/global-install-guide.md`：Python 化前的旧运维文档，仅保留作参考

## 功能概览

- 检索 Crossref、PubMed、Elsevier、Springer、Web of Science
- 按 `TDM -> OA -> Sci-Hub -> Browser` 瀑布抓取全文
- 按 `PyMuPDF -> Marker -> Docling` 瀑布解析 PDF
- 把论文保存为带 YAML front-matter 的 Markdown
- 用内置 ChromaDB 对已保存论文做语义检索
- 作为单一 stdio MCP 服务接入 Claude、Codex、Cursor 等客户端

## 安装

推荐方式：

```bash
uv tool install "grados[all]"
grados setup --all
```

其他方式：

```bash
# 核心安装
uv tool install grados

# 全量安装，包含更重的 PDF 解析器
uv tool install "grados[full]"

# 零安装运行
uvx "grados[all]" version

# 传统 Python 安装
pip install "grados[all]"
```

## 快速开始

1. 运行 `uv tool install "grados[all]"`。
2. 运行 `grados setup --all`。
3. 编辑生成的配置文件 `~/GRaDOS/config.json`。
4. 运行 `grados status` 检查依赖、浏览器资产和 API Key。
5. 在 MCP 客户端中用 `grados` 或 `uvx "grados[all]"` 启动服务。

## 命令

| 命令 | 作用 |
| --- | --- |
| `grados` | 启动 MCP stdio 服务器 |
| `grados setup --all` | 创建目录、生成 `config.json`、安装浏览器资产、预热模型 |
| `grados status` | 查看配置、依赖、运行时资产和 API Key 状态 |
| `grados paths` | 查看当前解析到的 GRaDOS 文件布局 |
| `grados update-db` | 从 `papers/` 构建或刷新 ChromaDB 索引 |
| `grados migrate-config --from /path/to/legacy` | 把 TypeScript 时代的安装迁到 Python 布局 |
| `grados version` | 查看版本信息 |

## 文件布局

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

## MCP 客户端配置

Claude Code / Claude Desktop：

```json
{
  "mcpServers": {
    "grados": {
      "command": "uvx",
      "args": ["grados[all]"]
    }
  }
}
```

Codex：

```toml
[mcp_servers.grados]
command = "uvx"
args = ["grados[all]"]
```

`uvx` 适合零安装 MCP 启动场景；长期本地使用仍以 `uv tool install "grados[all]"` 加 `grados` 可执行命令为主。

如果你想指定自定义数据根目录，请在 MCP 客户端环境变量里设置 `GRADOS_HOME`。

## MCP + Skill 结构

当前仓库保留的是轻量级的 MCP + skill 集成方式，而不是 Claude plugin 打包：

- `.mcp.json` 提供仓库级 `grados` MCP 配置示例，并保留可选的 `playwright`
- `skills/grados/SKILL.md` 提供结构化科研工作流
- `skills/grados/references/tools.md` 记录 skill 依赖的工具契约

如果你不使用仓库内置的 MCP 配置文件，可以把同样的 `grados` 服务器定义复制到自己的客户端设置中，再把 skill 文件放进 agent 的技能目录。

## 从 TypeScript 版迁移

这部分面向旧版 Node.js / TypeScript 用户，也就是还在使用 `grados-config.json`、`markdown/`、`lancedb/` 那套布局的用户。

### 发生了什么变化

- 安装方式从 `npm` 切到 `uv` / `pip`
- 运行时变成一个单独的 Python 包
- 本地语义检索统一改为 ChromaDB
- 默认数据根目录变成 `~/GRaDOS/`
- 主配置文件改成 `config.json`

`mcp-local-rag` 和 LanceDB 不再属于推荐方案。

### 推荐迁移流程

```bash
uv tool install "grados[all]"
grados migrate-config --from /path/to/legacy
grados status
```

如果你还想顺手把浏览器资产和模型一起准备好：

```bash
grados setup --all
```

### `grados migrate-config` 会做什么

- 读取旧版 `grados-config.json`
- 在当前 GRaDOS 数据根目录中写入 Python 版 `config.json`
- 把已保存 Markdown 论文复制到 `papers/`
- 把 PDF 归档复制到 `downloads/`
- 把托管浏览器资产复制到 `browser/`
- 把模型缓存复制到 `models/`
- 忽略旧版 LanceDB 数据

迁移命令的目标是保留有价值的内容，而不是继续沿用旧运行时结构。

### 路径映射

| 旧版 | Python 版 |
| --- | --- |
| `grados-config.json` | `config.json` |
| `markdown/` | `papers/` |
| `downloads/` | `downloads/` |
| `.grados/browser/` | `browser/` |
| `models/` | `models/` |
| `lancedb/` | 删除 |

### 配置差异

需要特别注意的变化：

- 现在用 `GRADOS_HOME` 选择整个数据根目录
- `--config` / `GRADOS_CONFIG_PATH` 属于旧模型，建议改成稳定的 GRaDOS home
- PDF 解析栈改为 `PyMuPDF -> Marker -> Docling`
- 语义检索改为内置 ChromaDB

迁移命令会自动把兼容的搜索、提取、Zotero 和 API Key 设置转换到新 schema。

### 如果你仍然需要旧布局

旧的 TypeScript 版本已经独立归档。当前主仓库就是 Python 主线；只有在你明确需要历史 TypeScript 代码时，才使用 `GRaDOS-legacy`。

## 开发

```bash
uv sync --all-extras
uv run grados version
uv run pytest
uv build
```

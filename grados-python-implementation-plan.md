# GRaDOS Python 化实施计划

## Document Status

- **本文件**：Python 化的权威工程计划、设计决策和完成度台账
- **`TODO.md`**：从本文件提炼出的当前执行快照
- **`grados-python-migration-plan.md`**：更早期的设计草案；其中安装设计、模块拆分、风险分析已整合进本文件，保留作历史参考
- **`status.md`**：Python 化前的 Elsevier / 浏览器工程日志；关键结论已整合进本文件的 Phase 1 / Phase 2 / 风险部分，保留作背景证据
- **`python/README.md`**：本地开发快捷入口；用户向内容已整合进根 README
- **`docs/claude-code-plugin-guide.md` / `docs/global-install-guide.md`**：旧 Node.js / mcp-local-rag 时代的运维文档；当前权威替代文档为根 README、MIGRATION、`.mcp.json` 与 `skills/grados/`

> **历史说明（2026-04-05）**：本文件保留了迁移实施期间的设计轨迹。其中文件中关于 `semantic` / `zotero` / `ocr` / `all` extras 的安装设计，现已不再代表当前发布状态。经过运行时审计，当前真实对外的包安装矩阵已收口为：`uv tool install grados`、`uvx grados`、`grados[marker]`、`grados[docling]`、`grados[full]`。如需当前用户安装方式，请以根目录 [`README.md`](README.md) / [`README.zh-CN.md`](README.zh-CN.md) 和 `pyproject.toml` 为准。

## Context

GRaDOS 当前是 TypeScript MCP 服务器（~6K LoC），依赖 Node.js + Python 双运行时，安装流程繁琐（5 步、~5 GB）。历史文档 `status.md` 与 `grados-python-migration-plan.md` 已确认 Python 迁移方向；本文件在此基础上给出当前仍然有效的实现设计与完成状态。

1. **安装体验对标/超越 zotero-mcp** — 以 `uv` 为首选安装器，一条命令 + 可选参数，`all` 安装全部推荐依赖
2. **文件目录直觉化** — 安装后所有依赖、运行时资产都在显眼、非隐藏的文件夹中

---

## 一、安装体验设计

### 1.1 依赖分层：基础包 vs 可选 extras

> **历史说明（2026-04-05）**：本节中的 extras 设计记录了迁移实施阶段的原始方案，不再等同于当前包元数据。当前发布版本已经移除了未接入运行时代码的 `semantic`、`zotero`、`ocr`、`all` extras；保留的真实 extras 只有 `marker`、`docling`、`full`。

#### 基础包（`uv tool install grados`，无需任何参数即可运行核心功能）

| 依赖 | 用途 | 大小 |
|------|------|------|
| fastmcp ≥ 2.0 | MCP 服务器框架 | ~5 MB |
| httpx | 异步 HTTP 客户端（替代 axios） | ~2 MB |
| pydantic ≥ 2.0 | 数据校验 + 配置模型 | ~10 MB |
| beautifulsoup4 | HTML 解析（搜索结果提取） | ~1 MB |
| lxml | XML/HTML 高性能解析（Elsevier XML 摄入） | ~10 MB |
| pymupdf4llm | 默认 PDF 解析器（100x 速度、AGPL） | ~20 MB |
| patchright | 浏览器自动化 Python API（CDP 反检测） | ~15 MB |
| chromadb ≥ 0.5.0 | 向量数据库（进程内语义搜索，替代 mcp-local-rag） | ~50 MB |
| platformdirs | 跨平台目录检测 | <1 MB |
| python-dotenv | 环境变量加载 | <1 MB |
| click | CLI 框架 | ~1 MB |
| rich | 终端美化输出（setup 向导、status 表格） | ~5 MB |

**基础包总计 ~120 MB**（含 ChromaDB 默认 ONNX 嵌入），涵盖：学术搜索、PDF 全文提取、PDF 解析、浏览器自动化、语义搜索。

> **设计决策**：pymupdf4llm、patchright、chromadb 放入基础包——PDF 解析、浏览器自动化、语义搜索是 GRaDOS 的三大核心能力。ChromaDB 自带基于 ONNX 的默认嵌入函数（all-MiniLM-L6-v2），无需 PyTorch，保持轻量。`sentence-transformers` 保留在 `semantic` extra 中供需要更多嵌入模型选项的用户。浏览器二进制下载由 `grados setup` 处理，不在 pip 包内。

#### 可选 extras

```toml
[project.optional-dependencies]
semantic = ["sentence-transformers >= 3.0.0"]  # 更多嵌入模型选项（基础包已含 ChromaDB + ONNX 默认嵌入）
zotero   = ["pyzotero >= 1.5.0"]
ocr      = ["pymupdf4llm[ocr]"]               # Tesseract OCR 支持
marker   = ["marker-pdf == 1.10.2"]            # 重量级神经网络 PDF（~2-3 GB）
docling  = ["docling"]                         # IBM Docling PDF（~500 MB）
all      = ["grados[semantic,zotero,ocr]"]     # 所有推荐依赖（不含 marker/docling）
full     = ["grados[all,marker,docling]"]      # 绝对全量（含重量级 ML）
dev      = ["pytest", "ruff", "mypy"]
```

> **关键决策**：`all` 不包含 marker/docling（它们各自 500MB-3GB），避免安装变成 30 分钟的下载。如果用户需要最强 PDF 质量，显式 `uv tool install "grados[marker]"`。`full` 是真正的"全量"。

#### 安装命令矩阵（uv 优先）

```bash
# ═══════════════════════════════════════════════════════════════
#  推荐方式：uv（速度快、自动管理环境、无需手动 venv）
# ═══════════════════════════════════════════════════════════════

# 核心安装（搜索 + 提取 + PDF 解析 + 浏览器 API + ChromaDB 语义搜索）
uv tool install grados                      # ~120 MB, <1 分钟

# 推荐安装（+ 更多嵌入模型 + Zotero + OCR）
uv tool install "grados[all]"               # ~400 MB, 1-2 分钟

# 绝对全量（+ Marker + Docling 神经网络解析）
uv tool install "grados[full]"              # ~3-4 GB, 10-30 分钟

# 零安装一次性运行（试用 / CI）
uvx "grados[all]" setup                     # 不持久化，自动清理

# ═══════════════════════════════════════════════════════════════
#  备选方式：pip（传统 Python 用户）
# ═══════════════════════════════════════════════════════════════
pip install "grados[all]"

# ═══════════════════════════════════════════════════════════════
#  MCP 客户端配置（Claude Desktop / Cursor / Claude Code）
# ═══════════════════════════════════════════════════════════════

# Claude Desktop claude_desktop_config.json:
#   "grados": {
#     "command": "uvx",
#     "args": ["grados[all]"]
#   }

# Claude Code .mcp.json:
#   "grados": {
#     "command": "uvx",
#     "args": ["grados[all]"]
#   }
```

> **为什么选 uv**：uv 的解析速度比 pip 快 10-100x，自动管理隔离环境，`uv tool install` 将 grados 注册为全局 CLI 工具而不污染系统 Python。`uvx` 模式更进一步——零安装即可运行，非常适合 MCP 客户端配置。

### 1.2 两阶段安装：uv tool install + grados setup

```
阶段 1: uv tool install "grados[all]"  → Python 包 + 轻量依赖（uv 自动创建隔离环境）
阶段 2: grados setup                    → 交互式向导，配置 + 运行时资产下载
```

#### `grados setup` 流程

```bash
# 交互式向导（首次安装推荐）
grados setup
#  → 检测/创建 ~/GRaDOS/ 数据目录（首次运行时确认路径）
#  → 生成 config.json
#  → 检测已安装的 extras，提示缺失推荐项
#  → 询问是否下载浏览器
#  → 询问 API keys（引导用户手动编辑，不在终端接收）
#  → 询问是否预热 embedding 模型

# 一键全量（CI/高级用户）
grados setup --all
#  → 下载 Chrome for Testing → ~/GRaDOS/browser/chromium/
#  → 创建 GRaDOS 专用浏览器 profile → ~/GRaDOS/browser/profile/
#  → 预热 embedding 模型 → ~/GRaDOS/models/embedding/
#  → 生成默认 config.json → ~/GRaDOS/config.json

# 按组件
grados setup --with browser          # 仅下载浏览器 + 创建 profile
grados setup --with models           # 仅预热 embedding 模型
grados setup --with browser,models   # 逗号分隔多组件
```

#### 完整安装一览（从零到可用）

```bash
# 两条命令，搞定一切
uv tool install "grados[all]"        # 1. 安装 Python 包（~1 分钟）
grados setup --all                   # 2. 下载运行时资产 + 生成配置
```

#### 安装体验对比

```
                           zotero-mcp                    GRaDOS (Python)
─────────────────────────────────────────────────────────────────────────
安装器                 pip / uv                      uv（首选）/ pip
安装命令               pip install zotero-mcp[all]   uv tool install "grados[all]"
零安装运行             ✗                              uvx "grados[all]"
初始化                 zotero-mcp setup              grados setup [--all]
浏览器支持             ✗                              ✓ (grados setup --with browser)
PDF 深度解析           markitdown (pdfminer)          pymupdf4llm (默认) + Marker (可选)
向量搜索               chromadb (extras)              chromadb (extras, 进程内)
MCP 服务器数量         1                              1 (消除 mcp-local-rag)
健康检查               ✗                              grados status
路径速查               ✗                              grados paths
总步骤                 2                              2
```

### 1.3 CLI 命令设计

```bash
grados                           # 启动 MCP stdio 服务器（供 Claude 等客户端调用）
grados setup [--all] [--with X]  # 初始化向导 / 运行时资产下载
grados status                    # 健康检查（版本、配置、API keys、依赖状态）
grados paths                     # 显示所有文件路径（数据根、浏览器、模型、数据库等）
grados update-db                 # 批量索引 papers/ 到 ChromaDB
grados version                   # 版本号
```

---

## 二、文件目录设计

### 2.1 核心原则

1. **不使用隐藏目录** — 不用 `.grados/`、不用 `~/.local/share/`、不用 `~/Library/Application Support/`
2. **单一数据根** — 所有 GRaDOS 管理的文件都在一个目录下，一目了然
3. **目录名自解释** — 看名字就知道里面是什么
4. **可自定义** — `GRADOS_HOME` 环境变量或 config 指定

### 2.2 默认数据根

| 平台 | 默认路径 |
|------|----------|
| macOS | `~/GRaDOS/` |
| Linux | `~/GRaDOS/` |
| Windows | `%USERPROFILE%\GRaDOS\` |

> **设计决策**：选择 `~/GRaDOS/` 而非 OS 标准隐藏路径，因为用户明确要求"符合直觉、不隐蔽"。它在 Finder/文件管理器中直接可见，用户不需要 `ls -a` 或特殊操作就能找到。通过 `GRADOS_HOME` 环境变量可自定义到任意位置。

### 2.3 完整目录结构

```
~/GRaDOS/                                    # 数据根（GRADOS_HOME）
│
├── config.json                              # 主配置文件
│
├── papers/                                  # 已保存论文（Markdown + YAML frontmatter）
│   ├── 2024-chen-composite-structures.md
│   ├── 2023-wang-vibration-analysis.md
│   └── assets/                              # 论文关联的图表资源
│
├── downloads/                               # 原始 PDF 文件存档
│   ├── 10.1016_j.compstruct.2021.114178.pdf
│   └── ...
│
├── browser/                                 # 浏览器运行时（grados setup --with browser 创建）
│   ├── chromium/                            # Chrome for Testing 可执行文件
│   │   └── chrome-mac-arm64/               # 平台特定的浏览器二进制
│   │       └── Google Chrome for Testing.app/
│   ├── profile/                             # GRaDOS 专用浏览器配置（持久化会话）
│   │   ├── Default/
│   │   └── ...
│   └── extensions/                          # 浏览器扩展
│       └── privacy-pass/                    # Privacy Pass（如启用）
│           └── 1.12.0/
│
├── models/                                  # ML 模型缓存（grados setup --with models 创建）
│   ├── embedding/                           # 向量嵌入模型
│   │   └── all-MiniLM-L6-v2/              # Sentence-Transformers 默认模型
│   └── ocr/                                 # OCR 模型（如安装 ocr extra）
│
├── database/                                # 本地数据库
│   └── chroma/                              # ChromaDB 持久化（语义搜索索引）
│
├── logs/                                    # 运行日志
│   └── grados.log
│
└── cache/                                   # 临时缓存
    └── scihub-mirrors.txt                   # Sci-Hub 镜像列表
```

### 2.4 `grados paths` 输出示例

```
$ grados paths

GRaDOS 文件路径
────────────────────────────────────────────
数据根目录      ~/GRaDOS/
配置文件        ~/GRaDOS/config.json
论文目录        ~/GRaDOS/papers/              (42 篇)
下载目录        ~/GRaDOS/downloads/           (38 个 PDF)
浏览器二进制    ~/GRaDOS/browser/chromium/    ✓ 已安装
浏览器配置      ~/GRaDOS/browser/profile/     ✓ 已创建
浏览器扩展      ~/GRaDOS/browser/extensions/  (1 个扩展)
嵌入模型        ~/GRaDOS/models/embedding/    ✓ all-MiniLM-L6-v2
ChromaDB        ~/GRaDOS/database/chroma/     (42 条索引)
日志目录        ~/GRaDOS/logs/
```

### 2.5 多模式支持

| 模式 | 数据根 | 触发条件 |
|------|--------|----------|
| **全局安装**（默认） | `~/GRaDOS/` | `pip install grados` 后直接使用 |
| **自定义路径** | `$GRADOS_HOME` | 设置环境变量 |
| **项目局部** | `./grados-data/` | config.json 中显式配置 `dataRoot: "./grados-data/"` |

---

## 三、pyproject.toml 设计

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "grados"
version = "0.6.6"
description = "Academic research MCP server — search, extract, and manage papers"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [{ name = "macfish" }]
keywords = ["mcp", "academic", "research", "papers", "pdf"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Science/Research",
    "Topic :: Scientific/Engineering",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
]

dependencies = [
    "fastmcp >= 2.0",
    "httpx >= 0.27",
    "pydantic >= 2.0",
    "beautifulsoup4 >= 4.12",
    "lxml >= 5.0",
    "pymupdf4llm >= 0.0.17",
    "patchright >= 1.50",
    "chromadb >= 0.5.0",
    "platformdirs >= 4.0",
    "python-dotenv >= 1.0",
    "click >= 8.0",
    "rich >= 13.0",
]

[project.optional-dependencies]
semantic = ["sentence-transformers >= 3.0.0"]
zotero   = ["pyzotero >= 1.5.0"]
ocr      = ["pymupdf4llm[ocr]"]
marker   = ["marker-pdf == 1.10.2"]
docling  = ["docling"]
all      = ["grados[semantic,zotero,ocr]"]
full     = ["grados[all,marker,docling]"]
dev      = ["pytest >= 8.0", "ruff >= 0.4", "mypy >= 1.10"]

[project.scripts]
grados = "grados.cli:main"

[project.urls]
Homepage = "https://github.com/STSNaive/GRaDOS"
Repository = "https://github.com/STSNaive/GRaDOS"

# ───── uv 专属配置 ─────

[tool.uv]
dev-dependencies = [
    "pytest >= 8.0",
    "ruff >= 0.4",
    "mypy >= 1.10",
]

[tool.ruff]
target-version = "py311"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP"]

[tool.mypy]
python_version = "3.11"
strict = true
```

> **uv 开发工作流**：开发者使用 `uv sync --all-extras` 安装所有依赖到 `.venv/`，`uv run grados` 运行而无需手动激活环境，`uv lock` 生成可复现的锁文件。`uv.lock` 纳入 Git 确保 CI/协作者环境一致。

---

## 四、Python 模块结构

```
python/                                # 迁移期间在子目录，稳定后提升到根目录
├── pyproject.toml
├── README.md
├── src/grados/
│   ├── __init__.py                    # 版本号 + 包元数据
│   ├── cli.py                         # Click CLI：grados / setup / status / paths / update-db
│   ├── server.py                      # FastMCP server 定义 + 全部 MCP 工具注册
│   ├── config.py                      # 配置加载：config.json 解析 + GRADOS_HOME 检测 + 多模式
│   │
│   ├── search/                        # 学术搜索模块
│   │   ├── __init__.py
│   │   ├── academic.py                # 多数据库瀑布搜索（Crossref/PubMed/Elsevier/Springer/WoS）
│   │   ├── resumable.py               # 分页状态管理（resumable-search 移植）
│   │   └── saved.py                   # 已保存论文搜索（ChromaDB 语义 + 关键词 fallback）
│   │
│   ├── extract/                       # 全文提取模块
│   │   ├── __init__.py
│   │   ├── fetch.py                   # PDF 获取瀑布：TDM → OA → Sci-Hub → 浏览器
│   │   ├── parse.py                   # PDF 解析瀑布：pymupdf4llm → Marker → Docling → pdfminer
│   │   ├── elsevier_xml.py            # Elsevier XML/JSON 原生摄入（新增，优于 PDF 重解析）
│   │   └── qa.py                      # 质量验证（最小字符数、paywall 检测）
│   │
│   ├── browser/                       # 浏览器自动化模块
│   │   ├── __init__.py
│   │   ├── manager.py                 # 托管浏览器生命周期（下载/启动/profile 管理）
│   │   ├── sciencedirect.py           # ScienceDirect 特化状态机（View PDF → 中间页 → 最终 PDF）
│   │   └── generic.py                 # 通用出版商浏览器流程（AIP 等）
│   │
│   ├── storage/                       # 存储模块
│   │   ├── __init__.py
│   │   ├── papers.py                  # Markdown 保存/读取/段落窗口
│   │   └── vector.py                  # ChromaDB 进程内索引/查询
│   │
│   ├── publisher/                     # 出版商 API 工具
│   │   ├── __init__.py
│   │   ├── elsevier.py                # Elsevier API + 元数据提取 + ScienceDirect 候选
│   │   ├── springer.py                # Springer API key 管理
│   │   └── common.py                  # PDF 内容验证、中间重定向解析
│   │
│   ├── zotero.py                      # Zotero 集成（pyzotero）
│   │
│   └── setup/                         # 安装向导模块
│       ├── __init__.py                # grados setup 入口逻辑
│       ├── browser.py                 # Chrome for Testing 下载 + profile 创建
│       ├── extensions.py              # Privacy Pass 扩展管理
│       └── models.py                  # Embedding 模型预热
│
└── tests/
    ├── test_search.py
    ├── test_extract.py
    ├── test_browser.py
    ├── test_config.py
    └── test_setup.py
```

---

## 五、分阶段实施计划

### Phase 0: 项目骨架 ✅ 已完成 (2026-04-02)

**目标**：可运行的空壳 MCP 服务器 + CLI + 配置系统

- [x] 创建 `python/` 子目录 + `pyproject.toml`（hatchling 构建、dependency-groups.dev）
- [x] 实现 `grados/cli.py`：Click CLI 框架（grados / setup / status / paths / version / update-db）
- [x] 实现 `grados/config.py`：
  - `GRADOS_HOME` 检测（环境变量 → `~/GRaDOS/`）
  - config.json 加载（camelCase→snake_case 自动转换，兼容现有 grados-config.json）
  - Pydantic 配置模型（SearchConfig, ExtractConfig, ZoteroConfig, ApiKeysConfig 等）
- [x] 实现 `grados/server.py`：FastMCP 空壳服务器（无工具，仅启动）
- [x] 实现 `grados setup`：
  - 创建 `~/GRaDOS/` 目录结构（papers/downloads/logs/cache）
  - 生成默认 config.json
  - 检测已安装 extras 并报告（含安装提示命令）
  - `--all` / `--with browser,models` 参数处理
  - `--with browser`：patchright install chromium → ~/GRaDOS/browser/chromium/
  - `--with models`：ChromaDB DefaultEmbeddingFunction 预热
- [x] 实现 `grados status`：版本、核心依赖版本、可选依赖、运行时资产、API keys 状态
- [x] 实现 `grados paths`：所有路径 + 文件统计 + 模式检测
- [x] 创建全部子包 `__init__.py`（search/extract/browser/storage/publisher/setup）

**实际验证结果**：
```
$ uv run grados version  → GRaDOS 0.6.6 / fastmcp 3.2.0 / chromadb 1.5.5
$ uv run grados setup    → 4 步完成，生成 ~/GRaDOS/config.json
$ uv run grados status   → 7/7 核心依赖 ✓，API keys/运行时待配置
$ uv run grados paths    → 10 个路径条目，含文件统计
```

**实施偏差记录**：
- `pymupdf4llm` 无 `[ocr]` extra → `ocr` 改为依赖 `pytesseract`
- `tool.uv.dev-dependencies` 已废弃 → 改用 `[dependency-groups] dev`
- chromadb 实际版本 1.5.5（远超 ≥ 0.5.0 下限）
- fastmcp 实际版本 3.2.0（远超 ≥ 2.0 下限）

### Phase 1: 核心 MCP 工具 ✅ 已完成 (2026-04-02)

#### 1.1 学术搜索 `search_academic_papers`

- [x] 移植 5 个搜索提供者（`search/academic.py`, 320 行）
  - Crossref: cursor 分页 + 5 分钟 TTL + etiquette email
  - PubMed: ESearch → ESummary → EFetch (abstracts) 三步流程
  - Web of Science: X-ApiKey 认证 + TS()/DO() 查询语法
  - Elsevier (Scopus): COMPLETE view + opensearch 分页
  - Springer: 保守单页策略 + keyword/doi 查询
- [x] 移植 resumable search（`search/resumable.py`, 170 行）
  - Base64URL continuation token 编/解码
  - DOI 去重（seen_dois 跨续搜保持）
  - 源级别疲尽跟踪（exhausted_sources）
  - Crossref cursor 过期自动重置
  - 每源每次最多 8 页抓取
- [x] 配置兼容：search.order / search.enabled

#### 1.2 全文提取 `extract_paper_full_text`

- [x] PDF 获取瀑布（`extract/fetch.py`, 190 行）
  - TDM: Elsevier (FULL JSON → text/plain → metadata) + Springer (JATS → HTML → PDF)
  - OA: Unpaywall API（优先 repository 源）
  - Sci-Hub: 镜像获取 + embed/iframe/button PDF 链接提取
  - Headless: Phase 2 占位（返回警告）
- [x] PDF 解析管线（`extract/parse.py`, 100 行）
  - pymupdf4llm（默认，进程内）
  - marker-pdf（可选，try-import）
  - docling（可选，try-import）
- [x] QA 验证（`extract/qa.py`, 50 行）
  - 最小字符数 + anti-paywall 模式 + 学术结构检测 + 标题匹配
- [x] Markdown frontmatter + 保存逻辑（`storage/papers.py`, 200 行）

#### 1.3 辅助工具

- [x] `read_saved_paper` — DOI/safe_doi/URI 三种定位 + 段落窗口 + section_query 跳转
- [x] `parse_pdf_file` — 复用解析管线 + 可选 DOI 保存
- [x] `save_paper_to_zotero` — Zotero Web API（`zotero.py`, 70 行）
- [x] `search_saved_papers` — 关键词 fallback（Phase 3 添加 ChromaDB 语义搜索）

#### Publisher 工具

- [x] `publisher/common.py` — PDF 魔数校验、bot 挑战检测、DOI 工具
- [x] `publisher/elsevier.py` — Elsevier TDM waterfall + ScienceDirect PDF 候选提取 + 中间重定向解析
- [x] `publisher/springer.py` — Springer Meta/OA/JATS/HTML/PDF 多路径

**实际验证结果**：
```
$ uv run python -c "import asyncio; from grados.server import mcp; print(len(asyncio.run(mcp.list_tools())))"
→ 6 tools registered: search_academic_papers, extract_paper_full_text,
  read_saved_paper, parse_pdf_file, save_paper_to_zotero, search_saved_papers
$ All module imports pass (0 errors)
```

**实施偏差记录**：
- FastMCP 3.x 中 `description` 参数改为 `instructions`
- Semantic Scholar / OpenAlex 已从 Python 默认配置移除，保持与 TS 原版支持范围一致
- Headless browser fetch 为 Phase 2 占位桩

### Phase 2: 浏览器自动化 ✅ 已完成 (2026-04-02)

**这是 GRaDOS 的核心差异化能力，必须完整移植。**

- [x] `grados setup --with browser`：
  - 下载 Chrome for Testing 到 `~/GRaDOS/browser/chromium/`（Phase 0 已实现）
  - 创建 GRaDOS 专用 profile 到 `~/GRaDOS/browser/profile/`（Phase 0 已实现）
- [x] 移植 Patchright Python 浏览器管理器（`browser/manager.py`，210 行）：
  - 启动/复用可见窗口（persistent context / ephemeral）
  - 持久 profile 加载（launch_persistent_context）
  - 自动关闭已捕获的 PDF 标签页（close_secondary_pages）
  - 浏览器解析优先级：managed → configured → system PATH
  - 平台检测（darwin arm64/x64、linux、windows）
  - 视口随机化（4 种分辨率指纹变异）
- [x] 移植 ScienceDirect 状态机（`browser/sciencedirect.py`，175 行）：
  - 着陆页检测 → View PDF 点击（expect_page popup + 直接导航 fallback）
  - 中间页观察（/pdfft, cfts/init, sciencedirectassets.com）
  - 最终 PDF 捕获（response listener + classify_pdf_content 校验）
  - 防重复开标签（attempted_urls 集合）
  - Modal/Cookie 拦截弹窗自动关闭（Escape + role-based + CSS selectors）
  - Dropdown trigger + candidate extraction + 中间重定向跟踪
- [x] 移植 AIP 等通用出版商流程（`browser/generic.py`，250 行）：
  - 主轮询循环（2 分钟 deadline，1 秒 tick）
  - Response listener PDF 捕获（content-type / URL / content-disposition）
  - Download listener PDF 捕获（await download.path()）
  - 通用 PDF 链接点击（a[href*="pdf"]）
  - URL 后缀 backfill（.pdf 页面直接 context.request.get）
  - Bot challenge 检测 + 手动验证等待
  - Event listener 注册/注销生命周期管理
- [x] 移植会话复用（`get_or_create_reusable_session`，跨 DOI 复用挑战状态）
- [x] 接入 fetch waterfall：`extract/fetch.py` Headless 策略调用 `fetch_with_browser`

**实际验证结果**：
```
$ uv run python -c "from grados.browser.manager import *; print('OK')"  → OK
$ uv run python -c "from grados.browser.sciencedirect import *; print('OK')"  → OK
$ uv run python -c "from grados.browser.generic import *; print('OK')"  → OK
$ uv run python -c "from grados.server import mcp; ..."  → 6 tools registered
$ URL detection tests: is_landing_page / is_pdf_flow_page assertions pass
$ patchright CLI available: True
```

**实施偏差记录**：
- TS 版 CDP fallback（Edge macOS pipe 失败时的 DevToolsActivePort 恢复）未移植 — Python Patchright 默认支持更好，无需
- `framenavigated` 事件监听器简化为轮询循环中的 challenge 检测，逻辑等价
- TS 版 `context.on('download')` 改为 per-page `page.on('download')` — Playwright Python BrowserContext 无 download 事件

### Phase 3: 语义搜索 ✅ 已完成 (2026-04-02)

- **说明（2026-04-03）**：本节记录的是 Python 化第一轮 Chroma 集成结果。按照后续方案收口，Phase 3 的“Markdown 主存储 + Chroma 附带索引 + 关键词 fallback”将被视为过渡实现，最终将由 Phase 4.4 中的 `papers_docs` / `papers_chunks` canonical-first 方案取代。
- [x] ChromaDB 进程内集成（`storage/vector.py`，165 行）
  - 使用 ChromaDB 默认 ONNX 嵌入（all-MiniLM-L6-v2）
  - 持久化到 `~/GRaDOS/database/chroma/`
  - 段落分块（1000 字符）+ DOI 去重 + cosine 相似度
  - index_paper / search_papers / index_all_papers / get_index_stats
- [x] 保存论文时自动索引（save_paper_markdown + chroma_dir 参数，server.py 两处调用已更新）
- [x] `search_saved_papers` 工具升级为 ChromaDB 语义搜索 + 关键词 fallback（索引空时提示 update-db）
- [x] `grados update-db` 批量索引命令（扫描 papers/ 目录，批量 upsert 到 ChromaDB）
- [x] 可选 `sentence-transformers` 支持 — ChromaDB DefaultEmbeddingFunction 已自带 ONNX all-MiniLM-L6-v2；`semantic` extra 预留用户扩展更多模型

**实际验证结果**：
```
$ uv run python -c "from grados.storage.vector import *; print('OK')"  → OK
$ uv run python -c "from grados.server import mcp; ..."  → 6 tools OK
$ ChromaDB get_index_stats → {'total_chunks': 0, 'unique_papers': 0} (空索引正常)
$ CLI update-db imports OK
```

### Phase 4: 打包与发布

- [x] PyPI 发布配置（pyproject.toml 完善：keywords、classifiers、urls；uv build 通过 sdist+wheel）
- [x] 主安装路径保持为 `uv tool install "grados[all]"`
  - `uv tool install` + `grados setup --all` 仍是面向长期使用者的推荐安装路径
  - `uvx` 仅用于零安装运行、MCP 客户端轻量接入和发布验收
- [x] 发布工作流补齐到“发布前验证 + 发布后验收”闭环
  - `publish.yml` 新增 `verify-release`：tag/version 校验、`uv run pytest tests -q`、`uv build`、本地 wheel 的 `uvx --from` / `uv tool install` smoke test
  - `publish.yml` 新增 `verify-pypi`：轮询 PyPI 上线，再执行真实 `uv tool install "grados[all]"` 与 `uvx "grados[all]" version`
- [x] 第三方依赖安装告警已记录（非阻塞）
  - 本地 `uvx --from` / `uv tool install` 过程中，uv 对部分第三方依赖的旧式版本约束做了兼容修正并打印 warning
  - 典型 warning 形式包括 `>= '2.7'`、`>3.4.*`、`>=3.5.*`
  - wheel 安装、CLI 启动、`uv run pytest tests -q` 均成功，当前判断为上游依赖元数据噪音，不阻塞 Phase 4
- [ ] `uvx grados[all]` 零安装验证
  - 已完成本地 wheel/入口/`requires-python >= 3.11` 元数据检查
  - 已完成本地 wheel 的 `uvx --from` 验证
  - 截至 2026-04-02，`https://pypi.org/pypi/grados/json` 返回 404，直接执行 `uvx "grados[all]" version` 仍失败
  - 说明 PyPI 发布后还需补一次真实远程验证
- [x] 编写迁移指南（现有 TypeScript 用户）
  - 配置文件格式兼容说明
  - `grados migrate-config` 命令（自动转换旧格式 + 迁移数据目录）
- [x] 移除 Claude plugin 分发路径，保留 MCP + skill 结构
  - `.mcp.json` 改为仓库级 MCP 配置示例
  - 删除 plugin 命令文档与 `.claude-plugin/` 元数据
- [x] 更新 README.md / README.zh-CN.md
- [x] 更新 `.mcp.json` / `skills/grados/*`
- [x] 统一为 ChromaDB-only 路径（不再保留 LanceDB / mcp-local-rag 未来安装方案）
- [x] 补齐 Python 自动化测试覆盖（首轮 smoke tests）
  - 已新增 `test_browser_smoke.py` / `test_cli_smoke.py` / `test_parse_smoke.py` / `test_search_smoke.py` / `test_server_smoke.py` / `test_storage_smoke.py`
  - 连同 `test_migration.py` 共 7 个测试文件
  - `uv run pytest tests -q` 已通过（15 passed）
- [x] 处理搜索源配置中的预留项
  - 已从 Python 默认配置与文档中移除 `SemanticScholar` / `OpenAlex`
  - 默认搜索源与 TS 原版重新对齐：Crossref / PubMed / WebOfScience / Elsevier / Springer
- [ ] 清理全量 Ruff 历史告警（非阻塞）
  - 本轮修改相关文件已通过 Ruff
  - 截至 2026-04-02，`ruff check src/grados tests` 仍存在 25 个旧模块遗留问题，建议后续单独清理
- [x] 恢复 paper MCP resources / resource templates 能力
  - 2026-04-03：已按收口决策恢复 `grados://papers/index` 与 `grados://papers/{safe_doi}`
  - `grados://about` / `grados://status` / `grados://tools` 不再作为首发必需项，当前由现有 CLI / tools 覆盖
- [x] 将本地论文库重构为 canonical-first 的 Chroma 架构
  - 2026-04-03：实施包 A 已完成 `papers_docs` / `papers_chunks`、canonical schema、mirror 降级与 `read_saved_paper` 的 Chroma-first 读取路径
  - 2026-04-03：实施包 E 已完成 `search_saved_papers` 的 metadata prefilter + dense retrieval + 论文级聚合
- [ ] 将 `migrate-config` 降级为兼容路径，并以本地 PDF 导入替代首发主入口
  - 2026-04-03：实施包 C 已完成 `grados import-pdfs` / `import_local_pdf_library`
  - 待完成：文档与帮助文字层面对 `migrate-config` 的 legacy compatibility 定位再收口
  - TS 迁移兼容不再主导当前发布路线
- [x] 决定并落实 saved-paper 的 canonical reading / structured navigation 契约
  - `read_saved_paper` 继续负责正文深读与 citation verification
  - 2026-04-03：已新增 `get_saved_paper_structure`，并将 `grados://papers/{safe_doi}` 收口为低 token overview resource
- [x] 打通 Elsevier / Springer `asset_hints` 到持久化 sidecar manifest
  - 2026-04-03：publisher -> fetch -> `save_asset_manifest` -> canonical metadata 链路已打通
  - 当前先采用 manifest-first：稳定保存资源线索与正文绑定关系，不在首发阶段强制下载二进制图片
- [x] 恢复或明确放弃 `extract.tdm` 的按 publisher 配置能力
  - 2026-04-03：已恢复 `extract.tdm.order` / `extract.tdm.enabled`
  - `fetch_paper` / `_fetch_tdm` 已按配置控制 Elsevier / Springer 顺序与开关
- [x] 将写作工作流升级为 citation-aware protocol
  - 2026-04-03：已在 `skills/grados/SKILL.md`、`skills/grados/references/tools.md`、`README.md`、`README.zh-CN.md` 中统一到 `search -> structure -> deep read -> cite -> verify` 协议
  - 首发阶段仍保持“写作由 skill 编排，不新增服务器端 LLM 摘要或写作 tool”
- [x] 补齐上述差异的回归测试
  - 2026-04-03：已补 `test_fetch_smoke.py` / `test_import_smoke.py` 以及对 `test_search_smoke.py`、`test_storage_smoke.py`、`test_server_smoke.py`、`test_cli_smoke.py` 的增强
  - `.venv/bin/pytest tests -q` 通过（`30 passed, 7 warnings`）

### Phase 4.1: 当前完成度与改进建议（2026-04-02）

- **说明**：本节结论是“发布收口视角”的阶段判断；2026-04-03 的 Phase 4.2 审计已确认仍存在若干能力/契约回退，因此不能据此宣称“Python 化已经完全完成”
- **完成度判断**：Python 主运行时已经具备对外可用的核心能力，当前工作的重心已从“功能移植”转为“发布验收 + 仓库收口 + 文档降噪”
- **已完成的主线**：CLI、6 个 MCP 工具、浏览器自动化、ChromaDB、迁移命令、首轮 smoke tests、发布 workflow、本地 wheel 验收
- **仍未完成的硬阻塞**：PyPI 真正上线后的远程验收
- **仍未完成的软问题**：
  - 仓库已切换到根目录 Python 主线，但仍需继续清理历史文档中的迁移期措辞
  - 仍需确认 PyPI 首发版本号与发布顺序是否直接采用 `v0.6.6`
  - 历史文档虽已标注和整合，但仍留在原路径，未来可考虑归档到 `docs/archive/`

### Phase 4.2: Python / TypeScript 功能对齐审计（2026-04-03）

- **审计范围**：当前根目录 Python 实现、`origin/main` 上仍保留的 TS `v0.6.4` 基线，以及历史能力记录 `status.md`
- **修正后的完成度判断**：Python 版已经达到“可用并可发布准备”的程度，但还**不能认定为完全完成 Python 化**；至少还有 6 类明确的功能/契约回退需要处理或显式降级
- **已确认仍保持对齐的主线**：
  - 学术搜索 5 源默认配置与 TS 对齐
  - 抽取主流程仍保留 TDM → OA → Sci-Hub → Browser waterfall
  - ScienceDirect 浏览器状态机、managed browser 优先级、复用 profile 等 late-TS 能力已在 Python 中延续
  - 本地存储已统一为 ChromaDB，不再依赖 LanceDB / mcp-local-rag

**已确认的问题**：

1. **MCP resources / resource templates 尚未迁移**
   - 证据：当前 Python 运行时 `await mcp.list_tools()` 返回 6 个工具，但 `await mcp.list_resources()` 与 `await mcp.list_resource_templates()` 均返回 `[]`
   - 对比：TS 原版除工具外，还提供 `grados://about`、`grados://status`、`grados://tools`、`grados://papers/index`、`grados://papers/{safe_doi}` 等资源入口
   - 影响：依赖 canonical resource URI 的客户端、skill 提示词和保存论文阅读链路目前只剩文本工具调用，能力面小于 TS 原版

2. **`search_saved_papers` 的关键词 fallback 明显弱于 TS**
   - 证据：当前 Python fallback 只检查标题与 DOI；正文中包含 `composite vibration`、标题不包含关键词时，运行结果仍为 `No papers matching ...`
   - 对比：TS 原版 lexical fallback 会扫描 Markdown 正文、章节标题和最佳段落，并返回 snippet / matched sections
   - 影响：一旦 ChromaDB 索引为空、损坏或尚未建立，Python 版对本地论文库的可搜索性明显下降

3. **迁移后的旧论文库会丢失“无需重建即可正文检索”的能力**
   - 证据：`migrate_legacy_install` 只复制 `papers/` 等目录并转换配置，不自动执行 `grados update-db`；对迁移后的 body-only 命中文档执行 `search_saved_papers("composite vibration")`，结果同样为找不到
   - 对比：TS 原版没有 ChromaDB 这一步依赖时，lexical fallback 仍可直接搜索已保存 Markdown 的正文与章节
   - 影响：这不是单纯的性能退化，而是迁移路径上的功能回退；旧用户迁移后若没手动重建索引，会误以为论文“没有被迁过来”

4. **saved-paper 相关 MCP 契约较 TS 明显变薄**
   - 证据：当前 Python 的 `extract_paper_full_text`、`read_saved_paper`、`search_saved_papers` 基本都返回格式化文本；`PaperSavedSummary` 只保留 DOI / URI / 文件路径 / 字数 / headings
   - 对比：TS 原版为保存、搜索、读取都提供了 canonical URI、relative path、preview excerpt、matched sections、resource link 等结构化内容
   - 影响：客户端如果要做更细粒度的 UI 呈现、阅读跳转或二次编排，目前只能从文本中二次解析，契约稳定性差于 TS

5. **Elsevier / Springer 的结构化资产提示没有被真正保存下来**
   - 证据：`status.md` 已把 Elsevier `XML/JSON` 中的 figures / tables / object URLs 记为已确认能力；当前 Python 的 publisher dataclass 仍保留 `asset_hints` 字段，但 `FetchResult` 与 `save_paper_markdown` 链路没有承接这些信息
   - 对比：TS 晚期实现已经把 asset hints、manifest、figures/tables 统计纳入保存摘要与 sidecar 文件
   - 影响：Python 版虽然保留了结构化全文入口，但图表/表格作为一等资产的那部分能力实际上还没有迁完

6. **`extract.tdm` 的按 publisher 配置能力缺失**
   - 证据：当前 Python `ExtractConfig` 只有 `fetch_strategy`，`_fetch_tdm()` 默认写死为 `Elsevier`、`Springer`
   - 对比：TS 配置示例与运行时都允许用户配置 `extract.tdm.order` / `extract.tdm.enabled`
   - 影响：这是配置层面的回退；虽然不一定阻断主流程，但少了对 publisher 优先级和开关的精细控制

7. **现有测试还没有守住这些回退点**
   - 证据：当前 smoke tests 只覆盖“6 个工具存在”“空库提示”“迁移会复制目录”“CLI 能跑”等基本路径，没有断言 resources/templates、正文 lexical fallback、迁移后正文检索、asset manifest 持久化与 richer saved-paper contract
   - 影响：如果不先补测试，后续发布很难判断这些差异是“有意简化”还是“遗漏回退”

**建议的收口顺序**：

1. 先决定 MCP 契约目标：是否恢复 TS 版的 resource + structuredContent 语义，还是接受 Python 版以工具文本为主
2. 无论契约目标如何，都应优先修复 `search_saved_papers` 的正文 fallback 与迁移后正文不可检索的问题，这是最直接的用户体验回退
3. 之后再决定 Elsevier/Springer 资产 sidecar 与 `extract.tdm` 是否属于 `v0.7.x` 必补项，还是明确降级到 Phase 5

### Phase 4.3: 方案确认后的逐项调研（2026-04-03）

#### 问题 1：MCP resources / resource templates

- **用户决策**：恢复该能力，但 URI 与语义不机械复刻 TS，而是统一到当前 Python 版已经存在的 canonical 约定上。
- **现状约束**：
  - Python 版 tool 已经稳定使用 `grados://papers/{safe_doi}` 作为 canonical paper URI。
  - `skills/grados/` 已把 `grados://papers/{safe_doi}` 当作 canonical deep-read resource 写入协议。
  - 当前真正缺的是“resource 层未实现”，而不是 URI 设计缺失。
- **调研结论**：
  - 这一项不需要大规模重做语义层，重点是把现有 Python 的 canonical paper URI 真正落成 MCP resource。
  - 相比一次性恢复所有 TS resource，优先级最高的是 saved-paper 相关资源，因为它们直接服务 Step 0 / Step 4 的阅读与验证链路。
- **具体计划**：
  1. 第一阶段仅恢复 Python 主线真正需要的两类资源：
     - `grados://papers/index`
     - `grados://papers/{safe_doi}`
  2. `grados://papers/{safe_doi}` 直接镜像已保存 Markdown 文件，确保它与 `read_saved_paper` 指向同一 canonical 内容源，而不是另一套派生摘要。
  3. `grados://papers/index` 采用 Python 当前 saved-paper 元数据字段，避免回退到 TS 的旧字段命名；若后续扩充结构化 contract，再同步扩展 index 字段。
  4. `grados://about` / `grados://status` / `grados://tools` 不作为首批阻塞项：
     - 若 FastMCP 实现成本很低，可顺手补上；
     - 若实现成本偏高，则延后到完成核心 paper resources 之后。
- **实施优先级**：高，但排在“本地论文检索体验修复”之后。

#### 问题 2：论文全文存储与搜索统一到 ChromaDB

- **用户决策**：Python 主线中，论文全文的存储与未来搜索都必须统一到 ChromaDB；不再保留“文件是主存储、Chroma 只是可选索引”的松耦合路线。
- **当前问题**：
  - 现实现状是 Markdown 文件保存在 `papers/`，ChromaDB 只是附带 chunk 索引。
  - `search_saved_papers` 在索引缺失时仍退回词法搜索，说明当前架构仍把“文件系统全文”当作第一真相源。
- **调研结论**：
  - 若要真正把 ChromaDB 作为核心能力，就应区分：
    1. **canonical research store**：ChromaDB 中的文档级与 chunk 级记录；
    2. **materialized mirror**：磁盘上的 Markdown 文件，供人类审计、导出和 resource 直读。
  - 也就是说，文件可以保留，但不应再成为搜索与深读能力成立的前提条件。
  - `extract_paper_full_text` 刚抓回 Markdown 时，仍应先经过一层统一格式化/规范化，再写入 ChromaDB；但不应把“structured summary”混进这个阶段的返回契约里，structured 内容应在问题 4 里单独设计。
- **具体计划**：
  1. 将存储拆成两个 Chroma collections：
     - `papers_docs`：每篇论文 1 条文档级记录，保存规范化后的完整 Markdown；
     - `papers_chunks`：面向检索的 chunk 级记录，保存分段文本与检索元数据。
  2. 规范化后的文档 schema 至少包含：
     - `doi`
     - `safe_doi`
     - `title`
     - `source`
     - `fetch_outcome`
     - `content_markdown`
     - `section_headings`
     - `content_hash` / `indexed_at`
  3. `extract_paper_full_text` / `parse_pdf_file` 保存成功的定义改为：
     - 文档级记录已写入 ChromaDB；
     - chunk 级索引已建立；
     - 如启用文件镜像，再写出 `papers/{safe_doi}.md`。
  4. `search_saved_papers` 未来只走 ChromaDB：
     - 正常路径查询 `papers_chunks`；
     - 结果聚合回论文级 hit；
     - 不再依赖当前这种“标题/DOI 词法兜底”。
  5. `read_saved_paper` 的长期目标也改为从 `papers_docs` 读取 canonical Markdown，再做 paragraph windowing；
     - 如果磁盘镜像存在，它只是一个与 `grados://papers/{safe_doi}` 对齐的可读副本，而不是唯一真相源。
  6. `grados update-db` 的定位需要调整：
     - 不再是“把现有主存储补索引”的核心命令；
     - 而是“重建/修复索引与镜像”的维护命令。
- **实施优先级**：很高。这一项决定后，问题 3 与问题 4 的入口设计都会更清晰。

#### 问题 3：迁移能力 vs 全新安装 / 本地 PDF 库导入

- **用户决策**：
  - 不把 TS → Python 迁移能力视为当前核心目标；
  - 当前重点改为“全新安装 + 导入用户已有 PDF 库 + 建立 ChromaDB 索引”。
- **调研结论**：
  - 既然迁移旧 TS 安装不是当前重点，那么此前审计中关于“迁移后正文检索回退”的问题不应继续主导首发设计。
  - 更符合产品方向的能力应该是：用户把已有 PDF 放进一个目录，GRaDOS 批量解析、规范化、写入 ChromaDB，并建立可自然语言检索的本地论文库。
  - 这一能力不应只放在 skill 层，因为真正的“批量导入 + 进度反馈 + 出错汇总”更适合先有一个确定的 CLI / MCP 原子入口，再由 skill 教模型何时使用。
- **具体计划**：
  1. 将 `migrate-config` 从当前主路线中降级：
     - 文档中不再把它列为推荐首发能力；
     - 后续可保留为兼容命令，或在 Python 主线稳定后单独移除。
  2. 设计新的本地导入主入口，优先级以 CLI 为主：
     - 候选命令：`grados import-pdfs --from /path/to/library`
     - 支持参数：
       - `--recursive`
       - `--glob "*.pdf"`
       - `--copy-to-library / --keep-in-place`
       - `--limit`
       - `--workers`
  3. CLI 导入命令的职责：
     - 扫描目录中的 PDF；
     - 逐篇调用现有解析流水线；
     - 规范化 Markdown；
     - 写入 ChromaDB 文档级与 chunk 级记录；
     - 输出导入成功 / 失败 / 跳过统计。
  4. 在 CLI 之外，再补一个面向 agent 的 MCP tool：
     - 候选名称：`import_local_pdf_library`
     - 适用于用户明确要求“把这个目录里的 PDF 纳入 GRaDOS 本地库”时由模型调用；
     - 返回批处理摘要，而不是返回全文内容。
  5. skill 层的职责不是替代导入工具，而是教模型：
     - 当用户已有本地 PDF 目录时，优先调用 `import_local_pdf_library`；
     - 若客户端不适合长任务，则建议用户先运行 CLI，再继续检索/综述流程。
- **实施优先级**：高，但在顺序上排在问题 2 之后，因为本地 PDF 导入的目标存储模型必须先定为 ChromaDB-first。

#### 问题 4：structured content 是否与原文读取分离

- **用户决策**：
  - 倾向于把“原文文本读取”和“structured content”拆成不同入口；
  - 让 skill 教模型按需选择，减少 token 浪费。
- **调研结论**：
  - 不应把 structured content 混进 `extract_paper_full_text` 的主返回契约里。
    - `extract_paper_full_text` 的职责应是“获取全文并入库”；
    - 它最多返回一个紧凑 receipt，而不承担阅读与分析接口的职责。
  - 也不建议把 structured content 定义为“由服务器调用 LLM 现算摘要”，因为这会引入模型依赖、重复成本和不可预测性。
  - 更合理的做法是新增一个**确定性结构化读取工具**，从已保存的 canonical Markdown / Chroma 文档中抽取：
    - 标题
    - DOI / canonical URI
    - abstract / preview
    - section headings / outline
    - figures / tables 引用概览（若资产层已实现）
    - 字数、段落数、可读窗口信息
  - 这样结构化 tool 负责“低 token 导航”，`read_saved_paper` 负责“引用前的深读验证”。
- **具体计划**：
  1. 保持现有三类职责分离：
     - `extract_paper_full_text`：获取并入库；
     - `search_saved_papers`：检索并粗筛；
     - `read_saved_paper`：深读与 citation verification。
  2. 新增一个结构化阅读 tool，候选命名：
     - `get_saved_paper_structure`
     - 或 `summarize_saved_paper_structure`
  3. 该 tool 的输出应是确定性结构，不依赖 LLM，总体字段建议包括：
     - `doi`
     - `safe_doi`
     - `canonical_uri`
     - `title`
     - `preview_excerpt`
     - `section_headings`
     - `section_outline`
     - `word_count`
     - `paragraph_count`
     - `assets_summary`
  4. skill 协议应更新为：
     - Step 0/2/3 之后，若只需要判断哪些论文值得精读，可先调用结构化 tool；
     - 只有进入最终综述、对比或引文核实时，才调用 `read_saved_paper` 读取正文窗口。
  5. 若实现资源层，则 `grados://papers/{safe_doi}` 仍是 canonical 原文资源；
     - structured content 不应覆盖或替代它，而应作为并行的轻量入口。
- **实施优先级**：中高。它依赖问题 2 的 canonical 存储模型，但可先于复杂资产系统落地。

#### 问题 5：figures / tables / asset linkage

- **用户决策**：
  - 先核对旧 TS 行为与官方 API 说明；
  - 目标不是单纯“把图片都下载下来”，而是保证图表资源能与正确论文和正文上下文稳定关联。
- **旧 TS 行为调研**：
  - `status.md` 已明确记录：Elsevier `XML/JSON` 中可拿到 paragraphs、figures、tables、object/resource URLs。
  - 归档仓库 `STSNaive/GRaDOS-legacy` 的晚期 TS 实现中，`PaperSavedSummary` 已包含：
    - `assets_count`
    - `figures_count`
    - `tables_count`
    - `assets_directory_relative_path`
    - `assets_manifest_relative_path`
  - 同一实现里，保存全文时会把 `assetHints` 传入保存链路，并返回 `assetRecords` / `assetsManifestPath`，这说明旧版确实已经把图表 sidecar 作为已保存论文的一部分来处理。
  - 这和用户此前看到“下载目录下有对应图片子文件夹”的现象是一致的；可以合理推断 TS 版晚期已具备“按论文落图表 sidecar”的能力。
- **官方 API / 条款调研结论**：
  - Elsevier 官方文档明确说明：
    - 文章附件应通过 **Object Retrieval API** 的 `META` 视图先取 attachment metadata；
    - 响应里会给出每个 attachment 的 `prism:url`、`ref`、`filename`、`mimetype`、尺寸与类型；
    - 随后可根据这些 URL 获取具体图像对象。
  - 官方文档还明确区分了 `IMAGE-THUMBNAIL`、`IMAGE-DOWNSAMPLED`、`IMAGE-HIGH-RES` 三类图像视图。
  - Springer 官方条款则明确提醒：若要把 images 作为 TDM Output 的一部分使用，需要额外联系版权方；也就是说，Springer 侧至少不能把“自动导出图片给第三方”当作默认安全路径。
- **设计含义**：
  - Python 版这部分不应只做“二进制图片缓存”，而应先把 asset 设计成**可追溯对象**：
    - 先有 manifest；
    - 再决定是否下载 binary；
    - 最后决定哪些信息进入检索层。
  - Elsevier 与 Springer 也不应完全同策略：
    - Elsevier 可以把 Object Retrieval 作为结构化图片获取主路径；
    - Springer 首发阶段应更保守，以 refs/captions/sections 级关联为主。
- **具体计划**：
  1. 定义跨 publisher 的 asset manifest schema，至少包含：
     - `paper_safe_doi`
     - `asset_id` / `ref`
     - `kind` (`figure` / `table` / `graphical_abstract` / `supplementary_material`)
     - `caption`
     - `source_url`
     - `mime_type`
     - `publisher`
     - `anchor_section`
     - `nearby_text`
     - `binary_status`
     - `local_path`（若已下载）
  2. 首发目标先做“manifest-first”：
     - 优先把图表与正文的关联信息保存下来；
     - 不把“全部二进制图像都下载完”作为首个阻塞条件。
  3. 在索引层，将 asset 以“文本可检索线索”接入，而不是直接向量化图像本身：
     - caption
     - figure/table label
     - 所在 section
     - 附近段落文本
  4. 对 Elsevier：
     - 依据 FULL JSON/XML 中的 figure/table/object URL 与 Object Retrieval META 返回的 attachment metadata 生成 manifest；
     - binary 下载可作为第二阶段或按配置启用。
  5. 对 Springer：
     - 首发先只保留结构化引用关系与 caption/section 锚定；
     - 二进制图片下载默认关闭，避免越过条款边界。
- **实施优先级**：中等。建议在问题 2、4、6 之后推进，但在真正发布“结构化论文库”前补上 manifest-first 版本。

#### 问题 6：恢复 `extract.tdm` 的按 publisher 配置能力

- **用户决策**：恢复该能力。
- **现状问题**：
  - 当前 Python `fetch_paper()` 已经保留了 `tdm_order` 参数位，但配置模型里没有 `extract.tdm`；
  - `_fetch_tdm()` 直接硬编码为 `Elsevier -> Springer`，导致配置层与执行层脱节。
- **调研结论**：
  - 这是一个典型的“实现留了接口，配置层没跟上”的问题；
  - 相比其它问题，这一项实现成本低、收益明确，适合尽快补齐。
- **具体计划**：
  1. 在 Python 配置模型中恢复 `extract.tdm`：
     - `order`
     - `enabled`
  2. 在默认配置生成与示例文档中同步加入该段，默认仍保持：
     - `order = ["Elsevier", "Springer"]`
     - `enabled = {"Elsevier": true, "Springer": true}`
  3. 调整执行层接口：
     - `fetch_paper()` 接收 `tdm_order` 与 `tdm_enabled`
     - `_fetch_tdm()` 在循环 publisher 时同时检查顺序与 enabled map
  4. 行为约定：
     - 若某 publisher 被禁用，直接跳过；
     - 若顺序表为空，则视为 TDM 阶段无可用 publisher；
     - 若某 publisher 缺少对应 API key，不报硬错误，只记录 warning 并继续后续策略。
  5. 文档层明确：
     - `fetch_strategy` 决定阶段顺序；
     - `tdm` 决定 TDM 阶段内部的 publisher 顺序和开关。
- **实施优先级**：高。建议在问题 2 定稿后尽快补上，因为它不依赖更大的存储改造。

#### 问题 7：测试策略只覆盖最终保留方案

- **用户决策**：只为最终决定保留和实现的能力补测试，不为已放弃的历史兼容路径补测试。
- **调研结论**：
  - 既然“TS 迁移兼容”已从当前主线目标中降级，那么围绕 `migrate-config` 的增强测试不应继续扩张。
  - 测试资源应该集中到新的 Python 主线能力：ChromaDB-first、本地 PDF 导入、canonical resources、structured read、asset linkage、`extract.tdm` 配置。
- **具体计划**：
  1. 测试矩阵改为围绕最终保留的 5 组能力：
     - paper resources
     - ChromaDB-first 存储与检索
     - 本地 PDF 库导入
     - structured content 轻量入口
     - asset manifest / linkage
     - `extract.tdm` 配置
  2. 不再把“迁移后未 rebuild 也能正文检索”作为首发必须测试项；对应迁移测试后续可保留基本 smoke，或在 deprecate/removal 阶段一并清理。
  3. 新增测试建议：
     - `test_paper_resources.py`
     - `test_import_local_pdfs.py`
     - `test_saved_paper_structure.py`
     - `test_asset_manifest.py`
     - `test_tdm_config.py`
  4. 现有 `test_search_smoke.py` / `test_storage_smoke.py` 应升级为：
     - 断言保存/导入成功后 ChromaDB 一定有文档级与 chunk 级记录；
     - 不再依赖“标题/DOI 词法 fallback 也许能兜住”。
  5. skill / README 中承诺的行为，必须对应至少一个回归测试，避免文档先行漂移。
- **实施优先级**：高，但应跟随各功能项落地，不宜先于设计定稿。

### Phase 4.4: 具体落地方案（2026-04-03）

- **当前生效的最终优先级**：
  1. canonical 原文质量
  2. 深读能力
  3. structured navigation
  4. 写作工作流编排与引用核实
  5. 基础检索
  6. reranking
  7. 独立 lexical ranker / BM25（仅在评测证明必要时再引入）
- **首发非目标**：
  - 不以 TS 迁移兼容为核心目标
  - 不在首发阶段引入独立 SQLite FTS / BM25 数据库
  - 不在服务器端加入 LLM 摘要/写作 tool
  - 不以“下载所有二进制图片资源”作为首个阻塞条件

#### 实施包 A：Canonical Paper Layer（P0，已完成 2026-04-03）

- **目标**：把“论文真相源”先做稳，确保后续读、写、核实都建立在高质量 canonical 原文上。
- **状态**：已完成。
- **本轮完成项**：
  1. 新建并启用 Chroma canonical collections：
     - `papers_docs`
     - `papers_chunks`
  2. 落实 canonical paper schema 并持久化：
     - `doi`
     - `safe_doi`
     - `title`
     - `authors`
     - `year`
     - `journal`
     - `source`
     - `fetch_outcome`
     - `content_markdown`
     - `section_headings`
     - `assets_manifest_path`
     - `content_hash`
     - `indexed_at`
  3. `save_paper_markdown` 调整为 canonical-first：
     - Chroma 中保存 canonical doc 与 retrieval chunks
     - Markdown 文件降级为可选 mirror
  4. `read_saved_paper` / `read_paper` 优先从 `papers_docs` 读取，mirror 缺失时仍可深读
  5. `list_saved_papers` 优先列出 canonical docs，避免只依赖 mirror 文件
- **验收记录（2026-04-03）**：
  - `.venv/bin/pytest tests/test_storage_smoke.py tests/test_server_smoke.py -q` 通过（`5 passed`）
  - `.venv/bin/pytest tests/test_storage_smoke.py tests/test_server_smoke.py tests/test_cli_smoke.py -q` 通过（`7 passed, 7 warnings`）
- **备注**：
  - 本包完成的是 canonical storage / read path；检索策略增强仍在实施包 E
- **交付物**：
  1. 定义 canonical paper schema：
     - `doi`
     - `safe_doi`
     - `title`
     - `authors`
     - `year`
     - `journal`
     - `source`
     - `fetch_outcome`
     - `content_markdown`
     - `section_headings`
     - `assets_manifest_path`
     - `content_hash`
     - `indexed_at`
  2. Chroma 内部拆分为：
     - `papers_docs`
     - `papers_chunks`
  3. `extract_paper_full_text` / `parse_pdf_file` 的保存成功语义统一为：
     - 规范化 Markdown 已入 `papers_docs`
     - 检索 chunk 已入 `papers_chunks`
     - 若启用 mirror，则 `papers/{safe_doi}.md` 已写出
  4. `read_saved_paper` 优先从 `papers_docs` 读取，再做 paragraph windowing。
- **验收标准**：
  - 在 mirror 文件暂时不存在时，`read_saved_paper` 仍能读取已保存论文
  - 同一 DOI 二次入库会基于 `content_hash` 去重或替换，不产生脏重复
  - 抽取和本地导入共用同一 canonical schema

#### 实施包 B：Read / Structure Layer（P0，已完成 2026-04-03）

- **目标**：让模型先低 token 定位，再决定是否深读。
- **状态**：已完成。
- **本轮完成项**：
  1. 恢复 paper MCP resources：
     - `grados://papers/index`
     - `grados://papers/{safe_doi}`
  2. 新增确定性结构化导航 tool：
     - `get_saved_paper_structure`
  3. 将 `grados://papers/{safe_doi}` 定义为低 token overview resource，而不是正文全文读取接口
  4. 明确契约分工：
     - `get_saved_paper_structure` 负责 paper card / section outline / preview
     - `read_saved_paper` 继续作为 canonical deep-read 路径
- **验收记录（2026-04-03）**：
  - `.venv/bin/pytest tests/test_storage_smoke.py tests/test_server_smoke.py -q` 通过（`8 passed`）
  - `.venv/bin/pytest tests/test_storage_smoke.py tests/test_server_smoke.py tests/test_cli_smoke.py -q` 通过（`10 passed, 7 warnings`）
- **交付物**：
  1. 恢复 MCP paper resources：
     - `grados://papers/index`
     - `grados://papers/{safe_doi}`
  2. 新增确定性结构化导航 tool：
     - 候选名称：`get_saved_paper_structure`
  3. 结构化输出至少包含：
     - `canonical_uri`
     - `title`
     - `preview_excerpt`
     - `section_headings`
     - `section_outline`
     - `word_count`
     - `paragraph_count`
     - `assets_summary`
  4. `extract_paper_full_text` 返回紧凑 receipt，不承担深读/综述职责。
- **验收标准**：
  - 模型可以仅用 structure tool 判断“哪几篇值得精读”
  - `read_saved_paper` 继续作为引用前唯一 canonical deep-read 路径

#### 实施包 C：Local Ingest Layer（P0，已完成 2026-04-03）

- **目标**：把“导入已有 PDF 库”变成当前主入口，替代迁移旧 TS 安装。
- **状态**：已完成。
- **本轮完成项**：
  1. 新增 CLI：
     - `grados import-pdfs --from /path/to/library`
  2. 新增 MCP tool：
     - `import_local_pdf_library`
  3. 支持参数：
     - `--recursive`
     - `--glob`
     - `--copy-to-library / --keep-in-place`
  4. 导入逻辑支持：
     - DOI 自动推断
     - DOI 缺失时回退到 `local-pdf/{sha16}` 标识
     - 基于内容 hash 的批内重复文件跳过
     - 基于 canonical identifier 的重复导入跳过
  5. 导入结果输出：
     - 扫描数 / 导入数 / 跳过数 / 失败数
     - 逐文件状态摘要
- **验收记录（2026-04-03）**：
  - `.venv/bin/pytest tests/test_import_smoke.py tests/test_server_smoke.py tests/test_cli_smoke.py -q` 通过（`11 passed, 7 warnings`）
  - `.venv/bin/pytest tests/test_import_smoke.py tests/test_storage_smoke.py tests/test_server_smoke.py tests/test_cli_smoke.py -q` 通过（`13 passed, 7 warnings`）
- **交付物**：
  1. 新 CLI：
     - `grados import-pdfs --from /path/to/library`
  2. 支持参数：
     - `--recursive`
     - `--glob`
     - `--copy-to-library / --keep-in-place`
     - `--limit`
     - `--workers`
  3. 新 MCP tool：
     - `import_local_pdf_library`
  4. 导入链路：
     - 扫描 PDF
     - 调用解析流水线
     - 写 canonical docs/chunks
     - 输出成功/失败/跳过摘要
  5. `migrate-config` 降级为兼容命令，不再作为 README 首发主路径。
- **验收标准**：
  - 用户给定一个 PDF 目录后，无需理解旧 TS 布局即可建立论文库
  - 导入过程可重复运行，并能识别重复文件/重复 DOI

#### 实施包 D：Writing / Verification Layer（P1，已完成 2026-04-03）

- **目标**：把 GRaDOS 从“能找论文”推进到“能支撑写论文”。
- **状态**：已完成。
- **本轮完成项**：
  1. skill 协议升级为 citation-aware writing workflow：
     - 检索 / 筛选
     - structure 导航
     - deep read
     - 生成带引用文本
     - 回到原文核实
  2. `grados://papers/{safe_doi}` 的定位已改为 low-token overview resource，不再与 `read_saved_paper` 混淆
  3. README / skill 已明确：
     - 不从 compact summary 或 overview resource 直接生成最终学术结论
     - 所有引用必须回读 canonical 原文
     - 本地 PDF 库可先导入再进入写作工作流
  4. 首发阶段保持“写作由 skill 编排，不新增服务器端写作 tool”
- **验收记录（2026-04-03）**：
  - 已在 `skills/grados/SKILL.md` 写入 evidence grid、citation-aware protocol 与 double-check 流程
  - 已在 `skills/grados/references/tools.md` 更新 `get_saved_paper_structure` / `import_local_pdf_library` / paper resources 契约
  - 已在 `README.md` 与 `README.zh-CN.md` 增补 import + structure + deep-read + citation verification 的推荐工作流
- **交付物**：
  1. skill 协议升级为 citation-aware writing workflow：
     - 检索/筛选
     - structure 导航
     - deep read
     - 生成带引用文本
     - 回到原文核实
  2. README / skill 中明确：
     - 不从 compact summary 直接生成最终学术结论
     - 所有引用必须回读 canonical 原文
  3. 首发阶段保持“写作由 skill 编排，不新增服务器端写作 tool”。
- **验收标准**：
  - 同一篇论文在被引用前，必须经过 `read_saved_paper`
  - 综述工作流可以在不依赖临时上下文记忆的前提下重复执行

#### 实施包 E：Retrieval Baseline（P1，已完成 2026-04-03）

- **目标**：满足论文综述场景的实际查询需求，但不过度设计检索系统。
- **状态**：已完成。
- **本轮完成项**：
  1. `search_saved_papers` 已重构为：
     - metadata prefilter
     - `where_document` 术语/短语约束
     - dense retrieval
     - 论文级聚合
  2. tool schema 已扩展可选过滤条件：
     - `doi`
     - `authors`
     - `year_from`
     - `year_to`
     - `journal`
     - `source`
  3. 首版 reranking 已按“dense score + lexical score”轻量启发式落地
  4. 继续维持 Chroma-only 单库路线，不引入独立 SQLite FTS/BM25
- **验收记录（2026-04-03）**：
  - `tests/test_search_smoke.py` 已覆盖 metadata filter、document-level lexical fallback、hybrid rerank
  - `tests/test_server_smoke.py` 已覆盖 `search_saved_papers` 的 filters / hybrid 输出契约
  - `.venv/bin/pytest tests/test_search_smoke.py tests/test_server_smoke.py -q` 通过（`13 passed`）
- **交付物**：
  1. `search_saved_papers` 重构为：
     - metadata prefilter
     - `where_document` 术语/短语约束
     - dense retrieval
     - 论文级聚合
  2. tool schema 扩展可选过滤条件：
     - `doi`
     - `authors`
     - `year_from`
     - `year_to`
     - `journal`
     - `source`
  3. reranking 作为可选二阶段精排：
     - 首版可先用轻量启发式 + dense score
     - 若评测证明必要，再上独立 cross-encoder reranker
  4. 首发阶段不引入独立 SQLite FTS/BM25 数据库。
- **验收标准**：
  - 概念性查询能召回相关论文
  - DOI / 作者 / 年份等精确约束可以仅通过 metadata filter 实现
  - 不依赖旧式 lexical fallback 才能得到可用结果

#### 实施包 F：Assets / Publisher Config / Tests（P1-P2，已完成 2026-04-03）

- **目标**：把结构化资产与 publisher 配置补齐，并用测试锁住行为。
- **状态**：已完成。
- **本轮完成项**：
  1. manifest-first 资产模型已落地：
     - `save_asset_manifest`
     - `papers/_assets/{safe_doi}.json`
     - structure card / resource 可回读 figures / tables / objects 计数
  2. `extract.tdm.order` / `extract.tdm.enabled` 已恢复
  3. publisher 资产线索已打通：
     - Elsevier asset hints
     - Springer asset hints
     - fetch 结果透传到 extraction 保存链路
  4. 回归测试矩阵已补齐到当前首发范围
- **验收记录（2026-04-03）**：
  - `.venv/bin/pytest tests/test_fetch_smoke.py tests/test_storage_smoke.py tests/test_server_smoke.py -q` 通过（`14 passed`）
  - `.venv/bin/pytest tests -q` 通过（`30 passed, 7 warnings`）
- **交付物**：
  1. manifest-first 资产模型
  2. `extract.tdm.order` / `extract.tdm.enabled`
  3. 回归测试矩阵：
     - `test_paper_resources.py`
     - `test_import_local_pdfs.py`
     - `test_saved_paper_structure.py`
     - `test_asset_manifest.py`
     - `test_tdm_config.py`
  4. 将现有 smoke tests 升级为 canonical-first 断言。
- **验收标准**：
  - asset caption / section / local_path 可以稳定追溯
  - TDM publisher 顺序和开关可以通过配置控制
  - 文档里承诺的主行为至少各有 1 个回归测试

#### 建议执行顺序

1. 实施包 A：Canonical Paper Layer
2. 实施包 B：Read / Structure Layer
3. 实施包 C：Local Ingest Layer
4. 实施包 D：Writing / Verification Layer
5. 实施包 E：Retrieval Baseline
6. 实施包 F：Assets / Publisher Config / Tests

#### 决策后续门槛

- **是否引入独立 lexical ranker / BM25**：仅在以下条件满足至少两条时再启动：
  - metadata filter + dense retrieval + reranking 评测明显不足
  - 术语密集查询、section heading 检索或图表标签检索持续失真
  - 本地论文库规模增长后，精确检索成为稳定瓶颈

### Phase 5: 增强（迁移完成后）

- [ ] 独立 lexical ranker / BM25（仅在评测证明 Chroma-only baseline 不足时）
- [ ] 更强的 reranker / cross-encoder 精排
- [ ] citation verification helper（如 skill-only 编排后仍显著增加人工/模型负担）
- [ ] agentic 多跳检索与跨论文比较增强
- [ ] Elsevier XML/JSON vs PDF 质量 A/B 对比
- [ ] 图表/表格作为一等资产
- [ ] 更丰富的 Zotero 工具（借鉴 zotero-mcp：标注搜索、笔记管理）
- [ ] 多 embedding 模型选项（OpenAI / Gemini）
- [ ] scite 引用情报集成

---

## 六、关键设计决策汇总

| 决策 | 选择 | 理由 |
|------|------|------|
| 数据根目录 | `~/GRaDOS/`（非隐藏） | 用户要求直觉化、非隐蔽；Finder/资源管理器直接可见 |
| 基础包含 pymupdf4llm | 是 | PDF 解析是核心功能，20MB 轻量级，没有它 extract 无法工作 |
| 基础包含 patchright | 是 | 浏览器自动化是 GRaDOS 核心差异，不含它无法获取付费论文 |
| `all` 不含 marker/docling | 是 | 它们各自 500MB-3GB，会严重拖慢 `pip install`；用 `full` 覆盖 |
| Elsevier XML/JSON 首选 | 是 | status.md 确认结构化全文可用，质量优于 PDF 重解析 |
| ChromaDB 放入基础包 | 是 | 语义搜索是核心能力；进程内集成消除 mcp-local-rag；自带 ONNX 嵌入无需 PyTorch |
| 首发检索后端 | Chroma-only managed backend | 先满足真实写作需求；独立 lexical DB 仅在评测显示必要时再引入 |
| 首发写作能力 | 以 skill 编排为主，不新增服务器端写作 tool | 当前优先级是 canonical 读、结构化导航和引用核实，而不是把生成逻辑固化在服务端 |
| 迁移期 `python/` 子目录 | 已完成收口 | 早期用于双版本并行；当前主仓库已切到根目录 Python 布局 |
| CLI 用 Click + Rich | 是 | 成熟生态，setup 向导/status 表格/paths 输出都需要美化 |
| Python ≥ 3.11 | 是 | ExceptionGroup、tomllib 内置、性能提升；3.10 已 EOL 2026-10 |
| uv 作为首选安装器 | 是 | 10-100x 快于 pip，自动隔离环境，`uvx` 零安装运行适配 MCP 客户端 |
| 构建后端用 hatchling | 是 | 轻量、标准兼容，uv/pip 均支持；pyproject.toml 中 `[tool.uv]` 补充 uv 专属配置 |
| 历史文档暂不删除 | 是 | 迁移期优先保留审计痕迹和旧测试思路，但必须在文档顶部明确“历史参考”状态 |

---

## 七、风险与对策

| 风险 | 对策 |
|------|------|
| pymupdf4llm AGPL 许可 | GRaDOS 本身开源兼容；如需更宽松许可，Docling (MIT) 可替代默认后端 |
| ~6K LoC 重写周期 | 逐工具移植，两版本并行，Phase 0-1 优先验证核心路径 |
| ScienceDirect 状态机精密 | Python Patchright API 与 TS 版高度一致，逻辑可直译 |
| `~/GRaDOS/` 可能被用户认为侵入性 | `GRADOS_HOME` 环境变量可自定义；`grados setup` 首次运行时确认路径 |
| marker-pdf 依赖 PyTorch | 隔离在 `marker` extra，不影响基础包和 `all` |
| 过早引入复杂检索栈导致路线过重 | 首发阶段坚持 Chroma-only baseline；先把 canonical、deep read、writing workflow 做稳，再用评测决定是否需要独立 lexical ranker |
| AI 写作时引用漂移或幻觉 | 以 canonical read + structure navigation + citation-aware skill 作为主防线，所有最终引用都必须回到原文核实 |

---

## 八、验证方案

每个 Phase 完成后的端到端验证：

**Phase 0**：
```bash
uv sync --all-extras              # 安装所有依赖到 .venv/
uv run grados version             # 验证 CLI 入口
uv run grados setup               # 验证向导流程
uv run grados status              # 验证状态检查
uv run grados paths               # 验证路径输出
```

**Phase 1**：
```bash
# MCP 工具调用测试（通过 Claude Code 或 MCP Inspector）
search_academic_papers("composite structures vibration")
extract_paper_full_text("10.1016/j.compstruct.2021.114178")
```

**Phase 2**：
```bash
uv run grados setup --with browser
# 通过 MCP 调用测试付费论文提取
extract_paper_full_text("10.1016/j.compstruct.2020.112569")  # non-OA
```

**Phase 3**：
```bash
uv run grados update-db
search_saved_papers("vibration analysis composite")
```

**Phase 4**：
```bash
# 模拟终端用户安装（从 PyPI）
uv tool install "grados[all]"
grados setup --all
grados status                    # 全绿

# 验证 MCP 客户端零安装模式
uvx "grados[all]" version
```

**Phase 4.4（首发工作流收口）**：
```bash
# 1. 导入已有 PDF 库
grados import-pdfs --from /path/to/papers --recursive

# 2. 通过 MCP/Inspector 验证 paper resources
#    list_resources -> grados://papers/index / grados://papers/{safe_doi}

# 3. 结构化导航 + 深读
search_saved_papers("composite vibration damping", limit=5)
get_saved_paper_structure(safe_doi="10_1016_j_compstruct_2021_114178")
read_saved_paper(safe_doi="10_1016_j_compstruct_2021_114178", section_query="discussion")

# 4. skill 工作流验收
#    使用 GRaDOS skill 生成一段带内联引用的综述文字；
#    对每条引用再次调用 read_saved_paper 回查原文支持段落
```

---

## 九、实施审计与构建修复（2026-04-05）

本节记录对 Python 化后仓库的两轮检查：(1) 文档 vs 实际代码的功能对齐审计；(2) 构建产物与工具链的质量审计。所有发现的问题已在本轮全部修复。

### 9.1 功能对齐审计

逐功能块对比文档（§1–§8）与当前代码的对齐情况。

| 功能块 | 状态 | 说明 |
|--------|------|------|
| 安装体验与依赖分层 | ✅ | pyproject.toml 结构与文档一致；`ocr` extra 已按 Phase 0 偏差从 `pymupdf4llm[ocr]` 改为 `pytesseract` |
| 文件目录与配置系统 | ✅ | GRaDOSPaths、Pydantic v2 配置模型体系、TDMConfig 均完整落地 |
| CLI 命令 | ✅ | 8 个入口（文档 §1.3 蓝图仅列 6 个，但 Phase 4.3/4.4 中已补齐 `import-pdfs`、`migrate-config`） |
| MCP 工具与资源 | ✅ | 8 工具 + 1 resource + 1 template，与实施包 A-F 交付物完全一致 |
| 学术搜索 | ✅ | 5 源 + resumable token + DOI 去重 + 源级疲尽跟踪 |
| 全文提取管线 | ✅ | TDM → OA → Sci-Hub → Headless 瀑布 + `extract.tdm` 配置化 + asset_hints 透传 |
| 浏览器自动化 | ✅ | manager + sciencedirect + generic 三模块；TS 适配简化均合理 |
| 存储层 | ✅ | canonical-first `papers_docs`/`papers_chunks` + Markdown mirror + hybrid 检索 |
| 本地 PDF 导入 | ✅ | `importing.py` + CLI + MCP tool；`--limit`/`--workers` CLI flag 未暴露（非阻塞） |
| 测试覆盖 | ✅ | 9 文件 30 函数；实施包 F 建议的 5 个独立文件合并到现有 smoke tests 中 |

#### 模块结构偏差

文档 §4 的模块树为规划阶段蓝图，实施阶段做了合理简化：

| 文档规划模块 | 实际位置 | 原因 |
|-------------|---------|------|
| `search/saved.py` | `storage/vector.py` + `server.py` | 搜索与存储天然耦合 |
| `extract/elsevier_xml.py` | `publisher/elsevier.py` | XML 摄入与 publisher API 同属一个领域 |
| `setup/browser.py` / `models.py` / `extensions.py` | `cli.py` 内联 | cli.py ~350 行尚可维护 |
| `importing.py` | `src/grados/importing.py` | Phase 4.3 后新增，文档 §4 未列出 |

所有规划功能均已实现，无功能缺失。

### 9.2 构建质量审计与修复

审计发现 7 个问题，已全部修复。

#### 9.2.1 sdist 包含不应发布的文件 ✅

pyproject.toml 缺少 `[tool.hatch.build.targets.sdist]` exclude 规则，导致 `.uv-cache/`（泄漏本地环境）、`marker-worker/`、工程文档、`skills/`、CI 配置等全部打入 sdist（68 文件）。

**修复**：添加 sdist exclude 规则，sdist 降至 43 个文件。

#### 9.2.2 mypy strict 42 个类型错误 ✅

pyproject.toml 声明 `strict = true` 但 11 个源文件共 42 个错误。主要类别：`server.py` 缺返回类型（~12 个）、BeautifulSoup `tag["attr"]` 返回 `str | list`（~8 个）、ChromaDB 返回 `Any`（~5 个）、泛型参数缺失（~9 个）。

**修复**：逐文件补齐类型注解、`str()` 转换、泛型参数。对第三方无类型 API（pymupdf、Playwright ViewportSize）使用针对性 `type: ignore`。另将 `resumable.py` 中 `__dataclass_fields__` 替换为标准 `dataclasses.fields()`。

#### 9.2.3 dev 依赖重复声明 ✅

`dev` 同时出现在 `[project.optional-dependencies]` 和 `[dependency-groups]`。

**修复**：删除 `[project.optional-dependencies] dev`，统一到 `[dependency-groups] dev`。

#### 9.2.4 缺少 py.typed ✅

缺少 PEP 561 标记文件，下游 mypy/pyright 无法识别 inline 类型。

**修复**：创建 `src/grados/py.typed`，已纳入 wheel。

#### 9.2.5 缺少 `__all__` ✅

26 个源文件无一定义 `__all__`，公共 API 边界不明确。

**修复**：在 `__init__.py`、`config.py`、`server.py` 三个关键对外模块添加声明。

#### 9.2.6 wheel 构建 ✅

无问题。wheel 仅含 `grados/` 包代码 + dist-info（32 条目），入口点正常，无 dev 泄漏。

#### 9.2.7 测试 DeprecationWarning ✅

ChromaDB 传递依赖（ONNX Runtime SWIG 绑定）产生 7 个 `SwigPyPacked has no __module__` 警告。

**修复**：在 `[tool.pytest.ini_options]` 添加 filterwarnings。

### 9.3 修复涉及的文件

| 文件 | 修改类型 |
|------|---------|
| `pyproject.toml` | sdist exclude · 删除重复 dev extra · pytest filterwarnings |
| `src/grados/py.typed` | 新建（PEP 561 标记） |
| `src/grados/__init__.py` | `__all__` |
| `src/grados/config.py` | `__all__` |
| `src/grados/server.py` | 返回类型注解 · `__all__` · 类型安全修正 |
| `src/grados/publisher/elsevier.py` | BS4 attrs `str()` 转换（8 处） |
| `src/grados/publisher/common.py` | 泛型类型参数 |
| `src/grados/extract/fetch.py` | BS4 attrs `str()` 转换（3 处） |
| `src/grados/extract/parse.py` | pymupdf `type: ignore` |
| `src/grados/search/resumable.py` | `dataclasses.fields()` 替代 `__dataclass_fields__` |
| `src/grados/storage/vector.py` | 消除 Any 返回 · 变量注解 |
| `src/grados/storage/papers.py` | 变量注解 |
| `src/grados/browser/sciencedirect.py` | 泛型类型参数 |
| `src/grados/browser/generic.py` | 泛型类型参数 |
| `src/grados/browser/manager.py` | Playwright ViewportSize `type: ignore` |

### 9.4 当前状态总结

| 维度 | 状态 |
|------|------|
| 功能完成度 | ✅ 8 工具 + 2 resource + 8 CLI 命令，Phase 0–4.4 全部落地 |
| `uv build` | ✅ sdist 43 文件（clean）+ wheel 32 条目 |
| `pytest tests -q` | ✅ 30 passed, 0 warnings |
| `mypy --strict` | ✅ 0 errors in 26 files |
| `ruff check` | ✅ All checks passed |
| PyPI 发布 | ❌ 本地验证通过，远程验收待 PyPI 上线后执行 |

**唯一硬阻塞项**：PyPI 真正上线后的远程 `uvx "grados[all]" version` 验收。

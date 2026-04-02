# GRaDOS Python 化实施计划

## Document Status

- **本文件**：Python 化的权威工程计划、设计决策和完成度台账
- **`TODO.md`**：从本文件提炼出的当前执行快照
- **`grados-python-migration-plan.md`**：更早期的设计草案；其中安装设计、模块拆分、风险分析已整合进本文件，保留作历史参考
- **`status.md`**：Python 化前的 Elsevier / 浏览器工程日志；关键结论已整合进本文件的 Phase 1 / Phase 2 / 风险部分，保留作背景证据
- **`python/README.md`**：本地开发快捷入口；用户向内容已整合进根 README
- **`docs/claude-code-plugin-guide.md` / `docs/global-install-guide.md`**：旧 Node.js / mcp-local-rag 时代的运维文档；当前权威替代文档为根 README、MIGRATION、`.mcp.json` 与 `skills/grados/`

## Context

GRaDOS 当前是 TypeScript MCP 服务器（~6K LoC），依赖 Node.js + Python 双运行时，安装流程繁琐（5 步、~5 GB）。历史文档 `status.md` 与 `grados-python-migration-plan.md` 已确认 Python 迁移方向；本文件在此基础上给出当前仍然有效的实现设计与完成状态。

1. **安装体验对标/超越 zotero-mcp** — 以 `uv` 为首选安装器，一条命令 + 可选参数，`all` 安装全部推荐依赖
2. **文件目录直觉化** — 安装后所有依赖、运行时资产都在显眼、非隐藏的文件夹中

---

## 一、安装体验设计

### 1.1 依赖分层：基础包 vs 可选 extras

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
version = "0.7.0"
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
$ uv run grados version  → GRaDOS 0.7.0 / fastmcp 3.2.0 / chromadb 1.5.5
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
- [ ] 恢复 MCP resources / resource templates 能力
  - TS 原版对外暴露了 `grados://about`、`grados://status`、`grados://tools`、`grados://papers/index` 与 `grados://papers/{safe_doi}`
  - 2026-04-03 运行时审计中，`await mcp.list_resources()` 与 `await mcp.list_resource_templates()` 均返回空列表
- [ ] 恢复 `search_saved_papers` 的正文/章节关键词 fallback 能力
  - 当前 Python fallback 仅匹配标题与 DOI；正文中存在命中词但标题不含关键词时会漏检
  - TS 原版 lexical fallback 可匹配 Markdown 正文、标题、章节标题并返回 snippet / matched sections
- [ ] 关闭“迁移后必须手动 `grados update-db` 才能按内容搜索”的回退
  - 当前 `migrate_legacy_install` 只复制论文和运行时资产，不重建 ChromaDB 索引
  - 在 Python fallback 仍较弱的情况下，这会让迁移后的旧论文库在未执行 `update-db` 前失去正文搜索能力
- [ ] 决定并落实 saved-paper 相关 MCP 契约
  - 当前 `extract_paper_full_text` / `read_saved_paper` / `search_saved_papers` 主要返回文本串
  - TS 原版还提供 canonical resource link、preview excerpt、relative path、matched sections 等结构化信息
- [ ] 打通 Elsevier / Springer `asset_hints` 到持久化 sidecar manifest
  - `status.md` 与 TS 晚期实现已确认 figures / tables / object URLs 是重要能力
  - 当前 Python publisher 层保留了 `asset_hints` 字段，但 fetch/save 链路尚未持久化这些资产提示
- [ ] 恢复或明确放弃 `extract.tdm` 的按 publisher 配置能力
  - TS 配置中曾支持 `extract.tdm.order` / `extract.tdm.enabled`
  - 当前 Python 版将 TDM publisher 顺序硬编码为 `Elsevier` → `Springer`
- [ ] 补齐上述差异的回归测试
  - 当前 smoke tests 未覆盖 resources/templates、正文 lexical fallback、迁移后正文检索、asset manifest 持久化与 richer saved-paper contract

### Phase 4.1: 当前完成度与改进建议（2026-04-02）

- **说明**：本节结论是“发布收口视角”的阶段判断；2026-04-03 的 Phase 4.2 审计已确认仍存在若干能力/契约回退，因此不能据此宣称“Python 化已经完全完成”
- **完成度判断**：Python 主运行时已经具备对外可用的核心能力，当前工作的重心已从“功能移植”转为“发布验收 + 仓库收口 + 文档降噪”
- **已完成的主线**：CLI、6 个 MCP 工具、浏览器自动化、ChromaDB、迁移命令、首轮 smoke tests、发布 workflow、本地 wheel 验收
- **仍未完成的硬阻塞**：PyPI 真正上线后的远程验收
- **仍未完成的软问题**：
  - 仓库已切换到根目录 Python 主线，但仍需继续清理历史文档中的迁移期措辞
  - 仍需确认 PyPI 首发版本号与发布顺序是否直接采用 `v0.7.0`
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

### Phase 5: 增强（迁移完成后）

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

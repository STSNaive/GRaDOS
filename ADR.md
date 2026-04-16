# GRaDOS 架构决策记录

## 说明
- 本文记录已经接受并落地的关键架构决策。
- `TODO.md` 只保留未完成事项；不再重复维护已完成决策。
- `CHANGELOG.md` 记录对外可见的行为变化；本文更关注“为什么这样设计”。

## ADR-000：release tag 必须晚于 version bump commit

- 状态：Accepted
- 日期：2026-04-15

### 背景
- 发布 workflow 会校验 Git tag `vX.Y.Z` 与 `pyproject.toml` 中的包版本完全一致。
- 如果先推 tag、后改版本，或在已有 tag 上重复试错，容易触发失败 workflow、重复 workflow，甚至让 release 记录与实际包内容错位。

### 决策
- 发布 `vX.Y.Z` 前，必须先把 `pyproject.toml` 与 `src/grados/__init__.py` 的版本号提升到同一版本，并提交到 git。
- 发布顺序固定为：`version bump commit -> push main -> create/push tag vX.Y.Z`。
- 默认依赖 tag push 自动触发 `publish.yml`；只有自动未触发或需要补跑时，才使用 `workflow_dispatch` 手动触发。
- 若必须重指已存在的 release tag，必须先确认是否会导致重复 workflow / 重复发布，再决定是否强推 tag。

### 结果与影响
- release tag、包版本、GitHub workflow 与 PyPI 版本之间保持一一对应。
- 版本错误会在本地 commit/tag 阶段被规避，而不是等到 CI 才暴露。
- 发布流程的重试方式明确为“先修版本与提交，再处理 tag”，避免同一版本产生多次含义不同的 run。

## ADR-001：`papers/*.md` 是唯一 canonical full-text source of truth

- 状态：Accepted
- 日期：2026-04-14

### 背景
- 项目早期同时让 Markdown mirror 与 Chroma doc record 承担“正文真源”职责。
- 这种双真源语义会让 `read/list/reindex/research_tools` 的行为不一致，也让恢复与重建边界越来越模糊。

### 决策
- `papers/*.md` 是唯一用户侧 canonical 全文来源。
- `database/chroma` 只承担可重建的检索索引职责，不再承担正文真源职责。
- `grados reindex` 的产品语义明确为“重建检索索引”，而不是“恢复论文正文”。
- 用户侧读论文、列论文、获取结构、深读上下文时，都应从 `papers/*.md` 读取 canonical Markdown。
- canonical mirror 的 durable 写入必须先于索引刷新；不得允许“只有索引、没有 `papers/*.md` 原文”的状态成为持久结果。

### 结果与影响
- 全文真源与检索索引职责解耦，系统语义更清晰。
- `papers/*.md` 必须保留足够 frontmatter 元数据，以支持仅依赖原文库重建索引。
- 如果原文文件缺失，应显式表现为源数据缺失，而不是偷偷回退到索引库正文副本。
- mirror 写入失败时，应让保存过程直接失败或终止在 canonical 层，而不是继续刷新派生索引。

## ADR-002：检索采用“索引召回 + canonical 原文回读”

- 状态：Accepted
- 日期：2026-04-14

### 背景
- 直接把 Chroma 中存储的 chunk 文本返回给模型，虽然快，但正文引用源与用户可见真源容易漂移。
- 研究工具与普通阅读工具如果读取的不是同一份正文，会带来 citation/audit 不一致。

### 决策
- 由 Chroma 负责召回与排序，确定“哪些论文相关、哪些 chunk/段落相关”。
- 索引 chunk metadata 中显式保存 `safe_doi`、`paragraph_start`、`paragraph_count`、`section_name` 等定位信息。
- 最终返回给模型的证据段落，必须依据命中的段落坐标回到 `papers/*.md` 中重新读取。
- research tools 与 user-facing read/search 工具统一遵循这条闭环。
- 对单次命中的相关段落，不额外做人为截断；应优先返回 canonical 原文中的相关段落窗口。
- 多论文聚合型工具仍可保留总预算控制，避免单次调用把过多全文一次性灌入上下文。

### 结果与影响
- 最终证据来自 canonical 原文，而不是索引副本。
- paragraph 切分规则必须保持稳定，否则命中的段落坐标会漂移。
- Chroma 中即使仍保留 derived document copy，也不再作为最终证据源；canonical reread 具有更高优先级。
- 后续优化重点包括 overlap chunk 去重、上下文扩窗和更强的闭环回归测试。

## ADR-003：默认归一化层采用 Docling；Elsevier 走 XML-first 确定性解析

- 状态：Accepted
- 日期：2026-04-14

### 背景
- GRaDOS 需要同时处理 PDF、HTML、图片、publisher-native XML/JSON 等不同输入。
- 如果直接把各来源的原始文本扁平写入 `papers/*.md`，后续 section/paragraph 结构会不稳定。
- Elsevier full-text API 的 JSON `originalText` 虽可用，但结构信息明显弱于 XML。

### 决策
- 非 Elsevier 的文档型输入，默认通过 Docling 归一化为统一 canonical Markdown。
- 默认 PDF 解析顺序调整为 `Docling -> Marker -> PyMuPDF`，`PyMuPDF` 只保留为 fallback。
- Elsevier full-text API 优先请求 `application/xml`，并走 publisher-native 的确定性解析。
- Elsevier 的 JSON `originalText` 与 `text/plain` 只作为 fallback，不再作为 canonical 主路径。
- 对已经高度结构化且可确定性解析的 publisher-native 输入，优先 deterministic parser，而不是机械再过一层通用转换器。

### 结果与影响
- 文档结构更稳定，section-aware chunking 和 paragraph reread 更可靠。
- Docling 成为默认安装与默认归一化路径的一部分，需要在 setup 阶段进行预热。
- Elsevier 的结构化正确性来自 XML 解析与校验，而不是依赖扁平全文猜结构。

## ADR-004：可靠性与可观测性优先于静默 fallback

- 状态：Accepted
- 日期：2026-04-14

### 背景
- 早期实现里存在两类风险：
  - 保存 mirror 成功但索引失败时，系统仍表现为“完全成功”
  - Parser fallback 失败或超时时静默吞掉异常，难以判断问题发生在哪一层

### 决策
- `save_paper_markdown()` 必须显式区分 mirror 写入状态与索引状态。
- 当 mirror 成功但索引失败时，上层 extract/import/parse 工具返回 partial-success / warnings，而不是伪装为完全成功。
- `marker_timeout` 必须成为真实生效的运行时契约，因此 Marker 改为独立子进程执行并按配置超时终止。
- Docling 与 Marker 的失败、超时、fallback 需要输出统一格式的 warning/debug。
- `grados setup` 在用户首次真实解析前预热 Docling，减少冷启动“像卡住”的体验问题。
- 可以保留为提高成功率所必需的运行时 fallback，但不再为了兼容而长期保留会模糊架构语义的双路径冗余。
- 所有保留的 fallback 都必须对调用方可见。

### 结果与影响
- 用户能明确知道“原文是否保存成功”“索引是否刷新成功”“解析器是否回退过”。
- 运行时问题更容易定位，调试信息也更容易统一呈现到 CLI / MCP receipt 中。
- 如果后续需要更强隔离，可以再把当前的子进程协议演进成常驻 worker。

## ADR-005：内部服务边界 typed 化，`server.py` 只保留薄入口与领域注册

- 状态：Accepted
- 日期：2026-04-15

### 背景
- 项目在演进过程中，`storage`、`research_tools`、`server`、`importing` 之间曾大量依赖裸 `dict[str, Any]` 传递结果。
- 这种隐式字段契约会让字段漂移、调用方静默吞错和后续模块拆分成本一起上升。
- 同时，原来的 `src/grados/server.py` 把工具注册、资源注册、格式化、参数契约与工作流编排都堆在一个入口文件里，已经形成明显的 monolith facade。

### 决策
- 模块内部高频服务边界优先使用 dataclass；MCP 边界在最外层再做序列化返回。
- `storage.papers`、`storage.vector`、`research_tools` 这类核心链路优先收紧 typed 结果对象，避免继续扩散新的裸 dict 契约。
- 不为当前仓库内部迁移长期保留兼容壳；调用方随类型收敛同步迁移，完成后移除 `.get(...)` / item-access 过渡层。
- `src/grados/server.py` 只保留 `FastMCP` 入口、模块导出与注册调用。
- MCP 处理逻辑按领域拆分到独立模块：
  - `search_tools`
  - `library_tools`
  - `research_tools_api`
  - `admin_tools`
- 共用配置、文档资源格式化和 selector 校验逻辑进入共享 helper，而不是继续堆在入口文件。

### 结果与影响
- 类型边界更清晰，`server` 拆分后的各模块可以围绕稳定对象工作，而不是继续传递松散 payload。
- 测试既能覆盖 MCP 边界，也能直接覆盖 research/storage 内部 dataclass 语义，降低字段漂移风险。
- 后续继续做策略注册表、embedding 缓存或更深的 server 模块拆分时，返工面会更小。

## ADR-006：`fetch / parse / browser` 采用静态策略注册表，而不是继续膨胀主流程分支

- 状态：Accepted
- 日期：2026-04-15

### 背景
- `extract.fetch`、`extract.parse` 与 `browser.generic` 曾经都依赖主循环里的 `if/elif` 分发。
- 这种写法在策略数较少时简单，但一旦继续增加 publisher、parser 或浏览器站点特化 flow，核心调度函数会越来越长，测试也只能围绕分支行为补丁式增长。
- 项目当前并不需要完整的动态插件系统；更需要的是一个清晰、可测试、能和配置顺序配合的静态扩展点。

### 决策
- `fetch`、`parse`、`browser` 三条流水线统一采用“静态 registry + 统一策略接口 + 配置控制顺序”的模式。
- `fetch` 层使用 fetch strategy registry；TDM 内部再使用 provider registry 组织 Elsevier / Springer 等 publisher adapter。
- `parse` 层将 PDF parser 与非 PDF normalizer 分成两类策略：
  - PDF parser registry 负责 `Docling`、`Marker`、`PyMuPDF`
  - normalizer resolver 负责把 `markdown/text/html/xml` 等格式映射到确定的归一化策略
- `browser` 层对页面动作流使用 page strategy registry，把 `ScienceDirect` 特化 flow 与通用 PDF 点击 flow 从主循环中拆开。
- 当前阶段只做代码内静态注册，不引入动态插件发现或运行时第三方扩展加载。

### 结果与影响
- 新增 publisher / parser / browser flow 时，优先新增策略实现并注册，而不是修改主调度函数结构。
- 配置中的顺序字段可以直接映射到 registry 执行顺序，行为更容易理解和测试。
- 核心 orchestration 函数更薄，更接近 FastAPI / Home Assistant 一类项目常见的“模块注册 + 统一调度”组织方式。
- 运行时 fallback 仍然保留在策略层，但 warning/debug 继续保持显式可见，不回退到静默失败。

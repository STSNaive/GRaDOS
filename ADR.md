# GRaDOS 架构决策记录

## 说明
- 本文记录已经接受并落地的关键架构决策。
- `TODO.md` 只保留未完成事项；不再重复维护已完成决策。
- `CHANGELOG.md` 记录对外可见的行为变化；本文更关注“为什么这样设计”。

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

---

## ADR-007：版本号由 git tag 唯一决定（hatch-vcs 动态版本）

- 状态：Accepted（取代原 ADR-000 "release tag 必须晚于 version bump commit"）
- 日期：2026-04-16

### 背景
- 原方案要求先手动 bump `pyproject.toml` 与 `__init__.py` 的版本号、commit、再打 tag 推送。
- 实际操作中多次因忘记 bump 或版本不一致导致 CI 失败、tag 需要重建。
- 手动维护两处版本号是不必要的同步负担。

### 决策
- 采用 `hatch-vcs`（基于 `setuptools-scm`），Python 包版本在构建时自动从 git tag 派生。
- `pyproject.toml` 使用 `dynamic = ["version"]`，不再包含静态版本号。
- `src/grados/__init__.py` 通过 `importlib.metadata.version("grados")` 在运行时获取版本。
- 构建时 `hatch-vcs` 自动生成 `src/grados/_version.py` 写入版本。
- Claude Code / Codex plugin JSON 中的 `version` 字段由 `scripts/release.py` 在打 tag 前统一更新。
- 发布流程简化为：`uv run python scripts/release.py X.Y.Z --push`（更新 plugin → commit → tag → push）。
- `publish.yml` 不再校验 tag 与文件版本一致性（因为版本本身来源于 tag）。
- 开发环境安装（`uv sync`）会显示带 dev 后缀的版本号（如 `0.6.9.dev3+g1a2b3c4`），发布包版本严格等于 tag。

### 结果与影响
- Python 包版本唯一真源为 git tag，plugin 版本由 release 脚本同步，不可能产生不一致。
- 发布操作步骤减少一半，消除了人为遗漏 bump 的风险。
- `_version.py` 是构建产物，已加入 `.gitignore`。

---

## ADR-008：外部调用统一 timeout / retry / 节流策略

- 状态：Accepted
- 日期：2026-04-17

### 背景
- `search`、`extract.fetch`、`publisher`、`browser` 各层对外部 API / 浏览器的调用，长期使用硬编码 30s timeout，无重试、无指数退避，也不区分各学术数据库上游的速率约束。
- `browser/generic.py` 的 `wait_for_load_state("networkidle")` 调用未传 timeout，实际依赖 try/except 兜底；遇到 SPA 背景轮询时主线程可能在浏览器侧长时间阻塞，消耗 2 分钟 deadline 的有效工作时间。
- PubMed / WoS 等数据库有明确的 req/s 上限（PubMed 3 rps 无 key / 10 rps 有 key，WoS 2 rps），无节流时批量查询易触发 429。
- 瞬时 5xx / 429 / 网络抖动在当前实现下直接失败，没有合理重试窗口。

### 决策
- 引入 `tenacity`（Apache-2.0，Py3.10+，活跃维护）作为统一重试层，不自写装饰器。
- 重试模板：`stop_after_attempt(max_attempts)` + `wait_exponential(multiplier=1, min=1, max=max_wait)` + `wait_random(0, 1)` 抖动；默认 3 次、max_wait=8s。仅对 429、5xx、`httpx.ConnectError`、`httpx.ReadTimeout`、`httpx.WriteError`、`httpx.PoolTimeout`、`httpx.RemoteProtocolError` 重试；存在 `Retry-After` 或 `X-RateLimit-Reset` 时优先遵守，响应头给出的 wait 值在 60s 上限内直接采用。
- 超时按阶段差异化（全部可经 config 热替换，新进程启动后立即生效）：
  - search 层 HTTP：connect 10s / read 30s
  - fetch / publisher HTTP（OA / TDM / Sci-Hub 非 PDF）：connect 15s / read 60s
  - PDF 下载：connect 15s / read 60s
  - browser `goto`：30s（保持）
  - browser `networkidle`：15s，失败 try/except 降级进入主轮询循环，不挂死
  - browser 总 deadline：120s
  - browser 主轮询：`0.5s → 1s → 2s` 指数退避
  - Marker：沿用 `config.extract.parsing.marker_timeout`
- 按数据库分档速率节流：PubMed 无 key ≥334ms / 有 key ≥100ms；WoS ≥500ms；Crossref 继续走 polite pool；Elsevier / Unpaywall / Springer 依赖响应头动态退避，无需前置节流。节流器为进程内 `asyncio.Lock` + 单调最小间隔，跨并发任务共享。
- 运行时 policy：`grados._retry` 模块级 `_CURRENT` 由 `install_runtime_defaults(config)` 在 MCP 工具入口 / CLI 启动时从 `~/GRaDOS/config.json` 注入；装饰器与 timeout getter 在每次调用时读取 `_CURRENT`，不做 import-time 冻结。
- 配置面：新增 `retryPolicy`（顶层）、`search.connectTimeout` / `readTimeout`、`extract.fetchConnectTimeout` / `fetchReadTimeout`、`extract.headlessBrowser.deadlineSeconds` / `networkidleTimeout` / `pollMinSeconds` / `pollMaxSeconds`；所有新字段有保守默认值，缺失字段走 Pydantic default，不破坏旧 config。
- 所有 retry / timeout / throttle 事件必须产生可观测 warning / debug（对齐 ADR-004）。

### 结果与影响
- 新增运行时依赖 `tenacity`；`uv tool install grados` 与 `uv sync` 自动拉取。
- 瞬时 5xx / 429 / `ConnectError` / `ReadTimeout` 不再立即失败；最坏路径总延时最多增加 `sum(wait_exponential) ≈ 7s / 调用`，或 `Retry-After` 指定值（最多 60s）。
- 用户可在 `~/GRaDOS/config.json` 直接调 timeout / retry knobs；MCP 工具重启（或下次 tool 调用）即生效，不需要改代码、不需要重装。
- `browser/generic.py` 的 15s `networkidle` 上限替代隐式默认，消除"networkidle 无超时"的潜在挂死风险；主轮询 `asyncio.sleep(1)` 改为 `0.5s → 1s → 2s` 指数退避，降低长页面空转 CPU 与事件循环负载。
- PubMed / WoS 的最小间隔节流由进程内 `_AsyncMinIntervalLimiter` 保障；Elsevier / Unpaywall 依赖响应头退避已通过 `_HeaderAwareWait` 实现。
- 未来新增 publisher 时，只需套用统一 retry 装饰器 + runtime getter，不重复手写超时 / 节流逻辑。

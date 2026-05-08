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
- `database/remote_metadata` 单独保存远程论文 metadata、fetch 状态与 browser resume 信息；它不是 `grados reindex` 要清理的可重建 paper index。
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
- `search_saved_papers`、`build_evidence_grid`、`compare_papers`、`audit_draft_support` 等 helper 可以返回 compact snippet、score、reread anchor 和 first-pass audit 状态，供外层 agent model 做 query planning、reranking、support judgment 和 synthesis；这些 helper 输出仍不是 citation evidence。
- GRaDOS server 不直接调用外层 agent model。模型判断停留在 MCP client / host agent 层，GRaDOS 只提供 deterministic retrieval、anchor 和 canonical reread entrypoint。

### 结果与影响
- 最终证据来自 canonical 原文，而不是索引副本。
- paragraph 切分规则必须保持稳定，否则命中的段落坐标会漂移。
- Chroma 中即使仍保留 derived document copy，也不再作为最终证据源；canonical reread 具有更高优先级。
- 可能跨上下文压缩或跨工具复用的 helper 输出，应在可用时携带 `canonical_uri`、`paragraph_start`、`paragraph_count`；没有精确坐标时必须显式降级，不能暗示 snippet 已经 citation-ready。
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
- 默认 PDF 解析顺序调整为 `Docling -> MinerU -> Marker -> PyMuPDF`。MinerU 作为需要 `MINERU_API_KEY` 的认证云端 fallback；`PyMuPDF` 只保留为本地轻量 fallback。
- Elsevier full-text API 优先请求 `application/xml`，并走 publisher-native 的确定性解析。
- Elsevier 的 JSON `originalText` 与 `text/plain` 只作为 fallback，不再作为 canonical 主路径。
- 对已经高度结构化且可确定性解析的 publisher-native 输入，优先 deterministic parser，而不是机械再过一层通用转换器。

### 结果与影响
- 文档结构更稳定，section-aware chunking 和 paragraph reread 更可靠。
- Docling 成为默认安装与默认归一化路径的一部分，需要在 setup 阶段进行预热。
- MinerU 只在用户配置 token 后参与 PDF 解析瀑布，避免无意上传本地论文 PDF 到第三方云端服务。
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
- 配置面：新增 `retry_policy`（顶层）、`search.connect_timeout` / `read_timeout`、`extract.fetch_connect_timeout` / `fetch_read_timeout`、`extract.headless_browser.deadline_seconds` / `networkidle_timeout` / `poll_min_seconds` / `poll_max_seconds`；所有新字段有保守默认值，缺失字段走 Pydantic default，不破坏旧 config。
- 所有 retry / timeout / throttle 事件必须产生可观测 warning / debug（对齐 ADR-004）。

### 结果与影响
- 新增运行时依赖 `tenacity`；`uv tool install grados` 与 `uv sync` 自动拉取。
- 瞬时 5xx / 429 / `ConnectError` / `ReadTimeout` 不再立即失败；最坏路径总延时最多增加 `sum(wait_exponential) ≈ 7s / 调用`，或 `Retry-After` 指定值（最多 60s）。
- 用户可在 `~/GRaDOS/config.json` 直接调 timeout / retry knobs；MCP 工具重启（或下次 tool 调用）即生效，不需要改代码、不需要重装。
- `browser/generic.py` 的 15s `networkidle` 上限替代隐式默认，消除"networkidle 无超时"的潜在挂死风险；主轮询 `asyncio.sleep(1)` 改为 `0.5s → 1s → 2s` 指数退避，降低长页面空转 CPU 与事件循环负载。
- PubMed / WoS 的最小间隔节流由进程内 `_AsyncMinIntervalLimiter` 保障；Elsevier / Unpaywall 依赖响应头退避已通过 `_HeaderAwareWait` 实现。
- 未来新增 publisher 时，只需套用统一 retry 装饰器 + runtime getter，不重复手写超时 / 节流逻辑。

---

## ADR-009：跨入口多阶段流程采用 shared workflow / service core，CLI 与 MCP 保持薄 adapter

- 状态：Accepted
- 日期：2026-04-21

### 背景
- `extract_paper_full_text`、`parse_pdf_file`、`import_local_pdf_library` 在一段时间内分别维护 `parse -> QA -> save -> reindex -> receipt` 的局部变体，导致 warning 聚合、partial-success 语义与 idempotency 字段解释分散在多个入口。
- `browser/generic.py` 与 `research_tools.py` 也在持续累积 orchestration、helper、typed result、入口兼容层等不同职责，单文件持续膨胀。
- 这类流程一旦同时服务 MCP、CLI 或多个 server tool，若不先收敛 shared core，后续新增入口或调 contract 时会快速回到“多处复制、行为轻微漂移”的状态。

### 决策
- 只要一个能力满足以下任一条件，就优先抽成 shared workflow / service core，而不是继续堆在入口文件里：
  - 被多个入口复用
  - 包含多阶段编排状态
  - 需要统一 partial-success / warning / idempotency / typed-result 语义
- shared core 默认返回 dataclass / typed result / stable dict，不直接耦合 MCP markdown receipt、CLI stdout 文案或参数解析细节。
- CLI、MCP 与 server tool 入口只负责：
  - 参数适配
  - 调用 shared core
  - 对外 payload / receipt 渲染
- `src/grados/workflows/` 用于承载跨领域、跨入口复用的流程型逻辑；领域内但职责已明显分化的模块，则拆到对应 package 下的子模块，而不是继续维持单文件 monolith。
- 对 browser / research 这类已有 public facade 的模块，第一阶段允许保留 facade / re-export 作为稳定入口，但 facade 不再新增 heuristics 或真正的工作流逻辑。

### 结果与影响
- canonical save、listener cleanup、citation cache、full-context / compare / audit 等关键契约可以在单一实现点维护，入口层不再重复解释。
- 测试边界可以更贴近真实职责：workflow 测试锁多阶段语义，adapter smoke test 锁对外 payload，避免所有护栏都堆在一个大文件对应的一组 smoke tests 上。
- 后续若继续给已有能力增加 CLI twin、MCP twin 或新的领域入口，优先复用 shared core，而不是复制一份 orchestration。

---

## ADR-010：Corpus 分层采用“先 canonical，后按需 working”的两阶段演进

- 状态：Accepted
- 日期：2026-04-23

### 背景
- `remote_metadata` 落地后，GRaDOS 已具备“先缓存远程 metadata，再决定是否 materialize 全文”的基础。
- 用户讨论中明确希望保留未来的 `working corpus + canonical library` 路线，但当前个人研究库通常仍在 100-500 篇量级，主要矛盾不是 Chroma 容量，而是长期库与探索性材料的边界。
- 如果现在立刻拆出 `working_docs/working_chunks`，会同时引入 promotion、跨库检索、双层去重和新的用户心智；在当前阶段，这些复杂度的收益还不如先把 `remote_metadata` 和 acquisition 主线做好。

### 决策
- `Phase 1` 继续保持单一长期库心智：摘要筛选通过后，`extract_paper_full_text` 成功获取的全文仍直接进入 canonical `papers/*.md` 与 `papers_docs` / `papers_chunks`。
- `Phase 1` 只在 canonical 记录上预留轻量 corpus 字段：`corpus=canonical`、`tier=stable`、`workset_id`、`promoted_at`、`promote_reason`。旧记录缺字段时，一律按 `canonical/stable` 解释。
- `working vs canonical` 的区别是“同一次筛选后的不同落盘层”，不是第二次 LLM 审核，也不是另一条 fetch 或 parse 流程。
- `search_saved_papers` 在 `Phase 1` 继续默认只搜索 canonical；working corpus 只有在未来真的引入后，才会通过显式选项参与检索。
- `Phase 2` 只有在专题式批量 materialize 已经明显把大量临时工作材料混入长期库时才触发；届时在同一 Chroma 实例内新增 `working_docs` / `working_chunks`，而不是引入新数据库。
- `Phase 2` 的触发信号包括：
  - 单个专题经常一次 materialize 数十到上百篇全文，其中明显有一批只是临时工作材料。
  - 用户频繁需要“只搜当前专题候选”，而不想污染长期 canonical 库。
  - 主库开始混入大量探索性全文，人工清理长期库变得痛苦。
- `Phase 2` 的 promotion 规则保持显式优先：用户显式 `pin` / `save` / `promote` 最优先；最多补轻量提示，例如同一论文被多次检索命中、被引用/导出、或跨多个 workset 复用时提示升格，但不默认自动升格。
- `Phase 2` 默认检索规则仍保持保守：canonical 是默认搜索面；working 只在用户显式选择“包含当前工作集”时并入。
- 去重规则以 `doi` / `safe_doi` 为全局主键；同一 DOI 不在同一 corpus 重复入库；promotion 时若 canonical 已有同 DOI，则只补 workset 关联和 provenance，不重复建第二份全文索引。
- merged search 中，同一 DOI 同时存在 canonical 和 working 时优先返回 canonical；working 只作为补充上下文，不与 canonical 抢主结果位。

### 结果与影响
- 当前版本维持一个长期库的简单用户心智，不额外增加操作面。
- corpus 字段已经预留，未来若进入 `Phase 2`，不需要重新改写 canonical 数据模型。
- 当前阶段的主要收益仍来自 `remote_metadata` 复用与 acquisition 优化，而不是更早引入 promotion 机制。

---

## ADR-011：全文获取默认顺序改为 `api -> browser -> oa -> scihub`

- 状态：Accepted
- 日期：2026-04-23

### 背景
- GRaDOS 的主要使用前提是机构访问权限与校园网环境，而不是公开 OA-only 场景。
- 在这种前提下，browser/PDF download 不是一个“最后才试的 headless fallback”，而是订阅全文获取的主路径之一。
- 旧的 `TDM -> OA -> Sci-Hub -> Headless` 顺序会把 browser 放得过后，导致本来可以直接通过机构权限拿到的 PDF，要先经过不必要的 OA / mirror 尝试。

### 决策
- fetch strategy 的 canonical 命名统一收敛为单词：`api`、`browser`、`oa`、`scihub`。
- 默认顺序改为 `api -> browser -> oa -> scihub`。
- `api` 表示 publisher API / TDM 路径；`browser` 表示机构权限下的浏览器/PDF download；`oa` 表示合法 OA shortcut；`scihub` 仍只保留为末级兜底。
- `TDM`、`OA`、`SciHub`、`Headless` 继续作为 legacy alias 兼容读取，但不再作为文档主命名。
- browser 结果在主流程中保留 `state`，至少区分 `ok`、`challenge`、`timeout`、`nobrowser`、`error`，避免把需要人工接力的 challenge 折叠成无差别的 `failed`。
- browser challenge 是一级可恢复状态：当 publisher 人机验证阻断 PDF 捕获时，流程保存 `manual=true`、`host`、`resume` 句柄、`trace` provenance 与当前 profile 信息到 `remote_metadata`，并在 receipt 中提示用户完成验证后重试。
- `resume_browser=true` 表示“从上一次 browser challenge 继续”，应优先使用保存的 publisher URL / browser profile，并从 `browser` strategy 开始，不再重新跑 `api` 优先的整条链。
- `trace` 最小字段集为 `via`、`state`、`host`、`time`、`hash`、`resume`、`manual`；这些 provenance 字段只进入 metadata，不进入 embedding 文本。

### 结果与影响
- 新配置和文档统一围绕 `api/browser/oa/scihub` 组织，用户心智更接近真实使用路径。
- 旧配置不需要一次性重写；legacy alias 仍可运行。
- Elsevier / ScienceDirect 等需要人工验证的页面不再表现为普通失败；用户完成一次验证后，可以复用项目自己的浏览器 profile 继续同一 DOI 的获取尝试。

---

## ADR-012：`indepth`、`paper_summary` 与 `research_checkpoint` 分层

- 状态：Accepted
- 日期：2026-04-29

### 背景
- GRaDOS 的普通搜索流程已经可以先返回远程 metadata / abstract，再按需获取全文；但复杂研究任务经常需要在搜索、全文获取、总结、证据回读和最终综合之间跨多轮对话持续推进。
- 只依赖聊天上下文保存中间判断时，一旦上下文压缩或任务中断，已经筛选过的论文、全文获取状态、关键证据位置和阶段性结论都可能丢失。
- 直接把 summary、checkpoint、全文 chunk 和远程 metadata 混在一个存储层里会降低可追溯性：派生总结可能被误当作引用依据，临时任务判断也可能污染长期论文知识。
- 用户需要一个更深入但默认可关闭的研究模式：它可以在同一个候选数量下继续获取全文和生成论文级总结，同时仍保持“最终回答以全文为准”的证据纪律。

### 决策
- 新模式统一命名为 `indepth`，包括 CLI、config、MCP schema 与内部字段；不使用 `in-depth` / `--in-depth` 作为主命名。
- `indepth` 默认关闭，作为第二阶段能力实现；开启后沿用基础搜索模式的同一个 `limit` / `N`，不新增第二套 top-N 概念。实现可以有硬性安全上限和失败降级，但用户可见心智保持为一个候选数量。
- `paper_summary` 是论文级、query-independent、可复用的派生产物，用于快速理解、导航和恢复上下文；它不是引用依据。最终回答、citation、audit 和比较仍必须回读 canonical `papers/*.md`。
- 不长期保存独立的 `topic_note`。当前任务相关判断写入 `research_checkpoint.current_findings` / `evidence_anchors`，避免一次任务的视角被误用于另一类问题。
- `research_checkpoint` 是一次 GRaDOS 研究对话的 durable workflow state，而不是论文全文库。一个 checkpoint 必须支持多篇论文，记录用户问题、搜索式、候选论文、全文获取状态、summary 关联、阶段性发现、证据锚点、失败原因和下一步动作。
- checkpoint folder 自动命名为 `{started_at}_{slug}_{short_hash}`，默认位于项目内专用目录；该目录必须受 gitignore / public-contract 规则保护，避免泄露本地研究材料或下载状态。
- 存储职责保持分层：
  - `remote_metadata` 保存远程发现结果、fetch 状态和 provenance。
  - `papers/*.md` 保存 canonical 全文，是最终证据源。
  - `database/chroma/` 保存可重建的全文向量索引。
  - `paper_summary` 与 `research_checkpoint` 保存派生理解和工作流恢复信息，通过 `doi` / `safe_doi` / `paper_id` / `paper_uri` / hash 与上述层关联。
- 搜索和提取结果应暴露本地状态，即使未开启 `indepth` 也应能告诉用户论文是否已保存、是否有全文、fetch 状态、summary 状态和 canonical paper URI。
- Browser challenge、metadata-only、partial success、summary failed 等状态必须被记录为可恢复状态，不应静默折叠成普通失败，也不应阻塞整批任务。
- 最终综合前必须统一学科专有名词：同一概念在输出中使用一个 canonical term；若论文之间用词冲突或领域规范不清，应结合已读 canonical 论文和必要的权威网络检索选择最常用、最规范的术语。不能为了统一术语而合并本来有差异的概念。
- 工具面保持最小化，优先复用 ADR-009 的 shared workflow / service core；CLI 与 MCP 只做薄 adapter。除非确有必要，不新增碎片化 checkpoint CRUD 工具组。

### 结果与影响
- 普通模式继续轻量运行；`indepth` 为需要全文级阅读和总结的研究任务提供显式开关。
- summary、checkpoint、全文和向量索引之间可以互相定位，但不会混淆谁是 canonical evidence。
- 上下文压缩或对话中断后，LLM 可以通过 checkpoint 恢复已筛选论文、阶段性发现和证据锚点，再回读全文继续工作。
- 运行面、schema 字段和恢复规则由 README、skill reference、`skills/grados/references/indepth.md` 与回归测试共同守护；`TODO.md` 不再重复维护已完成清单。

---

## ADR-013：saved-paper selector 与 DOI-derived paper id 采用 opaque identifier 语义

- 状态：Accepted
- 日期：2026-05-06

### 背景
- `read_saved_paper`、`get_saved_paper_structure` 和 `grados://papers/{safe_doi}` 允许调用方直接提供 saved-paper selector。
- 旧实现把 selector 直接拼进 `papers_dir / f"{safe_doi}.md"`，会把 `../` 之类路径片段带入文件系统解析。
- 旧 `safe_doi_filename()` 还把所有非字母数字字符替换为 `_`，导致不同 DOI 可能得到同一个 paper id、Markdown 文件名、PDF 文件名、asset manifest 和 remote-metadata id。

### 决策
- `safe_doi` / `grados://papers/{safe_doi}` 是 GRaDOS 返回的 opaque paper id，不是让调用方自行按 DOI 标点推导的路径片段。
- 所有 caller-provided saved-paper selectors 必须先通过 filename-token allowlist；最终路径再用 `Path.resolve()` 解析，并确认仍位于 canonical `papers/` 目录下。
- 新写入的 DOI-derived id 使用“可读 slug + normalized DOI hash”格式。slug 方便人工扫读，hash 承担唯一性；不得再把纯下划线 slug 当唯一主键。
- DOI lookup 保持向后兼容：按 DOI 读取时先尝试当前 hash id，再尝试旧版纯下划线 id；如果旧文件已经存在且 frontmatter DOI 匹配，写入同一 DOI 时继续使用旧 id，避免无意义迁移。
- remote metadata、paper summary、raw PDF 和 asset manifest 等 DOI-derived artifact 统一复用当前 collision-resistant id；调用方应优先使用保存回执、搜索结果或 resource URI 返回的 id。
- Springer acquisition 保持 metadata 与 full-text 分层：Meta API 命中但 OA JATS / HTML / PDF 都不可用时，结果仍是可缓存、可展示的 `metadata_only`，而不是 generic failure。
- `extract.sci_hub.endpoints` 表示 ordered fallback list。单个 endpoint 的 `not_found` 只说明该 endpoint 未命中；只有全部 endpoints 都未命中时才返回最终 `not_found`。

### 结果与影响
- saved-paper 读取不再允许路径穿越到 `papers/` 之外。
- 新保存的 paper id 会比旧版多一个短 hash 后缀；旧的 `10_1234_demo` 形式仍可通过 DOI lookup 或明确 legacy selector 读取。
- 用户文档和 tool reference 应避免暗示 safe DOI 可以从 DOI 简单替换标点得到；示例应把它描述为“GRaDOS 返回的 paper id”。
- DOI collision 不再能覆盖 canonical Markdown、PDF、asset manifest、Chroma join key 或 remote metadata 记录。
- Springer metadata-only 与 Sci-Hub endpoint fallback 都保持可观测，不把可恢复/可缓存状态折叠为普通失败。

---

## ADR-014：Codex in-app browser 不作为 PDF acquisition backend

- 状态：Accepted
- 日期：2026-05-06

### 背景
- GRaDOS 已把 `browser` 作为机构权限 PDF download 的一等获取路径，并通过 managed Chrome / Patchright 维护 persistent profile、download capture、challenge resume 和 `BrowserFetchResult` 契约。
- 曾评估过把 Codex Browser Use 的 in-app browser 作为另一个 PDF acquisition backend。
- 本地 runtime probe 已证明该 in-app browser 在当前 Codex 环境中不支持文件下载，触发下载时返回 `Downloads are not supported by Codex In-app Browser.`。

### 决策
- Codex Browser Use 的 in-app browser 不进入 GRaDOS 的 PDF acquisition backend 或默认 `extract.fetch_strategy.order`。
- 现有 managed Chrome / Patchright 继续作为 GRaDOS 内部 browser strategy 的主路径。
- Codex in-app browser 只可作为页面观察、调试或预览表面；不能被文档或代码描述为可稳定产出 PDF artifact 的获取后端。
- Codex Chrome extension 可以作为 disabled-by-default 的 `codex` 配置项参与 `extract.fetch_strategy.order`，但语义是 host-agent handoff：GRaDOS 只在该顺序位置返回 Chrome extension 下载 receipt，由 host agent 在 GRaDOS 进程外拿到本地 PDF 路径，再调用 `parse_pdf_file` 回流入库；不得伪装成 server 内部下载 backend。

### 结果与影响
- GRaDOS 的 acquisition contract 继续围绕可由 Python runtime 控制和验证的后端组织。
- 不保留 speculative in-app browser backend TODO，避免再次把不可下载的 UI surface 误写成获取路径。
- `codex` 的配置顺序只控制 host-action 出现时机；真正下载和本地 PDF 路径识别仍属于连接了 Codex Chrome extension 的 Codex host agent 工作。

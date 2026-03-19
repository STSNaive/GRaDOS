# GRaDOS Headless Browser 改进计划

> 目标：混合架构 — 保留 Puppeteer 快速路径 + 新增 LLM 辅助浏览器路径

## Phase 1: 强化当前 Puppeteer (快速路径, 0 token) ✅

- [x] 将 `puppeteer-core` 替换为 `puppeteer-extra` + `puppeteer-extra-plugin-stealth`
  - 绕过 Cloudflare、reCAPTCHA 等常见反爬检测
  - 减少 CAPTCHA 触发率，降低对 LLM 辅助路径的依赖
- [x] 添加浏览器指纹随机化 (viewport 随机化 + `--disable-blink-features=AutomationControlled`)
- [ ] (可选优化) 通过 `page.createCDPSession()` 使用 `Fetch.enable` 增强 PDF 拦截
  - 当前 `page.on('response')` 已可用，CDP Fetch 是进一步优化

## Phase 2: 新增 `parse_pdf_file` MCP 工具 ✅

- [x] 在 `src/index.ts` 中添加 `parse_pdf_file` 工具
  - 输入：本地 PDF 文件路径 + 可选 DOI/expected_title
  - 复用现有 parsing waterfall (LlamaParse → Marker → Native)
  - 如果提供 DOI，自动保存 Markdown 到 papers 目录
  - 已更新 ListTools、CallTool、grados://about、grados://tools 资源

## Phase 3: 更新 SKILL.md 引导 LLM 辅助路径 ✅

- [x] 添加 Step 3b: 浏览器辅助提取工作流
  - browser_navigate → browser_snapshot → browser_click → parse_pdf_file
- [x] Step 0 增加 `localRag.enabled` 配置开关判断

## Phase 4: 配置与文档 ✅

- [x] `mcp-config.example.json` 添加 `localRag.enabled` 开关
- [x] `mcp-config.example.json` 添加 `playwrightMcp` 配置参考
- [x] `tools.md` 添加 `parse_pdf_file` 和 Playwright MCP 工具参考
- [x] README.md 添加 Playwright MCP 安装说明 + 工具表更新
- [x] README.zh-CN.md 同步中文版更新

## Phase 5 (长期/可选): 移除 Puppeteer 依赖

- [ ] 验证 LLM 辅助路径在多种出版商页面的成功率
- [ ] 若稳定率 > 80%，移除 `puppeteer-extra` 依赖，简化部署
- [ ] 考虑将 Headless 阶段从 fetchStrategy 中标记为 deprecated

---

## 参考资料

- Playwright MCP: https://github.com/microsoft/playwright-mcp (npm: `@playwright/mcp`)
  - 26+ 工具，accessibility tree 模式，下载文件自动跟踪
  - Token: ~13.7K base，页面内容视复杂度而定
- Chrome DevTools MCP: https://github.com/ChromeDevTools/chrome-devtools-mcp (不推荐，无下载跟踪)
- browser-use: https://github.com/browser-use/browser-use (不推荐，token 成本过高 $0.1-1/次)
- Stagehand: https://github.com/browserbase/stagehand (不推荐，需 Browserbase Cloud)

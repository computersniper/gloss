# 版本记录 / Changelog

本项目 **Gloss（旁注）** 的更新记录。English summary follows each section.

---

## v2.0.0-beta.1

- 经典论文阅读器新增 GlossX 研究工作台，可在应用顶部切换。工作台支持 Agent 会话、研究项目、可引用的论文资料与带 Git 历史的工作区。
- 延续 Gloss 的安装入口和本地论文库，并支持通过数据目录设置打开 GlossX 预览版的库。
- 从 Gloss 的后续开发中保留论文标签与阅读视角、视觉解释和 Markdown 笔记。

*Gloss adds the GlossX research workbench and keeps the classic reader as the first-launch mode. This prerelease keeps the existing install channels and can open a Gloss 1.x library or a GlossX preview library.*

---

## v1.0.2 — 2026-07-30

这一版让论文分析任务更透明、更可控，并进一步完善了插件市场、独立设置与本地发布体验。
*Gloss now makes paper analysis more visible and controllable, with richer plugins, per-feature settings, and improved local releases.*

### 🤖 可观察的研究任务 / Observable research tasks
- Summary、Deep Paper Note、Mind Map 与插件改为可取消的后台任务，并用研究助手动画展示当前阶段、耗时和进度。
- 打开页面不再自动调用 AI；Gloss 会优先恢复本地结果，没有结果时再由用户主动运行。
- 多个任务可以同时进行，切换页面后也会继续运行并保留状态。

### 🧠 模型、上下文与用量 / Models, context & usage
- Summary、Translate、Deep Paper Note、Mind Map、Chat、Explain 和每个插件都可以单独选择 Provider、模型与推理强度。
- 新增省 Token 的共享论文会话模式；兼容的 Claude 与 Codex 任务可复用论文上下文，避免反复发送全文。
- 设置页新增本地 AI 用量统计，并区分 Provider 返回的精确值与估算值。

### 🧩 插件市场与设置 / Plugins & settings
- 可安装插件从 3 个扩展到 9 个，新增实现蓝图、证据表、术语词汇表、图表导读、汇报提纲与阅读路线。
- 插件可声明自己的研究助手、论文内容要求和专属设置；不满足运行条件时不会浪费 AI 请求。
- 已安装插件增加设置入口；卸载前会确认，并清理该插件生成的本地结果。

### 🖥️ 发布与文档 / Releases & documentation
- Windows 提供可直接运行的 x64 便携 EXE；Linux 与 macOS 继续通过 npm 或 Bun 运行本地网页版。
- 新增发布下载说明、插件开发规范、产品主页入口，以及 Mind Map 和插件市场展示图。

---

## v1.0.1 — 2026-07-21

这一版把 Gloss 从阅读器进一步扩展为可持续工作的论文研究空间，并完善了桌面端和跨平台发布体验。
*Gloss now keeps richer research context across sessions, adds extensible reading tools, and ships a more polished desktop experience.*

### 💬 持久化 AI 会话 / Persistent AI chat
- 每个 Gloss chat 对应独立、持久化的 Codex session；后续提问使用 `codex exec resume`，切换论文或页面不会丢失上下文。
- Codex session 丢失时可从 SQLite 历史自动重建；不同 provider 之间切换时不会错误复用上下文。
- 首轮回答后自动生成简短会话标题，并支持随时内联编辑；标题、消息与 session 映射统一持久化。
- 同一会话的请求串行执行，普通 Summary、Translate 等一次性任务继续保持临时会话。

### 🧩 插件与可扩展面板 / Plugins & extensibility
- 新增插件市场和扩展面板，插件可以像内置 Summary、Notes、Mind Map、Scholar、Skills 一样加入侧栏。
- 已安装插件支持卸载，并清理对应本地缓存；预留更多即将推出的扩展入口。
- DeepPaperNote 与个人 Notes 保持独立，避免生成式研读笔记和用户手写笔记混在一起。

### ✍️ PDF 批注与对话附件 / Annotation & chat attachments
- 新增个人笔记、彩色荧光标注与自由绘图，支持铅笔、写字笔、荧光笔和橡皮擦；内容持久化到本地。
- 阅读器“眼睛”菜单可分别控制笔记、高亮和绘图等覆盖层。
- 新增自由套索截图：在 PDF 上圈选任意区域，预览后直接加入 Chat；Codex、Claude、OpenAI 与 Anthropic provider 均可接收对应图片或提取文本。

### 🖥️ 桌面端、导入与界面 / Desktop, imports & UI
- Windows EXE 使用 Gloss 图标、单实例运行、自动选择空闲端口，并避免 AI 请求时弹出命令行窗口。
- 关闭应用时可提示正在导入或生成的任务将中断，并支持“下次不再提示”。
- 论文导入改为持久化异步队列，支持进度展示和取消；下载上限调整为 300 秒并改进 arXiv/代理兼容性。
- Provider 选择器增加连接状态灯和对应品牌标识，并修复本地 Codex 测试连接误超时。
- 优化 Mind Map 连线路由，减少穿过节点和不必要的弯折。
- npm/Bun、Linux、Windows 和 SSH 使用说明及 CI/Release 流程同步完善。

---

## v0.1.0 — 2026-07-17

首个完整版本：从对 [Moonlight](https://www.themoonlight.io/) 的本地重构起步，逐步长成一个功能完整的本地 AI 论文阅读器。
*First full release — a complete local AI paper reader.*

### 📖 阅读器 / Reader
- 采用 **pdf.js 官方文本层**，选中体验与浏览器一致（修复部分文本选不中）。*Official pdf.js TextLayer for browser-quality selection.*
- 渲染**超采样**（≥2×）更清晰；修复"只有第一页缩放"的 bug。*Supersampled render; fixed zoom-only-page-1.*
- **阅读模式**：正常 / 护眼(Sepia) / 夜间(反色)。*Reading modes: Normal / Sepia / Night.*
- 大纲栏可**拖动、折叠**并修正章节标题识别；PDF 与侧栏之间**可拖动分隔**；深色滚动条；**显示/隐藏批注**开关。
- 选中文字弹出菜单：Explain / Translate / 加入会话 / 高亮，紧贴选区显示。

### 🌐 翻译 / Translate
- **逐句翻译 + 持久缓存 + 跨页复用**，重进自动恢复；每句独立方框，默认只显示中文、可切换显示原文。
- **点击译文 → 在 PDF 中闪烁定位**原文。*Click a sentence to flash-locate it in the PDF.*
- **从 LaTeX 源翻译（arXiv）**：按解析出的章节（Abstract / Results …）逐节翻译或一次全部，补齐 PDF 提取漏掉的文本。*Translate arXiv LaTeX source by section or all — nothing missed.*

### 💬 对话与面板 / Chat & panels
- 对话**按论文保存历史**、支持**多会话**、可新建/删除；**加入会话**（PDF 或各面板选中→送入 Chat 并作答）。
- **速览 / 研读笔记 / 思维导图（交互式 React Flow：类型化节点+摘要+详情抽屉）**；LaTeX 公式用 KaTeX 正确渲染。
- **参考文献**解析+补全+可点击；**学术搜索/相关推荐**；**技能**运行 Claude/Codex skills。

### 🧩 arXiv
- **TeX 源码标签**：读取 arXiv 的 LaTeX 源（精确、无提取损失）。*Read arXiv LaTeX source in a TeX tab.*

### 🤖 提供方 / Providers
- `local_claude`（默认，免 key）/ `local_codex`（免 key）/ `anthropic` / `openai`，后两者均可填 **base_url**（兼容端点/代理）。
- 进阶设置：模型 + 思考深度（effort）；**测试连通性**按钮。*Model + reasoning effort; a Test-connection button.*

### 🎨 界面 / UI
- **7 套配色主题**；**界面语言**默认英文、可在设置切换中文（仅界面文案，内容/翻译按你的语言设置）。
- 品牌更名 **Moonlight → Gloss（旁注）**，新 logo 与封面海报。

### ⚙️ 质量 / Quality
- **异步操作不被打断**：总结/笔记/导图/翻译/解析/学术检索在切换标签后仍继续。*Long ops survive tab switches.*
- **侧栏常驻**：切换标签保留滚动位置与思维导图视图。*Panels stay mounted — scroll & mind-map viewport preserved.*
- 生成结果持久缓存，重进不重生成。

---

> 完整功能说明见 [README.md](./README.md) / [中文文档](./README.zh.md)。

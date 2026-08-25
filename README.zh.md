<p align="center">
  <img src="./docs/logo.svg" width="84" alt="Gloss logo" />
</p>

<h1 align="center">Gloss · 旁注</h1>

<p align="center"><b>照亮论文的批注 · 本地自托管的 AI 论文阅读助手</b></p>

<p align="center">
  <img src="./docs/cover.svg" width="760" alt="Gloss —— 一束光照亮一页论文，就像在字里行间写下的旁注" />
</p>

<p align="center"><sub><i>一束光，照亮一页论文 —— 就像在字里行间写下的旁注。</i></sub></p>

<p align="center"><a href="./README.md"><b>English →</b></a> · <a href="./CHANGELOG.md"><b>版本记录</b></a></p>

<p align="center">
  <a href="https://www.npmjs.com/package/gloss-local"><img src="https://img.shields.io/npm/v/gloss-local?label=npm" alt="npm 版本" /></a>
  <a href="https://github.com/computersniper/gloss/releases/latest"><img src="https://img.shields.io/github/v/release/computersniper/gloss?label=release" alt="GitHub 版本" /></a>
  <a href="https://github.com/computersniper/gloss/actions/workflows/ci.yml"><img src="https://github.com/computersniper/gloss/actions/workflows/ci.yml/badge.svg" alt="CI 状态" /></a>
</p>

<p align="center"><a href="https://computersniper.github.io/gloss-site/"><b>🌐 访问 Gloss 主页</b></a></p>

---

Gloss（旁注）是一个 **自托管、本地运行** 的科研论文阅读工具。它用一个 FastAPI 后端提供基于
React / PDF.js 的阅读器界面，并驱动一个大模型帮你速览、研读、翻译、答疑。

大模型 **默认使用你本机的 `claude` CLI**，
此外还支持本机的 `codex` CLI、Anthropic API，以及 **任意 OpenAI 兼容端点**（本地 vLLM、claude 代理等）。
它还能 **加载并运行 Claude / Codex 技能（skills）**，直接作用在你正在读的这篇论文上。

一切都跑在你自己的机器上：既可以使用 Windows 桌面应用，也可以在浏览器中打开网页版。

---

## 👀 预览

在同一个本地工作区里读论文、查看 AI 梳理的结构，并管理自己的论文库。想看可交互的产品介绍，
可访问 [Gloss 主页](https://computersniper.github.io/gloss-site/)。

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="./docs/reader-summary.png" alt="Gloss 阅读器：论文 PDF 旁展示 AI 生成的结构化摘要" />
    </td>
    <td width="25%" valign="top">
      <img src="./docs/app-mindmap.png" alt="Gloss 交互式思维导图与论文概念详情面板" />
    </td>
    <td width="25%" valign="top">
      <img src="./docs/app-extensions.png" alt="Gloss 插件市场：内置与已安装模块" />
    </td>
  </tr>
  <tr>
    <td valign="top"><b>阅读器演示</b><br />真正的 PDF 文本层与结构化 AI 速览并列，还能继续批注、对话与记笔记。</td>
    <td valign="top"><b>交互式思维导图</b><br />不离开阅读器即可浏览论文概念与它们之间的关联。</td>
    <td valign="top"><b>插件市场</b><br />按自己的工作流安装、打开或卸载阅读模块。</td>
  </tr>
</table>

<p align="center"><sub>以上为桌面应用截图；网页版使用同一套本地工作区。</sub></p>

---

## ✨ 功能一览

| 功能 | 说明 |
|---|---|
| 📖 **PDF 阅读器** | 基于 PDF.js，带真正可选中的文本层；选中文字即弹出操作（Explain / Translate / 加入会话 / 高亮） |
| 🧠 **速览 Summary** | 一键生成 TL;DR + 问题 / 方法 / 结果 / 创新点 / 局限 |
| 📝 **研读笔记 Notes** | 结构化精读笔记，可 **一键复制** 或 **下载 `.md`** |
| 🗺️ **思维导图 Mind Map** | 交互式 React Flow 导图：缩放、展开 / 折叠、点击节点看该节点的摘要与相互连接 |
| 💬 **对话 Chat** | 像同事一样提问 —— 基于全篇 **BM25 检索（RAG）** 作答，支持**持久化 Codex 会话**、自动且可编辑的标题、流式输出与多会话 |
| 💡 **解释 Explain** | 选中一段公式 / 术语 / 表格，一次性解释清楚；数学用 **KaTeX** 渲染 |
| 🌐 **翻译 Translate** | **逐句翻译**、结果 **持久缓存**、**跨页范围复用**；默认只显示中文，可切换 **显示原文**；**点击译文可在 PDF 中闪烁定位**；arXiv 论文可 **直接翻译 LaTeX 源**，不漏文本 |
| 📐 **arXiv LaTeX** | **TeX 源码标签**：读取论文 LaTeX 源（精确、无提取损失），并可从源翻译 |
| 🖍️ **自动高亮 Auto-highlight** | AI 按类别挑出关键句，并 **锚定到 PDF 原文位置** 标色 |
| ✍️ **批注 Annotations** | 支持 **Markdown + LaTeX 编辑/预览**的个人笔记、彩色高亮、铅笔、写字笔、荧光笔与橡皮擦，全部锚定在页面并本地保存 |
| 📎 **套索加入对话** | 在 PDF 上自由圈选区域，预览截图后直接作为附件加入 Chat |
| 🔗 **参考文献 References** | 解析文末参考文献，经 **Crossref / arXiv** 补全，**每条可点击跳转** |
| 🔭 **学术搜索 Scholar** | 通过 **Semantic Scholar / arXiv** 找相关论文，一键 **导入** |
| 📚 **论文库 Library** | 通过 **arXiv id / DOI / URL / 上传 PDF** 导入，集中管理 |
| 🧩 **技能 Skills** | 发现并运行 **Claude / Codex 技能**，作用于当前论文 |
| 🔌 **插件 Plugins** | 从内置插件市场安装或卸载可扩展的阅读面板 |
| ➕ **加入会话** | 在 PDF 或各面板里选中内容 → 一键送入 Chat，让 AI 就这段内容回答 |
| 🎨 **主题与阅读模式** | 内置 **7 套配色**，另有 PDF 阅读模式 —— **正常 / 护眼 / 夜间** |
| 🗣️ **多语言** | **界面语言**（English / 中文，默认英文），以及 AI **回答语言** 与 **翻译目标语言** |
| ⚙️ **体验** | 长任务切换标签也不中断；侧栏常驻，**滚动位置与思维导图视图都会保留**；每个提供方可 **测试连通性** |

插件作者可查阅声明式的 [Gloss 插件开发规范](docs/plugin-authoring.zh.md)。可取消
任务、阶段进度、模型设置、共享上下文模式与默认研究助手均由宿主统一提供，因此
最小插件只需声明分析提示词与侧栏入口。

---

## 🚀 安装与运行

### 交给 Claude Code 或 Codex 安装 😄

把下面这句话发给 Claude Code 或 Codex：

```text
请在这台电脑上安装并运行 https://github.com/computersniper/gloss。选择当前系统最简单的安装方式，保留已有 Gloss 数据，确认 /api/health 正常后告诉我本地地址。
```

Gloss 同时提供原生 Windows 桌面应用，以及可在 Windows、Linux、macOS 本地运行的浏览器网页版。两者的后端、论文与生成结果都保存在你自己的电脑上。

### Windows 桌面应用

下载 [Gloss-windows-x64.exe](https://github.com/computersniper/gloss/releases/latest/download/Gloss-windows-x64.exe)，双击即可在独立桌面窗口中运行；不会显示命令行窗口，也不会打开浏览器标签页。如果默认端口被占用，Gloss 会自动选择可用端口。

### 选择发布版本

每个标签版本都会提供 Windows 便携桌面包，以及用于本地网页版的 npm 包。请在
[最新 Release](https://github.com/computersniper/gloss/releases/latest) 中选择适合你电脑的方式：

| 平台 | 启动方式 |
| --- | --- |
| Windows | 下载 [Gloss-windows-x64.exe](https://github.com/computersniper/gloss/releases/latest/download/Gloss-windows-x64.exe) |
| Linux / macOS | 使用下一节 npm 或 Bun 启动本地网页版 |

Windows 下载均为独立 `.exe`，无需解压。各平台的注意事项见
[发布下载说明](docs/releases.md)。

### 本地网页版——Windows、Linux、macOS，使用 npm 或 Bun

需要 Node.js 18+ 和 Python 3.11+：

```bash
node --version
python --version
npm --version          # 或：bun --version

npx gloss-local@latest
# 或
bunx gloss-local@latest
```

启动后，在浏览器中打开终端显示的本地地址（通常是 `http://localhost:8010`）。

首次运行会自动创建隔离环境并安装后端依赖，以后直接复用。也可以运行
`npm install --global gloss-local` 或 `bun add --global gloss-local` 永久安装，再用 `gloss`
启动。

Linux 和 macOS 与上面完全使用同一组命令；无需原生 `.exe`、`.app` 或 `.dmg`。若 Debian / Ubuntu
首次运行提示缺少虚拟环境，请安装：`sudo apt install python3-venv`。

### 从源码运行（开发者）

需要 Git、Node.js 20.19+ 和 Python 3.11+：

```bash
git clone https://github.com/computersniper/gloss.git
cd gloss
npm run setup
npm start
# 或：bun run setup && bun run start
```

#### 系统原生脚本（可选）

```bash
# Linux
scripts/setup.sh
scripts/run.sh

# Windows PowerShell
.\scripts\setup.ps1
.\scripts\run.ps1
```

打开 `http://localhost:8010`。可以用 `--port` 和 `--host` 修改地址，例如：
`npx gloss-local@latest --port 6006 --host 127.0.0.1`。

### 远程 Linux / SSH

先在服务器启动 Gloss，再从本机建立 SSH 隧道：

```bash
# 远程服务器
npx gloss-local@latest --host 127.0.0.1 --port 8010

# 本机
ssh -N -L 8010:127.0.0.1:8010 -p <SSH端口> user@server
```

然后在本机打开 `http://localhost:8010`。

数据默认保存在 Windows 的 `%LOCALAPPDATA%\Gloss\data`，以及 Linux 和 macOS 的
`$XDG_DATA_HOME/gloss`（默认为 `~/.local/share/gloss`）；可用 `GLOSS_DATA_DIR` 修改。

---

## 🧰 CLI

```bash
./gloss serve                  # Linux/macOS：启动 web 应用
.\gloss.ps1 serve              # Windows PowerShell
./gloss open <pdf|arxiv|doi>   # 导入 + 启动服务 + 打开浏览器
./gloss import <src>           # 把本地 PDF / arXiv / DOI 加入论文库
./gloss ls                     # 列出论文库
./gloss summarize <id|pdf|arxiv>   # 在终端打印一份速览
./gloss chat <id>              # 终端里的交互式对话
./gloss skills                 # 列出发现到的 Claude / Codex 技能
```

例如：`./gloss open 1706.03762` 打开《Attention Is All You Need》并启动阅读器。

---

## 🤖 AI 提供方与进阶设置

在 **Settings** 里选择 provider（生成的 `config.json` 位于数据目录中）：

| Provider | 说明 | 需要 key？ |
|---|---|---|
| `local_claude` | **默认**。调用本机 `claude` CLI，走订阅登录 | ❌ 免 key |
| `local_codex` | 调用本机 `codex` CLI，走 ChatGPT 订阅 | ❌ 免 key |
| `anthropic` | Anthropic API，或任意 Anthropic 兼容端点 | ✅ 填 `base_url` + `api_key` |
| `openai` | 官方 OpenAI **或任意 OpenAI 兼容端点** | ✅ 填 `base_url` + `api_key` |

`openai` 可指向 **本地 vLLM** 或本地 **claude 代理**（如 `http://127.0.0.1:8899/v1`），完全离线用自己的模型。

**进阶设置**（每个 provider 可单独配）：模型（如 `sonnet` / `opus`、完整 model id，或留空用 CLI 默认）与
**思考深度 / reasoning effort**（`local_claude`：`low … max`；`local_codex`：`minimal … high`）。**回答语言** 与
**翻译目标语言** 也都可配（默认均为中文）。key 会被 Settings API 掩码，运行时数据目录不进入版本库。

---

## 🏗️ 架构简述

```
backend/          FastAPI（一个进程挂载所有 /api/*，并托管已构建的前端 SPA）
  app/providers/  local_claude · local_codex · anthropic · openai · registry（LLM 抽象层）
  app/pdf/        PyMuPDF 解析（文本块 + bbox）+ 结构启发式（章节/引用/分句）
  app/features/   summarize · explain · translate · chat(RAG) · highlight · notes · mindmap · citations
  app/search/     scholar 检索 / 推荐（Semantic Scholar / arXiv）
  app/skills/     发现并运行 Claude/Codex 的 SKILL.md 技能
  app/library/    SQLite 库 + 导入器（arXiv/DOI/PDF）
  app/routers/    papers · ai · citations · scholar · annotations · skills · settings
frontend/         React + Vite + pdfjs-dist + KaTeX + React Flow（build 到 frontend/dist，由后端托管）
cli/ gloss.py     scripts/ gloss.mjs · setup/run/dev（.sh + .ps1）
```

**数据** 位于上文所述的系统数据目录：一个 SQLite 库，加上每篇论文的目录，
其中包含 `original.pdf` 与解析后的 `parsed.json`。

---

## 💡 小贴士

- **本地 `claude` / `codex` 独立运行**：走你已有的订阅登录（无需 API key），每次回答只针对当前论文，
  不会和你个人的 Claude / Codex 配置混在一起。
- **数据都在本地**：论文、批注、笔记、对话都存在系统数据目录中的 SQLite 库与每篇论文的文件里；
  除下面的外部检索外，数据不出你的机器。
- **外部检索优雅降级**：References、Scholar 等外网请求统一走机器的 HTTP 代理；某个站点连不上（离线）时会
  **优雅降级** 到本地已解析数据，不会整页报错。
- **生成结果持久缓存**：总结 / 笔记 / 导图 / 翻译 / 高亮 首次生成后落盘保存，重进论文直接复用，不重复消耗算力与额度。

---

## 🙏 致谢

Gloss 是对 **[Moonlight](https://www.themoonlight.io/)**（"陪你读论文的 AI 同事"）的一次 **本地自托管重构** ——
从零重写，让它完全跑在你自己的机器上。设计上还参考了两个很棒的项目：

- **[Understand-Anything](https://github.com/Egonex-AI/Understand-Anything)** —— 交互式节点连线 **思维导图**
  （React Flow、点击聚焦、可折叠节点）的灵感来源。
- **[DeepPaperNote](https://github.com/917Dhj/DeepPaperNote)** —— 结构化 **研读笔记** 的灵感来源。

衷心感谢以上三个项目的作者。🙏

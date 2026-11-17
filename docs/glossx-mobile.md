# GlossX 移动版（iPad / iPhone / Android）

目标：iPad、iPhone、Android 三端共用一套代码。**iPad 先做**，然后是 iPhone，最后是 Android。

移动端不运行 Claude Code / Codex，提供两种独立入口：

- **连接电脑**：连接电脑上正在运行的 GlossX，由电脑上的 CLI 干活，使用电脑的论文、对话和工作区，不复制到手机。
- **本机 API 工作区**：App 自带同一份 React 前端，使用自己填写的 OpenAI 兼容 API 与模型直接对话，不依赖电脑。设置与对话保存在这台设备，与电脑的数据隔离。界面可以离线打开，模型回复仍需要网络；本机模式不会提供电脑上的终端、CLI 或文件系统。

2026-10-11 用户要求推进本机 API，覆盖此前“直接调用 API 不在范围”的规划；保留既有 Capacitor 薄壳与共享前端方案。不在本期范围内：离线模型推理、上架 App Store / Google Play。

## 多平台代码怎么组织

**建议：界面只有一份（`frontend/`），每个平台只有一层很薄的原生壳（`mobile/`）。** 平台之间的差异靠运行时判断来处理，不复制代码，也不拉分支。

```
frontend/            唯一的界面代码（React），桌面、浏览器、平板、手机共用
  src/mobile.ts        判断是不是移动 App、手机尺寸、⌘/Ctrl 等平台差异
  src/glossx.css       用 .compact（手机）、.narrow（平板竖屏）、.gx-mobile 区分布局
backend/             唯一的后端；移动端通过它调用电脑上的 CLI
  app/remote_access.py 配对码校验，以及“允许移动设备”开关
mobile/              Capacitor 原生壳，三端共用一份配置
  capacitor.config.json
  www/                 App 自带的模式选择/电脑连接页
    local/             构建生成的共享 React 前端，不提交生成产物
  ios/                 Xcode 工程（iPhone 和 iPad 是同一个 App）
  android/             Gradle 工程
  assets/              图标和启动图的源文件
desktop/             Electron 桌面壳（不受影响）
```

为什么这样组织：

| 做法 | 结论 | 原因 |
| --- | --- | --- |
| 界面只有一份，原生壳很薄（Capacitor） | 采用 | 桌面端的改动，移动端不用重新装 App 就能用上；三端行为一致 |
| 每个平台各写一套界面 | 不采用 | 每个功能要写三遍，迟早会对不上 |
| React Native / Expo | 不采用 | PDF 阅读器和工作台都得重写 |
| Flutter | 不采用 | 要换语言，前端全部重写 |

约定：

1. 平台差异写成运行时判断（`isMobileClient()`、`useCompact()`、`(pointer: coarse)`），不要按平台复制组件。
2. 只有确实需要原生能力（相机扫码、推送、分享面板）时，才往 `mobile/ios` 或 `mobile/android` 加代码，而且优先用 Capacitor 插件。
3. 原生工程 `mobile/ios` 和 `mobile/android` 提交进仓库，因为里面有手改的配置（明文局域网访问、`glossx://` 链接、本地网络权限）。`npx cap sync` 只复制文件，不会覆盖这些配置。
4. CI 按平台分别检查：**Mobile build** 工作流会打出 Android 调试 APK，并检查 iOS 工程能否编译。

## 连接方式

不需要 Mac、不需要 Apple 开发者账号、也不需要装 Tailscale：手机直接用浏览器，扫码就配对。

**在家里（同一个 Wi‑Fi）**

1. 电脑上打开 GlossX → 设置 → 移动端访问，打开“允许”后重启。桌面 App 会改为在局域网的固定端口 8010 上监听（`npm start` 本来就对局域网开放）。
2. 设置页显示一个二维码，里面是一次性登录链接 `http://<电脑IP>:8010/pair?c=<一次性码>`。
3. 用 iPhone、iPad 或安卓手机的相机扫码，浏览器打开后就已登录。点“分享 → 添加到主屏幕”，以后从主屏幕打开就是全屏，和 App 一样。

**在外面（外网访问）**

1. 同一个设置页打开“外网访问”。第一次会自动下载 Cloudflare 的 `cloudflared`（也可以自己装好放进 PATH，或用 `GLOSS_CLOUDFLARED` 指定路径）。
2. 它建立一个免费的 Cloudflare 快速隧道，得到 `https://<随机词>.trycloudflare.com` 地址，二维码切到“外网”就是这个地址的一次性登录链接。不用开路由器端口，不用注册 Cloudflare 账号。即使电脑只在本机监听（没打开“允许”），外网访问也能用。
3. 隧道地址每次重启 GlossX 或隧道都会变，变了以后手机重新扫一次码。

原生 App（`mobile/`）仍然保留，可以手动填地址和配对码连接；但要装到 iPhone 上需要 Mac 和 Xcode，所以 iPhone 优先用上面的浏览器方式。

**本机 API 模式**

在 App 首页点“打开本机 API 工作区”，进入自带的 `local/index.html?mode=local`。填写 OpenAI 兼容服务地址、模型和自己的 API key。返回连接页可随时改为连接电脑。启动 App 始终显示两种入口，已保存的电脑点选后连接，不自动跳走。

共享前端开发时也可以访问 `?mode=local`。浏览器直接调用服务受到服务端 CORS 限制；原生 App 使用 Capacitor HTTP。API key 不发送给连接的电脑，也不与电脑配置自动同步。

安全规则：

- 本机访问和以前一样不需要登录；其他设备必须先配对，否则返回 401。经隧道过来的请求虽然来自本机的 cloudflared，但带有 Cloudflare 的转发头，一律当作其他设备。
- 二维码 10 分钟有效、只能用一次，生成新码会让旧码失效。扫码成功后发给这台设备一个设备令牌（服务端只存哈希），最多 8 台设备，可以在设置页逐个移除。
- 手动连接用配对码（`?token=`、Cookie 或请求头都可以）。“换配对码”会同时移除所有已配对设备。
- 同一来源 60 秒内失败 30 次会暂时拒绝（429），防止猜码。
- 只有电脑本机能查看配对码、开关这些功能。终端功能仍然只限本机使用。
- 局域网连接是明文 http，只在自己的 Wi‑Fi 里用；外网走 https，但内容会经过 Cloudflare。`GLOSS_REMOTE_AUTH=off` 可以关掉校验，仅适用于外面已有认证代理的情况。

## 分阶段计划

| 阶段 | 内容 | 验收标准 |
| --- | --- | --- |
| 1. iPad | 连接与配对；双指缩放 PDF；论文默认按页宽打开；竖屏时侧栏和 AI 面板只开一个；分栏可以用手指拖动；外接键盘显示 ⌘ 快捷键；Apple Pencil 标注 | 用 Xcode 装到自己的 iPad 上，能连上电脑、读论文、和智能体对话、批准工具调用 |
| 2. iPhone | 底部标签栏（阅读 / 论文库 / 项目 / 智能体 / 设置 / 更多），一次只显示一个面板；适配刘海和 Home 条安全区 | 在 iPhone 上走通同样的流程，输入框不被键盘挡住 |
| 3. Android | 同一套代码打包 APK，由 CI 自动产出调试版 | 从 CI 下载 APK，装到安卓手机上走通同样的流程 |

## 当前进度

| 项目 | 状态 |
| --- | --- |
| 后端配对码、移动端访问开关、二维码 | 已完成，有测试 |
| 扫码网页配对、添加到主屏幕（PWA）、设备管理 | 已完成，有测试，在浏览器里以 iPhone 尺寸测过 |
| 外网访问（Cloudflare 快速隧道） | 已完成，有测试（用模拟的 cloudflared） |
| Capacitor 原生壳、连接页、图标 | 已完成 |
| 原生壳本机 API / 连接电脑双入口、内置共享前端 | 已实现构建与接线，原生设备验收待做 |
| iPad：双指缩放、按页宽打开、竖屏侧栏和 AI 面板只开一个 | 已完成，在浏览器里以 iPad 尺寸测过 |
| iPhone 底部标签栏布局 | 已完成，在浏览器里以手机尺寸测过 |
| Apple Pencil 标注 | 未验证 |
| iOS / Android 原生编译 | 由 Mobile build 工作流验证 |
| 装原生 App 到 iPhone / iPad | 需要 Mac 和 Xcode，见 `mobile/README.md`；没有 Mac 时用浏览器扫码 |

## 待定问题

- 第一阶段是不是只给自己用（用 Xcode 直接安装），暂时不走 TestFlight？
- 本机 API 模式的前端已打包进 App，更新它需要重新构建 App；连接电脑模式仍使用电脑上的最新前端。

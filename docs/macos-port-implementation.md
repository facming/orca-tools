# macOS 模块实施与安全方案

本文件配合 [macOS 移植技术方案](macos-port-plan.md) 使用。所有功能必须逐模块验收；不支持的功能明确显示 unsupported，不影响其他模块。

## 1. Node.js 平台 runtime

建议 runtime 工厂统一提供：

- platform：win32 / darwin。
- home、dataDir、configDir、cacheDir、logDir。
- orcaCli、RuntimeClient SDK resolver。
- ssh、sshKeygen。
- openUrl(url)、openPath(path)、chooseDirectory(initialPath)。
- spawnDetached(executable, args, options)。
- 安全目录创建、配置读写和文件权限管理。

macOS 建议目录（须按 Orca 沙盒/应用约定确认）：

| 内容 | 建议位置 |
| --- | --- |
| Toolkit 数据 | ~/Library/Application Support/OrcaTools/ |
| GPU Bridge 配置/token/任务 | ~/Library/Application Support/LocalGpuBridge/ |
| 日志 | ~/Library/Logs/LocalGpuBridge/ |
| 缓存 | ~/Library/Caches/LocalGpuBridge/ |
| 用户自启动 | ~/Library/LaunchAgents/ |

私有配置/token 文件建议 0600，密钥目录 0700。密钥不得进入 Git、日志、命令行参数或网页响应。

## 2. Accounts

复用账号列表、邮箱遮罩、用量快照验证、切换状态和错误处理。适配真实 Orca SDK/CLI 路径、user-data path、environment 和 RPC。远程 OAuth 凭据继续留在 VPS/远程 Orca，不下载到 Mac；保留账号归属验证。

**阶段 2 实现边界（2026-11-05）：** Mac 的 `GET /api/accounts` 只返回当前进程中的脱敏快照或 preview 提示，`GET /api/status` 只读本地状态；初始化、激活、页面打开及总览计数不构造 RuntimeClient，不调用 RPC。明确点击刷新时，通过既有 `POST /api/accounts/refresh` 使用用户配置的 environment；选择或禁用操作继续做远程前置校验。RuntimeClient 使用平台层已核验的 `userDataPath`、`cli` 和 `loadSdk()`，保留 30 秒超时，不额外推断或读取 profile。SDK 可能更新 environment 最近使用状态，页面在首次刷新前提示该行为。

Accounts 只有在请求成功、`isRemote`、`ok`、`_meta.runtimeId`、账号响应及本地偏好均通过校验后显示 ready；这是最近一次成功请求的状态，并非持续连接探测。失败或 environment 更改会清除快照和 ready，下一次访问必须由用户明确触发。超时不自动重试账号切换，也不报告成功。配对修复后，下一次显式请求重新构造失败的 client。旧 SDK 用量双快照验证及 Windows 的现有读取流程保留。

Mac 偏好文件位于 `runtime.configDir`：目录 0700，文件 0600，只保存 environment 和禁用的账号 ID。损坏文件、外部改动、不安全权限、符号链接均拒绝覆盖；写入使用独占临时文件和原子重命名。当前仅 mock 测试及 mock 浏览器交互通过，真实 RPC 验收仍待代码审查和人工授权。Downloads / GPU 不在本阶段开放。

**验收（仍待真实 RPC 授权）：** 至少两个远程账号正确显示；未知/过期/不可用用量如实呈现；切换后新建远程 Claude 会话使用目标账号；不声称切换会迁移已有会话。

## 3. Downloads

使用 macOS 系统 OpenSSH。每任务独立 Ed25519 key；私钥目录 0700、私钥 0600；保留 host-key 严格校验和远程只读 forced-command reader。

目录选择优先 Orca/宿主原生能力；若使用 osascript，脚本必须固定、用户路径作为参数传递，禁止拼接脚本源代码。打开下载目录使用 macOS open。验证中文/空格路径、大文件、断点恢复、磁盘不足、取消和异常退出。

**验收：** 并行下载、暂停/继续、断点恢复、重试、SHA-256 全通过；远程权限不弱于 Windows；取消目录选择不改变原设置。

## 4. GPU：分阶段实现

### Phase A：ComfyUI Bridge（优先交付）

复用 bridge.py、ComfyUI HTTP adapter、workflow library、任务/产物管理、token、peer allowlist 和 VPS MCP。

新增 Mac 安装器：
- 选择并验证 Python、ComfyUI 根目录、输入/输出和 workflow 目录。
- 生成本机私有 config、随机 token 和 launcher。
- 安装当前用户 LaunchAgent；支持启动、停止、状态、日志和卸载。
- ComfyUI 默认只监听 127.0.0.1；Bridge 只监听配置的 Tailscale 地址。
- 不要求 root 常驻服务，不把 token 写入 Git、终端输出或远程返回。
- Apple Silicon 验证 PyTorch MPS，不假设 CUDA/OptiX。

**验收：** VPS Claude 可列 workflow、提交任务、查状态、取消并下载产物；错误 token/非 allowlist 来源被拒绝；公网无法访问 Bridge；服务重启状态正确。

### Phase B：Godot / Blender / Unity

每个应用独立适配、独立验收：

- **Godot**：验证 macOS 可执行文件、参数、截图和子进程控制。
- **Blender**：验证 Metal、脚本、渲染和产物；CPU 明确回退。
- **Unity**：移除 D3D11 参数，验证 macOS Editor、CLI 和 MCP。

每个应用必须有 installed/available/unsupported 明确状态。不能因 ComfyUI 可用就把其他应用标记在线。

### Phase C：VRoid / Tripo

单独验证 macOS 软件版本、插件注入、浏览器 profile、登录态和凭据存储。未验证的功能保持 disabled/unsupported，不影响其他模块。

## 5. macOS 安全边界

Windows Job Objects 和 Defender Firewall 不能直接等同于 macOS 能力。PF 不是按应用路径工作的等价应用级防火墙；不能为了复刻 Windows 而部署未经审查的全局 PF 规则。

- 管理页仅绑定 127.0.0.1；Bridge 只绑定指定 Tailscale 地址。
- Tailscale peer allowlist、Bearer token、严格路径校验继续保留。
- LaunchAgent 以当前普通用户运行；不要求 root 常驻。
- 项目脚本拥有运行用户权限，文本过滤不构成沙箱；不可信项目禁止执行。
- 在验证可靠的 macOS 出站隔离方案前，不允许显示 Network privacy ready，不允许宣称具有 Windows 版相同隔离能力。
- 必要时先交付 ComfyUI Bridge，将原生脚本执行标记为受限或暂不支持。
- 权限提升、系统扩展、TCC 文件授权、防火墙规则和删除/覆盖行为必须由用户明确确认。

## 6. 测试要求

- Node 平台 adapter：路径发现、目录选择、open、spawn、SSH keygen 参数及错误分支。
- Downloads：并行/续传/重试/hash、中文路径、权限、取消、磁盘空间错误。
- Python Bridge：配置权限、LaunchAgent 生命周期、监听地址、peer/token 校验、任务重启恢复。
- 原生应用：每个应用独立的版本、架构、GPU backend、启动、超时、取消和产物验收。
- 至少覆盖 Apple Silicon arm64；Intel x86_64 是否支持须实际验证后再承诺。
- 每阶段保留 Windows 测试，防止跨平台改造引入回归。

## 7. 明确禁止

- 不绕过 Orca 插件审核、应用签名、沙盒或 TCC 授权。
- 不开放公网管理端口，不关闭 SSH host-key 校验，不添加通用 shell。
- 不提交 token、OAuth、SSH 私钥、真实 IP、用户目录或本机配置。
- 不因测试失败而伪造 ready/connected/complete 状态。
- 不把 Windows Job Objects/Firewall 的能力直接映射成 Mac 安全保证。
- 不自动执行不可信项目脚本，不自动覆盖用户文件或清理未跟踪产物。

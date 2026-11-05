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

**阶段 3 实现边界（2026-11-05）：** Controller 在配置完整且平台 CLI、SDK、OpenSSH 可用时初始化 DownloadManager。初始化和被动页面读取不会连接 VPS 或生成密钥；初始状态为 configured（远程尚未检查），未配置 environment 为 needs-setup，依赖或请求失败为 error/offline。ready 仅代表最近一次远程请求成功，不代表持续连接。Accounts 保持阶段 2，GPU 保持 unsupported。

Transport 使用 `runtime.cli/ssh/keygen/childEnv/downloadsDir`。每任务 Ed25519 密钥和 known_hosts 独立，Mac 私有目录 0700、私钥/known_hosts/任务 JSON/partial 文件 0600；不安全权限、符号链接及异常元数据失败关闭。SSH 隔离用户配置、agent 和全局 known_hosts，使用 StrictHostKeyChecking=yes、HostKeyAlias 和来自 Orca 受信通道的 host key；同任务 host key 改变时拒绝覆盖 pin。cleanup 只有得到 SSH `KEY_REMOVED` 或既有 Orca 清理回执才删除本地密钥，失败保留 Clear access 提示，阻止更换远程目标。

目录浏览复用 RuntimeClient 的 `files.browseServerDir`；项目根来自既有 `orca repo list --environment … --json`。Projects 返回项目入口，拒绝 `/`、项目外路径、点段、控制字符、反斜杠和相似前缀。进入子目录前逐层确认目录且 `isSymlink === false`；不跟随符号链接，回包必须确认远程身份、POSIX 路径与当前项目路径。隐藏文件、过滤和每页 200 项保留。下载准备在既有固定脚本内解析源文件真实路径并检查项目包含关系，之后调用未改协议的 Python reader；没有新增通用 shell 入口。SDK 若不能确认目录类型/符号链接信息，浏览失败关闭，留待真实兼容性验收。

引擎保留任务模型、1–16 并发、全局 16 连接预算及重试逻辑。补充原子 0600 元数据写入、落盘分块 SHA-256 的恢复校验、完整写入处理、Mac 单管理进程锁、死进程锁恢复和 APFS 硬链接发布检查点。启动后任务保持 paused，只有 Resume 才继续远程操作。发布中断仅在目标 inode 和 SHA-256 都匹配时认定是本任务产物；现有文件不会被覆盖。非硬链接文件系统的复制回退若在发布期间中断，可能需要人工检查，仍不覆盖目标。

默认目的地为 `~/Downloads/Orca`，目录按需创建；已有原生目录选择器与偏好逻辑保留，取消不改设置，支持中文、空格及较长 Mac 路径。权限拒绝给出 Files and Folders / 改选目录建议，磁盘不足给出释放空间提示，不请求提权。完成卡片的 Open folder 经 `runtime.openPath` 打开 Finder。

**离线验收与待办：** 本阶段全部传输、RuntimeClient、SSH/keygen 使用临时目录、mock 与合成字节；Python reader 的注册/读取/清理只在隔离 HOME 执行，host public key 为合成值。实际 APFS 的并行写入、权限、硬链接和 SIGKILL 恢复已离线验证；TCC 拒绝仅为 EPERM/EACCES 模拟，Finder 启动为参数/路由夹具。真实 VPS 传输、真实 host-key 变更、SDK 远程回包和真实 TCC 授权仍未验收。宿主空闲 worker 回收限制继续存在，关闭/重开后须恢复任务，不增加保活绕过机制。具体测试结果和人工步骤见兼容性审计第 11 节。


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

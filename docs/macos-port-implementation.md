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

## 8. 阶段 4：macOS ComfyUI Bridge 实现（2026-11-05）

### 复用与协议审计

| 文件 | 本阶段职责 |
| --- | --- |
| `services/local-gpu-bridge/bridge.py` | 复用 HTTP 服务、Bearer 常量时间比较、任务提交/查询/取消、持久化和产物流；加入平台能力边界，Mac 在进入 native 路由前拒绝。 |
| `workflow_library.py` / `job_reuse.py` / `remote_privacy.py` | 工作流 API/editor 转换、有限参数、幂等 request_id、重启恢复和远程回包脱敏继续共用。 |
| `comfy_startup.py` | 共用固定启动参数和回环健康检查；拒绝 HTTP redirect。Windows 自动启动行为保留。 |
| `bridge_platform/{base,windows,macos}.py` | 平台能力接口、原 Windows native 控制、Mac 路径/环境/MPS/进程/LaunchAgent/Tailscale 校验。包名避免遮蔽标准库 `platform`。 |
| `macos_service.py` / `scripts/install-local-gpu-macos.py` | Mac 专用服务、仅回环管理接口和独立安装/管理入口。Windows 安装器只增加复制平台包。 |
| `plugin/client.py` | VPS MCP 不改动；测试使用原 Client 和现有 stdio MCP，用合成服务代替 VPS。 |
| Toolkit `modules/gpu.cjs` / `gpu-macos.cjs` | Windows 路径保留，Mac 增加权限检查、实际健康请求和专用面板。Accounts/Downloads 生产逻辑不变。 |

Windows `config.json` 保留内联 token、data_dir、listen、allowed_peers、comfy_url/comfy_startup 和 native/tripo 配置；原 launcher 格式仍为 `{pythonw: <existing pythonw.exe>}`。Mac config 不存 token，独立 token 文件按 0600 管理，launcher 使用 `{platform: "darwin", python, label, manager_url}`，不伪造 pythonw。

HTTP 协议保持：`GET /v1/status`、`GET /v1/workflows`、`GET/POST /v1/jobs`、`GET /v1/jobs/{id}`、`POST /v1/jobs/{id}/cancel`、`GET /v1/artifacts/{id}/{index}`、`POST /v1/assets`。提交仍为 `{workflow_id, parameters, request_id}`，request_id 为 32 位十六进制。任务只允许修改已暴露的 literal 参数，保留类型、范围、enum、asset 相对路径限制。

ComfyUI 仍使用 `/queue`、`/prompt`、`/history/{prompt_id}`、`/view`、`/upload/image`、`/object_info`。取消只删除本 Bridge 的排队 prompt；运行中任务明确拒绝全局 interrupt，与 MCP `cancel_pending_job` 的既有契约一致。停止托管 ComfyUI 是本机显式管理动作，会终止它正在处理的任务。重启读取原 jobs，不自动重投提交结果不确定的任务；已完成产物继续通过原下载接口读取。

### 安装与管理

只支持原生 macOS arm64、Python >= 3.10。用户选择已经安装且信任的 ComfyUI 根目录、Python 环境和目录；默认发现根目录下 `.venv/bin/python3` 或 `venv/bin/python3`，保留 venv 可执行文件路径而不解引用到系统 Python。检测使用独立 `-I` Python probe，导入 torch 及必要基础依赖并读取 MPS built/available；失败明确拒绝安装/启动，不运行 pip、不下载模型、不改环境。

```sh
python3 scripts/install-local-gpu-macos.py install --settings /path/to/private-settings.json
# 先审阅预览，再由用户明确执行：
python3 scripts/install-local-gpu-macos.py install --settings /path/to/private-settings.json --confirm
python3 scripts/install-local-gpu-macos.py status
python3 scripts/install-local-gpu-macos.py stop
python3 scripts/install-local-gpu-macos.py start
python3 scripts/install-local-gpu-macos.py logs
python3 scripts/install-local-gpu-macos.py uninstall
python3 scripts/install-local-gpu-macos.py uninstall --confirm
```

不带 `--settings` 时交互选择 ComfyUI 根目录、Python 和 Tailscale 地址。settings 允许字段为 `comfy_root`、`comfy_python`、`input_directory`、`output_directory`、`workflow_dirs`、`listen`、`allowed_peers`、`tailscale`；未知字段拒绝。输入/输出默认使用 ComfyUI 的 `input` / `output`，workflow 默认 `user/default/workflows`，全部要求已有目录。Tailscale CLI 从 PATH 发现或由用户指定绝对路径；不猜测 app bundle、不改变网络配置。

默认存储：

- `~/Library/Application Support/LocalGpuBridge/`：app、config.json、token、launcher.json、client.local.json、jobs、workflows。
- `~/Library/Logs/LocalGpuBridge/`：固定诊断和 LaunchAgent 日志。
- `~/Library/Caches/LocalGpuBridge/`：预留私有缓存目录。
- `~/Library/LaunchAgents/org.orca.local-gpu-bridge.plist`：当前用户服务。

Bridge 自有目录 0700，token/config/launcher/client export 和 app 文件 0600；umask 077。检查属主、类型和权限，拒绝符号链接。已有安装不覆盖；失败后保留已创建内容供人工检查，不自动清理。已有 LaunchAgents 父目录可以是安全的 0755，不擅自 chmod 共享目录。plist 使用参数数组、`-I`、当前用户 GUI domain，无 shell、无 token 参数、无 root LaunchDaemon。显式拒绝 root/euid 提权用户；start/stop/uninstall 先校验私有 launcher 元数据及完整 plist 与受管模板一致。`KeepAlive=false`，异常退出不自动重启；用户显式 `start` 对已加载但退出的 agent 使用 kickstart，对未注册的使用 bootstrap；`stop` 使用 bootout。每次启动重新完成配置、Token、本机接口和 Tailscale 身份校验。卸载先预览，仅停止服务并删除自己的 plist/launcher；保留全部应用、配置、token、任务、缓存和日志，更不会删除 ComfyUI、模型或工作流。重装与升级保留人工审查边界，不自动覆盖残留安装。

安装会注册并启动 Bridge，但不会自动启动 ComfyUI。用户在 `http://127.0.0.1:18773/` 点击 Start，选择的可信 ComfyUI 才会执行。专用进程组支持正常停止、超时终止和必要的 SIGKILL；不从持久化 PID 杀进程，不接管外部 ComfyUI。如果端口已被手动启动的实例占用，拒绝托管启动并提示人工处理。

启动固定 `--listen 127.0.0.1`，禁用 custom nodes、API nodes 和浏览器自动打开；启动探测、每次 Bridge 后端调用前及后台监测均用系统 lsof 核验 8188 端口的实际监听地址为 127.0.0.1、PID 属于当前持有的子进程，发现 wildcard、IPv6、Tailscale 地址、其他 PID 或探测异常时拒绝访问并停止自己的子进程；旧版本缺少禁用参数时拒绝安装。MPS 可用时使用 ComfyUI 的原生 MPS 选择，不添加 CUDA/OptiX 参数；不可用则明确报告 `backend=cpu, cpu_fallback=true` 并加入 `--cpu`。自定义节点工作流不在本阶段支持范围。ComfyUI 本身必须是用户审核过的可信安装，禁用节点不是操作系统沙箱。第三方 stdout/stderr 不转存，避免敏感内容进入 Bridge 日志；日志只记录固定诊断，调试输出需用户另外在可信本地环境检查。

### 网络与 ready

Bridge 启动前要求 Tailscale `BackendState=Running`、Self 在线、指定地址位于 `100.64.0.0/10` 且属于 Self，并用系统 ifconfig 确认该地址实际存在于本机 utun 接口。绑定后、开始接收请求前再次验证。安装时记录 Self 与批准 peer 的稳定节点 ID。每个请求先验证 Bearer 和非浏览器 Origin，再实时读取 Tailscale status，核对本机地址/身份、peer IP/节点 ID/在线状态。不接受 X-Forwarded-For 身份。未授权、Tailscale 离线、身份/IP 变化、CLI 超时均拒绝；启动失败不换地址，禁止公网、IPv6、LAN 和 wildcard。后台每轮间隔 1 秒重新检查配置、Tailscale 和本机接口；探测命令分别有 4 秒超时。发现故障立即锁定拒绝远程请求，并关闭监听及已接受的 keep-alive/下载连接、管理端口和受管 ComfyUI。恢复网络不会清除锁定，必须显式 start 并通过全部检查。登录时网络尚未就绪而退出的服务也需用户显式 start。

远程 API 和本地管理 Bearer 逐请求读取 0600 Token 文件，原子替换为有效 Token 后旧值立即失效；远程鉴权还在系统探测后重读 Token，避免探测期间轮换仍放行旧值。其他配置变化须重启；无效 Token/权限异常失败关闭。Token 轮换不自动改写已有 client export，客户端需经私密渠道同步。

管理服务只监听 127.0.0.1，检查 Host、会话 token、Origin 和固定路由；远程 Bearer 不写入 HTML。Toolkit 读私有 token 后调用 `/api/health`。只有 installed、launch_agent、tailscale、token、peers、comfyui 六项均真且受管进程及 `/queue` 实际通过时显示 ready。launcher 存在不能判定 ready，token 权限异常、服务/ComfyUI 离线时为 offline。Mac 不自动运行 Windows launcher/native；Toolkit activation 不自动启动 GPU。

没有 Job Objects/Windows Firewall 等价承诺，没有 PF、全局防火墙、通用 shell 或远程脚本接口。原 VPS 客户端的 metadata privacy prerequisite 不变；本阶段没有修改真实 VPS 权限。真实安装、可信代码选择、Tailscale 配置、私密 client export 传输及 GPU 渲染仍等待 ChatGPT 审查和人工批准。

## 9. 阶段 5A Godot：实施前复用清单（2026-11-05）

| 文件 | 审计结论与复用边界 |
| --- | --- |
| `native_control.py` | 复用项目 ID、`engine/operation/arguments/request_id` 任务信封及状态/产物语义；不实例化 Windows NativeControl，不复用自动 Git reset、同步或任意脚本代理。 |
| `native_process.py` | Windows Job Objects、PowerShell/COM 出站校验保持原样；macOS 单独持有 Popen 子进程，不接管保存的 PID 或外部编辑器。 |
| `bridge.py` | 保留认证、逐请求网络校验、ComfyUI 路由；Mac 原生路由采用显式 allowlist，其他操作返回 unsupported。 |
| `native_blender.py` | 仅参考异步任务、超时和产物登记结构；不导入 bpy，不变更 Blender 行为。 |
| `native_godot.gd` | 复用 SceneTree 场景截图脚本；Mac 不使用 Windows 离屏位置参数，不宣称桌面截图或无头 GPU 截图。 |
| `plugin/client.py` | 复用项目列表、任务状态/等待与产物下载；增加 Godot open/run/stop/logs 和显式脚本授权字段，不更改 ComfyUI tools。 |
| `install-local-gpu.py` / Windows settings | Windows 使用配置 exe、复制托管 exe、Job Objects 和防火墙；全部保留。Mac 接受已有 app bundle 或明确配置的原生 Mach-O 文件，不复制/安装 Godot。 |
| `bridge_platform/macos.py` / `macos_service.py` | 扩展现有平台工厂和健康状态；Godot 与 ComfyUI 子进程、可用性独立。网络与 Token 安全门槛继续共用。 |
| Toolkit `gpu-macos.cjs` / panel | 保留 ComfyUI 六项 ready 检查；单独呈现 Godot installed/available/running/unsupported/error。 |

### 配置、发现和授权

Mac 安装 settings 可增加可选 `native`；字段与 Windows 的 `godot_executable/projects` 命名一致，但 Mac 严格拒绝其他引擎、Git 自动同步和通用执行配置。已经安装的 Godot 无需重新安装。以下全为占位路径，须人工选择可信安装和测试项目：

```json
{
  "native": {
    "godot_executable": "/path/to/trusted/Godot.app",
    "projects": [{
      "id": "approved-game",
      "root": "/path/to/approved/project",
      "godot_subdir": "client",
      "enabled": true,
      "allow_scripts": true
    }]
  }
}
```

`godot_subdir` 默认空字符串（项目根）；Windows 原有默认值不变。应用 bundle 根据 Info.plist 解析可执行文件；也可明确指定原生二进制。禁止配置路径/祖先符号链接、可执行文件组/其他人可写、无执行权限及非原生 arm64 Mach-O。当前支持 native arm64 的 Godot **4.5+ stable、major 4 编辑器**（含 universal arm64）；Intel/Rosetta、3.x、4.4 及更早版本、开发版本和 export template 为 unsupported。`--version` 与 `--help` 均在最小环境下限时检查；还须确认 editor/import/headless/recovery-mode 参数实际存在，不能仅凭文件名或版本声明 available。

依据 [Godot 4.5 命令行文档](https://docs.godotengine.org/en/4.5/tutorials/editor/command_line_tutorial.html)：macOS 从 bundle 内二进制启动；未知参数会被忽略，因此能力探针必须验证参数支持。`--recovery-mode` 用于导入前检查，不能理解为运行项目时的安全沙箱。

```sh
python3 scripts/install-local-gpu-macos.py discover-godot
python3 scripts/install-local-gpu-macos.py discover-godot --godot-search-dir /path/to/apps
python3 scripts/install-local-gpu-macos.py discover-godot --godot-executable /path/to/trusted/binary
```

发现只读取候选元数据，不执行二进制、不修改配置，也不授权候选。默认只查系统/用户 Applications，可指定任意已安装位置；不假定实际安装路径。健康探针只执行**已经明确配置**的二进制。安装器继续先预览；新增 native 会显示在预览中，不下载或复制 Godot，不运行项目。

现有阶段 4 安装和保留数据不会被安装器覆盖。本轮没有对真实安装执行升级；既有安装更新须在审查后人工停止服务、备份并核对应用代码和私有配置，再显式启动。不得把本实现当成已完成实机部署或自动无损升级工具。

### 任务和进程

| 操作 | Mac API / 行为 |
| --- | --- |
| 项目列表/状态 | 复用 `GET /v1/native/projects`、`GET /v1/native/status`；只返回 ID 与有效项目能力，不输出本地绝对路径。 |
| open/run/import | `POST /v1/native/jobs`，`engine=godot`，相应 operation；MCP `godot_operation`。open 启动编辑器，run 运行主场景或指定 scene，import 使用 headless editor recovery-mode。 |
| script/capture | 复用 `run_project_script` / `capture_scene` 和原任务信封；增加顶层 `authorized:true`。script 默认 headless；capture 复用 `native_godot.gd`，需要 GUI/rendering 会话，不伪造 headless 截图成功。 |
| 状态/等待 | 复用 `native_job_status` / `wait_native_job`；状态为 queued/running/succeeded/failed/cancelled。记录退出码；超时为 failed + timed_out。 |
| stop/cancel | MCP `godot_stop` → `POST /v1/native/godot-stop`；`godot_cancel` → `POST /v1/native/jobs/{id}/cancel`。只作用于本实例持有的子进程。 |
| 日志 | `godot_logs` → `GET /v1/native/jobs/{id}/logs`；返回固定生命周期诊断，原始 stdout/stderr 与引擎 log-file 丢弃，不返回用户路径、异常文本或脚本输出。此为刻意限制，不能宣称完整应用日志。 |
| 产物 | 复用 `fetch_native_artifact` 和 `/v1/native/artifacts/{id}/{index}`；本阶段只开放验证后的 PNG。 |

所有项目操作（包括 open/import）都要求本地 `enabled:true`、`allow_scripts:true` 和每次请求 `authorized:true`；客户端不会自动设置授权。没有通用 shell、远程可执行文件选择、原生文件下载、自动 Git reset/pull、同步上传或 Go/server broker。其余原生路由返回 HTTP 404 + `code:unsupported`。旧 Windows capture/script 请求不带新字段时保持原格式；ComfyUI tools 和请求格式完全不变。

项目在配置加载时登记 canonical root、device/inode；每次提交/执行核对身份，验证 project.godot、相对 scene/script，并扫描项目拒绝符号链接（最多十万条目）。真实 Godot 任务会按其正常行为导入资源/写入 `.godot`，编辑器可保存项目；仅应授权专用可信副本。本轮测试从未执行真实 Godot，也未触碰用户项目。

启动使用参数数组、shell=false、新 session，HOME/TMPDIR 指向独立任务目录，PATH 固定系统目录；不继承 DYLD、Python、Token、SSH agent 等变量。每个项目最多一个受管任务，其他项目和 ComfyUI 独立。默认超时 120 秒，范围 1–3600 秒，含导入和实际任务；编辑器也受此限制。保存 PID、启动时间、项目身份和用户授权标记，但远程状态不公开 PID/本机 inode。

仅在内存持有的 Popen 子进程仍存活时向其 session 进程组发送 TERM，必要时 KILL；不扫描/接管/终止外部 Godot，不根据落盘 PID 发信号。重启将未完成任务标记失败且结果未知，不自动重投；相同 request_id 不重复执行，不同内容复用 ID 拒绝。服务正常退出/安全监测失败时关闭 Godot 与 ComfyUI。损坏或不安全的 Godot 任务存储仅禁用 Godot，ComfyUI 继续使用原检查。

**不构成沙箱。** 路径/链接校验限制 Bridge 的输入输出；无法约束用户授权脚本自行访问其他文件、网络、调用 OS API 或在执行中改变文件。最小环境变量也不移除当前用户权限。进程组不是 Windows Job Objects，不能保证终止已脱离 session 的后代；SIGKILL/断电可能遗留进程，重启不接管，需人工检查。Mac 永不报告 Network privacy ready/出站隔离成功。

### 产物与独立状态

截图只读取此任务的输出；脚本 `output_paths` 须显式指定最多 16 个尚不存在的项目相对 PNG 文件，不覆盖/登记已有文件。产物上限 32 MiB、尺寸最大 4096×4096，校验 PNG 签名、块 CRC、IHDR、解压尺寸/过滤字节与完整结束，支持 8-bit RGB/RGBA 非交错 PNG。目录描述符逐层 O_NOFOLLOW 打开，拒绝符号链接、硬链接、非普通文件；复制到私有任务目录并登记大小/SHA-256，下载重新校验并从已验证字节发送。其他格式明确 unsupported。媒体自身可能承载脚本生成的信息，PNG 格式检查不构成内容脱敏沙箱。

Toolkit 面板独立显示 Godot `installed/available/running/unsupported/error`。只有安装、路径、权限、版本、arm64 与 Bridge API 安全健康全部通过才报告 available/running；Bridge 健康仍依赖 LaunchAgent、私有配置/token、Tailscale 和 peer，**不依赖 ComfyUI 运行**。Bridge 不健康时已核验 Godot 只报告 installed；探针错误为 error，未配置/不兼容为 unsupported。ComfyUI 六项 ready 检查保持不变。

## 10. 阶段 5B：Blender macOS 原生适配

基于 PR #6 实际最新 HEAD `f09aa4d8ca128a8ebdc172017c4ac22fedea5b8f` 开发；任务中列出的 `87841d18a2092869bbe90dfe42d9866f7bcb1f2b` 是其祖先。保留最新 Godot MCP 显式授权修复。开发分支 `feature/macos-blender`，Draft PR base 为 `feature/macos-godot`；不改 main、不合并。

### 本地选择与运行

`bridge_platform/macos_blender.py` 的 `discover()` 只读发现 `/Applications`、登录用户 Applications 中的 Blender.app，以及明确指定的 app/binary。发现不会执行或自动选择程序。用户在现有私有配置的 `native.blender_executable` 中填写选定的绝对路径，可选择已有不同版本；`native.blender_device` 为 `metal`（默认）或 `cpu`。现有 `native.projects` 使用 `id/root/enabled/allow_scripts`，Godot 的 `godot_subdir` 不影响 Blender 根目录。配置变化须本地审查后重启 Bridge。

探针验证 canonical 路径、无符号链接、可执行且不可被 group/other 写入的普通文件、含 arm64 的 Mach-O，以及实际 `--version` 返回的稳定 Blender 4.2+（major 4）或 major 5。Intel/Rosetta、预发布、未知主版本返回 unsupported。探针超时/执行异常返回 error。没有自动下载、安装、版本替换、系统驱动修改或额外 GPU 依赖。

Blender 维护独立的每项目 GUI 会话，因此连续的 create/transform/material/render 保留场景。所有进程均由当前实例以参数数组、`shell=False`、新 session 启动，使用 `--factory-startup --disable-autoexec --python-exit-code 1`。HOME/TMPDIR/BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS 指向私有会话目录，禁用 Preferences 自动保存和自动备份；不改用户原配置，不发现/接管已运行的 Blender。空闲会话保留至 Bridge 退出；每个执行任务默认 300 秒，脚本/batch 的 timeout_seconds 保留 10–7200 秒语义。

### MCP 与文件行为

复用 `blender_operation`、`blender_tasks`、`cancel_blender_task`、`native_job_status`、`wait_native_job`、`fetch_native_artifact`；请求仍为 engine/project_id/operation/arguments/request_id 信封。Blender 操作参数验证抽至共享 `native_blender_validation.py`，Windows 委托该模块并保留自己的路径策略、Job Objects 和防火墙实现。Windows 安装器同步打包共享依赖。

- scene_info、open、save、create_primitive、set_transform、set_material、set_camera、delete_object、import_asset、export_asset、render、execute_code、run_script、viewport_capture、batch 复用原 worker。
- 灯光通过显式授权的 Python bpy 操作；没有添加与 Windows 冲突的 set_light 参数。rotation 为弧度。viewport_capture 需要 GUI 中实际存在 VIEW_3D。
- Python（含 batch 内步骤）同时要求本地 allow_scripts=true 和顶层 authorized=true；缺失、false 或非布尔 true 均在排队/启动前拒绝。客户端只转发授权，不自动补 true。普通 typed 操作要求本地 enabled 项目。
- Mac 当前只开放 Cycles 和 GLB/OBJ/VRM 资产输入输出；Eevee、其他资产导入格式、replace=true 明确 unsupported。OBJ 导出禁用 MTL sidecar；GLB 含材质。VRM 仅在隔离会话中实际可加载的既有扩展可用时执行，否则任务 error_code=unsupported。普通用户配置下安装的扩展不会自动带入私有会话，也不会自动安装扩展。
- save/export 写入任务暂存区，再通过逐级目录描述符、O_NOFOLLOW、O_EXCL 发布到已存在的项目父目录。拒绝覆盖、重复输出、路径穿越与符号链接。批处理的发布发生在 worker 返回后；不能在同一批次里 open 尚未发布的新文件。script output_paths 只能登记提交时不存在的文件。
- 项目 canonical root/device/inode 在提交和执行时重验；输入/产物读取拒绝链接、硬链接、非普通文件，单文件最多 32 MiB。PNG 校验格式/CRC/像素；其他资产是受管任务产生的字节，不宣称完成模型内容安全验证。复制后登记 bytes/SHA-256，下载重新核验。失败 batch 的已登记成功产物仍可下载。
- 每任务独立 ID、幂等冲突检查、进度、固定日志、取消与超时。脚本结果沿用过滤/大小限制并增加 POSIX 路径、凭据字段脱敏；原应用 stdout/stderr 丢弃。重启未完成任务为 failed + outcome_unknown，不重投、不用历史 PID 发信号。

### Metal 与状态边界

渲染前在当前 Blender 内尝试 Cycles METAL backend 并枚举实际 METAL 设备，只启用匹配设备；不按芯片名字推断能力。选择 CPU 时不枚举 GPU。无 backend、无设备或枚举异常明确返回 CPU fallback；已枚举 Metal 但渲染报错时尝试一次 CPU，CPU 也失败则任务失败。结果保留 artifact/hardware_render，并增加 render_device.backend、metal_available、metal_devices、cpu_fallback、fallback_reason；CPU 主动选择时 metal_available=null（未探测）。渲染卡住由任务超时终止受管进程，不伪造回退成功。

Toolkit 分别显示 ComfyUI/Godot/Blender 状态。Blender available/running 必须通过 installed/path/permissions/version/architecture/bridge_api 全部检查。available 表示原生执行检查通过，**不代表 Metal 已验收**；Metal 按每次 render 检测。ComfyUI 原六项 ready 判断未变；三种引擎的任务、进程、存储异常和退出清理独立。

**没有安全沙箱，也没有 macOS 出站网络隔离。** 授权 Python 仍以登录用户权限运行，可自行访问文件/网络、改变项目或派生进程；路径防护与新文件发布约束 Bridge 自身，无法阻止受信任脚本主动覆盖文件。进程组不等价 Windows Job Objects，脱离 session 的后代或断电/SIGKILL 遗留进程需人工检查。媒体内容不由日志脱敏处理。

实现参考 [Blender 官方命令行文档](https://docs.blender.org/manual/en/4.2/advanced/command_line/arguments.html) 和 [Cycles 官方设备实现](https://github.com/blender/blender/blob/main/intern/cycles/blender/addon/properties.py)。本轮仅 Mock Blender、Mock Metal、临时项目、合成文件和回环 HTTP；真实版本/Metal/GUI、扩展、TCC/Gatekeeper、信号及 VPS 链路均待 ChatGPT 审查后人工授权验收。

## 11. 阶段 5C：macOS Unity（2026-11-05）

### 架构和本地启用

`bridge_platform/macos_unity.py` 是独立 adapter；Windows `native_unity.py`、`native_unity_commands.py`、`native_control.py`、`native_process.py` 不修改。复用现有 `/v1/unity/start|stop|tools|call`、`unity_start/stop/tools/call` 和 native task/status/cancel/artifact 协议。Mac `unity_start` 返回 Editor 会话任务，`unity_call` 返回编辑任务；通过 `native_job_status` / `wait_native_job` 查询，产物用 `fetch_native_artifact`。不要自动重试编辑。

仓库现有 Pipeline 通过外部 official CLI 动态发现工具，未 vendoring Editor 源码；无法仅凭动态名称确认其文件/组件操作边界。本阶段保留外层 MCP 架构，新增固定白名单 `OrcaUnityBridge.cs`，**不声称兼容任意官方 Pipeline 版本/工具名**。不启动 CLI、不安装 Pipeline、不调用其 eval/menu/batch/安装/build 功能。用户应先读取本会话的 `unity_tools`，使用返回的固定工具名。Windows 继续原 Pipeline。共享 MCP 四个 Unity 工具均要求 boolean `authorized`；Windows HTTP 仅消费新增信封字段，显式 false/非 true 拒绝，旧无字段 HTTP 兼容和原 Windows guard 保留。

审查和人工批准后，用户在专用可信项目副本中手动放置本仓库扩展为 `Assets/Editor/OrcaUnityBridge.cs`。Bridge 比较文件完整内容，不自动复制或更新项目脚本。本地 `native.unity_executable` 指向已有 Unity.app 或其 `Contents/MacOS/Unity`；`native.projects` 中配置 `id/root/enabled=true/allow_scripts=true`，可选相对 `unity_subdir`。未设置 allow_scripts 或请求缺少 `authorized=true` 均拒绝启动及 Editor 操作。启用脚本意味着信任整个项目和已有插件，**不可信项目禁止启用**。

发现 API `discover()` 只读 `/Applications`、用户 Applications 中的 Hub/Editor 元数据及用户指定路径，不运行 Hub、不自动选版本。没有 Unity/模块安装下载、账号/授权激活、项目升级、Git 自动同步、Preferences/Player Settings/Graphics API 修改接口。Editor 自身正常导入会写入已批准项目的 Library/Temp/元数据；已安装依赖和本地许可须先准备好，Bridge 不承诺阻止 Unity/项目代码的网络行为。

### 版本和图形边界

- 仅 native macOS arm64 Mach-O（包含 arm64 slice 的 universal 可候选）；拒绝 Intel/Rosetta、无执行权限、可被 group/other 写入的 executable 和符号链接路径。
- 当前实现白名单为稳定 `6000.0.<patch>f<revision>`，不是“所有 Unity 6 已验证”。完整版本从 bundle metadata 读取，必须唯一、且与 `ProjectVersion.txt` 完全一致；API 心跳再次核对 `Application.unityVersion`。缺失/不明确版本直接 unsupported，不执行版本探针，不猜版本，不升级项目。
- 最低系统要求采用 `max(macOS 11, LSMinimumSystemVersion)`。这是预检下限，不代表对每个新 macOS 版本都完成验收。[Unity 6.0 系统要求](https://docs.unity.com/en-us/engine/6000.0/manual/get-started/install-and-upgrade/getting-started-installing-unity/system-requirements)和 [Metal 兼容性](https://docs.unity.com/en-us/engine/6000.0/manual/platform-specific/cross-platform-features/graphics-apis/metal/requirements-and-compatibility)用于确定这一保守范围。
- 固定启动 `-force-metal`，不接受额外 CLI 参数，不包含 D3D11/D3D12。API 实测 `SystemInfo.graphicsDeviceType == Metal`，读取 macOS Graphics API 是否自动或包含 Metal。只接受 Built-in/标准 URP/HDRP 类型；manifest 中 URP/HDRP 必须为 `17.0.x`，其他版本/自定义来源拒绝且不改文件。[Unity 6.0 URP 兼容性](https://docs.unity.com/en-us/engine/6000.0/manual/render-pipelines/universal-render-pipeline/requirements)说明图形包与 Editor 发布关联；实际 shader、HDRP feature、质量档位和 GPU 性能仍须实机验证。
- `available` 仅表示安装/路径/版本/系统/Bridge 预检通过；启动未连接时是 `installed`。只有当前 Popen 仍存活、心跳 session/PID/project/version 对应、五秒内有效、Metal 与图形检查通过才是 `running`。健康状态不代表项目内容经过安全审计。

### 固定 Editor 操作

| 工具 | 边界 |
| --- | --- |
| editor.status / scene.get / hierarchy.get / gameobject.get | Editor 状态、加载场景、最多 1000 个场景对象；输出再次限制大小和脱敏 |
| gameobject.create / delete / transform.set | 场景对象及局部变换；不能把任意资产/隐藏内部对象当 GameObject 修改 |
| component.add / set | 只允许 Camera（field of view、orthographic size）、Light（intensity、range）、BoxCollider（is trigger）及值域检查 |
| inspector.get | Transform 和上述组件的有限属性；其他组件只报告类型和 properties unsupported，不反射读取任意序列化内容 |
| scene.save | `path` 必须是尚不存在的 `Assets/*.unity`；不覆盖 `.meta` 或场景，不改变当前 Scene 路径/dirty 标记 |
| play.start / stop | 请求 Editor Play Mode 转换；通过 editor.status 确认实际状态 |
| gameview.capture | 使用 Unity ScreenCapture Game View，须 Play Mode/可渲染图形会话；非桌面截图；PNG 最多 4096×4096、32 MiB |
| console.get | 读取扩展加载后最多 100 条日志，省略 stack；后端过滤 secret、绝对路径、IP；不声称包括扩展加载前的全部 Console |
| assets.list / asset.import | 列表仅安全的 PNG/JPEG/OBJ/Scene；import 只对已有 PNG/JPEG/OBJ 调用 AssetDatabase.ImportAsset；不上传任意脚本、不开放 delete/move/package 操作 |

场景保存遵循 [SaveScene 的相对路径要求](https://docs.unity.com/en-us/engine/6000.3/script-reference/unityeditor/scenemanagement/editorscenemanager/savescene)：后端通过目录描述符排他创建 `Assets/OrcaBridge-<task-id>/`，Editor 将场景副本写为该目录的 `scene.unity`，后端校验后以 O_EXCL 发布到请求的新目标。暂存目录和 Unity 创建的 `.meta` 保留，供人工恢复/清理，不自动删除；资产列表不暴露暂存目录。Scene/PNG 均复制到私有任务目录、记录长度和 SHA-256，下载再次校验并发送已验证字节。源场景含有的游戏数据和截图内容不做隐私净化。

### 任务、安全与待验收

私有文件队列连接扩展和 Bridge，不新增 HTTP 监听端口。每会话随机 secret 只经最小环境传入；请求再次校验 authorized/session/task ID，响应有同一身份。原 Tailscale/token/peer/RuntimeSecurity 保护外层接口。项目路径 canonical + inode 固定，禁止系统/隐藏目录、路径穿越和符号链接，文件读取使用 O_NOFOLLOW、单硬链接、32 MiB 限额。整个项目最多扫描 100000 项，过大或有链接时失败关闭。

独立 Unity store 和 Popen session；一个 Editor 会话、一次一个编辑任务。Popen 使用参数数组、shell=False、start_new_session=True、私有 HOME/TMPDIR；原始应用 stdout/stderr 丢弃，日志返回固定诊断或脱敏 Console。只终止当前持有的 Popen 进程组，不寻找/接管外部进程；已有 UnityLockfile 拒绝且不删除。停止、取消、异常和超时结束受管会话；记录退出码、timed_out/outcome_unknown。start 默认总会话上限 300 秒（包括导入），HTTP/native task 可设 1–3600 秒；编辑默认 60 秒。应在截止前完成保存。重启时活动任务 failed + outcome_unknown，不向存盘 PID 发信号，不重投。

**非沙箱边界不变：** C#、Editor 插件、import callbacks、Play Mode 拥有登录用户的文件与网络权限；白名单扩展不能约束可信代码主动访问系统。进程组不等价 Windows Job Objects，脱离 session 的后代以及 Bridge 被 SIGKILL 后的遗留需人工处理。未知任务不承诺可安全重试。私有 HOME 不等价操作系统 Preferences 隔离，实际 Unity/许可行为必须在专用账户或副本验证。

本轮只有 Mock Editor/Pipeline、合成 Mach-O/PNG/场景、临时项目、真实回环 HTTP 和 Python/Node 校验。**未编译或运行真实 Unity C# 扩展，未验证真实 Editor/Metal。** 人工批准后须验证：准确 bundle metadata/版本及本地许可、扩展编译/domain reload、全部 Editor API、相对路径 SaveScene 和恢复暂存、Game View 的异步 PNG、URP/HDRP 和 Metal、超时/取消/退出码、TCC/Gatekeeper/Preferences、外部进程不受影响，以及 VPS MCP 链路。失败时停止，不降级到鼠标键盘、shell 或 C# eval。

## 12. 阶段 6A：VRoid / Tripo macOS 技术侦察（2026-11-05）

完整证据、验证程度和人工条件见 [VRoid / Tripo macOS 技术审计](macos-vroid-tripo-audit.md)。本阶段基于 PR #8 HEAD `fe0a0157c1e0d686fe06fe9eed6846abc715cd06`，从 `feature/macos-unity` 创建 `research/macos-vroid-tripo-audit`，以该功能分支为 Draft PR base。只改文档，不实现或启用 VRoid/Tripo，不修改 main、不合并。

- VRoid 官方有 macOS 产品并列出 M1 支持；社区插件在固定 revision 声称定制 arm64 BepInEx/IL2CPP 栈已跑通，但 README 仍标记未随发布包支持 Mac。所依赖的外部构建栈未取得，本仓库额外 VRM 导出扩展也没有 Mac 验证。不能从 Windows、通用 Unity 或 x64 BepInEx 支持推导兼容；未达依赖、实例身份、文件安全及出站隔离要求时保持 unsupported。
- Tripo 官方 OpenAPI 有上传、生成、任务查询和结果下载链路；官方 MCP 当前说明为 Blender Addon alpha。现有桥使用另一个社区 Studio 插件及会员接口，账户/计费/对象语义不可静默切换。优先建议官方 API 独立 backend；保留 VPS 名称、参数和 workflow 信封，未证明可映射的操作明确 unsupported。
- 下一阶段候选模块为 Mac VRoid adapter/插件构建验收、安全会话/产物、Tripo OpenAPI/workflow adapter、专用 Keychain 凭据存储及可选新建 browser profile 管理。估算复用范围与平台专属部分见审计第 4–6 节，均不代表已经实现。
- 安全门槛包括：不读取现有浏览器/账号数据；不自动登录；新 profile 也不等于 OS 沙箱；CDP 归属、TCC、最小子进程环境、符号/硬链接与竞态防护、下载目标限制、POSIX 路径/日志脱敏、结果未知不重投。不能通过删 guard、开放高权限 UI 自动化或改系统保护获得兼容。

本阶段仅公开资料和源码静态审查，未启动真实应用/浏览器、安装插件、访问 Keychain/Cookie/密码/Token/OAuth、修改用户模型项目、上传素材或消耗积分。Mac 软件运行、登录与权限、插件编译/注入、生成与导出、VPS 全链路均未实测。后续须经 ChatGPT 审查和人工批准，按报告的离线验证、专用授权与最小真实链路分阶段推进。

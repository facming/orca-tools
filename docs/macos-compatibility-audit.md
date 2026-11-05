# Orca Toolkit · macOS 阶段 0 兼容性审计

2026-11-04 · Apple Silicon 实机 · Orca 1.4.248 · Toolkit 1.3.1

**结论：本机 Orca 具备移植所需的插件宿主、CLI 和 SDK。当前 Toolkit 无法原样启动。** 宿主的离线验证器接受现有 manifest，随包 Node 可以导入插件入口。入口的第一步却只搜索 Windows 的 `orca.exe`，本机已复现失败。Accounts 的远程调用条件得到部分验证，真实插件激活、配对握手和远端授权均待验证。阶段 0 的只读侦察已完成，端到端兼容性门槛仍待审查。

起点是干净的 `main`，已核对 origin 的 fetch/push 目标均为 `facming/orca-tools`。获取并切换至 `docs/macos-port-plan` 后，本地与远端基线均为 `54d22776ff55`。核对了同目录的技术方案、模块实施方案及 Codex 工作流程。以下只记录可公开的产品版本、包内相对位置、权限类型和测试判定。

## 1. 本机依赖与 GPU 软件

| 项目 | 检查依据 | 实测结果 |
| --- | --- | --- |
| macOS / CPU | `sw_vers`、`uname -m`、`sysctl` | macOS 15.7.5（24G624），Apple Silicon arm64。硬件支持 arm64，本次探针未在 Rosetta 下运行。Intel 未测。 |
| GPU | `system_profiler SPDisplaysDataType`，仅保留型号与 Metal 字段 | Apple M1 Max，Metal 3。当前 PATH Python 的包目录未检出 torch；PyTorch MPS、Blender Metal 和各应用的 GPU 执行均未验证。 |
| Node.js / Python | PATH 程序的版本输出与 Mach-O 架构 | Node.js 20.20.2、Python 3.12.13，均为 arm64。 |
| Orca 内置运行时 | 随包可执行文件的 Node 模式读取 `process.versions` 和 `process.arch` | Electron 43.8.4 / Node.js 24.22.1 / arm64，存在 `node:module.registerHooks`。 |
| OpenSSH | PATH 定位、`ssh -V` | 系统自带 `ssh` 和 `ssh-keygen` 均存在。OpenSSH 9.9p2 / LibreSSL 3.3.6。未运行 keygen、SSH 登录或传输。 |
| Tailscale | `tailscale version` 首行、应用包版本 | CLI 和应用均为 1.104.2。未查询登录、地址、对端、连接状态或 ACL。 |
| Unity | Spotlight 定位、应用包版本、主程序 `lipo -archs` | Editor **6000.5.4f1 arm64**，Unity Hub **3.19.5 arm64**。未打开工程或检查许可。 |
| ComfyUI | 应用目录、Spotlight 应用及源码标记、PATH、Homebrew 已有安装目录 | 未确认安装。索引中同名目录位于仓库，仅为技能目录，缺少 ComfyUI 的 `main.py` 和 `comfy/model_management.py`。版本及架构未知。 |
| Godot / Blender | 同范围查找应用包、同名程序及包管理器目录 | 两者均未确认安装。Blender 同名索引项不是可执行文件。版本及架构未知。 |

应用查找覆盖系统和用户应用目录、Spotlight 可见项及 PATH。未遍历私人项目、受保护目录、未挂载卷或所有 Python 虚拟环境。即使 Unity 已安装，其 CLI、Metal 后端和 MCP 也需分别验收。

## 2. Orca 定位、插件加载及授权边界

从系统应用目录与 Spotlight 独立定位到同一个 Orca.app。读取其 Info.plist 的产品版本、包标识和可执行文件名，再枚举该包的程序文件、读取随包启动脚本与 ASAR 的宿主代码。下表是本次构建的发现结果，供复核及升级比较。

| 代号 | 本机构建的位置（根目录脱敏） | 依据 |
| --- | --- | --- |
| `<APP>` | `<系统应用目录>/Orca.app` | 包标识 `com.stablyai.orca`，主程序 `Contents/MacOS/Orca` 为 arm64。 |
| `<CLI>` | `<APP>/Contents/Resources/bin/orca` | 有执行权限的 Bash 包装器。当前 shell 的 PATH 无 `orca`。 |
| `<OUT>` | `<APP>/Contents/Resources/app.asar.unpacked/out` | 包内实际枚举结果。CLI 包装器用 `ELECTRON_RUN_AS_NODE=1` 调用随包主程序，入口为 `<OUT>/cli/index.js`。 |
| 插件宿主 | `<APP>/Contents/Resources/app.asar` 中的 `out/main/index.js`；worker 为 `<OUT>/main/plugin-host-entry.js` | 桌面主进程选择解包的 worker 并 `fork`，通过 IPC 发送插件根、入口和获准能力。 |
| SDK | `<OUT>/cli/runtime-client.js` | CommonJS 具名导出 `RuntimeClient`；实机 `require` 成功，具体实现为 `cli/runtime/client.js`。 |
| `<PROFILE>` | `<HOME>/Library/Application Support/orca` | SDK 的 darwin 默认路径。对该目录及下列两个子目录分别做了 `lstat`，没有读取配置文件。 |

**manifest 和入口。** 随包 `plugin validate plugins/toolkit --json` 返回退出码 0、`valid=true`、0 个错误、`needsApproval=true`。识别出 `trusted-worker`、`notifications:show`、5 个命令和 3 个快捷键。Toolkit 声明 manifest v1、pluginApi 1、Orca ≥1.4.237。宿主支持 manifest v1/v2 和 API major 1。`commands.register`、`log`、`host.call` 及 `notifications.show` 对应的能力在宿主代码中均可定位。快捷键在 Mac 上的实际响应、通知权限和命令注册尚未实测。

**生命周期与工作目录。** 宿主按 `pluginRoot + mainEntry` 生成文件 URL，安装模块加载钩子并动态导入，调用默认导出的激活函数，关闭时可调用具名 `deactivate`。当前 fork 未显式传 `cwd`，worker 继承父进程工作目录；尚无真实 worker 的 cwd 观测值。资源发现应基于模块或包位置。宿主以白名单传递 HOME、PATH、临时目录及部分系统变量，另设 `ELECTRON_RUN_AS_NODE=1`。白名单不含 `ORCA_USER_DATA_PATH`、`ORCA_ENVIRONMENT` 或配对变量；使用非默认 profile 时，必须验证 profile 来源。

**实际加载到哪一步。** 在随包 Node 中以文件 URL 导入仓库的 `main.mjs`，其 CJS 依赖成功解析，默认导出与 `deactivate` 均为函数。单独运行入口使用的 `windowsRuntime()`，得到预期的 CLI 查找错误。仓库 `plugins/toolkit/main.mjs` 第 6–22 行显示该错误发生在启动服务及注册命令之前。授权后的完整入口还可能读设置、启动本地服务及 GPU Bridge，因此没有执行激活函数或向宿主发送初始化消息。

**数据目录与 TCC。** 默认 `<PROFILE>` 目录模式为 0700，`plugins`、`plugins-data` 均为 0755。宿主分别用两者管理插件代码和数据。按本插件的限定名称点查，两处均不存在 `local-tools.toolkit` 子目录。此观测限默认 profile，未枚举用户的其他插件，也未验证插件写入和升级后的保留行为。

主程序签名的 entitlements 中无 `com.apple.security.app-sandbox`。Info.plist 含 Desktop、Documents、Downloads、Local Network 等用途声明。宿主将 trusted worker 描述为以当前用户的访问能力执行代码，并对宿主 API 另行检查批准能力。用途声明和 POSIX 模式均不能证明 worker 已获 TCC 授权。本次未查询 TCC 数据库，未借宿主试读受保护文件或触发系统授权框。

## 3. CLI、SDK 与远程 Accounts RPC

**CLI。** 包内 `<CLI> --version` 实际输出 1.4.248。根帮助及 `environment list`、`repo list`、`terminal create/read/close`、`plugin`、`status` 的帮助均退出 0。当前帮助包含 `--json`、远程命令的 `--environment <selector>` 与 `--pairing-code <code>`。Downloads 使用的 `--worktree`、`--title`、`--command`、`--terminal`、`--limit` 均出现在对应命令帮助中。这里只读取帮助，没有列出 environment、仓库或终端。

**构造接口。** 本版本签名为 `new RuntimeClient(userDataPath, requestTimeoutMs, remotePairingCode, environmentSelector, cliExecutable, originalArgs?)`。SDK 支持缺省值，其中默认超时为 60 秒。Toolkit 当前传前五参，超时 30 秒，第三参显式传 `null`，第四参指定 environment。包内具备 `isRemote` 和 `call(method, params, options?)`。SDK 与 CLI 的相对布局在这一次构建恰好符合仓库 `remote-runtime.cjs` 的推导；该文件在没有覆盖值时还会拼接 `APPDATA/orca`，需要适配。SDK 的 `getDefaultUserDataPath()` 可处理 darwin 默认目录。

**调用条件。** 本机包内的 RPC 参数目录和主程序处理器均有 `accounts.list`（`refreshUsage`，权限 `workspace`）及 `accounts.selectClaude`（`accountId`，权限 `accounts-admin`）。两者是远端 runtime 的权限；插件的通知授权不能替代它们。SDK 选定远程配对后动态导入 WebSocket 传输，业务调用可先经 `status.get` 协议预检。本机构建 runtime 协议为 3，兼容的最低服务端协议为 2。Toolkit 在 `modules/accounts.cjs` 第 40–51 行还检查 `isRemote`、响应 `ok` 与 `_meta.runtimeId`，这些检查应保留。

**为什么停在传输边界。** `cli/runtime/runtime-remote-pairing.js` 会用 selector 从真实 profile 解析配对材料；`cli/runtime/client.js` 和兼容性预检在收到成功响应时可调用 `markEnvironmentUsed`。`shared/runtime-environment-store.js` 第 161–205 行显示，按最近使用时间或 runtime 身份变化可能回写记录。无 selector 的本地发送路径也会读取带认证信息的 runtime metadata。因此，只在空的替代 profile 和无网络进程内测试 SDK。未连接真实 environment，未执行账号列表、用量或切换请求；远端协议、账户权限、真实回包结构及当前连接状态均未知。

## 4. 探针结果与复核方法

| 探针 | 结果 | 能证明的范围 |
| --- | --- | --- |
| CLI 版本和 8 项帮助（根帮助及第 3 节所列子命令帮助） | 9/9 退出 0 | CLI 可独立运行，参数存在。 |
| 官方离线插件校验 | 1/1 通过，0 个 manifest 错误，须人工批准 | 校验源目录，不运行插件。首次把空设备重定向也禁写时，包装器在校验前退出 1；仅允许空设备输出后通过。 |
| 随包 Node 与 PATH Node，分别执行同一组 5 项探针 | 两个运行时均为 5/5 符合预期 | SDK 在显式空 profile、空配对及空 selector 下构造成功，`isRemote=false`；不存在的 selector 被拒；WebSocket 两个发送函数可导入；插件入口导出正确；Windows runtime 的 CLI 查找如期失败。 |
| 随包 Node 的传输与兼容性探针 | 3/3 符合预期 | WebSocket 发送函数对负超时返回 `invalid_argument`，在建连前终止。协议门对合成的服务端版本 3 放行、版本 1 返回 `incompatible_runtime`。实际握手和远端响应未测。 |
| GPU 远程文本脱敏，2 个完全虚构的本地路径 | 1 通过，1 失败 | Windows 路径被过滤，macOS 用户目录形式的绝对路径原样保留。使用替身主机名和空环境，只记录布尔判定。见 `services/local-gpu-bridge/remote_privacy.py` 第 40–55 行。 |

复核时，先以 `mdfind "kMDItemFSName == 'Orca.app'cd"` 与应用目录交叉定位，核对包标识，再在该包中枚举名为 `orca`、`runtime-client.js`、`plugin-host-entry.js` 的文件，区分桌面包与其嵌套 helper。对 CLI 使用上表已定位的包装器，读取版本、帮助及离线校验结果。SDK 探针通过清空继承环境、指定不可用作 profile 的替代路径、在子进程禁网和禁写文件来隔离；官方校验允许写入空设备。避免把 CLI 的原始 JSON、命令环境或系统完整清单贴入审查记录。

本阶段未运行完整仓库回归、安装器、真实 Downloads 或 GPU 测试。部分测试会生成文件、密钥、打开监听或启动子进程。Windows 回归和 Intel 兼容性均无本次实机结果。

## 5. 三个模块的复用与适配

为便于复查，选取下列直接参与模块运行的源文件，按基线的物理行数（含空行与注释）计算“可整文件复用候选”的占比。含平台调用的文件即使有共享逻辑，其行数也不进入分子。三组样本不含共同宿主入口、Web UI、测试、文档及 VPS MCP。这是静态范围估算；最终需修改的行数和实机通过率仍未知。

| 模块 / 样本中候选行占比 | 可整文件复用候选 | 样本中须适配或拆分的文件 |
| --- | --- | --- |
| Accounts **84/93 行，90%**（2/3 文件） | `modules/accounts.cjs`、`account-preferences.cjs`。复用邮箱遮罩、用量归属校验、切换并发保护及偏好逻辑。 | `remote-runtime.cjs`。从已验证的 SDK、CLI 和正确 profile 构造 client；宿主管理的数据根需注入偏好模块。 |
| Downloads **335/627 行，53%**（5/10） | `modules/downloads/` 下的 `engine.cjs`、`remote-reader.py`、`settings.cjs`，以及 `remote-browser.cjs`、`download-preferences.cjs`。涵盖分片、暂停/续传、重试、SHA-256、防覆盖落盘、远程只读 reader 及 POSIX 浏览。 | `modules/downloads/` 下的 `transport.cjs`、`runtime.cjs`、`server.cjs`，以及 `directory-picker.cjs`、`choose-directory.ps1`。替换 Windows OpenSSH 路径、Explorer、PowerShell/WinForms 和 Windows runtime 回退；确认 macOS 私有目录与 key 模式、含空格路径及取消选择的行为。 |
| GPU **412/2611 行，16%**（3/13） | `workflow_library.py`、`job_reuse.py`、`comfy_startup.py`。现有启动器已有 POSIX 文件锁和非 Windows 进程参数分支。 | Bridge 下的 `bridge.py`、`manage.py`、`native_process.py`、`native_control.py`、`native_blender.py`、`native_unity.py`、`native_tripo.py`、`remote_privacy.py`，以及 `plugins/toolkit/modules/gpu.cjs`、`scripts/install-local-gpu.py`。其中仍可提取 ComfyUI HTTP、任务/产物和鉴权逻辑。 |

Node 文件名相对 `plugins/toolkit/`，Bridge 文件名相对 `services/local-gpu-bridge/`。共同入口另有三个适配点：`Install.cjs` 第 4–15 行依赖 Windows 安装目录及内部 installer；`controller.cjs` 第 9–22 行绑定本地数据根；`main.mjs` 第 7–22 行和 `server.cjs` 第 76 行使用 Windows runtime、URL 文件关联和 Explorer。Downloads 面板还提示用户操作 Windows 对话框。

Downloads 的 SSH 协议可以保留。`transport.cjs` 第 29–36、82–88、116–121 行和 `remote-reader.py` 第 24–87 行给出了每任务 Ed25519 key、严格 host-key 校验、限定范围的 forced-command reader 及清理逻辑。Mac 验收需覆盖目录 0700、私钥 0600、异常退出后清理和目标文件防覆盖。本次只审阅代码。

GPU 的主要代价在本地执行。安装器绑定 LOCALAPPDATA、`pythonw.exe`、Windows Startup 和复制的 exe；`native_process.py` 第 14–46 行依赖 Defender 检查及 Windows Job Object；`native_control.py` 第 153–172 行含 PowerShell 提权路径。Unity 指定 `-force-d3d11`，Blender Cycles 指定 OptiX，Tripo 依赖 Windows 浏览器路径和凭据处理。ComfyUI HTTP Bridge 可先单独验收，待处理 Mac 路径脱敏后再对远程开放。Godot、Blender、Unity 各自需要进程和后端验证；出站隔离未获证实前，原生执行不得报告安全就绪。

## 6. 阻塞项、阶段 1 建议及人工参与

| 优先级 | 阻塞或未决问题 | 后续验收动作 |
| --- | --- | --- |
| 阶段 0 的宿主门槛 | 尚无获准 worker 内的生命周期、SDK 加载或 cwd 实测。原版 Toolkit 的 GPU 默认开启，激活还会尝试启动 GPU 服务。 | 经 ChatGPT 审查、用户批准后，在 Orca UI 人工审核单独的最小诊断插件。只验证命令注册、SDK 导入及停用，记录相对位置或布尔结果，隔离 GPU 启动。 |
| 远程门槛 | 配对 profile、协议、远端 `workspace` / `accounts-admin` 授权未知，真实状态探针也可能回写。 | 如需补测，由用户选择专用测试 environment，并单独同意 SDK 使用其配对材料及可能的使用时间更新；届时只验证非账号状态调用的成功标志。真实 Accounts 验收留到阶段 2。 |
| macOS 文件权限 | 宿主的 TCC 状态、用户选目录后的访问及升级保留尚未知。 | 在后续专门验收中由用户选择测试目录、决定是否授权。分别验证允许、拒绝和取消。升级保留在隔离测试 profile 中验证。 |
| GPU | 缺少可确认的 ComfyUI/Godot/Blender 安装；已复现 Mac 路径脱敏缺口。 | GPU 阶段再由用户指认非标准安装，逐应用验证。先修复脱敏并验证远程错误与产物元数据，再启用对应能力。 |

**阶段 1 的最小改动建议，待批准。**

1. 在 `plugins/toolkit/platform/` 引入方案指定的 runtime 工厂，先封装现有 Windows 行为，再接入 darwin。按正在使用的宿主定位并验证包标识、CLI 可执行性、SDK 导出和 profile 来源。遇到多安装、非默认 profile 或包布局变化，返回脱敏诊断。
2. 分离 Orca profile、Toolkit 私有数据、缓存和日志目录。让主入口、controller、两个 Downloads 服务入口及传输构造函数都使用同一个 runtime。替换 URL/目录打开、目录选择、SSH/keygen 路径及子进程环境清理。优先宿主目录选择能力，若需 `osascript` 则固定脚本、路径经 argv 传入。Mac 安装优先由 Orca UI 导入。
3. 对路径空格/中文、CLI/SDK 缺失、替代 profile、权限拒绝、选择取消及子进程环境做离线测试。确保 Mac 能力缺失时关闭对应功能，避免入口顺带启动 GPU；在 Windows 上通过已有回归。阶段 1 的验收限于平台接口，Accounts 真实读取、下载和渲染分别在后续阶段执行。

本次仅新增本报告。未安装、启用或批准插件，未使用真实账号或凭据，未创建密钥、监听、LaunchAgent，未修改应用包、TCC、PF、防火墙、Tailscale 或系统扩展。报告留在方案分支工作区，未暂存、提交、推送或创建 PR；等待 ChatGPT 审查及用户下一阶段指令。

## 7. 阶段 1 实施记录（2026-11-04）

本节基于阶段 0 提交 `e20df27`。Toolkit 现在使用同一个平台 runtime 初始化管理服务、Accounts、Downloads、目录选择和 GPU 模块。Windows 沿用原设置、下载任务及 GPU 安装目录，启用 GPU 时保留启动动作。Mac 管理页面仅监听 IPv4 回环，Accounts 和 Downloads 显示为预览。服务端拦截账号、远程浏览、传输和 GPU 管理操作，目录选择及本地偏好可以单独使用。

| 检查对象 | 实现与证据 |
| --- | --- |
| CLI / SDK | 从实际 worker 可执行文件向上核对 app 的包标识，再对照 Spotlight 与系统、用户应用目录中的候选项。只在该包内使用第 2 节记录的相对布局，检查 CLI 执行权限及 SDK 的构造器和调用方法。无法识别宿主、发现多个安装或布局不兼容时停止初始化。 |
| Profile | 显式覆盖须指向可访问的绝对目录。否则使用 SDK 默认值或已验证的 darwin 路径。复查当前构建的安装器发现，审核后的入口在 `<PROFILE>/plugins/local-tools.toolkit/<contentHash>/` 下。入口与默认 profile 不匹配时停止，避免从另一份 profile 查配对。真实宿主能否传递显式覆盖仍待核实。 |
| 目录与进程 | 由 `os.homedir()` 推导 Toolkit 的 Application Support、Caches 和 Logs 路径。实际使用的私有数据目录以 0700 创建，拒绝属主不符、符号链接或组及其他用户可访问的目录。本地配置及目录偏好以 0600 创建。Mac 子进程仅继承指定环境字段，使用系统 OpenSSH，打开页面和目录均以参数数组调用系统 open。 |
| 目录选择 | 在本版本公开的 worker API 和方法定义中未发现原生目录选择接口。使用固定 AppleScript，初始目录作为单独参数传入。取消与执行失败分别处理，选择结果只保存本地路径。 |
| 错误处理 | 管理服务向页面返回固定诊断，屏蔽文件系统异常、解析异常和外部命令输出。下载错误的历史自由文本在加载时不再传给页面。 |

**安全验证。** 在临时 HOME、隔离 profile 下，用本机 Orca 1.4.248 的随包 Node 成功解析 CLI 和 SDK，分别检查了显式覆盖和模拟安装目录下的 SDK 默认 profile。CLI 版本检查通过，官方离线插件校验为 valid，0 个错误，仍需人工审核。系统 AppleScript 编译器接受选择脚本。这些探针没有激活真实 worker、创建 SSH 密钥、连接远程环境或启动 GPU 进程。

| Node.js 20.20.2 / Apple Silicon | 结果 |
| --- | --- |
| `node --test plugins/toolkit/test-platform.cjs` | 6/6 通过。包含临时 Windows 安装布局、Mac 路径及权限、多安装、SDK 导出与 profile、子进程参数、目录选择和模拟激活。 |
| `node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs` | 共 48 项（含上述 6 项），47 通过，0 失败，1 跳过。模拟宿主注册 5 个命令，两个预览页面可访问，GPU 启动计数为 0。 |
| Windows 专用集成测试 | 需要在已安装 Orca CLI 和系统 OpenSSH 的 Windows 上运行原生程序。该用例保留在全量套件中，仅在 Windows 上执行。本次通过的是 Windows 适配夹具及模拟 GPU 启动回归。 |

**阶段 1 测试范围确认（2026-11-04）。** 用户确认本阶段不测试 Windows 相关功能。上表如实保留范围确认前在 Mac 上运行的夹具和模拟启动结果。Windows 原生探针未运行，Windows 功能不作为本阶段验收或合并条件，不安排补测。Windows 代码和已有测试用例继续保留，实机兼容性未经本次验证。

**管理页验收反馈。** 人工截图显示 Overview 的 Downloads 卡片停留在 “Active transfers”，同页模块状态已为 preview。修正卡片的预览文案，并在 Downloads 表单内显示可见提示。使用临时数据目录和独立 Chrome 配置验证了两处实际渲染结果，下载按钮禁用，本地目录选择按钮可用。已安装插件的更新与复验仍需通过 Orca UI。

**管理页逐项复核。** 实际 Orca 插件设置显示 Toolkit 1.3.1、本地来源、普通程序执行能力、桌面通知及五个命令。“Open Manager” 从获准 worker 打开回环管理页。旧界面将 GPU 显示为 Creative tools，页脚写死了 v1.2.1，总览刷新还会把 Accounts 的 preview 覆盖为 Unavailable。修复版导航注明 GPU projects 和 GPU workflows，Connected tools 标明 GPU。两个 GPU 子页显示 unsupported，隐藏启动操作。总览在预览模式下跳过账号计数和不支持的 GPU 查询，页脚版本取自 manifest。

在独立数据目录的实际浏览器页面逐项验证了总览、Accounts、Downloads 和两个 GPU 入口。以替代选择器完成含中文和空格的目录选择、取消和刷新，路径保留。本机已安装版的 Downloads 按钮触发了真实 osascript 目录选择进程，未确认该次选择保存或原生取消结果。手动切换真实 Orca 插件开关至关闭后，再次确认管理端口拒绝连接，GPU 管理端口亦未监听。另观察到一次开关仍显示开启但管理端口已关闭，重新运行命令可恢复。当前 Orca 代码存在空闲 worker 回收机制，缺少本次具体退出原因的证据。

**待审查及人工验收。** 本次复核时最新修复尚未安装；后续实机结果见第 8 节。非默认 profile、Intel Mac、配对握手、账号与远程下载、GPU 执行仍留待后续阶段。

## 8. 获准更新后的实机复验（2026-11-05）

用户明确批准 `5e891a2` 的本地来源 Toolkit 1.3.1 更新与启用。通过 Orca 官方界面的“从文件夹”流程安装，确认本地来源并启用，未修改安装数据库或绕过审核。新安装目录中的 48 个文件与该提交的插件源码逐一一致。以下结果来自真实 Orca worker 和实际管理页。

| 检查项 | 结果 |
| --- | --- |
| 宿主启动与监听 | Open Manager 命令返回 ready，管理服务仅监听 IPv4 回环。 |
| 总览及版本 | Accounts、Downloads 的卡片和模块状态均为 preview；页脚显示 v1.3.1。 |
| Accounts | 页面显示 macOS preview，未展示或读取真实账号。 |
| Downloads | 表单内显示 macOS preview；远程浏览、Start download、Pause all 禁用，本地目录选择按钮可用。 |
| GPU | 侧栏显示 GPU projects、GPU workflows，总览明确显示 GPU unsupported；两个子页均显示 macOS 不支持，未提供启动按钮。宿主 GPU Workflows 命令返回管理页 workflows 地址，GPU 管理端口未监听。 |
| 真实目录选择 | 向初始目录传入含中文和空格的空测试目录，确认 worker 启动系统 osascript。自动化工具未暴露该系统对话框的可操作目标，未完成原生选择保存、取消和刷新保留的实机验收。替代选择器的既有测试不能替代此项。 |
| 停用与恢复 | 在原生选择器等待期间通过 Orca 开关停用插件，确认管理端口拒绝连接，原 worker 和该 osascript 子进程退出；GPU 管理端口仍未监听。随后重新启用并运行 Open Manager，管理页恢复。 |
| 空闲生命周期 | Orca 插件日志明确记录 `worker reaped after idle period`，确认此前开关开启但页面断开的一次原因为宿主空闲回收。长期驻留验收未通过；当前可重新执行 Open Manager 恢复。需后续审查宿主支持的生命周期方案，不以伪造活动绕过回收。 |

针对 Mac 平台、模拟激活和统一页面重新运行以下测试：6 通过、0 失败、3 个不匹配用例跳过。Windows 用例未执行，继续遵循第 7 节范围排除。

```sh
node --test --test-name-pattern='^(running macOS|missing, non-executable|profile override|open and folder selection|macOS activation|all views share one shell)' plugins/toolkit/test-unified-ui.cjs plugins/toolkit/test-platform.cjs
```

本轮未调用真实 Accounts RPC，未执行远程下载、生成密钥或启动 GPU/ComfyUI，也未修改 TCC 或其他系统安全设置。原生目录选择的保存、取消、刷新保留及 TCC 拒绝仍需可操作的系统对话框验收；不能宣称阶段 1 实机验收全部通过。

## 9. 人工目录选择失败与修复（2026-11-05）

用户实际打开系统目录对话框并选择含中文、空格的测试目录后，页面返回 “The folder selection window could not finish.”，此项验收失败。非交互探针复现了 `set selection to <directory alias>` 的 AppleScript -10006 错误：`selection` 与只读属性冲突，故障发生在选择结果赋值处，尚未进入配置写入。此前仅编译脚本及替代 Node 子进程回执的测试未覆盖此运行时错误。

Toolkit 1.3.2 改用 `chosenFolder` 接收 alias，并将交互命令分离为固定的 `requestFolder` handler。仍由固定脚本接收 argv，不拼接用户路径。新增 `test-macos-directory-picker.cjs`：测试脚本继承生产 `run` handler，仅替换需要点击的目录选择动作，由系统 osascript 执行真实的赋值、alias 转换和异常处理，再通过管理 HTTP 路由验证保存、取消与失败不改配置、重启后读取及 0600 配置权限。路径含中文、空格、引号及美元符号；原始错误继续脱敏。

同一回归测试在临时源码副本恢复旧 `selection` 变量后按预期失败；修复后通过，生产脚本编译成功。以下套件结果为 7 通过、0 失败、3 跳过，Windows 用例未执行：

```sh
node --test --test-name-pattern='^(macOS AppleScript|running macOS|missing, non-executable|profile override|open and folder selection|macOS activation|all views share one shell)' plugins/toolkit/test-macos-directory-picker.cjs plugins/toolkit/test-platform.cjs plugins/toolkit/test-unified-ui.cjs
```

用户随后明确批准 `1a5f3ee` 的 1.3.2 更新。已通过 Orca 官方界面确认本地来源、安装并启用，49 个插件文件与该提交一致，实际页面显示 v1.3.2，GPU 管理端口未监听。本地数据目录权限为 0700。

**人工配合实机复验通过。** 从新版 Downloads 打开真实系统目录窗口，由用户点击选取含中文及空格的测试目录；页面返回 “Download folder selected.”，刷新后路径保留，生成的目录偏好文件权限为 0600。再次打开窗口并由用户点击取消，等待提示消失、Browse 按钮恢复，原目录不变；再刷新确认仍保留。此前“选中目录即报错”的故障不再出现，选中保存、刷新保留、原生取消三项通过。

上述结果来自用户点击原生对话框及自动化读取页面回执，区别于仅替代对话框的自动测试。TCC 拒绝行为仍未验证。第 8 节已确认的宿主空闲回收限制未在此次修复中处理。


## 10. 阶段 2 Accounts 实施记录（2026-11-05）

基线为 PR #2 的最新 head `a2ad0c36e47e1192a97f7f4bc6cf9e6ee908131c`；本地阶段 1 分支与 origin 相同，开始时工作区干净。新分支 `feature/macos-accounts` 从 `feature/macos-platform-runtime` 创建，Draft PR 以该阶段 1 分支为 base，不修改 main。

**实现。** 复用 `modules/accounts.cjs`、`remote-runtime.cjs`、Controller、server 和原账号卡片页面。RuntimeClient 从阶段 1 的 `runtime.loadSdk()`、`runtime.userDataPath`、`runtime.cli` 和显式配置的 environment 构造，保留 30 秒超时和 profile 发现校验。没有自动发现 environment 或读取配对材料的新入口。

Mac 激活、初始化、页面打开、`GET /api/accounts`、`GET /api/status` 及总览刷新均不产生 Accounts RPC；页面先显示 preview 和手动刷新提示。只有用户明确刷新、切换或启用/禁用账号时才调用已有业务流程。请求成功且通过远程身份、回包和本地偏好校验后显示 ready；错误或配置更改会清除快照和 ready。ready 表示最近一次请求成功，不表示持续探测连接。错误按固定代码映射为权限、配对、协议、超时和断开诊断，未知错误给出通用检查建议，不输出 SDK 原始消息。

保留邮箱脱敏、当前账号、登录状态、用量归属与旧 SDK 双快照检查、用量和重置时间；补充 `usageStatus.state` 的 ready / stale / unknown / error 分类，原细分 code 保留。账号切换继续校验 ID、当前状态、`expectedActiveAccountId`、单次切换锁、配置是否改变和切换后的 `activeAccountId`。即使目标已是当前账号，也先验证 expected ID。切换超时或确认读取失败不自动重试、不报告成功。

Mac 本地偏好恢复到 `runtime.configDir`。目录 0700、文件 0600，仅保存 environment 与账号 ID；独占随机临时文件后原子替换。损坏文件、启动后外部修改、不安全模式、属主不符或符号链接均失败关闭，不自动修复覆盖。Windows 原有偏好保存与远程读取方式保留。

| 检查项 | 本轮证据与结果 |
| --- | --- |
| Accounts 针对性测试 | `node --test plugins/toolkit/test-accounts.cjs plugins/toolkit/test-macos-accounts.cjs`：22 通过，0 失败。 |
| Toolkit 全量 Node 测试 | `node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs`：61 项，60 通过、0 失败、1 跳过。跳过的是需要已安装 Windows Orca/OpenSSH 的原生探针；Mac 上的 Windows 夹具与模拟启动回归通过，不等于 Windows 实机验收。 |
| RuntimeClient / RPC | 仅 mock SDK：Mac 构造参数、environment 更改、无配置拒绝、isRemote=false（含缓存 client）、ok=false、缺失/非法 runtimeId、SDK 加载失败及不兼容响应；初始化和被动 GET 的 client 构造与 RPC 计数均为 0。 |
| 数据与偏好 | 合成邮箱、登录标记、重置时间、用量归属、四种用量状态；偏好保存/恢复/环境隔离、0600/0700、损坏和外部改动拒绝、符号链接与不安全权限拒绝。未保存 OAuth、Cookie、Token 或密码。 |
| 切换与错误 | 非法 ID、缺失/需登录账号、expected ID 不匹配、配置漂移、重复切换与切换中禁用/刷新拒绝、切换后 active ID 不匹配；权限/配对/协议/超时/断开模拟错误；切换及后验读取失败均不成功、不重试。 |
| 自动化页面交互 | Node VM 执行生产 Accounts 脚本并经过真实回环 HTTP 路由，模拟 DOM 验证刷新、显示、禁用/启用、切换期间按钮锁定、失败及设置更改。独立浏览器访问同一 mock SDK 管理服务，完成可见刷新、三个合成账号卡片、禁用/启用和切换后 Selected 验证；总览显示 3，Downloads 预览、GPU unsupported。 |
| 浏览器发现的修复 | 统一 shell 样式隐藏模块标题栏，原 Refresh usage 按钮随之不可见；将按钮移到内容区并复验可见、可点击，补充回归断言。没有重新设计管理页。 |

**测试与真实验收严格区分。** 本轮没有安装或激活真实 Orca 插件，没有调用真实 `accounts.list` / `accounts.selectClaude`，没有读取真实账号、用量、配对材料、OAuth、Cookie 或 Token，也没有自动使用真实 environment。HTTP 服务只在回环地址与临时配置目录中运行，所有 Accounts 数据来自 `fixtures/accounts.cjs` 的合成 RuntimeClient。没有开放 Downloads 或 GPU。

**仍待验证。** 真实远程配对、权限与 RPC 错误码/回包兼容性、真实用量归属及切换后新建 Claude 会话的账号，须在 ChatGPT 审查和人工授权后单独验收；不宣称真实账号能力已通过实机验证。阶段 1 的宿主空闲 worker 回收及 TCC 拒绝未在本阶段解决，Intel 与 Windows 原生环境未做实机验证。

## 11. 阶段 3 Downloads 实施记录（2026-11-05）

开始时工作区干净。fetch 后 `feature/macos-accounts` 与 origin 一致，最新提交为 `c68b6e5c04deb97311ef07fe5c0ca6eceb353519`。从该提交创建 `feature/macos-downloads`，Draft PR base 为 `feature/macos-accounts`，不修改 main，不合并 PR。

**实现与安全边界。** Mac 使用阶段 1 runtime 的 CLI、SDK、OpenSSH、childEnv 和 downloadsDir，Controller 解除 Downloads preview。缺少 environment 为 needs-setup；依赖检查通过后为 configured，初始化/页面 GET 不构造远程 client、不启动 SSH/keygen；只有明确浏览/下载/恢复/清理操作才访问远程。ready 表示最近一次远程请求成功，错误后为 offline/error，不以本地初始化成功冒充远程连接。

每任务独立 Ed25519 密钥、known_hosts 和 HostKeyAlias；固定 StrictHostKeyChecking=yes，隔离 SSH 用户配置、agent 与全局 known_hosts。Mac 数据/密钥目录 0700，私钥/known_hosts/任务 JSON/partial 0600，拒绝不安全权限与链接。host key 来自既有 Orca 通道，同任务 pin 改变拒绝更新。清理必须确认回执才删除本地密钥，失败保留可重试 Clear access 和更换目标拦截；重启不自动访问 VPS 清理授权。

远程目录以既有 `repo list` 项目根为范围，由 RuntimeClient `files.browseServerDir` 只读浏览。拒绝项目外路径、根目录、点段、控制字符、相似前缀和目录符号链接；逐层确认目录及回包路径。隐藏开关、过滤、200 项分页保留。下载准备的固定脚本在注册前校验真实源路径属于项目，`remote-reader.py` 协议与生产文件未修改，没有增加通用 shell 能力。

引擎保留并行/重试/暂停/继续/完整 SHA-256/独占目标发布，补充持久化分块摘要恢复检查、完整写入、Mac 进程锁和死进程锁恢复。APFS 硬链接发布中断后，仅在 inode 与 SHA-256 均确认属于该任务时恢复完成，不覆盖已有文件。默认 `~/Downloads/Orca`，沿用原生选择器及取消保留设置；权限拒绝与磁盘不足提供固定诊断，完成后的 Open folder 调用 runtime.openPath。Accounts 仍为阶段 2 行为；GPU 仍 unsupported。

| 测试 | 结果与范围 |
| --- | --- |
| Downloads 专项 Node | 32 项：31 通过、0 失败、1 跳过。包含下载模块、远程浏览、Windows/Mac 目录选择夹具。 |
| 全部 Toolkit Node | 72 项：71 通过、0 失败、1 跳过。Accounts、平台 adapter、统一 UI、GPU 原有回归通过。 |
| Windows | 原有适配夹具、路径/命令参数与模拟 GPU 启动通过；唯一跳过项是需要 Windows Orca/OpenSSH 实机的原生探针，不等于 Windows 实机验收。 |
| SSH | Mock exec/spawn 验证 Mac/Windows 参数、Ed25519、childEnv、0700/0600、quoted known_hosts 路径、host-key pin 变化拒绝、StrictHostKeyChecking、限定 read、SSH 清理与 Orca fallback、清理失败保留密钥。没有调用真实 ssh/keygen。 |
| 目录权限 | 合成项目根、Mock RuntimeClient 覆盖初始项目列表、隐藏文件、名称过滤、分页、中文、路径越界、符号链接、回包未确认、非远程 client 与 SDK 错误脱敏。 |
| 引擎/APFS | 临时目录中使用 24 MiB 合成文件，验证并行上限、进度/速度、重试、暂停/重开/继续、已完成块损坏后的重传、完整 hash 失败、独占目标、元数据/partial 权限、进程锁、磁盘不足后重试。SIGKILL 杀死隔离 Node 子进程后恢复死进程锁和任务，仅 Resume 继续传输；APFS 发布中断用故障注入验证同 inode 恢复。 |
| 页面与本地操作 | 回环 HTTP + mock manager 验证 needs-setup/configured/ready/offline/error、任务列表、禁止带待清理任务更换环境、缺失 SSH 工具关闭能力、Finder openPath 路由。Node VM 执行生产 Downloads 脚本验证实际任务和连接状态文案；平台测试验证 `/usr/bin/open` 参数。 |
| 原生目录选择与 TCC | 本轮运行既有非交互 AppleScript/目录选择测试，覆盖中文空格、保存、取消及刷新保留。真实点击验收证据见第 9 节；本阶段未再次请求真实 TCC 授权。TCC 拒绝仅 EPERM/EACCES 模拟，不作为真实权限验收。 |
| Python reader | 隔离 HOME、合成源文件、公钥和 host public key：register forced-command/restrict、0600 元数据、精确范围、越界/任意命令拒绝、源文件变化拒绝、仅清理本任务授权通过。生产 reader 未修改。 |

执行命令：

```sh
node --test plugins/toolkit/modules/downloads/test*.cjs plugins/toolkit/test-remote-browser.cjs plugins/toolkit/test-directory-picker.cjs plugins/toolkit/test-macos-directory-picker.cjs
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
python3 plugins/toolkit/modules/downloads/test_remote.py
git diff --check
```

**真实 VPS 尚未测试。** 本阶段未安装/激活新插件、读取真实 SSH 私钥/配对材料、生成真实 SSH 密钥、访问真实远程文件或修改 Tailscale/PF/防火墙/TCC。全部下载流量来自 mock，APFS 证据仅为本地临时目录离线测试，不能写成真实传输验收。

**待 ChatGPT 审查及人工批准后的验收步骤：**

1. 通过 Orca 官方 UI 审核并更新插件；人工指定专用测试 environment 和项目，明确授权真实配对、每任务密钥生成及远程临时授权操作。打开页面时确认未配置为 needs-setup，配置后仍明确远程尚未检查，GPU unsupported。
2. 在专用 Linux 项目中放置合成大文件、中文/空格文件名、隐藏文件和项目外符号链接。验证项目范围、分页/过滤，越界拒绝；若 SDK 缺少符号链接类型或 canonical path 信息，保持失败关闭，记录兼容性问题，不降低校验。
3. 人工选择 Mac 测试目录，分别验证允许、取消和 TCC 拒绝；确认无需管理员权限、取消不改原目录、错误不泄漏连接信息。
4. 对合成大文件运行 4/8/12/16 并发，暂停/恢复，断网重试，关闭/重开 Orca 后恢复；独立对比 SHA-256，验证预存同名文件不被覆盖。用完成卡片 Open folder 确认 Finder。
5. 验证完成/取消/Clear access 后仅移除本任务远程 authorized_keys 条目与元数据；中断清理时保留提示，恢复连接后可重试。真实 host-key 变更只在专用测试服务器安排，必须失败关闭。

**剩余限制。** 真实 SDK/CLI/SSH 回包、项目根与符号链接语义、真实 TCC、长时间大文件与真实网络中断均待授权验收。阶段 1 已确认的 worker 空闲回收仍可能中断传输，需重开管理页并 Resume；不伪造保活。准备阶段若在创建 partial 后、元数据落盘前的极短窗口退出，保留文件并失败关闭，可能需 Clear access 后新建任务。非硬链接文件系统复制回退在发布中断时可能需要人工检查，绝不自动覆盖。PID 复用或不完整锁按占用/异常处理，需要人工确认后修复；不强行移除活进程锁。Intel Mac、Windows 原生和断电耐久性未验收。

## 12. 阶段 4 ComfyUI Bridge（2026-11-05）

基线 `feature/macos-downloads` = `872b7508ad4d7eab0c4e4d6e73c9e7910516d454`，fetch 后本地/远端相同，开始时工作区干净。从此创建 `feature/macos-gpu-comfyui`，Draft PR base 为阶段 3 分支，不修改 main、不合并 PR。

实现和文件复用清单见 [实施文档第 8 节](macos-port-implementation.md#8-阶段-4macos-comfyui-bridge-实现2026-11-05)。本阶段仅 ComfyUI，远程 MCP 文件和请求格式未改。Mac 不实例化 native/Tripo 控制，也不复制这些控制脚本到 Mac 安装。Windows installer 额外复制平台包，其余 Windows 安装行为保留。

| 要求 | 离线证据 |
| --- | --- |
| arm64 / Python 检测、解释器发现 | Darwin/arm64/版本门槛、Intel/Rosetta 拒绝、venv 路径保留、缺失解释器拒绝；不运行用户 Python。 |
| 根目录与必要依赖 | 合成 main.py/cli_args.py/目录，缺失及不支持禁用 custom/API nodes 的版本拒绝，依赖 probe 成功/失败为 mock。 |
| MPS / CPU fallback | 模拟 torch MPS 可用/不可用；验证明确 backend 和 --cpu 参数，未导入真实 torch。 |
| Token / 配置权限 | 随机 token、0600 文件、0700 目录，错误权限/符号链接/重复写入拒绝，Token 不在 config.json 或 plist 参数中。 |
| LaunchAgent | 临时目录生成 plist，plistlib 解析和系统 plutil -lint 通过；bootstrap/print/bootout/kickstart 均为 mock，没有注册真实服务。 |
| 生命周期 | mock Popen/killpg、固定回环参数、MPS/CPU 分支、启动超时、外部占用不接管、停止只针对持有的子进程。 |
| Tailscale 地址 / 身份 | Mock status JSON 验证本机地址、稳定节点 ID、批准 peer；非本机地址、0.0.0.0、公网、IPv6、LAN 全拒绝。 |
| 认证 / allowlist | HTTP 错误 token 拒绝、peer 拒绝、节点 ID 漂移、离线/超时拒绝；真实 socket 仅回环，用测试 adapter 将连接身份映射到合成 peer。 |
| ComfyUI / 参数 | 本地 mock HTTP 实现 queue/prompt/history/view，类型、范围、未知参数、错误 graph 和 URL/redirect 拒绝。 |
| 任务 / 下载 / 重启 | 原 MCP Client 调用 workflow/submit/query/cancel/fetch，合成产物字节校验，重启加载任务、幂等重试及未知提交不自动重投；运行中 cancel 明确拒绝。 |
| 离线 / 脱敏 | Bridge/ComfyUI/Tailscale 故障、受管进程丢失、HTTP 错误与日志异常不泄漏合成 secret；管理页 token 与远程 Bearer 分离。 |
| Toolkit | 六项 ready 条件逐项置假、launcher 存在但 token/config 异常、HTTP 错误脱敏；Mac native/import 写路由拒绝，Accounts 被动读取和 Downloads 阶段 3 回归保留。 |
| 安装 / 卸载 | Preview 不写文件、不跑 probe；安装/卸载在临时 HOME + mock 系统命令执行；卸载保留 ComfyUI、workflow、token 和任务数据。 |

测试结果：

- GPU ComfyUI Python 专项：68 项通过，0 失败（包括新增 Mac 测试 23 项）。
- 全部 Toolkit Node：78 项，77 通过、0 失败、1 项需要 Windows Orca/OpenSSH 的原生探针跳过；其中 Mac GPU 专项 6 项通过。
- 全部 Python Bridge：164 项，161 通过、0 失败、3 跳过。跳过 Windows Job Objects 实机用例及两项 Windows Tripo 文件系统编码用例；不代表 Windows 实机或 Mac Tripo 验收。
- `git diff --check` 通过。安装版模块在隔离 Python `-I` 下可导入；只执行 `--help`，不启动服务。

执行命令（仓库根目录）：

```sh
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_macos_bridge.py'
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_*.py'
node --test plugins/toolkit/test-macos-gpu.cjs
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
git diff --check
```

GPU 专项集合为 `test_macos_bridge`、`test_bridge`、`test_comfy_startup`、`test_comfy_history`、`test_workflow_library`、`test_job_reuse`、`test_instance_lock`，将 `services/local-gpu-bridge` 加入测试模块路径后通过 unittest 加载。

**既有测试问题的独立核查。** 对阶段 3 原始 commit 做临时 archive 复跑，原有 141 项中出现 3 failures + 1 error，与本次首轮相同：Blender 合成路径未 canonicalize（Mac `/var` 别名）、Windows Firewall 夹具未模拟平台边界、两项 Tripo Windows 路径编码在 POSIX 上不成立。修正前两项测试夹具；对后两项添加 Windows-only 声明，Windows 上仍执行。没有修改 Blender/Tripo 生产实现，也没有把跳过写成通过。测试中原有 malformed workflow 的预期异常日志不表示套件失败。

**没有真实 GPU 验收。** 本轮没有安装/启用新 Orca 插件、读取真实 Bridge 配置或 Token、查询真实 Tailscale 节点、注册 LaunchAgent、启动用户 ComfyUI、下载模型或提交真实生成任务。所有安装数据、token、IP 和任务均为临时合成夹具。真实 MPS、ComfyUI 版本/依赖、实际 Tailscale CLI 回包、实际登录服务与信号生命周期、真实 GPU 输出质量/性能均未验证。

**审查后需要人工授权的步骤：**

1. 选择可信 ComfyUI 与 Python 环境（本阶段禁用 custom/API nodes），确定已有输入/输出/workflow 目录。审核安装预览后才执行 `--confirm`。
2. 确认本机 Tailscale 地址和批准 peer，验证实际 CLI/status schema；失败时停止，不放宽绑定或身份检查。客户端私密配置只经批准的安全通道传输，不放入命令行/日志/PR。
3. 验证用户 LaunchAgent 启停和登录行为；在回环管理页显式启动 ComfyUI，检查 MPS 或 CPU fallback、六项 ready 条件、队列取消及小型可信工作流产物。
4. 用专用环境验证错误 token、非批准 peer、Tailscale 离线和本机 IP 变化；确认 Bridge 拒绝请求、ComfyUI 仅监听回环。无需 PF 或常驻管理员权限。
5. 审批 Orca 插件更新后验证实际面板。测试卸载预览和服务注销，核对模型、工作流与全部保留数据不变。

**限制。** custom/API nodes、Intel/Rosetta、其他 GPU 应用不支持；不承诺进程组等同于 Job Objects 或出站沙箱。强制杀死 Bridge/断电可能遗留 ComfyUI，重启拒绝接管端口，需人工核对并停止原实例。未知提交不自动重投；运行中任务只能本机停止整个托管 ComfyUI。没有自动升级/覆盖/删除保留数据的功能。真实 MPS、网络、LaunchAgent 和 Windows native 均待实机验收；原有 Toolkit worker 空闲回收限制继续存在。


## 13. PR #5 针对性安全修复（2026-11-05）

本次仅修复 `feature/macos-gpu-comfyui` 的网络、鉴权和服务生命周期边界，未增加 GPU/远程功能。以下结果替代第 12 节的运行期恢复语义及测试计数。

| 安全边界 | 修复和离线证据 |
| --- | --- |
| 本机 Tailscale 绑定 | 启动前及绑定后检查 Self/节点 ID，并核对 ifconfig 的 utun IPv4；Self 有地址但接口缺失、地址落在 en0、地址漂移全部拒绝，测试断言没有地址回退。 |
| 运行期监测 | 每轮间隔 1 秒执行检查；Tailscale/ifconfig/lsof 各有 4 秒超时。请求同时实时检查，不仅依赖后台结果。故障检测后锁定拒绝并退出；恢复不自动解锁。 |
| 每请求鉴权 | 对 status、任务列表/状态/提交/取消、工作流、产物、上传及未知/禁用路由逐一测试：同一个 keep-alive socket 先成功请求，再错误 Token 或切换未授权 peer，全部返回固定 403，未调用后端。忽略伪造 X-Forwarded-For。 |
| Token 轮换 | 私有 Token 文件原子替换后，同一连接新 Token 成功、旧 Token 拒绝；本地管理 Bearer 同步失效。系统探测完成后再次读 Token；非法权限/文件内容失败关闭。 |
| 服务停止/重启 | 实际临时回环服务器使用生产 serve/关闭逻辑；地址消失、变化、Tailscale 断线、接口消失、探测超时、ComfyUI 监听异常后，Bridge 和管理端口拒绝新连接，已接受 socket 收到 EOF。退出/SIGTERM 的共同清理路径、管理端口启动失败亦关闭资源。恢复前重启不绑定端口，恢复后新实例重新验证。 |
| LaunchAgent | plistlib 参数数组正确保留空格、XML 特殊字符和引号；系统 plutil lint 通过。无 UserName/root LaunchDaemon；拒绝 root/euid 提权。KeepAlive=false，mock 异常退出只允许显式 kickstart，服务重新检查安全条件。 |
| 停止/卸载 | 校验完整 plist 与 launcher 元数据及权限；截短参数、替换程序、修改 KeepAlive/UserName 均拒绝 stop/uninstall，不删除文件；正常卸载保留用户标记文件、配置/Token、ComfyUI 和工作流。 |
| ComfyUI | 固定 127.0.0.1:8188 和既有禁用参数；lsof Mock 验证受管 PID 与实际监听地址，拒绝 wildcard/公网/Tailscale/IPv6/其他 PID/探测异常；健康/后端访问之前检查，每次后端调用重做。只终止当前持有的子进程；服务最终停止后迟到的 Start 被拒绝。 |

验证命令及结果：

```sh
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_macos*.py'
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_*.py'
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
git diff --check
python3 scripts/check.py
```

- macOS 专项 44 项通过，其中 `test_macos_security.py` 新增 21 项。
- 全部 Python Bridge 185 项：182 通过、0 失败、3 Windows 专属跳过。
- 全部 Toolkit Node 78 项：77 通过、0 失败、1 Windows 原生探针跳过。
- diff whitespace、源码/语法/私密文件扫描通过。预期 malformed workflow 夹具的日志异常不表示测试失败。

**剩余风险/未验证项。** 所有 Tailscale、ifconfig、lsof、launchctl、Popen/killpg 都是 Mock；未查询真实网络状态、注册 LaunchAgent、读取真实配置/Token、访问 VPS、下载模型或执行真实 GPU。实际 macOS Tailscale utun 命名/status 格式、lsof 输出及权限、真实登录/信号和 MPS 仍需后续实机验收。探测是有超时的周期检查，不是 OS 网络沙箱，故障出现到检测存在间隔；检测后立即锁定，关闭已接受连接可能中断下载。ComfyUI 必须仍是可信本地安装，Bridge 不绕过其认证或自动接管外部实例。SIGKILL/断电可能遗留 ComfyUI；禁止自动重启和外部进程接管，需人工处理。Token 文件应以 0600 原子替换并同步客户端；其他配置修改须显式重启。原有 Windows 专属跳过仍不代表 Windows 实机验收。

## 14. PR #5 安全边界复核与追加修复（2026-11-05）

本次开始时工作区干净，实际当前分支为 `feature/macos-godot`。fetch 后安全切回用户指定的 `feature/macos-gpu-comfyui`，本地/远端均为 `b2fdd22204af25e0cecf8e73f87a7fc684267b83`；PR #5 仍为 Draft，base 为 `feature/macos-downloads`。该基线已含第 13 节安全修复，本次只补充边界问题，没有带入 Godot 分支代码、创建新 PR、修改 main 或合并。

| 审查项 | 本轮修复 / 证据 |
| --- | --- |
| 本机监听地址 | 除 Tailscale Self/node ID 外，要求目标地址只出现一次，且位于具有 UP/RUNNING 标志的 utun；停用/未运行接口、目标地址同时出现在 en0 或另一 utun、未解析接口头全部失败关闭。没有地址回退。 |
| 网络监测与进程并发 | ComfyUI listener 检查与启停共享生命周期锁，避免检查旧进程却操作新进程。后台 monitor 在 Start 占锁时跳过该次 listener 检查，继续 Tailscale 监测；Start 自身检查 listener。此跳过仅供 monitor，不能用于 HTTP/ready 授权。 |
| 错误 ComfyUI listener | 在等待受管子进程 TERM/KILL **之前**设置全局 revoked；即使停止较慢，远程请求也立即拒绝。测试在 Mock child.wait 内验证已经撤销访问。 |
| 停止与迟到启动 | 若 stop/final stop 发生在 dependency probe 期间，probe 返回后拒绝 Popen；不在服务停止后短暂创建子进程。测试使用事件同步和 Mock Popen，不启动 ComfyUI。 |
| 每请求认证 | 保留全部远程 API 的逐请求 Bearer/peer 检查和 Token 轮换。补充本地管理请求检查当前安全状态；浏览器 session 不绕过配置/Token 权限/网络失败。慢 POST body 读取后再次验证安全状态与 Bearer，GET 在网络探针后重新验证 Bearer，避免期间轮换的旧凭据继续读取或启动。 |
| 产物流撤销 | 打开后端流后、发送成功头之前，以及每次阻塞 read 返回之后，重新检查 revoked；已撤销时丢弃刚读取的数据并关闭连接。固定错误信息不输出凭据。 |
| 清理失败 | 各层 finally 保证即使 remote/admin socket 清理抛错，也继续尝试后续服务和受管 ComfyUI 清理。 |
| LaunchAgent / 卸载 | 原有 plist 参数数组、普通用户 GUI domain、0700/0600、KeepAlive=false、完整 plist/launcher 校验保持。卸载在耗时 launchctl 操作前记录两个待删除文件的身份，操作后重新校验内容、权限、inode/时间/大小；即使替换成同样字节也拒绝删除，保留两个文件和用户数据。没有 root LaunchDaemon、自动覆盖或权限提升。 |

**新增测试：** `test_macos_security_edges.py` 共 12 项，覆盖停用/重复接口、撤销先于子进程等待、monitor 不受 Start 锁阻塞、probe 中停止阻止 Popen、慢请求期间断网/Token 轮换/权限变化、GET 探针期间轮换、产物流打开/读取期间撤销、清理抛错和 bootout 期间文件替换。此前 21 项安全测试完整保留，包括运行中地址消失/变化、Tailscale 断线、未授权 peer、错误 Token、keep-alive 逐请求认证、LaunchAgent 异常退出、Bridge 重启、错误 ComfyUI 地址，以及停止后真实回环端口拒绝连接/已接受 socket EOF。

**原基线复现：** 在临时 archive 中使用相同新增测试对 `b2fdd22` 做定向验证。两个产物流测试分别观察到撤销后仍发送 200 头、仍发送后端数据；卸载测试观察到 bootout 期间替换的同内容 plist 仍被删除。三项均未通过；卸载的后一子用例还因前一失败已删除文件而报错。新实现下这些用例及全部回归通过，没有用真实服务或扩大权限来验证。

执行命令及结果：

```sh
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_macos_security_edges.py'
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_*.py'
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
git diff --check
python3 scripts/check.py
```

- 新增边界测试：12 通过，0 失败。
- 全部 Python Bridge：197 项，194 通过，0 失败，3 Windows 专属跳过；包括已有 macOS 44 项和本轮新增 12 项。
- 全部 Toolkit Node：78 项，77 通过，0 失败，1 Windows 原生探针跳过。Toolkit 生产文件未改。
- diff whitespace、源码/语法/私密文件扫描通过。已有 malformed workflow 夹具的预期异常日志不表示失败。

**剩余风险和未验证项：** 本轮仍只用临时目录、Mock Tailscale/ifconfig/lsof/launchctl/Popen/killpg 及合成回环 HTTP，未读取真实配置/Token、连接 VPS、注册 LaunchAgent、启动真实 ComfyUI 或执行 GPU 工作流。实际 utun/flag/lsof 输出、信号时序、MPS、TCC 和 Windows 实机仍待人工授权验收。监测是有超时的轮询，不是网络沙箱；撤销与 socket 写入之间仍不是内核原子隔离，已经发出的数据无法撤回。卸载复验覆盖耗时系统操作窗口，不保证抵御同一登录用户在最终检查与 unlink 之间恶意并发替换；不对不可信本地用户代码作沙箱承诺。SIGKILL/断电可能遗留 ComfyUI，服务保持禁止自动接管和自动重启，需人工检查。对实际安装的代码更新仍须独立审查和人工批准，本任务只更新源码和原 Draft PR。

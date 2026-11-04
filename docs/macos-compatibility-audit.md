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

**待审查及人工验收。** 独立审查后，由用户决定是否在 Orca UI 审核插件并验证激活、停用和真实目录对话框的选择、取消及权限拒绝。获准 worker 的实际环境、非默认 profile 和 Intel Mac 仍未验证。配对握手、账号授权与用量、真实下载和各 GPU 后端分别留待后续阶段单独验收。

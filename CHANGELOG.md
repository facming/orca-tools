# Changelog

## 2026-11-05 — PR #6 Godot MCP 显式授权修复

- 将 capture_scene 和 run_project_script 的 authorized 加入 inputSchema.required；godot_operation 原有必填授权不变。MCP 缺少授权字段时在分发前拒绝。
- 保留 macOS 后端 authorized=true 和本地脚本许可检查；缺少字段或 false 仍拒绝且不创建任务/进程。Windows 工具调用需显式提供授权字段，原网络隔离、脚本权限与执行策略不变。
- 新增 5 项 MCP schema/分发/Windows 兼容测试，并扩展三个 Godot 工具的缺少/false 授权回环 HTTP 拒绝测试。全部 Python Bridge 234 项（231 通过、3 跳过），Toolkit Node 80 项（79 通过、1 跳过）。全部为 Mock 和临时数据，没有执行真实 Godot 或用户项目。

## 2026-11-05 — macOS Godot 阶段 5A

- 新增独立 Mac Godot adapter：元数据发现已有 app/明确指定二进制、arm64/Mach-O/权限与 Godot 4.5+ stable major 4 编辑器能力检查；不安装或复制用户 Godot。
- 复用 native MCP 任务/状态/PNG 下载，支持打开、运行、headless import/script、场景截图、停止、取消及固定诊断日志。执行需本地项目许可和每请求显式用户授权；无通用 shell、自动 Git 同步或 Windows Job Objects/出站沙箱等价声明。
- 独立持有进程、记录身份和退出状态，超时/取消只终止本实例子进程；重启不接管历史 PID、不自动重投未知任务。产物限制为经过路径/链接、PNG 和 SHA-256 校验的 PNG。
- Toolkit Mac 面板分别显示 Godot installed/available/running/unsupported/error；ComfyUI 六项 ready、安全检查和原 Windows 实现保留。未开放能力明确 unsupported。
- 新增 30 项 Godot Mock 测试和 2 项 Node 状态回归；使用临时项目、合成可执行文件头/PNG 与 Mock 进程，未执行真实 Godot/用户脚本、修改用户项目或部署实际服务。真实图形、版本、信号和权限验收待 ChatGPT 审查及人工批准。

## 2026-11-05 — PR #5 安全边界追加修复

- 对已有网络安全修复复核：Tailscale 目标地址须唯一且位于 UP/RUNNING 的 utun，拒绝停用/重复接口；ComfyUI listener 检查与启停串行化，后台网络监测不被启动锁阻塞。
- ComfyUI listener 异常先撤销远程访问再等待进程停止；停止发生在依赖探针期间时不再启动新子进程；清理异常仍继续停止受管 ComfyUI。
- 本地管理请求在网络检查、慢请求体读取后重验安全状态和 Bearer；session 不绕过配置/Token 权限失败。产物流打开或读取期间发生撤销时不再发送成功头或新读出的数据。
- 卸载在 launchctl 操作前后核对托管 plist/launcher 的完整内容和文件身份；文件被替换时保留文件并拒绝删除，继续保留用户数据。
- 新增 12 项 Mock 边界测试；全部 Python Bridge 197 项（194 通过、3 Windows 专属跳过），Toolkit Node 78 项（77 通过、1 Windows 原生探针跳过）。未访问真实 VPS、启动真实 ComfyUI/GPU、修改 main 或引入 Godot 功能；原 PR #5 保持 Draft。

## 2026-11-05 — PR #5 macOS Bridge 网络安全修复

- 启动前和绑定后核验 Tailscale Self 与本机 utun 地址；运行期后台监测地址消失/变化、离线、配置变化和探测失败，锁定拒绝并关闭所有服务连接。无监听地址回退；网络恢复后须显式重启并重新校验。
- 所有远程 API 每请求校验 Bearer、peer IP/节点身份/在线状态；Token 从私有文件实时读取，覆盖 keep-alive 与原子轮换，错误响应保持固定脱敏内容。
- LaunchAgent 保持当前用户 GUI domain、参数数组及 KeepAlive=false；拒绝 root/euid 提权，start/stop/uninstall 校验完整 plist 和 launcher 元数据，保留用户文件。
- 托管 ComfyUI 固定 127.0.0.1:8188，并在启动、后端调用前和后台用 mock 可替换的 lsof 探测实际监听/PID；异常停止受管子进程，服务退出后拒绝迟到的 Start。
- 新增 21 项安全回归；全部 Python Bridge 185 项（182 通过、3 Windows 专属跳过），Toolkit Node 78 项（77 通过、1 Windows 原生探针跳过）。仅临时目录、Mock Tailscale/ifconfig/lsof/launchctl/Popen 和回环 Mock ComfyUI；未连接真实 VPS 或执行 GPU 工作流。

## 2026-11-05 — macOS ComfyUI Bridge 阶段 4 开发记录

- 复用 Bridge HTTP、工作流、任务/产物和原 VPS MCP；新增 macOS 平台 adapter、私有配置/token、独立安装管理 CLI 与用户级 LaunchAgent。安装/卸载先预览确认，保留用户软件、模型、工作流和任务数据。
- Bridge 只绑定经验证的本机 Tailscale IPv4；每请求检查 Bearer、allowlist 节点身份和在线状态，离线/IP 变化拒绝，不退化到公网。管理页与托管 ComfyUI 仅监听回环。
- 原生 arm64/Python 环境检查、MPS 检测及明确 CPU fallback；固定启动参数禁用 custom/API nodes，管理受管进程的启动、停止和超时。Mac 原生 GPU/脚本/通用 shell 均不开放，无 PF 或 Windows 隔离等价承诺。
- Toolkit Mac GPU ready 要求安装、LaunchAgent、Tailscale、Token、peer 与实际 ComfyUI 健康全部通过；专用面板保留 Windows 行为，Accounts/Downloads 继续沿用阶段 2、3 实现。
- GPU 专项 68 项通过；Toolkit Node 77 通过/1 Windows 原生跳过；Python Bridge 161 通过/3 Windows 专属跳过。全部使用合成任务、临时目录及 mock，不代表真实 MPS/Tailscale/LaunchAgent/GPU 验收。真实安装、插件更新、私密配置传输与 GPU 任务待审查和人工批准。

## 2026-11-05 — macOS Downloads 阶段 3 开发记录

- 解除 Downloads preview，复用平台 runtime 和下载引擎；初始化仅表示本地配置可用，远程成功/失败按实际请求更新，任务页面显示持久化状态。Accounts 保持阶段 2，GPU 保持 unsupported。
- 每任务 Ed25519/known_hosts、StrictHostKeyChecking、forced-command reader 与授权清理保留；Mac 状态目录 0700、私钥/known_hosts/任务文件 0600，隔离 SSH 用户配置和 agent。远程浏览及下载源限制在已有 Orca 项目目录，拒绝路径越界和目录符号链接。
- 增加 Mac 管理进程锁、异常退出恢复、持久化分块校验、APFS 发布中断恢复、磁盘不足/TCC 拒绝诊断；中文和空格目录、原生选择取消、Finder 打开路由保留。
- 本阶段仅 mock 与本地离线测试；真实 VPS 下载、真实 SSH 连接和 TCC 拒绝尚未测试，不构成真实传输验收。未生成真实 SSH 密钥、访问真实远程文件或修改系统网络设置。

## 2026-11-05 — macOS Accounts 阶段 2 开发记录

- 复用 Accounts 业务及阶段 1 platform runtime，支持用户配置的远程 environment。Mac 激活、页面打开、总览计数和状态检查不构造 RuntimeClient、不调用 Accounts RPC；用户点击刷新或账号操作后才访问远程。
- 通过回包校验后解除 Accounts preview，失败撤销 ready 并显示脱敏诊断。保留远程身份、用量归属、切换并发及前后状态校验；补充明确的 ready / stale / unknown / error 用量状态和可见刷新按钮。
- 恢复 Mac 本地禁用偏好：0700 目录、0600 文件，仅存 environment 与账号 ID，拒绝损坏、外部改动、符号链接及不安全权限；Windows 原偏好行为保留。
- Accounts mock 测试 22/22 通过；全部 Toolkit Node 测试 60 通过、0 失败、1 项 Windows 原生探针跳过。浏览器交互使用合成账号和 mock SDK。真实远程配对、账号读取、用量和切换均未验证；Downloads / GPU 保持 preview / unsupported。

## 2026-11-05 — macOS 目录选择修复 / Toolkit 1.3.2

- 修复选择目录后 AppleScript 向只读 `selection` 属性赋值导致的 -10006 错误，改用局部变量接收目录 alias。
- 新增实际调用系统 AppleScript 解释器的非交互回归测试，只替换目录对话框动作，验证返回路径、HTTP 保存、取消与失败保留、重启读取以及错误脱敏。含中文、空格、引号和美元符号的路径通过；恢复旧变量名可重现失败。
- Mac 针对性测试 7 项通过，Windows 功能未测试。新版本已获用户批准并通过 Orca 官方界面更新、启用；用户配合点击系统对话框后，选中保存、刷新保留、原生取消三项实机复验通过。

## 2026-11-05 — macOS Toolkit 管理页实机复验

- 经用户批准，通过 Orca 官方界面更新并启用 `5e891a2`。确认 Downloads 预览提示、GPU 入口及 unsupported 状态、manifest 版本显示在实机生效。
- 验证停用关闭管理端口和待处理的目录选择进程，重新启用可恢复。Mac 针对性测试 6 项通过，未执行 Windows 功能测试。
- 记录尚未完成的原生目录选择保存与取消验收，以及 Orca 空闲 worker 回收导致管理页断开的已确认限制。

## 2026-11-04 — macOS 移植阶段 1 开发记录

- Toolkit 入口和管理服务共用平台 runtime。Mac 按运行中的 Orca 包、已审计的相对布局及 profile 定位 CLI 和 SDK，使用私有数据目录与系统 OpenSSH。
- Mac 可打开 Accounts 和 Downloads 预览页面并选择本地目录。真实远程操作和 GPU 启动已在服务端关闭，Windows 保留原目录及 GPU 启动行为。
- 新增平台和模拟激活测试。Apple Silicon 上全量 Node 套件 47 项通过、1 项 Windows 原生探针跳过。按用户确认，阶段 1 不测试 Windows 相关功能，不要求 Windows 实机补测。

## 2026-10-29 — Tripo Studio / MCP 1.7.1

- GPU 模块加入 `tripo-local`：经桥调用本机的 Tripo Studio 会员账号（第三方插件 Ador-able/tripo-studio-mcp），工具列表运行时从桥上拉取，另有 `tripo_upload`、`tripo_download` 传文件。
- 支持国内站：`tripo.region = "cn"` 时桥自动维护一份只改站点地址和本机端口的插件副本（studio.tripo3d.com / api-cn.tripo3d.com），独立数据目录、浏览器 profile 和调试端口；上游写法变了就拒绝打补丁。
- 账号、会话和画廊工具不对 VPS 开放；本机路径只以 `tripo:inputs|assets|archive/...` 代号进出，嵌套 JSON 里的路径也会替换。
- 新增 `scripts/tripo-login.py`：在专用 profile 里手动登录一次，不保存密码。
- 桥启动即拉起 Tripo 后台并每分钟保活（登录期间暂停）；后台在 bootstrap 下停掉时自动重启，只读查询重试一次，提交不重试。登录助手日志记录 Tripo 状态。
- 下发给 VPS 的工具说明和操作说明统一为英文，schema 中的非 ASCII 说明去掉，不暴露站点区域。
- 登录期间拒绝调用并说明原因；工具列表用上次成功的缓存，VPS 会话随时打开都能看到全部工具。
- 增加区域补丁、路径代号、越界路径、上传类型和 VPS 工具过滤的测试。

## 2026-10-27 — Blender VRM 扩展 / MCP 1.5.2

- 增加 VRM 4.10.2 离线安装器与固定校验，不开放 Blender 公网访问，不覆盖普通用户偏好设置。
- 受控 Blender 自动加载已安装的 VRM 扩展并初始化其偏好项；禁止把 factory-startup 配置自动保存为普通 Blender 的配置。
- scene_info 报告 VRM 导入/导出能力，import_asset/export_asset 支持项目内 .vrm 文件。
- 验证 VRM 导出/导入往返；VRoid Studio 自身导出仍不在上游 MCP 接口内。

## 2026-10-27 — VRoid 编辑器与角色状态 / MCP 1.5.1

- 区分编辑器连通状态和角色模型就绪状态，避免将主菜单的 ready=false 误判为仍在启动。
- state 返回 editor_connected、model_ready、phase 和明确的下一步操作；awaiting_model 应新建或打开角色，无需空等。
- 更新工具说明和 skill；客户端升级即可生效，不重启本机编辑器或 GPU 服务。

## 2026-10-27 — VRoid Studio / MCP 1.5.0

- 在现有 Toolkit GPU 模块加入 vroid-local：通过本机编辑器 API 新建/打开角色、预设和参数调整、镜头、预览与 .vroid 保存。
- 独立 Studio 副本、固定依赖校验、离线 IL2CPP 启动、持久防火墙与按需启动。
- 项目相对路径及显式覆盖、文本信息过滤、仅模型预览及 PNG 元数据清除；不暴露原始命令、整窗截图或通用 shell。
- 增加部署步骤和上游 API 限制说明；上游 1.0.0 尚无 VRM 导出工具。

## 2026-10-26 — Windows 音频产物与 VPS 文件时间 / MCP 1.4.1

- 修复 SaveAudioAdvanced 等节点返回 Windows 反斜杠子目录时，桥接过滤掉已保存音频的问题；保留路径越界检查。
- 支持从 ComfyUI history 恢复旧成功任务的空产物列表，无需重新生成音效。
- ComfyUI、Godot/Blender 产物和项目文件下载统一按接收 VPS 时钟设置文件时间，回执明确保存时间和时区。
- 增加 Windows 路径、旧任务恢复、三类下载及非整点时区的回归测试。

## 2026-10-26 — 失败任务复用 request_id

- Git/Godot/Blender 与 ComfyUI 支持同 request_id、同参数重新提交已失败任务，创建新一轮 job，保留原记录。
- 返回 attempt/retry_of，按持久化轮次选择最新任务；并发重试去重，服务重启后仍然有效。
- 进行中、成功、未知或已取消的任务保持原结果；更改参数仍拒绝。现有 MCP 工具接口兼容，无需 reload plugin。
- 增加并发、恢复、限流、参数绑定及 ComfyUI 重试测试。

## 2026-10-25 — GitHub SSH 代理排错文档

- 补充 GitHub SSH 443 经本机 SOCKS 代理、Windows OpenSSH 与 MSYS SHELL 兼容问题的配置和验证步骤。
- 说明 GitHub 代理应只匹配对应 Host，保留 Tailscale 私网 SSH 与主机密钥校验。

## 2026-10-25 — Go 离线构建缓存路径修复

- 修复 GOENV=off 隐藏用户配置的 Go 模块/编译缓存位置，导致已下载依赖仍报 module lookup disabled 的问题。
- 受管构建使用明确的本机缓存路径；安装器读取并保存原 Go 缓存配置。保留 GOPROXY=off、固定工具链及现有网络隔离。
- 增加缓存环境与安装器回归测试。

## 2026-10-24 — ComfyUI 自动启动 / Toolkit 1.1.1

- 修复桥接已自启但 ComfyUI 未启动，导致重启后工作流不可用的问题。
- 本机配置 comfy_startup 后，桥接登录启动、Toolkit 激活及 GPU 入口均可启动现有 ComfyUI；仅回环监听，沿用现有模型和输入/输出目录。
- 跨进程启动锁防止重复实例；等待占用端口，不中断现有队列，失败记录本机日志。配置和启动参数不暴露给 VPS。

## 2026-10-24 — Orca Toolkit 1.1.0

- Accounts 改为本机内置页面，直接通过已配对 Orca 连接列出和选择远程账号，保留邮箱遮罩、用量与重置倒计时。
- 不再依赖远程浏览器页、worktree 或单独的 VPS 账号面板；切换校验当前选择、连接和结果，已有 Claude 会话保持原账号。
- Downloads 增加远程目录/文件选择器、筛选和隐藏文件选项，按远程环境记住上次目录及文件地址。
- 继续沿用一个插件、回环管理 API、Host/Origin/token 校验；远程浏览仅使用固定只读 RPC。

## 2026-10-24 — Orca Toolkit 1.0.1

- Downloads 增加 Browse 文件夹选择按钮，保留手动输入路径。
- Windows 原生目录选择支持中文和空格；取消不改变原目录，记录上次选择或成功用于下载的目录。
- 目录选择接口沿用本机 Host/Origin/token 校验，不向 VPS 传输本机目录，不开放任意 shell。
- 继续仅分发一个 Orca Toolkit 插件；原有 GPU 管理页面及后台服务保持各自职责。

## 2026-10-24 — GPU Bridge 配音修复

- Qwen3 TTS 开放语言、预设音色与朗读风格参数；语言/音色使用节点 enum 验证，保留设备与模型路径过滤。
- 增加跨进程单实例锁并关闭 Windows HTTP 端口复用，避免重复启动导致管理页和私网接口版本不一致。
- 修复任务恰好在 history/queue 两次查询之间完成时被误判 unknown 的竞争窗口。
- 增加中文/英文调用说明、参数测试、单实例及完成状态竞争测试。

## 2026-10-24 — Orca Toolkit 1.0.0

- 合并为一个 Orca Toolkit 插件，内部分 Accounts、Downloads、GPU 模块。
- 增加 18774 统一管理页与模块设置，下载页面合入同一服务。
- 下载记录与断点保留原目录，旧设置可迁移；远程目标变更前检查未完成任务与未清理访问。
- 故障模块不阻止整个工具集启动；登录助手只激活 Toolkit。
- 后台账号服务、GPU 服务及远程 MCP 继续独立运行，保留既有隔离与凭据边界。

## 2026-10-24 — 仓库首版

- 整理 Account Switcher、Parallel Downloads、GPU Workflows、Local GPU Bridge 与自启助手源码。
- 将旧部署的个人 IP、用户路径、项目名称和 Python 路径改为配置项；未导入真实账号、token、私钥、日志和素材。
- GPU Workflows 1.0.1 从本机 launcher.json 读取 Python；Parallel Downloads 1.0.3 从私有 settings.json 读取远程目标。
- 保留 Bridge/MCP 1.4.0 的 Git 唯一项目同步、渲染前自动更新、覆盖前备份、Godot 参数及 Blender Python。
- 提供 Windows/Linux 安装脚本、多账号配置步骤、持久防火墙、自启、升级与移除文档。
- 通用自启助手只激活用户已审核启用的插件，不携带原机器的审核哈希或自动同意未知版本。

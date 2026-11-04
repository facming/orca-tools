# macOS 移植技术方案

> 状态：规划中，尚未开始 macOS 代码移植。基线为 main；Windows 现有功能必须保持兼容。
> 协作：ChatGPT 负责架构、任务拆解和审查；Codex 在 Mac 本地仓库执行；用户负责关键授权和安全决策。

## 1. 目标与结论

在 macOS 版 Orca 中支持 Orca Toolkit 三个模块：

1. **Accounts**：查看 VPS/远程 Orca 环境中的 Claude 账号、用量和重置时间，切换远程账号。
2. **Downloads**：从 VPS 并行下载文件，支持暂停/续传、重试、SHA-256 校验和远程目录浏览。
3. **GPU**：让 VPS 上的 Claude Code 调用 Mac 本机 ComfyUI、Godot、Blender 等应用，提交任务并取回产物。

**技术上可行，但不能简单替换 Windows 路径。** 应保留业务逻辑、HTTP/MCP 协议和任务模型，新增 macOS 平台适配层；安装、自启动、目录选择、进程管理和网络安全分别实现。

~~~text
Orca macOS / Orca Toolkit
  ├─ Accounts ── Orca RuntimeClient / 远程 RPC
  ├─ Downloads ─ Node.js + macOS OpenSSH
  ├─ GPU ─────── Mac Local GPU Bridge
  │                ├─ ComfyUI（优先）
  │                └─ Godot / Blender / Unity（分阶段）
  └─ Platform adapters
       ├─ Windows（保留）
       └─ macOS（新增）
              └─ Tailscale / SSH / VPS Claude MCP（协议尽量不变）
~~~

不在本次范围：重写 VPS Claude MCP、替换 Orca 远程连接机制、自动安装商业软件、自动迁移 OAuth 或浏览器登录态。

## 2. 现有源码审计

Codex 开工时需对照本地工作树复核文件和行号。

### Toolkit（Node.js）

| 文件 | Windows 假设 | macOS 适配 |
| --- | --- | --- |
| plugins/toolkit/main.mjs | 直接调用 windowsRuntime()、Windows 文件关联打开页面 | 平台 runtime 工厂；使用 open 或 Orca 支持的方式 |
| plugins/toolkit/modules/downloads/runtime.cjs | LOCALAPPDATA/APPDATA/SystemRoot、orca.exe、ssh.exe | macOS 路径发现、系统 OpenSSH |
| plugins/toolkit/Install.cjs | 假定 Windows Orca 安装路径 | 分平台安装；优先 Orca UI 导入 |
| plugins/toolkit/directory-picker.cjs | PowerShell WinForms | macOS 原生目录选择器 |
| plugins/toolkit/modules/downloads/transport.cjs | SSH/keygen 路径来自 Windows runtime | 保留 SSH 协议和安全校验，改用平台可执行文件 |
| plugins/toolkit/remote-runtime.cjs | 从 CLI 相对位置推导内部 RuntimeClient SDK | 运行时发现器；Mac 实机验证 |
| plugins/toolkit/modules/gpu.cjs | launcher.json 假定 pythonw | 跨平台 launcher |
| plugins/toolkit/controller.cjs | 数据目录绑定 Windows runtime | 使用 runtime 标准目录 |

Accounts 业务逻辑主要通过 Orca 内部 RuntimeClient 调用远程 accounts.list / accounts.selectClaude，平台依赖较少。关键风险是 macOS Orca 是否提供兼容的插件 API、RuntimeClient SDK、CLI 和远程 environment。

Downloads 的分片、任务状态、校验、重试和 Python reader 协议可较多复用。必须保留严格 SSH host-key 检查、每任务临时 Ed25519 key、forced-command 只读访问和完成后的密钥清理。

### Local GPU Bridge（Python）

| 文件 | Windows 假设 | macOS 适配 |
| --- | --- | --- |
| scripts/install-local-gpu.py | 仅允许 Windows、LOCALAPPDATA、复制 exe、Startup 快捷方式 | macOS 安装器、用户目录、LaunchAgent |
| native_process.py | Windows Job Objects、PowerShell、防火墙 COM | 独立 Mac 进程管理/安全状态，不声称等价 |
| native_control.py | PowerShell 提权安装出站规则 | 平台安全 adapter；未验证不得报告 ready |
| bridge.py / comfy_startup.py | 服务和 ComfyUI adapter 大体可跨平台 | 复用服务层，补 Mac 路径/进程测试 |
| native_unity.py | 含 -force-d3d11 | 移除 Direct3D 假设，验证 Metal/Unity CLI |
| native_blender.py | Cycles 设备选择含 OptiX | 检测 Metal，CPU 明确回退 |
| native_tripo.py | LOCALAPPDATA、Windows 浏览器/credential store | 验证 Mac Chrome profile、登录态和凭据 |
| docs/vroid.md | 依赖 Windows VRoid/BepInEx/Unity | 不承诺 Mac，先验证产品和插件注入能力 |

## 3. 第一技术门槛：macOS Orca

阶段 0 必须在用户实际安装的 macOS Orca 上验证：

- 是否有 macOS 桌面版、插件宿主和兼容 manifest/API。
- 插件 Node.js 环境、模块加载、激活生命周期和工作目录。
- RuntimeClient SDK 的真实位置、构造参数和 RPC 行为。
- Orca CLI 的真实路径、参数和 environment 列表。
- 插件数据目录、应用沙盒/TCC 文件授权和升级行为。

**禁止猜测并硬编码** /Applications/Orca.app/... 或 Contents/Resources/...。从实际安装包和进程环境探测，再实现多候选路径发现。失败时给出诊断，不绕过签名、沙盒或权限。若插件/RPC 不兼容，阶段 0 停止并提交报告，不伪造 Accounts 可用。

## 4. 架构原则

- 业务层共享：Accounts、Downloads 引擎、任务模型、Web UI、Bridge HTTP API、ComfyUI workflow adapter、VPS MCP 尽量共用。
- 系统能力隔离：路径、启动、目录选择、SSH、进程树、自启动、网络安全经 adapter 提供。
- Windows 不回归；能力缺失时失败关闭；避免在业务模块散落平台判断。

建议新增目录：

~~~text
plugins/toolkit/platform/{index.cjs,windows.cjs,macos.cjs}
services/local-gpu-bridge/platform/{__init__.py,base.py,windows.py,macos.py}
~~~

先定义最小接口，再逐个迁移调用点，不要一次性大规模移动文件。

详细模块实施、macOS 安全边界、阶段验收和 Codex 工作方式见：
- [macOS 模块实施与安全方案](macos-port-implementation.md)
- [macOS 开发阶段与 Codex 协作流程](macos-port-codex-workflow.md)

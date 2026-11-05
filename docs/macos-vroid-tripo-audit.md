# 阶段 6A：VRoid Studio / Tripo macOS 技术可行性审计

审计日期：2026-11-05（Asia/Shanghai）。项目：facming/orca-tools。流程：ChatGPT Brain → Codex 执行 → ChatGPT 审查 → 人工批准。

## 1. 范围、基线与结论

开始时 `git status --short --branch` 无改动；执行 `git fetch origin` 后，[PR #8](https://github.com/facming/orca-tools/pull/8) 的 `headRefName=feature/macos-unity`、`headRefOid=fe0a0157c1e0d686fe06fe9eed6846abc715cd06`，与本地 HEAD、`origin/feature/macos-unity` 一致。从该分支创建 `research/macos-vroid-tripo-audit`；交付 Draft PR 的 base 为 `feature/macos-unity`。不修改 main、不合并。

本阶段只静态阅读仓库代码、公开上游源码和厂商文档，未启动 VRoid、Tripo 或浏览器，未安装/编译插件，未读取账号、Keychain、浏览器 Cookie/密码/Token/OAuth 或已安装插件的运行态配置，未登录、上传、生成、下载模型、消耗积分，也未修改用户模型项目、防火墙、Tailscale、浏览器配置或系统扩展。Git 操作和文档检查不代表应用实测。

**结论：优先推进 Tripo 官方 OpenAPI 的独立适配设计；VRoid 保留为有条件的社区插件研究。两个应用在本项目的 macOS 自动化均未验收，本阶段不开放功能。** Tripo 官方 API 与现有 Studio 会员接入的账户、计费、对象和参数语义不同，迁移须显式选择，不能静默替换。VRoid 的 macOS/Apple Silicon 产品支持不等于 BepInEx 插件支持；已有社区 arm64 成功声明，但依赖栈尚不可据此复现，且本仓库 VRM 导出扩展没有 Mac 证据。[V1–V6、T1–T6、R1–R7]

### 验证程度

| 标记 | 含义 |
| --- | --- |
| 官方支持 / 文档核实 | 厂商公开产品文档或正式 API；不表示本机运行通过 |
| 社区支持 / 上游声明 | 插件维护者文档或源码；不提升为 pixiv/Tripo 官方承诺 |
| 本仓库静态确认 | 在上述固定 HEAD 阅读实现；没有执行真实应用 |
| 理论可行 / 设计建议 | 依据协议和代码提出的工程方案；未实现、未压测 |
| 尚未验证 / 阻塞 | 运行、架构、供应链、权限或语义证据不足；默认 unsupported |

下文的功能表引用第 10 节证据编号。所有估算和新增模块均为建议，不是已交付能力。用户要求“实测后填写”的项目明确保留为未实测，因为本阶段禁止真实软件操作。

## 2. VRoid Studio：逐项判断

| 问题 | 结论与验证程度 | 证据 |
| --- | --- | --- |
| 1. 原生 macOS 版本 | 有官方 macOS 安装包和 Mac 系统要求；指 macOS 产品版本，不等于已核验某安装包的 arm64 Mach-O。未下载/启动 | V1、V2 |
| 2. Apple Silicon | 官方详细要求列出 Apple M1 Series；可确认官方列入支持设备。不能据此认定所有 M 系列、macOS 版本和发布包都原生 arm64。上游另有指定版本 arm64 运行声明；本机/二进制架构未验证 | V2、V5 |
| 3. 现有插件机制 | 本仓库使用内部 API + BepInEx 6 IL2CPP + Harmony，不是 pixiv 正式 Studio 插件 SDK。Windows 安装包不能照搬 Mac | R1、R2、V4、V5 |
| 4. BepInEx macOS 实现 | 上游有 macOS x64 Mono 和 IL2CPP 构建/安装说明；IL2CPP 为 bleeding edge。官方构建列表不能证明可直接用于 VRoid arm64。社区定制 arm64 栈见下文 | V5、V6 |
| 5. 正式插件/API 操作人物 | 本次查阅的 pixiv 资料未发现公开的 Studio 人物编辑 API。VRoid Hub API/SDK 用于加载/展示 Hub 角色，不提供本地 `.vroid` 编辑。不能把 SDK 或 Unity Editor API 当作 Studio API | V3、V4、V10 |
| 6. 创建/打开/保存 `.vroid` | 产品 UI 和社区插件均支持此工作流。桥已有 `create/open/save`；Mac 插件逐项调用、版本往返和写入隔离未验证 | V1、V4、V7、R1 |
| 7. 脸型/发型/服装/参数 | 官方 UI 支持预设和参数编辑；插件公开 Face/Hairstyle/Body/Outfit、预设、滑块与颜色。不能承诺纹理绘制、任意发束操作或全部新功能都已暴露 | V1、V4、R1 |
| 8. 截图/VRM 导出 | 官方 UI 有模型拍摄和 VRM 0.0/1.0 导出。桥的 screenshot 为模型预览；自动 VRM 导出是仓库附加 C# 扩展，调用内部 coroutine。Mac 预览/导出/返回编辑状态尚未验证 | V8、V9、R1、R2 |
| 9. 不模拟鼠标键盘 | 社区插件直接调用内部 API，具备此路径；本仓库导出也不操作系统文件对话框。但 Mac 运行依赖未通过，当前不能承诺主要操作可用 | V4、V5、R2 |

### 2.1 社区 arm64 证据的边界

固定上游 `brainrom/vroid-studio-mcp@ab9ad4f90d6ba6486a234a975c86e3298ccfd75e`，与本仓库导出安装器固定 revision 相同。其 README 将 macOS 定位为研究中、未包含在发布包；同一 revision 的 `docs/macos.md` 声称 Unity `6000.0.62f1` 上原生 arm64 插件加载、Harmony 和 17890/17891 服务已端到端验证。两者分别描述“发布支持”与“定制栈试验”，不能只取成功描述。[V4、V5、R2]

该文档依赖另一个名为 `il2cpp-macos-reverse` 的仓库（`tools/bepinex-arm64/`），并说明 interop 生成需要额外修复；arm64 有跳过的 runtime hooks，Harmony patch 的异常不得越过 native detour，否则可能挂死/崩溃。文档将 Rosetta/x64 inline hooks 路线标为不支持。按同作者路径查询该依赖返回 HTTP 404，**只说明本次路径不可访问，不证明仓库不存在或没有其他发布渠道**。未取得可审计依赖源码、构建方法、固定版本/校验和和授权说明，不能安装或承诺复现。[V5；GitHub 只读查询记录]

因此：Intel 原生 x64 的 BepInEx 可用性、Apple Silicon 上 Rosetta 的可用性、原生 arm64 定制栈是三个不同命题。不要通过 Wine、Rosetta 或通用 Unity 支持推导 VRoid Mac 支持。后续应优先要求可复现的 arm64 来源及安全审查；若必须关闭 SIP、Gatekeeper、全局库验证或安装系统扩展才能工作，本方案不接受，继续 unsupported。[V5、V6；设计门槛]

### 2.2 Unity、VRM 与 `.vroid` 不是同一接口

- `VRoid SDK` 是 Unity 应用调用 VRoid Hub 的包；UniVRM 是 Unity 中的 VRM 导入/导出实现。它们都不是向封装好的 VRoid Studio.app 安装任意 Unity Editor 脚本的入口。阶段 5C 的 Unity Editor 扩展不能直接复用为 Studio 插件。[V3、V10、R2]
- `.vroid` 保存可编辑源数据，`.vrm` 是导出的头像资产。旧官方 FAQ 说不能把 VRM 重新当 `.vroid` 编辑；较新的 Dress-up 文档允许 VRM/XAvatar/`.vroid` 作为换装基模。应按功能区分，不能笼统写“Studio 完全不能导入 VRM”，也不能承诺从 VRM 恢复原有脸型/头发参数。现有 `open` 只接受 `.vroid`，没有 Dress-up/XRoid 导入操作。[V9、V11、R1]
- 本仓库 `ExportHandlers.cs` 反射调用 `EnterToVRMExport`、`ExportVRM0/1`，依赖 IL2CPP delegate、类型和内部字段；Windows 已有验证记录仅属于 Windows。Mac 需重新验证程序集、回调异常、Metal 预览、两种导出格式、超时清理、元数据及保存恢复。[R2；设计建议]
- `set_vrm_license` 是桥端文件处理，可以独立评估复用；不代表 Studio 插件能运行。当前代码默认 `commercial_modifiable`，而 VPS 工具描述仍写 restrictive defaults，存在文案与行为差异。后续实施必须统一说明、验证源素材权利，不应在本次审计偷偷改默认授权。[R1、R2、R6]

## 3. Tripo：逐项判断

| 问题 | 结论与验证程度 | 证据 |
| --- | --- | --- |
| 1. Mac 浏览器能否使用 | 官方提供 Web Studio，帮助中心建议 Chrome/Edge。没有发现本次可引用的完整 macOS/Safari/WebGL 版本矩阵；Mac Chromium 路线理论可行，未登录或实测生成下载。不是已验证的本地 Mac 原生应用 | T1、T5、S2 |
| 2. 官方 API/MCP | 有正式 OpenAPI，也有 Tripo-Developer 官方 MCP 仓库；后者 README 标为 alpha，当前通过 Tripo Blender Addon 集成，不是现有 Studio workflow MCP 的直接替代品 | T1–T4 |
| 3. Windows Chrome profile 依赖 | 服务本身和 OpenAPI 不依赖 Windows profile。现有桥默认 Windows AppData/浏览器 exe/凭据约定；上游 launcher 已有 darwin 路径，不能因此认定整个桥已支持 Mac。无需也禁止复制真实 Windows profile/Cookie | R4、T6、T7 |
| 4. macOS 登录态管理 | 首选官方 API 的用户专门授权凭据，由本机 Keychain 受限保存；网页模式只能在后续批准的全新专用 profile 中由用户交互登录。会话失效/验证码/二次验证交回用户，不自动提交密码 | S1、T2、T7；设计建议 |
| 5. 独立 profile | Chromium 支持 `--user-data-dir`。应用独立目录和 profile 可隔离日常浏览数据；它不是 OS 安全沙箱，也不能消除同用户进程及 CDP 风险 | S2、S3、T7 |
| 6. 上传/生成/查询/下载 | 官方接口涵盖图片上传、模型任务、按 ID 查询及结果下载 URL，另有模型转换。具体限制见下表；本阶段只核实文档 | T2、T8–T11 |
| 7. 不依赖 UI 自动化 | 官方 OpenAPI 可完成核心链路，无需浏览器/模拟输入/CDP。现有 Studio 社区插件仍依赖浏览器会话和部分页面执行，不能称为纯官方 API | T2、T6、T7 |
| 8. VPS MCP 名称和参数 | 可保留外层名称、参数形状、路径句柄及 workflow 信封，但只能兼容已明确映射的语义；官方 API 不自动拥有 Studio 目录、模板、旧 ID、会员额度和全部编辑能力 | R4、R6、T2、T6、T12；设计建议 |

### 3.1 三条路径比较

| 路径 | 支持性质 | 收益与限制 | 决策（建议） |
| --- | --- | --- | --- |
| 本机 Bridge → 官方 OpenAPI | Tripo 正式 API | 无浏览器会话依赖；核心链路明确；仍需 API 授权/额度、素材上传同意和安全下载；Studio 能力不能一对一推断 | 第一优先；设计和离线 Mock 后再人工授权小额验收 |
| 官方 MCP → Tripo Blender Addon | 官方仓库、alpha | 已存在 MCP，但依赖 Blender/Add-on，工具语义与本仓库不同；没有证据保证现有 workflow 契约 | 作为参考，不直接替换 VPS 工具或自动安装插件 |
| 保留 Ador-able Studio 插件 | 社区网页接口 | 接近现有会员工作流，但网页变化、登录/风控、浏览器身份与平台依赖较多 | 独立可选研究；不作为官方 API 失败后的自动回退 |

现有文档明确使用 Studio 会员积分而非 API 积分。官方文档要求单独 API key，故必须把两类接入视为不同授权/计费后端；账户和余额是否可关联应由用户/厂商确认，不能猜测互通。国内站的主机替换/BOS 补丁是本仓库社区适配，不能当作官方 OpenAPI 的中国站合同，也不能自动改主机、迁移身份或绕过地区/套餐限制。[R4、T2、T6]

### 3.2 官方核心链路与兼容差异

| 环节 | 文档确认的接口能力 | 适配约束（未实现） |
| --- | --- | --- |
| 授权 | OpenAPI 使用 Bearer API key | 不从网页或现有账号提取；仅使用用户另行配置的授权 key，不放入 MCP 参数/返回或 shell 命令 |
| 图片上传 | multipart 直接上传 PNG/JPEG/WEBP；返回 `image_token`。STS 上传支持图片及 GLB/OBJ/FBX/STL，文档限 20 MB | 本仓库桥上传限 40 MiB，不能不检查就转发；实际发送取桥/provider 两者较严上限，并核验字节单位。`image_to_model.file.file_token` 与上传返回字段名不同，需要显式转换 |
| 生成 | `POST /v2/openapi/task`；`text_to_model`、`image_to_model` 等任务，返回 `task_id` | workflow step 映射到 provider task；型号/选项白名单及扣费提示；不将 Studio `tier` 无依据地映射为官方 `model_version` |
| 查询 | `GET /v2/openapi/task/{task_id}`，含 status/progress/output | 官方要求用创建任务的同一 API key 查询；换 key 后不可把“找不到”当作可重新提交。持久记录不含密钥的 credential 标识、workflow-step-task 关系 |
| 下载 | 查询结果含模型等 URL；文档默认有效期五分钟 | 本机及时归档，过期重新查询已有任务；不重新生成、不向 VPS 暴露签名 URL；HTTPS/主机/重定向/体积检查后返回受限句柄 |
| 格式转换 | `convert_model` 支持 GLTF/USDZ/FBX/OBJ/STL/3MF，按版本及绑定状态有限制 | 只公开支持的组合；不能把 `.vroid`/VRM 作为 Tripo API 导出格式承诺 |

来源：T2、T8–T11；现有 40 MiB 限额见 R4/R6。上述 endpoint 只用于说明，未向服务发起授权请求。官方返回有 `cancelled` 状态不代表已核实可远程取消的接口；本地取消只能停止排队/后续步骤，已经提交的远端任务及费用不能承诺撤销。[T10、R4；设计建议]

## 4. Windows 代码复用与现有安全差距

| 代码/机制 | 可复用部分（静态判断） | 必须调整或验证的部分 |
| --- | --- | --- |
| `native_vroid.py` | 操作白名单、参数选择器、项目相对路径语义、覆盖同意、限额、JSON-RPC、队列锁、预览过滤、`save(.vrm)` 兼容逻辑 | 不能原样导入即宣称 Mac 可用：依赖 native_control/native_process；exe.parent 配置布局、窗口显示、启动/退出、插件/版本/架构和身份验证需独立 adapter |
| `native_control.py` / `native_process.py` | 项目授权、任务语义、安全策略可参考 | WinDLL、Job Objects、user32、PowerShell 防火墙为 Windows 专属；ManagedProcess 在非 nt 拒绝。不能删除 guard 或把 POSIX process group 当成等价隔离 |
| `vroid-export/ExportHandlers.cs` / `native_vrm.py` | 导出工作流设计、GLB/VRM 元数据处理候选 | C# 内部类型/IL2CPP ABI、native 回调与线程、Mac 写文件须重新验收；文件处理应接入 Mac 安全 I/O。PNG 清元数据不等于像素隐私保证 |
| `native_tripo.py` | workflow 状态语义、上传/下载句柄、别名、禁止自动重投、输出最小化可参考 | 默认 AppData、exe 发现、Windows credential helper、复制补丁式地域适配不应直接移植；`environment()` 全量继承环境可能把无关秘密传给 Node；shutdown 需核实本实例身份 |
| Tripo 上游 | launcher 有 darwin Chrome/Edge 路径、专用 profile 所有权检查、后台生命周期设计 | credential-store 对非 Windows 返回 interaction_required；没有 Mac Keychain 实现。CDP provider 会捕获授权请求头并在内存维护会话，部分操作有页面 evaluate；这仍是敏感会话访问，不能在本阶段运行 |
| `plugin/client.py` | VPS stdio MCP、HTTP 路由、JSON 信封、文件句柄、下载收据可大量保留 | Tripo 输入 schema 运行时透传，只有隐藏工具黑名单，不能假设任意升级后工具安全；需固定 schema 快照/允许列表。返回 SHA-256 是接收端计算，并非已与发送端摘要比对 |
| `remote_privacy.py`、bridge JSON 出口 | 现有嵌套 JSON、身份/凭据字段和 Tripo 资源 URL 过滤 | 过滤偏重已知字段和 Windows 路径；原始异常、POSIX 路径和未知字段未被完整证明安全。日志与二进制产物不自动经过该出口 |
| 已有 Mac runtime | RuntimeSecurity、私有目录、项目身份/安全文件打开、任务与产物机制可参考 | 需各 adapter 专用能力检查；不能复制当前“不提供出站隔离”的状态就声称满足 Windows VRoid guard |

依据：R1–R8、T7、T12。这里的“复用”指下一阶段可提取的逻辑，不意味着本次执行或修改。

特别需要保留为待修复项：[R1、R3–R7]

1. VRoid RPC 固定连 `127.0.0.1:17891/mcp/`；检查启动前端口空闲不能证明后续响应一定来自受管编辑器。需每会话身份/版本绑定、端口归属检查；回环不等于认证。
2. Tripo `to_local()` 只转换匹配的句柄，未匹配字符串原样透传；远端参数中的本机路径不能仅靠该转换器当作被约束。Mac 需按固定 schema 对每个文件字段强制句柄及获批根目录。
3. Tripo `inside()` 用 resolve 检查目录归属，不等于逐级拒绝符号链接/硬链接和消除 TOCTOU；upload 仅检查扩展名/大小，file 读取没有产物登记要求。后续需受控目录、安全打开、内容类型/限额、暂存发布和摘要复验。
4. `native_vroid` 的 `logging.exception`、Unity logFile、ManagedProcess 原始 stdout/stderr，及 Tripo 的异常路径都可能留下本地路径/服务返回细节。现有 JSON 过滤不能证明日志无登录态。应以固定错误码/字段允许列表、合成秘密测试证明，而不是检查真实日志。
5. Mac 当前 `macos_service.load_config()` 明确拒绝 tripo，native dispatcher 只有 Godot/Blender/Unity；受限 native HTTP 分支不开放 VRoid/Tripo。文档审计不能改变这一事实。

### 4.1 复用率估算口径

为规划提供**逻辑模块复用估算，不是代码行统计、完成度或功能覆盖率**。将协议/schema、验证、安全文件、业务状态、输出过滤、进程、凭据、平台/依赖八类职责分别评估；保留职责也可能重写实现。上游版本和能力范围变化会显著改变比例。[R1–R8、T7、T12；工程估算]

- VRoid：桥接业务逻辑候选约 40–60%；VPS 外层协议约 80–100%；Windows 进程/防火墙/安装实现约 0–10%。定制 arm64 注入栈不计入“现有可复用代码”，其供应链是阻塞项。
- Tripo：采用官方 API 时桥接逻辑约 30–50%，VPS 名称/信封约 70–90%；网页 backend/密码 helper 基本不复用。保留社区 Studio 路线约 50–70%，但登录、归属、文件和异常安全仍需重审。较高复用率不是优先选择该路线的理由。

## 5. 需要新增的 macOS 模块（建议名称，均未创建）

| 模块 | 职责/前置条件 | 来源 |
| --- | --- | --- |
| `bridge_platform/macos_vroid.py` | 发现指定 app 元数据；完整版本/arm64/签名与依赖清单；受管会话、能力探测；插件失败关闭。不得执行自动安装/升级 | R1–R3、V5 |
| VRoid 插件构建/验收清单 | 固定 arm64 loader/interop/Harmony/插件源码与校验和；独立复核 ExportHandlers 的 native 异常边界；最小模型 round-trip | R2、V5、V6 |
| VRoid 安全传输/产物 adapter | 受管实例身份、序列化修改、超时未知状态、`.vroid`/PNG/VRM 暂存验证、无覆盖发布；网络隔离不达标不启用 | R1–R3、R8 |
| `bridge_platform/macos_tripo.py` + `tripo_openapi.py` | Mac 生命周期/本地授权与官方 HTTPS backend；固定 endpoints；不接受任意远程 URL/命令 | R4、T2 |
| `tripo_workflow_adapter.py` | 固定 Studio 风格 schema 的允许子集 → 官方 task；账本、幂等、状态/限额/错误映射、能力目录 | R6、T10、T12 |
| `macos_credentials.py` | 使用 Security.framework 的专用 Keychain item；精确 service/account/访问控制；仅按用户授权写入/读取本应用项，支持撤销 | S1、T7 |
| `macos_browser_session.py`（仅可选 Studio 路线） | 全新专用 user-data-dir、人工登录交接、profile 锁、CDP 归属、子进程清理；不发现/读取个人 profile | S2、S3、T7 |
| 共享安全 I/O 与输出适配 | 私有存储、逐级文件描述符校验、下载白名单/限额/摘要、POSIX 路径和日志过滤；复用并扩展既有 Mac 机制 | R4、R6–R8 |

Keychain 不是让上游 Windows 密码自动登录在 Mac 上复活的理由。官方 API 路线只需要专门授权的 API 凭据；Studio 路线优先让浏览器自身保存新会话，桥不导出 Cookie/JWT。若社区 backend 必须捕获专用会话授权头，应在未来方案中单独列出、审查并获批；本阶段没有这项授权。[T7、S1；设计建议]

## 6. MCP 兼容性方案

兼容目标分三层：名称/参数形状、响应/错误语义、真实功能。只完成第一层不得宣称完全兼容。保留 Windows backend，不强迫旧部署迁移；Mac 未支持的操作返回明确 unsupported/错误，不能伪造成功或降低安全要求。[R1、R4、R6；设计建议]

| 对外契约 | 保留方式 | 差异处理 |
| --- | --- | --- |
| `vroid_tools()` | 继续 `GET /v1/vroid/tools` | 按实际插件版本返回允许操作；插件/隔离未达标不声称 ready |
| `vroid_operation(project_id, operation, arguments?)` | 继续 `POST /v1/vroid/call`、既有 MCP content/isError、state/model_ready/phase | 保留相对 `.vroid`/`.vrm`、`replace`、`discard_changes` 和 `set_param` selector；`save(.vrm)` 与 export 同一校验链；Mac 不支持的能力明确失败 |
| `tripo_upload(path)` | VPS 绝对源路径留在 VPS，本机只收到 filename/base64，返回 `tripo:inputs/...` | 不让该 path 被解释为 Mac 任意路径；40 MiB bridge 限制和 API 更严限制分层解释，预检在付费提交前完成 |
| `tripo_download(path, destination_dir)` | path 为受限句柄，destination_dir 是 VPS 目录；保留大小/SHA-256 收据 | Mac 不接受任意下载 URL；增强产物完整性而不把本机路径/签名链接返回 |
| `tripo_get_status` / `tripo_get_workflow_capabilities` | 保留名称及原 schema；状态最小化，目录只列可用能力 | 使用本地明确选择的 backend；用户能理解 API 与 Studio 计费区别；不把 API 授权伪装成网页登录成功 |
| `tripo_start_workflow(name, steps)` | steps 保留 `step_id/operation/input/depends_on/bindings`；返回 workflow_id | 本地持久 workflow 映射官方 task；先验证整个 DAG 及所有操作，不支持部分不得先扣费。Studio 专属 tier/ID 不猜测映射 |
| `tripo_get_workflow` / cancel / retry / log | 保留 workflow_id、原可选 limit 等；原状态信封可映射 | 超时后查询既有 task；无远端取消能力要明确；retry 是新任务，用户明确请求且可能再次扣费，不自动补发 |
| `tripo_list_workflows` | 保留 limit/status，查询当前 backend 的本地账本 | 不混入其他账户/后端任务，不宣称云端完整历史 |
| 模型/图片/模板目录工具 | 固定原输入 schema 后逐个证明映射 | OpenAPI 未证明的 Studio 列表/模板返回 unsupported；不得悄悄用本地归档冒充全部 Studio 云端库 |

Tripo 的精确部署 schema 不在仓库中固定，而由运行包动态提供。本次只读公开上游 `45df024…` 的 schema，**没有读取用户已安装包或启动 tools/list**，不能据此保证当前实际安装版本一致。后续以批准的固定上游版本生成无账号数据的 schema fixture，做名称/required/类型/默认值/错误契约差异检查；再用合成上传、路径攻击、过期 URL、401/403/429、结果未知和重启恢复测试验收。三个隐藏工具 `tripo_set_account/tripo_clear_account/tripo_open_gallery` 仍不对 VPS 开放；禁止自动开放新增 `tripo_*` 工具。[R4、R6、T12]

安全要求若无法在现有参数中表达，应先设计本地授权/能力配置或向后兼容的新增授权字段，评审后实施；不能因保持参数兼容而自动设置授权或取消路径检查。[R6、R8；设计建议]

## 7. macOS 安全、隐私与权限

| 风险/权限 | 审计结果与方案边界 | 验证程度/来源 |
| --- | --- | --- |
| Keychain | 适合保存用户专门授权的小型秘密；精确读取本应用项，使用 API/受限 helper，避免 `security ... -w SECRET`、argv、环境变量或明文配置。锁定/拒绝时要求本地交互，不降级明文。签名 helper 的访问控制/LaunchAgent 行为待测 | 官方能力 + 设计；S1、T7 |
| profile 隔离 | 未来仅创建应用私有、0700、无链接的新 user-data-dir；固定浏览器/channel，独立账户/区域/backend 范围，互斥打开。禁止复制日常 profile、读取 Cookies/Login Data、从现有会话导入 Token；已有目录所有权不明即拒绝 | 官方机制 + 设计；S2、T7 |
| 浏览器 Preferences | 上游启动器会修改其专用 profile 的 Preferences 以关闭密码保存等功能；原样运行也属于配置写入。本阶段未运行。未来仅允许审查后管理新建应用专用配置，不改用户现有浏览器配置 | 源码确认；T7 |
| CDP | 调试接口可访问敏感会话；只允许本实例专用浏览器，优先受管 pipe（需另验上游支持），否则 loopback + 身份/锁校验。不能接管 9222 上未知 Chrome，不暴露给 LAN/Tailscale/VPS。marker/随机端口不能抵御同用户恶意进程 | 官方风险 + 设计；S3、T7 |
| TCC Files and Folders | Documents/Downloads/Desktop、网络/可移动卷可能要求用户授权；优先私有 Application Support 暂存和明确选中的项目根。拒绝时返回固定权限错误，不请求 Full Disk Access 兜底 | 官方说明 + 待实机；S4 |
| Accessibility / Automation | 仅直接 API/HTTP 和应用内模型渲染通常无需模拟输入权限；Apple Events 控制应用需 Automation，系统 UI 控制需 Accessibility。若核心能力仅能靠这些高权限 UI 操作实现，停止并单独报告，不实现自动兜底 | 官方权限 + 设计推断；S4 |
| Screen Recording | 截取桌面/其他窗口需单独评估屏幕录制许可；优先应用内部模型 PNG，不采集账号页面/完整窗口。是否触发提示须按真实 capture 实现验证 | 官方权限 + 待实机；S5、R1 |
| 上传路径 | 只能使用获批项目/暂存中的登记文件；逐级 O_NOFOLLOW、普通文件、单硬链接、体积/魔数、名称和 inode 校验；拒绝 profile/密钥/系统路径，素材上传本身也需用户授权 | 设计；R4、R8、T8/T9 |
| 下载路径 | 只下载当前授权任务产物；HTTPS 主机/每次重定向校验，拒绝回环/私网及任意外部 URL；不跨主机转发 Authorization。限大小/超时，私有暂存/O_EXCL 发布、SHA-256 复验。归档防 Zip Slip/链接及解压炸弹；媒体仍可能含敏感内容 | 设计；R4、R6、T10 |
| 子进程 | 参数数组、shell=False、固定可执行路径、最小环境、私有临时目录和本实例 Popen/session；正常退出、超时、取消分级清理，不按落盘 PID/pkill 扫描用户浏览器。Chrome helper 脱离 session、SIGKILL/崩溃遗留须实测，进程组不是 Job Objects | 静态差距 + 设计；R3、R4、R8、T7 |
| 日志/MCP | 只返回固定错误码、状态、允许的进度及产物句柄；不记录 Authorization/Cookie/JWT/STS、签名 URL、响应体或浏览器 trace/HAR。原始应用日志禁入 MCP；合成秘密覆盖嵌套 JSON、异常、stdout/stderr、POSIX 路径。不能保证原始第三方日志天然安全 | 静态差距；R1、R3、R4、R7、T7 |
| 出站网络/供应链 | Tripo 云 API 本来需要网络，只能承诺应用层目标限制；VRoid 现有 Windows guard 的出站限制不能被删除。Mac 无等价隔离证据时 VRoid 自动化保持禁用；未来另立隔离评审，不在本阶段修改防火墙/系统扩展 | 静态确认 + 门槛；R1、R3、R8 |
| 付费/访问控制 | Studio 下载也可能有订阅要求。遇登录、验证码、权限不足、内容拒绝、付费/额度或限流错误时停止或按正式规则等待；不提取账号信息、不换身份绕过、不自动改套餐 | 官方资料 + 设计；T5、T2 |

独立 profile、Keychain、路径校验、输出脱敏、Tailscale 分别解决不同问题，都不构成可约束任意插件代码的 OS 沙箱。插件、C# 和浏览器进程仍在用户权限下；不能用“只监听回环”或“已脱敏”取代供应链/权限审查。[R3、R8、S1–S4；工程判断]

## 8. 技术方案总表与实施顺序

| 项目 | VRoid | Tripo |
| --- | --- | --- |
| macOS 原生支持 | 官方 macOS 产品/M1 支持；**本机未实测**，不能填已通过 | 官方 Web 应用；**Mac 浏览器未实测**，不主张存在已核实的本地原生 app |
| 核心 API | 未发现正式 Studio 编辑 API；社区内部 API/BepInEx；VRM 导出是本仓库扩展 | 正式 OpenAPI 核心链路；官方 MCP 为 Blender alpha；现有 Studio MCP 是社区产品 |
| 代码复用率 | 桥逻辑估算 40–60%，详见 4.1 | 官方 API 桥逻辑 30–50%；社区路线 50–70%，详见 4.1 |
| 新增模块 | Mac app/版本/架构探针、arm64 构建验收、受管会话、插件身份、产物与隔离适配 | OpenAPI backend、workflow 映射、Keychain、安全 I/O；可选独立 browser-session |
| 安全风险 | 非官方 native 注入、版本 ABI/异常崩溃、内部文件访问、原日志、缺乏等价出站隔离 | 授权 key/会话、CDP、素材外传、签名 URL、重复扣费、网页契约漂移 |
| 开发难度 | 高且有前置阻塞；依赖栈不可复现时工期不可估 | 官方 API 核心链路中等；Studio 全功能兼容高，不能按“改路径”估算 |
| 推荐实施顺序 | 第二；先解决依赖/隔离证据，再单独批准验证 | 第一；先官方 API 合同与离线适配，确认计费和授权后做最小真实链路 |

本表为第 2–7 节证据的工程判断；没有以降低安全标准换取可用性。

### 分阶段建议与放行标准

1. **6A（本次）文档交付**：固定代码/证据、列风险与未验证事项；仅三个文档改动，检查、commit、push、Draft PR。ChatGPT 审查后由人决定下一阶段。
2. **6B（建议）Tripo 官方 API 契约/离线实现**：先确认用户愿意使用 API 授权和计费；冻结 backend、schema、操作/模型映射及地域范围。只用合成凭据/Mock 测试授权拒绝、上传限额、下载重定向、幂等和超时未知；保持现有 Windows Studio 行为。不能读取真实登录态作为 fixture。[T2、T8–T12、R4、R6]
3. **6C（建议）Tripo 人工授权验收**：专用 API key 由用户在本地安全配置，明确费用上限和可上传素材；验证一张图片→任务→查询→本地归档→VPS 下载、摘要、过期/换 key/撤销、重启恢复。若用户必须使用 Studio 会员，应先单独批准专用 profile 路线及浏览器会话访问，不自动回退。[S1–S3、T10]
4. **6D（建议）VRoid 依赖可复现性与安全审查**：取得定制栈源码/版本/校验和/许可证；验证 app 的具体版本、架构、签名与依赖兼容。先解决出站隔离、受管实例及异常边界；不修改原安装、不关闭系统保护。条件不足则继续 unsupported。[V5、V6、R1–R3]
5. **6E（建议）VRoid 最小功能与扩展验收**：批准的专用测试环境/素材副本中逐步验证 create/open/save、单个脸/发/服参数、预览、两种 VRM 导出和回到编辑；验证错误/超时/崩溃不会重投或破坏已保存文件。通过后才扩大功能、加入 VPS 契约验收。[R1、R2、V4]

以上编号是建议阶段，不表示已经获准实现、安装或操作真实账号/项目。无需为了本次文档交付请求额外权限。

## 9. 后续需要用户提供的人工验证条件

| 应用/主题 | 人工准备的条件 | 需要留下的非敏感证据 |
| --- | --- | --- |
| VRoid 产品 | 指定已有安装包版本/来源、Mac 型号类别/芯片架构/macOS 版本；专用测试账户或可恢复测试环境；无真实项目的合成模型 | app metadata/Mach-O 架构、运行版本、图形和文件往返结果；不含用户名/序列号/绝对用户路径 |
| VRoid 插件 | 经批准、可获取且可复现的 arm64 栈；固定源码/构建清单与校验和；独立批准的测试副本及隔离方案 | 插件/interop 版本、loopback 归属、未改系统保护证明、操作通过/失败矩阵及崩溃恢复结果 |
| Tripo API | 用户选择 API 后端/区域；在本机配置新的最小必要授权 key；明确小额预算和可上传素材权利 | 凭据只记不含秘密的引用；记录请求类别、任务状态、额度变化、文件大小/摘要，不提交 key/签名 URL |
| Tripo Studio（若选择） | 同意使用新专用 profile；由用户完成正常登录及所有验证码/MFA；明确会员/导出权益 | 只记录 authenticated 布尔、浏览器版本、测试结果；不导出 Cookie/密码/JWT，不录制登录页 |
| TCC/生命周期 | 人工确认实际所需 Files and Folders 权限；可撤销授权；允许测试受管进程退出/崩溃 | 权限拒绝/撤销、锁冲突、外部浏览器不受影响、没有后台遗留或明确恢复步骤 |
| MCP | 独立测试桥/VPS 会话和固定 schema，批准远端可见产物 | 参数/错误兼容、敏感数据合成注入检查、任务未知不重投、Windows 回归 |

不要求用户在聊天或 PR 提供任何密码、Cookie、Token 或 OAuth 数据。安装插件、真实账号验证和模型写入属于后续独立批准范围。[本次用户约束；第 7 节方案]

## 10. 证据索引

外部资料均于 2026-11-05 查阅。链接是公开文档/源码，不含签名下载地址或账号数据；厂商动态页面未来可能变化。社区 GitHub 引用固定 revision，避免把未来 HEAD 当成本次证据。官方支持描述不等于实机验收。

### 仓库证据（固定基线 fe0a0157c1e0d686fe06fe9eed6846abc715cd06）

| ID | 来源及定位 |
| --- | --- |
| R1 | [native_vroid.py](../services/local-gpu-bridge/native_vroid.py)：FIELDS、rpc/settings_ready/ensure_started、validate/call/filtered；[docs/vroid.md](vroid.md)：Windows 已有部署和操作说明 |
| R2 | [install-vroid.py](../scripts/install-vroid.py)、[install-vroid-export.py](../scripts/install-vroid-export.py)：win-x64 BE788/源码 revision、安装和编译；[ExportHandlers.cs](../services/local-gpu-bridge/vroid-export/ExportHandlers.cs)、[native_vrm.py](../services/local-gpu-bridge/native_vrm.py)：内部导出/元数据 |
| R3 | [native_control.py](../services/local-gpu-bridge/native_control.py)：inside/private_result/project；[native_process.py](../services/local-gpu-bridge/native_process.py)：EgressGuard、ManagedProcess、user32、日志 |
| R4 | [native_tripo.py](../services/local-gpu-bridge/native_tripo.py)：REGIONS/HIDDEN、inside/to_local、environment、tools/call/upload/file、profile_dir/open_login/stop；[docs/tripo.md](tripo.md)：Studio/地区/登录/会员接口约定 |
| R5 | [macos_service.py](../services/local-gpu-bridge/macos_service.py)：load_config；[macos_native.py](../services/local-gpu-bridge/bridge_platform/macos_native.py)：引擎路由；[bridge.py](../services/local-gpu-bridge/bridge.py)：受限 native 路由 |
| R6 | [plugin/client.py](../services/local-gpu-bridge/plugin/client.py)：VROID_TOOLS、TRIPO_FILE_TOOLS、tripo_tools/upload/download、serve；Tripo 动态 schema 未固化 |
| R7 | [remote_privacy.py](../services/local-gpu-bridge/remote_privacy.py)：PRIVATE/remote_text/remote_json；[bridge.py](../services/local-gpu-bridge/bridge.py)：send_json 的 resources 过滤 |
| R8 | [macos.py](../services/local-gpu-bridge/bridge_platform/macos.py)、[macos_godot.py](../services/local-gpu-bridge/bridge_platform/macos_godot.py)、[macos_blender.py](../services/local-gpu-bridge/bridge_platform/macos_blender.py)、[macos_unity.py](../services/local-gpu-bridge/bridge_platform/macos_unity.py) 及 [实施文档](macos-port-implementation.md)：已存在 Mac 运行/文件/授权机制及非沙箱边界 |

相对链接用于在此分支阅读；历史定位可使用 [固定基线仓库树](https://github.com/facming/orca-tools/tree/fe0a0157c1e0d686fe06fe9eed6846abc715cd06)。

### VRoid / BepInEx 外部资料

| ID | 证据来源 | 性质 |
| --- | --- | --- |
| V1 | [VRoid Studio 产品与下载](https://vroid.com/en/studio) | pixiv 官方；macOS 下载、参数编辑 |
| V2 | [Operating environment requirements](https://vroid.pixiv.help/hc/en-us/articles/900006049066-Operating-environment-requirements) | pixiv 官方；Mac/M1，优先于产品页简略 Intel 表格 |
| V3 | [VRoid Hub API / SDK 范围](https://developer.vroid.com/en/api/) | pixiv 官方；Hub 加载/展示，非 Studio 编辑 |
| V4 | [vroid-studio-mcp README @ ab9ad4f](https://github.com/brainrom/vroid-studio-mcp/blob/ab9ad4f90d6ba6486a234a975c86e3298ccfd75e/README.md) | 社区；版本、内部 API、未发布 Mac 支持 |
| V5 | [macOS 状态 @ ab9ad4f](https://github.com/brainrom/vroid-studio-mcp/blob/ab9ad4f90d6ba6486a234a975c86e3298ccfd75e/docs/macos.md) | 社区验证声明，非本项目实测；所提外部依赖未取得 |
| V6 | [BepInEx IL2CPP macOS 安装](https://docs.bepinex.dev/master/articles/user_guide/installation/unity_il2cpp.html)、[Mono macOS 安装](https://docs.bepinex.dev/master/articles/user_guide/installation/unity_mono.html)、[构建列表](https://builds.bepinex.dev/projects/bepinex_be) | BepInEx 上游，列 macos-x64；不代表 pixiv 支持 |
| V7 | [保存模型](https://vroid.pixiv.help/hc/en-us/articles/360012333774-Saving-a-model-you-created)、[模型保存位置](https://vroid.pixiv.help/hc/en-us/articles/900007039003-Where-are-3D-models-and-custom-items-saved) | pixiv 官方；前者含旧版本说明，不用于推断当前路径 |
| V8 | [Camera / Exporter](https://vroid.pixiv.help/hc/en-us/articles/360012474773-Camera-Exporter) | pixiv 官方 UI 功能，不是自动化 API |
| V9 | [VRM export feature](https://vroid.pixiv.help/hc/en-us/articles/15760756822297-I-want-to-learn-more-about-the-VRM-export-feature) | pixiv 官方；格式/导出，导入说法须结合 V11 |
| V10 | [UniVRM](https://vrm.dev/en/univrm/) | VRM Consortium；Unity VRM 包，不是 Studio 插件入口 |
| V11 | [Dress-up 导入基模](https://vroid.pixiv.help/hc/en-us/articles/38722733769241-Getting-Started-with-the-Dress-up-Feature-for-those-who-want-to-dress-up-their-characters) | pixiv 官方；VRM/.vroid/XAvatar 的换装范围 |

### Tripo 外部资料

| ID | 证据来源 | 性质 |
| --- | --- | --- |
| T1 | [OpenAPI Introduction](https://docs.tripo3d.ai/get-started/introduction.html) | 官方；Web Studio 与 API 产品入口 |
| T2 | [Authentication / Quick start](https://docs.tripo3d.ai/get-started/quick-start.html) | 官方；Bearer、任务提交/查询 |
| T3 | [官方 MCP 仓库](https://github.com/Tripo-Developer/tripo-mcp) | Tripo-Developer，官方身份说明 |
| T4 | [官方 MCP README @ 7e8ec9e](https://github.com/Tripo-Developer/tripo-mcp/blob/7e8ec9eeb751e38f7bd1955f9df961c2676c5e38/README.md) | alpha / Blender Addon 范围，未安装运行 |
| T5 | [Studio 下载问题](https://www.tripo3d.ai/help/features/why-cant-i-download-my-model) | 官方；Chrome/Edge、成功状态与订阅限制 |
| T6 | [Studio 社区 MCP README @ 45df024](https://github.com/Ador-able/tripo-studio-mcp/blob/45df024ff8196ddd3f9226a082b87ce5b4c459c2/README.md)、[SECURITY](https://github.com/Ador-able/tripo-studio-mcp/blob/45df024ff8196ddd3f9226a082b87ce5b4c459c2/SECURITY.md) | 社区作者自己的产品/安全说明，非第三方安全认证 |
| T7 | [browser-launcher.ts](https://github.com/Ador-able/tripo-studio-mcp/blob/45df024ff8196ddd3f9226a082b87ce5b4c459c2/src/auth/browser-launcher.ts)、[credential-store.ts](https://github.com/Ador-able/tripo-studio-mcp/blob/45df024ff8196ddd3f9226a082b87ce5b4c459c2/src/auth/credential-store.ts)、[cdp-session-provider.ts](https://github.com/Ador-able/tripo-studio-mcp/blob/45df024ff8196ddd3f9226a082b87ce5b4c459c2/src/auth/cdp-session-provider.ts) | 社区源码静态核实；darwin/profile/非 Win 凭据/CDP |
| T8 | [直接上传](https://docs.tripo3d.ai/file-upload/quick-upload-directly.html) | 官方；图片类型、20 MB、image_token |
| T9 | [STS 上传](https://docs.tripo3d.ai/file-upload/upload-in-sts.html)、[Image to model H2](https://docs.tripo3d.ai/model-generation/image-to-model-v2-0-v2-5.html) | 官方；临时上传、格式、file_token/object、版本 |
| T10 | [查询任务和结果](https://docs.tripo3d.ai/task-query/get-your-task-result.html) | 官方；同 key 查询、状态、五分钟 URL |
| T11 | [模型转换](https://docs.tripo3d.ai/export/conversion.html) | 官方；格式/绑定/版本限制 |
| T12 | [schemas.ts @ 45df024](https://github.com/Ador-able/tripo-studio-mcp/blob/45df024ff8196ddd3f9226a082b87ce5b4c459c2/src/public-api/schemas.ts) | 社区源码；WorkflowStep/StartWorkflow/控制参数 |

### macOS / 浏览器官方资料

| ID | 证据来源 | 用途 |
| --- | --- | --- |
| S1 | [Apple Keychain Services](https://developer.apple.com/documentation/security/keychain-services) | 加密小型秘密及访问控制；读取了官方 Markdown 正文，未访问本机 Keychain |
| S2 | [Chromium User Data Directory](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md) | Mac 路径、独立 user-data-dir |
| S3 | [Chrome remote debugging 安全变更](https://developer.chrome.com/blog/remote-debugging-port) | Chrome 136 起默认 profile 调试限制及专用目录建议 |
| S4 | [Apple：控制应用文件访问](https://support.apple.com/guide/security/controlling-app-access-to-files-secddd1d86a6/web) | TCC、Files and Folders、Accessibility/Automation；搜索工具遇 429 后普通公开 HTTPS 读取正文成功 |
| S5 | [Apple：屏幕与系统音频录制权限](https://support.apple.com/guide/mac-help/control-access-to-screen-and-system-audio-recording-mchld6aa7d23/mac) | 屏幕录制权限说明；搜索工具遇 429 后普通公开 HTTPS 读取正文成功，实机权限行为仍未验证 |

## 11. 本阶段交付检查

仅新增本报告，更新 `docs/macos-port-implementation.md` 和 `CHANGELOG.md`。检查记录：

- `git diff --check`：通过；提交前同时检查暂存差异。
- `python3 scripts/check.py`：通过，182 个源码/文档文件；未发现私有运行配置或脚本识别的凭据。
- 三份交付文档的额外敏感信息扫描：未匹配凭据值、JWT、签名 URL、私钥或绝对用户路径；扫描不是对未知格式秘密的形式化证明，也未读取真实秘密验证。
- 报告中的相对文件链接：全部可解析；改动范围仅三个 Markdown 文档。不新增功能代码或运行真实应用测试。

提交消息：`docs(macos): audit VRoid and Tripo compatibility`；交付研究分支及 Draft PR，不合并。软件实测的未完成项见第 9 节，不因文档检查通过而标记应用可用。

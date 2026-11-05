# 阶段 7B-5：Orca macOS 宿主集成（Draft，生产仍阻塞）

基线 PR #14 / `feature/macos-security-finalization`，HEAD
`b3f8c4f0d98b64bab573c4bd95d01902811f87e6`。
开发分支 `feature/macos-orca-host-integration`。调查日期 2026-11-05。

> 阶段 7B-6 的版本溯源、显式 FD 3 修正和签名架构以
> [Toolkit Host API 设计](toolkit-host-api-design.md) 为准。当前仍未启用可信 Host 路由。

## 调查结论

本仓库包含 Toolkit，**不包含 Orca Host 源码或签名发布流水线**。本机只读检查
Orca **1.4.249**，没有修改应用包、安装生产服务、迁移真实配置或连接 VPS。

| 问题 | 证据与结论 |
| --- | --- |
| Toolkit 在哪个进程 | `Contents/Resources/app.asar` 的 `out/main/index.js` 使用 `child_process.fork` 启动解包的 `out/main/plugin-host-entry.js`。Toolkit 是独立 Node worker，不是 Electron 主进程或 renderer。worker 的模块钩子加载 `main.mjs`，调用默认 activate。 |
| RUN_AS_NODE 能否关闭 | Host 的 worker 环境构造器强制 `ELECTRON_RUN_AS_NODE=1`，manifest 没有覆盖项。CLI 和其他 Host 子进程也依赖它；插件不能直接关闭。已安装 Framework 的 fuse 检查拒绝，Info.plist 没有 `OrcaToolkitConfigClient=1`。不能仅增加方法就认定整个应用可以 enrollment。 |
| 是否有主进程通道 | 有。`api.host.call` 经 fork IPC 的 req/res 到主进程；`api.host.has` 查 SDK 的方法集合。主进程根据自身保存的 pluginKey、批准记录、surface、capabilities、参数 schema 和 lifecycle scope 路由。 |
| 有无可信 Bridge 接口 | 现有 SDK 方法包含通知、storage/settings/secrets、workspace、terminal 等，没有 Toolkit 配置/native Bridge 方法。通用 terminal/命令执行不能替代安全接口。本次没有使用它们绕过校验。 |
| 插件是否有独立可信身份 | Host 有逻辑 pluginKey `local-tools.toolkit`；磁盘 JS、声明的 ID、用户批准和 shared Electron 签名不能构成独立代码签名身份。当前 generic worker 不可 enrollment。 |
| 命令能否完成维护 | `api.commands.register` 及 `command.invoke` 支持结构化参数/结果。现在注册的 `toolkit.configuration` 统一支持 read/patch/status/preview/confirm/backup/restore。只有实现并封装可信 Host 后才能在实际用户配置服务上成功。旧 Host 保留页面和命令，显示 host-required。 |

复核了随包 `plugin-host-entry.js` 与其 `plugin-host-api-*`、
`plugin-module-candidate-paths-*` chunk，以及 ASAR 主进程方法分发代码。
`main.mjs` → `server.cjs` → Controller 的启动失败已有捕获；7B-5 补齐的是
可信通道、异步读取/刷新和结构化维护操作，而不是把失败吞掉当作成功。

## 本仓库已实现

- `host-client.cjs` 只调用版本化 `toolkit.configuration.v1`。SDK 缺方法时明确
  `host-required`；不执行 Bridge、不写回旧 JSON、不读取旧配置作为 fallback。
- 每个 runtime 注入独立配置 client，不全局替换 transport。settings、下载偏好、
  创建/释放 target lease 都使用它；异步释放未确认前保留下载私钥。
- `host-handler.cjs` 是供 **Orca 签名构建纳入主进程** 的集成组件，当前不会在
  worker 中注册或执行。必须传入 Host-owned session authorizer，没有默认放行。
  校验封闭操作 schema 后，以固定路径、空参数和固定环境异步启动 Bridge；
  限制请求/响应大小及超时，没有 path/env/script/PID/requirement 执行入口。
  7B-6 改为 main 内 native socketpair 和显式 fd 3；stdin 为 ignore，原始 socket 保持至响应完成；
  原生测试已覆盖异步 Node 的真实签名拒绝，避免把断连误认为身份认证。
- SQLite 服务、Bridge SCM_RIGHTS、原始 socket 审计令牌、双重签名/UID 检查和
  生产 enrollment 条件均未放宽。真正创建 Bridge 输入 socket 的必须是获准 Host。
- Settings 每个窗口保留自己的 store_id/epoch/revision，仅提交变更字段：独立字段
  合并、重叠变更冲突、恢复后旧 epoch 拒绝。刷新服务状态不会偷偷替换窗口编辑基线；
  **Reload settings** 才载入新的基线。保存结果不明时，相同提交重用 request_id。
- Mac 维护面板支持明确选择两个 JSON 文件或逐项默认值、预览原始精度内容/哈希、
  确认该快照、创建备份、输入备份 ID 后确认恢复、显示结果、重试同一请求。
  文件在浏览器读取，以 base64 传入；Host 不接受任意本地路径。每文件 UI 限 650 KB，
  协议请求上限 1.9 MB。预览不导入；确认使用服务保存的快照，不重读可能变化的源文件。
- 确认迁移/修改/恢复后重读 Controller 状态；离线时禁用配置依赖操作，恢复后可手动重载。
  Windows 继续同步 JSON 配置；原 UI 导航、Accounts/GPU/MCP 名称和远程协议保留。

## Orca Host 必须完成的改动

1. 在 Host SDK 方法表、参数/响应 schema、主进程路由增加下面的 v1 方法。
   只在实现可用时由 `host.has` 返回 true。设置专用的内建 Toolkit 授权域，
   不允许普通插件通过自行声明 capability、publisher 或 ID 获取权限。
   当前 manifest 只保留已支持的通知 capability，避免伪造一个现有 SDK 不认识的权限。
2. 把 `host-handler.cjs`、协议验证代码及可信 Toolkit payload 纳入签名资源封印和
   root 保护的发布包。Host-owned authorizer 必须从实际 IPC session 表获取身份，
   绑定内建 Toolkit、确切受审内容、有效 capability、生命周期 generation、命令来源；
   禁止开发插件、可变内容、其他插件/renderer 或通用 `commands.run` 转借权限。
   撤销/停用/重载应撤销旧 session；授权检查在调用 Bridge 前完成。
3. 用封印的内建模块或受审隔离执行机制替代依赖 RUN_AS_NODE 的可信调用路径，
   同时迁移 CLI/其他依赖，不能对整个 Electron 打开任意脚本入口以兼容旧 worker。
   明确关闭 RunAsNode、NodeOptions、CLI inspect，打开 ASAR 完整性和 OnlyLoadAppFromAsar；
   保持 hardened runtime，拒绝不安全 entitlements/外部模块注入。
4. 主进程通过 native binding 创建原始 Unix socketpair，并把 bridgeFd 映射到 Bridge fd 3，不能转交 worker 的原始通道（它仍是不可信
   原始调用者），也不能传伪造 PID/UID/token。主进程必须先完成上述授权，否则成为
   confused deputy。服务独立验证 Bridge 和这个主进程的内核 audit token 与确切 CDHash。
5. 添加签名 opt-in `OrcaToolkitConfigClient=1`，完成 Developer ID、资源/目录保护、
   所有架构 fuses、重签/升级重新 enrollment。用最终包完成真实主进程、独立服务 UID、
   恶意插件、伪造 session、重签、RUN_AS_NODE、debug/injection、升级和撤销测试。

本仓库不能修改 Host 的闭源构建或补造其签名；以上是发布前置条件，不是已交付功能。
`authorizeSession` 回调只是集成边界，不是认证的替代品。UI token 只是本地 HTTP 会话
控制，不会被 SQLite 服务当作签名身份。现有 CLI `migrate.cjs` 是旧低层入口，普通
Node/RUN_AS_NODE 执行它仍会被服务拒绝，不能用它绕开本限制。

## API 与命令

Host 方法：`api.host.call('toolkit.configuration.v1', {version: 1, request})`。
Host 处理器：`createHostHandler(authorizeSession)(envelope, hostOwnedSession)`。
返回 `{ok:true,result}` 或 `{ok:false,error}`；Host IPC 断开/超时可能有未知提交结果，
不得自动更换 request_id 重放 mutation。

Toolkit 命令参数用 `operation`；Host/SQLite 请求用 `op`，`confirm` 映射 `import`。
命令不会接受旧式任意路径 `args`。所有 mutation 可提供稳定 request_id；省略时命令
生成 UUID，需跨断线重试的调用方应自己生成并保留它。HTTP `POST /api/configuration`
采用相同命令参数，保留 Host/Origin/token/JSON/大小校验。

| operation | 其他字段 | 返回 |
| --- | --- | --- |
| status（默认） | 无 | state、store_id、epoch、target_leases |
| read | namespace: settings 或 downloads-preferences | raw_json、revision、store_id、epoch |
| patch | namespace、store_id、epoch、revision、patch、request_id 可选 | 新版本与 raw_json |
| preview | sources 两个命名空间各为 base64 原始字节或 null；request_id 可选 | preview_id、hashes、documents |
| confirm | preview_id、hashes、request_id 可选 | 导入状态 |
| backup | 无 | backup_id，备份仍留在服务私有目录 |
| restore | backup_id、request_id 可选 | 新 epoch 和状态 |
| lease-acquire / lease-release | task_id；acquire 另需 target；request_id 可选 | 内部下载租约结果 |

例如从 `read` 返回值保留版本后调用：

```js
await command({operation: 'patch', namespace: 'settings',
  store_id: snapshot.store_id, epoch: snapshot.epoch, revision: snapshot.revision,
  request_id: crypto.randomUUID(),
  patch: [{op: 'set', path: ['gpu', 'enabled'], value: false}]});
```

不要将 raw_json 解析后整体回写，未知大整数和小数应留在服务中。UI 仅提交已知字段
patch。恢复不能覆盖活动下载 target leases，服务会拒绝；本地仍保留旧任务/待清理任务的目标变更保护。备份 ID 不是文件路径。
结果面板保留本页最近一次结果；持久状态可通过 status/read 查看，幂等 mutation
使用原 request_id 重取结果。没有新增跨会话审计记录查询 API。

## 自动化验收与边界

- `node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs`：
  **101 项：100 通过、1 项原有 Windows-only 跳过**。
- `python3 -m unittest discover -s plugins/toolkit/config-service -p 'test_*.py'`：
  **17/17**。新增链路调用生产 `main.mjs` 注册的命令、Host adapter/handler、HTTP 路由，
  使用测试专用 ad-hoc C caller 创建原始 socket → 生产 Bridge relay → 真正的
  Security.framework 双身份检查 → SQLite。覆盖预览/错误确认/导入、并发窗口、
  冲突、精度、备份/新 epoch/重试、错误签名调用者、直接 Node、HTTP 越权及服务离线。
  **替身仅代表缺失的 Host 身份和分发，不代表已安装 Orca 的成功端到端验收。**
- `node scripts/test-orca-host-probe.cjs /path/to/Orca.app`：本机 **1.4.249 通过**。
  真实随包 SDK worker、真实 command.invoke、生产 Toolkit main/client/server；
  临时 HOME/profile/插件副本，GPU 替身和随机 HTTP 端口。确认命令可注册、页面可打开、
  缺方法返回 host-required、不调用 Host 配置接口或写 JSON。
  探针 fork 用 JSON serialization 兼容系统 Node 与 Electron 的 V8 版本；现有 Host 用 advanced。
- `node:test` 的维护面板 VM 操作，以及真实 Chrome 临时 SQLite fixture：预览、确认、
  备份、修改配置通过；结果和页面截图检查通过。GPU 未配置的一次 HTTP 400 符合预期，
  这不是远程 GPU/MCP 验收。UI 复测没有接触用户配置。
- Orca 1.4.249 官方离线 `plugin validate`：valid=true、0 errors，6 个命令与既有 capability 均兼容；没有安装/批准插件。
- GPU Python 回归 **301 项：298 通过、3 项原有跳过**；安装器 **8 项：5 通过、3 跳过**。
  `scripts/check.py` 和 `git diff --check` 通过。

没有运行生产 LaunchDaemon 安装、sudo 跨 UID harness、真实迁移、生产 VPS 或原生 Windows。
既有安全测试全部保留；本轮没有重跑未修改的 Docker SSH 角色部署验收。
发布仍受 PR #14 的其余人工验收边界约束，不能据此解除 RELEASE BLOCKED。

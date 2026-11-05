# Toolkit trusted Host API — 阶段 7B-6

> 阶段 7B-7 实现与最新限制见 [7B-7 记录](toolkit-host-7b7.md)。本文保留为 7B-6 审计快照。

审计日期：2026-11-05（Asia/Shanghai）。状态：**开发准备，禁止生产 enrollment**。
本阶段没有安装自定义 Orca、修改官方上游、部署系统服务或迁移真实用户数据。
本文在 Toolkit 与 Orca fork 同步保存；Toolkit 是协议/Bridge 的所有者，Orca 是身份路由和签名包的所有者。

## 1. 版本基线：1.4.249，不能按 main 的版本字符串选择源码

| 项目                  | 实测值                                                                                                   |
| --------------------- | -------------------------------------------------------------------------------------------------------- |
| Mac                   | macOS 15.7.5 / 24G624，arm64；Apple clang 17；Xcode 工具链可用                                           |
| 可用磁盘              | 开始时约 2.4 TiB                                                                                         |
| GitHub                | gh 已登录 facming，repo/workflow scope；fork/clone 成功                                                  |
| 已安装应用            | `/Applications/Orca.app`；Info.plist 与 asar package.json 均为 1.4.249                                   |
| 签名                  | com.stablyai.orca；Lovecast LLC / 6CX3WHS9HZ；hardened runtime；`codesign --verify --deep --strict` 通过 |
| 正式源码基线          | `v1.4.249` → `fff61ecbacae6ff390d57bb9e54cd4d0e4fd3087`                                                  |
| 安装包 build metadata | `orca-local-build.json` 的 commit 为 `0a06fe962178179e9358750de4d8a071027785b9`                          |
| main 审计快照         | `4fee14f89b4ea63db5f43459c4e358e4470894cb`；package.json 为 1.4.214                                      |
| Plugin Host API       | 正式基线 1.3 / pluginApi major 1；main 快照 1.4 / major 1                                                |

**已解释 metadata 与 tag 不同，不能把 metadata commit 当作源码 HEAD。**
[官方 macOS 构建 41935194504](https://github.com/stablyai/orca/actions/runs/41935194504)
的 headSha 是 `0a06fe9`，日志明确显示 checkout `fff61ecb`，并设置
`ORCA_RELEASE_JAVASCRIPT_SOURCE_SHA=fff61ecbacae6ff390d57bb9e54cd4d0e4fd3087`。
`config/electron-builder.config.cjs` 的 afterPack 优先使用 ORCA_BUILD_COMMIT，
否则使用 GITHUB_SHA，而非先读取 checkout HEAD；该 release workflow 没有设置
ORCA_BUILD_COMMIT。因此本机 metadata 记录的是 workflow dispatch ref。
[release workflow](https://github.com/stablyai/orca/blob/fff61ecbacae6ff390d57bb9e54cd4d0e4fd3087/.github/workflows/release-mac-build.yml)
和 [release tag](https://github.com/stablyai/orca/releases/tag/v1.4.249) 相互印证。
这是签名包元数据、发布流程和运行探针的一致性核对，不声称完成整包可重现构建。

比较结果（均为固定 commit 的本地 git tree/diff，不是易变网页）：

- `v1.4.249...main快照` 左右独有 commit 为 **22 / 158**，两条线已经分叉。
  `git diff --shortstat` 为 **5806 files, +52589 / -342969**（含重命名识别）；
  大量删测以及 onboarding、skills、UI、运行时变化，不能整体覆盖。
- metadata commit → tag：378 个文件变化；相关插件运行时代码相同，插件目录的
  5 个差异文件全是测试。该事实也解释了旧探针为何可以运行，但不是选择 metadata 分支的理由。
- main 新增 `appearance:write`、`appearance.read` / `appearance.apply`，涉及
  plugin-host-service-bindings、plugin-host-method-bindings、plugin-service-host-call
  和生成的 orca-host.d.ts / reference；Host API minor 从 3 升到 4。
  新 API 是增量变化，现有入口没有观察到签名不兼容；仍须在升级时运行插件回归。
- 指定的 worker fork/env/protocol、plugin-command-registry、preload plugin-host-api
  在基线与 main 快照间没有生产代码变化；Toolkit 方法在两者中都不存在。
- main 的 package.json 1.4.214 是开发线当前文件内容，正式发布版本在 release 分支提升；
  它既不代表 main 比 1.4.249 老，也不代表已安装 1.4.249 具备 main 的 API 1.4。

本机实际 SDK 探针已确认：Toolkit 六个命令可注册、维护页面可打开、方法缺失时
返回 host-required、不产生配置 JSON/Bridge 回退。该探针使用临时 HOME 和插件副本。

开发依赖已按锁文件安装（未运行全部 postinstall，不构建/启动自定义应用），初始化了
检查需要的 Bun。另将仓库 pin 的 Node **24.22.1** 下载到
`/Users/apple/Library/Caches/orca-toolkit-dev/node-v24.22.1-darwin-arm64`，按
node-runtime-pin.ts 校验 archive 与 executable 两个 SHA-256；使用该目录的 bin/include
进行复测，不修改全局 Node。pnpm 固定 **12.14.0**；`pnpm with 12.14.0 ...` 避免
宿主 pnpm 自动给环境锁文件增加管理器条目；没有提交任何 lockfile 漂移。

## 2. Fork 与分支结构

- [Orca fork](https://github.com/facming/orca)：本地 `/Users/apple/Documents/ChatGPT/orca`。
- origin = `https://github.com/facming/orca.git`。
- upstream fetch = `https://github.com/stablyai/orca.git`，本地 upstream push URL 设为
  `DISABLED`，remote.pushDefault 和 gh 默认仓库均为 facming/orca。
- `main` 保留 fork 时上游快照，不用于第一阶段兼容开发。
- `baseline/installed-1.4.249` 固定在 `fff61ecbacae6ff390d57bb9e54cd4d0e4fd3087`。
- `feature/toolkit-trusted-host` 从该 baseline 创建，Draft PR 指向 fork 的 baseline，
  避免把上游 main 的 158 个独有提交混入评审。
- [Toolkit PR #15](https://github.com/facming/orca-tools/pull/15) 继续使用
  `feature/macos-orca-host-integration`，base 为 PR #14 的
  `feature/macos-security-finalization`，保持 Draft。

## 3. 关键源码审计

以下路径均相对 Orca 基线；对应源码可从固定
[源码树](https://github.com/stablyai/orca/tree/fff61ecbacae6ff390d57bb9e54cd4d0e4fd3087) 查看。

| 源码                                                                      | 当前行为与 Toolkit 修改位置                                                                                                                                                                                                                 |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| src/main/plugins/workers/plugin-host-process.ts                           | fork 解包 entry；没有显式 execPath，继承 process.execPath；execArgv=[]，advanced serialization，stdio ignore/pipe/pipe/ipc。闭包 executeHostCall 绑定 worker，req/res 按 id 返回。私有路由必须绑定真实 child/connection，而非请求中的名字。 |
| workers/plugin-worker-env.ts                                              | allowlist 环境，强制 ELECTRON_RUN_AS_NODE=1。不能简单关闭应用 fuse，否则其他插件和 Node-mode 子系统失效。                                                                                                                                   |
| workers/plugin-worker-spawn-spec.ts、startup/controller/lifecycle         | root、manifest revision、reviewedContentHash/devRevision 决定 worker 复用；新增受信实例 generation 与撤销必须跟随生命周期。开发路径不得创建受信实例。                                                                                       |
| workers/plugin-worker-host-calls.ts、plugin-host-runtime.ts               | pending host calls、AbortController、cancel/exit 响应丢弃；主进程新增路由必须捕获失败并完成 promise，不能阻塞 IPC 或遗失 pending。                                                                                                          |
| src/main/plugins/plugin-service-host-call.ts                              | findApprovedPlugin → manifest capabilities → services → live scope 检查；只是普通插件准入，不是内建 Toolkit 证明。                                                                                                                          |
| workers/plugin-host-call-adapter.ts、plugin-host-methods.ts               | qualified key、严格 request/schema、capability/surface、mutation audit、effect 前 scope.assertActive；复用错误/审计风格，不把 private authority 掺入 public capability。                                                                    |
| src/main/plugins/plugin-command-registry.ts                               | 从 approved manifest 注册 command、alias/replaces 与键位；普通批准不能赋予 Toolkit 身份，也不能让同名命令覆盖受信命令。                                                                                                                     |
| src/shared/plugins/manifest 与 approval                                   | manifest/pluginApi 校验；consent fingerprint 覆盖权限和执行模型、worker 内容；用户批准 trusted-node-worker 仍不等于内建封装。                                                                                                               |
| src/shared/plugins/workers/                                               | public API schema、major/minor、surface、协议、SDK has/call；has 当前查静态 WORKER_METHOD_NAMES，不能仅在 main 加方法便期待 Toolkit 能发现。                                                                                                |
| src/preload/api/plugin-host-api.ts、src/main/ipc/plugins.ts               | preload 是管理/面板接口；普通 plugins:invokeCommand 接收 pluginKey/commandId，不能成为受信 Toolkit 的任意参数入口。                                                                                                                         |
| resources/plugins/types/orca-host.d.ts、docs/reference/plugin-host-api.md | 从 public schema 生成。专用方法不加入普通插件可申请的 capability；后续单独生成内建 SDK 类型/文档。                                                                                                                                          |

**另一个必须关闭的代理入口：**Toolkit 当前 `server.cjs` 把 token 放进 loopback
HTML；Origin/header 防护用于浏览器请求，无法区分同 UID 的普通 Node 插件。
恶意本地进程可以读取页面并构造相同 Origin/header。因此不能把现有
`/api/configuration` 或通用 command.invoke 的请求直接提升为受信配置操作。
本阶段没有启用真实 Host 路由，现有安装仍 fail closed；后续需迁到 Host 拥有的
面板/命令上下文，不能靠给当前 localhost UI 再添加一个可读 token 解决。

## 4. 架构选择

| 方案                      | 改动与维护                                                                         | 安全边界                                                                             | 决定                                                                 |
| ------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| A 主进程内建整个 Toolkit  | 需移动 HTTP、下载和工具执行等大量代码，升级耦合大                                  | 可保持 authority 本地，但扩大主进程攻击面                                            | 只纳入小型配置 authority，不搬整个 Toolkit                           |
| B 主进程专用受信 IPC 路由 | 在 worker 分发前加入独立路由，保留 public registry；小型 native binding + 封装策略 | 连接身份、封印资源、命令来源、签名 runtime 四层共同约束                              | **首选**，分阶段实现                                                 |
| C 独立封装 Toolkit Host   | 独立生命周期、部署、签名及升级，更多组件                                           | native helper 的独立签名更清楚；但如果接收通用 Electron 发来的任意请求，仍是代理漏洞 | B 的签名边界无法合理拆分时再选；不能把 interpreter 换名字当安全 Host |

选择 B 不等于“现在把同一 Electron 签名加入 allowlist”。主进程须先满足第 7 节
的独立可验证执行边界；否则维持 host-required。整个插件系统不关闭隔离，普通
worker 继续 RUN_AS_NODE，直到独立运行时的完整兼容替代通过验收。

## 5. toolkit.configuration.v1 合约与专用主进程鉴权

调用形状与 PR #15 保持兼容：

```js
await api.host.call('toolkit.configuration.v1', {
  version: 1,
  request: { op: 'status' }
})
// { ok: true, result: ... } 或 { ok: false, error: ... }
```

| 用户操作      | wire op / 参数                                                  | 约束                                                                     |
| ------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| read          | read / namespace                                                | settings 或 downloads-preferences；保留 raw_json 精度                    |
| patch         | patch / namespace, store_id, epoch, revision, patch, request_id | 字段级 set/delete；拒绝原型字段；版本冲突不覆盖                          |
| status        | status                                                          | 显示 migration-pending/ready 等服务状态                                  |
| preview       | preview / 两份 sources, request_id                              | base64 原始字节或显式 null defaults；不接受路径                          |
| confirm       | import / preview_id, hashes, request_id                         | public command 的 confirm 映射既有 import；只提交已预览 hash；无隐式迁移 |
| backup        | backup                                                          | 返回服务生成的 backup_id；不接受输出路径；重试可能新建备份               |
| restore       | restore / backup_id, request_id                                 | 恢复后新 epoch；不得覆盖活动目标租约                                     |
| lease-acquire | lease-acquire / task_id, target, request_id                     | 主进程绑定实际下载任务与目标，不信任任意 worker task_id                  |
| lease-release | lease-release / task_id, request_id                             | 只释放该受信任务拥有的租约；确认前不删相关私钥                           |

worker 和 main 都用严格 closed schema；未知操作/字段、路径/脚本/env/PID/签名需求
一律拒绝。frame ≤ 1,900,000 字节、响应 ≤ 2 MiB；并发配额在主进程和服务两侧
分别实施。mutation request_id 跨超时保持不变，恢复后的旧 epoch 必须拒绝。
backup 当前非幂等，无须伪称它与所有 mutation 一样支持 request_id。

主进程 admission 需要满足的条件（**设计要求，当前尚未挂接 live route**）：

1. 封装清单由发布构建生成并随 app 签名，固定 Toolkit 全部可执行资源、入口和依赖 hash；
   从受保护 bundle 装载，拒绝 userData/dev/symlink escape、NODE_PATH、动态外部 JS。
   整包校验不能只校验 manifest，更不能校验后又从可写目录载入。
2. 主进程仅在上述装载成功后，为真实 child/MessagePort 建立私有 connection record：
   builtin 类型、release digest、generation、instance、tenant/profile、revocation state。
   使用主进程对象表/闭包查找；payload 中的 pluginKey/capability/session/PID 均不能建表。
3. 每个调用还有主进程拥有的 origin：受信窗口/面板 senderFrame、document generation、
   受信 command admission 或内建 task engine。普通 plugin commands、renderer 管理 API、
   serve RPC/远程/mobile、HTTP 页面均不能任意创建这个上下文。
   confirm/restore 等操作绑定预览摘要和用户确认；租约绑定真实 task record。
4. 主进程发起操作时创建短期、限定 operation/request digest 的 invocation record；
   worker 只返还执行结果，不可凭常驻 session 代理任意 localhost 请求。
   可复用的读取会话也只交给 Host 自有界面。不得把可重用 bearer token 放入页面/env/磁盘。
5. 执行副作用前重新检查 connection generation、封装摘要、origin、operation scope、
   grant、取消信号。退出/重启、禁用、更新、导航、撤销立即作废；等待队列中的操作不执行。
   服务一旦提交后取消只停止等待，不能声称回滚；查询/同 request_id 重试确定结果。
6. 不修改普通 manifest 的 capability 允许列表。SDK 的 private extension discovery 仅
   在上述实例初始化时注入；普通 worker 的 has 返回 false，伪造 method 仍由 main 拒绝。
   全局静态 has 即使未来表示“支持”，也永远不作为授权依据。
7. main 审计记录 actor/generation、操作、request_id、结果和必要摘要；不记录原始配置、
   私钥/token。服务的 tenant/code requirement 独立验证是第二道门。

待修改位置：新增 `src/main/toolkit/` authority/router；在 worker startup 和
executeHostCall 闭包中注入私有路由与连接记录；在 worker 生命周期撤销；扩展内建
SDK discovery，保持 public SDK 1.3 行为；为 Host 自有面板增加窄 preload 接口并校验
senderFrame；保留 public command registry，不允许其给 Toolkit 路由授权。

现有 command invoke timeout 为 30s；Bridge 30s、adapter 31s，存在外层先超时的情况。
未来专用路由应统一 deadline，例如 service 25s / Bridge 27s / adapter 28s / outer 30s，
预览大请求应异步任务化。本轮不更改 public worker timeout；未知结果必须明确报告。

## 6. 原生通信：原始 socket 必须由 Host 创建

```text
Host main（native N-API socketpair）
  pair[0] ── JSON newline ── pair[1]
  保持存活                 spawn Bridge: stdio[3] = 原始 pair[1]
                                  │ SCM_RIGHTS（原始 fd，不重建 socket）
                                  ▼
                      独立 UID SQLite 服务
                      先验 Bridge audit token，再验原始 socket peer token
                      读取原始 socket → SQLite → Bridge stdout → main Promise
```

- Orca fork 新增 `native/toolkit-host/socketpair.c`：仅在调用进程创建
  AF_UNIX/SOCK_STREAM，两个 FD 设置 FD_CLOEXEC；非 macOS 拒绝。
  未挂接 app bootstrap，未改 fuses/签名或 public API。
- Toolkit `host-handler.cjs` 接收 main 私有注入的 createSocketPair；缺失则失败。
  net.Socket 接管 hostFd；spawn 固定 Bridge、固定环境，stdin 为 ignore，
  原始 bridgeFd 明确映射 fd 3。spawn 后父进程关闭 bridgeFd；失败路径全量关闭。
  Host 不调用 end()，在 response/close/error/timeout 后清理；异步多块响应完整拼接。
- `bridge.c` 只接受 fd 3 的 Unix stream，通过 SCM_RIGHTS 转交；拒绝 pipe/file 和
  意外复用成 Bridge 自己连接的 fd 3。stdin 不参与身份链。支持部分写、EINTR 和有界响应。
- `ipc.py` 严格收取一个 descriptor，拒绝截断/多 FD/非 Unix stream；在整次请求和
  响应生命周期中持有原始 channel，错误路径关闭所有接收 FD。
- `peer_identity.c` 用 LOCAL_PEERTOKEN 获取 audit_token → kSecGuestAttributeAudit →
  SecCodeCopyGuestWithAttributes → SecCodeCheckValidity。没有使用 caller 自报 PID/UID。
  服务分别验证 Bridge 和原始 Host；Bridge 身份本身不授予配置权限。
- Bridge 在生产 main 中仍检查服务 UID 为独立非 root 账户。测试 relay 只替换这一跳的
  同 UID 环境连接，不替换 SCM_RIGHTS、Security.framework 或 SQLite 实现。

fd 3 是本轮明确的 Bridge ABI 变更。部署必须成套更新 Host adapter/native binding、
Bridge 和服务；旧 stdin caller 对新 Bridge 会失败，不能自动回退。当前没有生产安装。

不要依赖 Node/libuv 当前怎样实现 `stdio:'pipe'`：某些版本实际采用 socketpair，
但这不是所要求的显式 Host socket 合约。本轮从该隐式依赖迁移到受控 FD 3。
不要让外部 helper 代 main 创建 pair：那样内核记录的是 helper，而非原始 Host。

## 7. macOS 签名、fuses 与打包

本机 Electron fuse wire v1/9 bytes 为 `101100011`：RunAsNode、NodeOptions、
NodeCliInspect 仍开启，embedded asar integrity 和 only-load-from-asar 未开启。
已安装 entitlements 也允许 DYLD 环境和 unsigned executable memory；只看
hardened runtime/Developer ID 不能放行。PR #14 的 caller_policy 维持拒绝，不能放宽。

[Electron 官方 fuses 文档](https://www.electronjs.org/docs/latest/tutorial/fuses)
确认关闭 RunAsNode 会影响现有 child_process.fork；Utility Process 是候选替代，但仍需
验证独立 helper 身份、协议/serialization、原生模块和生命周期兼容性，不能直接互换。

推荐 B 的发布前置方案：

- **分离主进程 runtime 与通用 worker runtime**：可信主应用的 Electron Framework
  关闭 RunAsNode/NodeOptions/inspect 注入，开启 asar integrity/only-load-asar；
  使用独立封装、独立签名标识/CDHash 的 Node 或第二个 worker runtime 承接插件、CLI、
  daemon 等现有 Node-mode 路径。若保留第二个 Electron 的 RUN_AS_NODE，只在那个不被
  配置服务允许的 runtime 开启。不能让两个可执行文件共享同一 permissive Framework
  然后只改名字；fuse 位作用在 Framework 镜像。
- 移除可信主应用危险 entitlement，保留运行所必需并验证的 allow-jit；关闭外部 loader、
  debug/inspect、任意 app path/脚本入口。所有通用入口的兼容替代尚未实现，因此本轮
  不关闭任何现有 RUN_AS_NODE。若拆分成本不可接受，重新评估 C，不能豁免此门槛。
- 修改 `config/electron-builder.config.cjs` 的资源布局、asarUnpack 和 afterPack，
  将 native binding 按架构构建/签名并纳入资源封印；先 fuses，再签 nested binaries，
  最后封印 outer app。可信配置模块不能落到用户可写/未校验的解包路径。
- 修改 `resources/build/entitlements.mac.plist` 与签名流水线时区分主应用、worker、Bridge，
  不使用统一 entitlement 继承来放宽可信调用者。沿用并扩展 verify-macos-entitlements。
- Fork 使用自己的 Developer ID/team/app identity 与独立更新渠道，不冒用上游发布证书。
  当前 enrollment 的 bundle ID allowlist 只有两个 com.stablyai.* ID，后续需显式支持
  经审查的 fork ID；不能改成任意团队/任意同名应用。签名 Info.plist opt-in 同时受验。
- 服务策略 pin 明确 Developer ID designated requirement + 所有支持架构的确切 CDHash，
  app/resources/root 保护、ACL、resource seal、fuses 全部检查；Bridge 单独 pin。
  同 UID 普通进程、正确签名的任意 Electron worker 都不能满足要求。
- arm64/x64 构建、签名、notarization、staple、最终 DMG/ZIP 验证及新版本 enrollment
  都在隔离测试机完成。更新先撤销会话，再验证新包和策略，失败维持关闭，禁止静默扩大信任。

## 8. Toolkit 接入与验收

Toolkit 继续通过 host-client.cjs 的 versioned api.host.call；只有受信 builtin 装载
获得 private extension。普通安装、Windows JSON 路径和其他插件不自动变成 builtin。
macOS 缺方法/未授权返回 host-required，无 JSON 或直接 Bridge 回退。未来 Host-owned
配置面板替代现有 localhost 配置入口，下载租约由受信 task engine 发起。

本阶段可复现的验证命令（所有数据库、二进制、profiles 均为临时目录）：

```sh
# Toolkit 仓库
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
TOOLKIT_SOCKETPAIR_SOURCE=/absolute/path/to/orca/native/toolkit-host/socketpair.c \
  python3 -m unittest discover -s plugins/toolkit/config-service -p 'test_*.py'
node scripts/test-orca-host-probe.cjs /Applications/Orca.app
python3 scripts/check.py
# Orca fork；原生编译命令见 native/toolkit-host/README.md
pnpm check:changed --base=baseline/installed-1.4.249 --maxWorkers=2
```

已执行：Toolkit Node **101 项：100 pass、1 Windows-only skip**；Python/native
**18/18 pass**；C binding/Bridge/临时 Host 以 `-Wall -Wextra -Werror` 编译；真实 SDK
探针通过。原生测试覆盖正式 relay + SCM_RIGHTS + 两跳 Security.framework + SQLite、
错误签名、无原始 FD、直接 Node、身份字段伪造、迁移/恢复/冲突/离线等。
新增 JS adapter 原始 FD 验证：Node 作为真实 socket creator 先被 native Host policy
拒绝，再仅在临时测试 policy 中 pin 该 Node CDHash，验证成功的异步 response 和 260 KB 跨多块响应完整性；
**该测试例外绝不能进入生产 enrollment**。正向原生 Host 用独立 ad-hoc C fixture。
Orca 最终按实际三个改动文件运行 check:changed，format 和 changed-code quality
两项检查通过；另有 native 编译和上述跨仓库运行验证。未声明完整应用测试通过。
首次检查受 pnpm 自动修改锁文件影响扩大到 39 项，且 Bun 未初始化造成 4 项失败；
初始化 Bun、恢复工具生成的锁文件后，使用固定 pnpm 对最终变更重跑通过。
Python 的 malformed ancillary warning 是故意发送截断 FD 的负向用例。

测试边界：未运行正式签名 Orca + 跨 UID 服务端到端、x64/Rosetta、Windows/Linux
运行时替代、完整 Electron build、notarization。生产可用性未验收，Draft 不解除。
上线前必须补齐以下门槛：

1. 已批准恶意插件伪造 Toolkit key/capability、复制 manifest、替换资源、dev root、
   继承/窃取 session、旧 generation、导航/reload/revoke、伪造 command origin 全拒绝。
2. 同 UID raw HTTP 客户端取得页面 token 后仍不能配置读写/确认/租约；普通 renderer、
   serve/mobile/远程 RPC 不可通过 command registry 借用内建 authority。
3. 正式签名包各架构实测 RUN_AS_NODE/inspect/NODE_OPTIONS/DYLD/custom-app 旁路拒绝；
   普通插件、CLI、daemon、更新全部通过兼容测试后才切换主 runtime。
4. 跨 UID 原生 Host → Bridge → 服务 → SQLite 正反向；错误 code hash/uid、pipe/file/
   TCP/multiple FD、creator 提前退出、fragmented response、超限、断连、超时、并发配额、
   cancel/revoke 前后提交、FD leak 与同 request_id 恢复均须验证。

## 9. 升级和维护

只维护小型 private router、builtin admission、native binding 与打包边界；不要 fork
全部 public plugin API。v1 协议冻结，新增破坏性行为使用 v2，保留旧方法兼容窗口。
主进程内部 authority 与服务 native code requirement 分开版本化。

升级按“读取安装包版本 + 发布 job 实际 checkout/artifact SHA → 固定新 baseline →
比较上述指定路径/API major/minor → 独立升级分支 → 原生/插件/签名回归 → 新 CDHash
审核 enrollment”进行。不按 main 的 package.version 自动合并，也不从 metadata 的
GITHUB_SHA 推断源码 HEAD。后续 fork 打包应明确设置 ORCA_BUILD_COMMIT 为实际 HEAD，
同时记录 artifact source SHA、workflow SHA、Toolkit digest，避免重现这次歧义。

PR #14/#15 仍是分层评审，不合并、不 ready、不修改上游。下一阶段先实现封印实例与
Host-owned invocation context，再完成 runtime 分离及签名包验证，最后才启用路由。

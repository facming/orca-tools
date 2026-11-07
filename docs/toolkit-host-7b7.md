# Trusted Host Authority — 阶段 7B-7

验证日期：2026-11-06（Asia/Shanghai）。**RELEASE BLOCKED**。
本记录覆盖阶段 7B-6 设计中“尚未实现”的部分；没有安装自定义应用、迁移真实配置、连接 VPS、修改 main、合并 PR 或推送官方上游。

## 基线和交付范围

- Orca fork：`feature/toolkit-trusted-host`，基线 `baseline/installed-1.4.249`（`fff61ecbacae6ff390d57bb9e54cd4d0e4fd3087`），本轮起点 `167cc8d847f2ce559a8dd4f772497b93acc8bc71`；Draft [PR #1](https://github.com/facming/orca/pull/1)。
- Toolkit：`feature/macos-orca-host-integration`，base `feature/macos-security-finalization`，本轮起点 `cd99b49fa441c5717396a404cc03c1fa6e880537`；Draft [PR #15](https://github.com/facming/orca-tools/pull/15)。
- 延续方案 B：主进程只内建最小配置实例。普通 Toolkit 插件仍是通用 worker，不获得配置身份；不存在由 pluginKey、manifest capability、command.invoke、localhost token 提升身份的路径。

## 已落地行为

### 配置兼容性

macOS settings 保存只投影并校验已知 UI 字段；未知顶层/嵌套字段不被提交到 patch，也不被删除。读取仍保留快照与服务版本。服务 raw_json 内的大整数、小数不经 JS 重序列化。Windows JSON 校验继续严格。新增从完整 Controller 快照保存、未知字段被 UI 改动、已知字段类型错误的回归。

### 主进程路由和生命周期

Orca `src/main/toolkit/` 实现：

- `toolkit-bundle.ts` 在执行模块前校验闭合资源清单和每个文件 SHA-256，拒绝额外依赖、symlink 和损坏资源；production loader 要求 root 保护的资源/祖先、无 ACL、完整 codesign 验证、所有 Electron Framework 架构的限制性 fuses。临时开发包不能通过 production loader。
- 仅加载固定的 commands、host-handler、errors 和 native socketpair，不加载 Toolkit 通用 main/server、用户插件或外部 JS。实例 UUID、递增 generation、context/grant 表保留在主进程。
- `toolkit-authority.ts` 校验封闭 version=1 envelope，复制已批准请求，限制每 context 4 / 全局 16 个未完成调用；短期 grant 不序列化给 worker/UI。异步批准之后、原生创建之前重新检查活性。撤销取消 Bridge；已经提交的写入不声称回滚，返回结果可能未知。
- `toolkit-panel-context.ts` 绑定实际 WebContents 和最初 mainFrame 对象、精确 file URL；拒绝普通 renderer、子 frame、导航后 frame 和伪造 session。窗口导航、崩溃、关闭、应用退出撤销；重新打开建立新实例/generation，旧调用不复活。
- Help 菜单的 Toolkit configuration 由 main 创建 sandbox + contextIsolation 窗口，禁用 Node、DevTools、弹窗、webview 和网络；只有该窗口的窄 preload 可使用配置 IPC。没有 renderer 可调用的 open/admit IPC。普通 Host method 表和 discovery 没有新增能力。
- `plugin-worker-host-dispatch.ts` 在 public executor 之前拒绝 `toolkit.configuration.v1`；异常 executor 转成有界失败响应，避免未处理 Promise rejection。真实恶意 worker 用伪造 Toolkit key、直接 `process.send` 绕过 SDK 后仍被拒绝，重载后同样拒绝。

### 双仓库接口与原生链

`toolkit.configuration.v1` envelope 保持 `{version:1,request}`；adapter reply 保持 `{ok,result|error}`。Host handler 接收内部 `AbortSignal`，不增加 wire identity 字段。调用链已经实际运行：

```text
main authority / Host-owned context
 → Toolkit commands.validate + host-handler
 → main N-API socketpair
 → Bridge inherited fd 3
 → SCM_RIGHTS 原始 fd
 → Bridge audit token + original Host audit token / Security.framework
 → 配置服务 SQLite → 异步 stdout response
```

Bridge 固定路径与最小环境、FD_CLOEXEC、父进程关闭 bridgeFd、Host channel 持有到完成，均保留。adapter 超时 28 秒（Bridge/service 原有 30 秒）：外层先终止等待时明确未知结果，不能保证服务回滚；相同 request_id 用于确定结果。撤销/超时强制终止 Bridge，关闭 channel；超限、非零退出、spawn 错误、格式错误均拒绝。

### localhost 与 UI

macOS `/api/configuration` 和 `/api/config-service` 即使持有 HTML 中的真实 token、正确 Origin 也返回 403；公开 `toolkit.configuration` 命令拒绝。维护面板改为仅调用 Host 自有 preload，浏览器中显示使用 Host 菜单的说明。通用插件的 Host client 仍 fail closed，不能作为 HTTP 服务的配置代理；其他 localhost 工具功能尚未迁入 Host。

当前 Host UI 允许 status/read/preview；backup 与非下载目标 patch 需要 Host 原生确认。**import/restore、下载目标 patch、全部 lease 操作保持拒绝**，因为 main 尚无可信任务所有权记录，不能绕过旧 Controller 的活动下载保护。这是明确功能缺口，不以现有 localhost/普通 worker 提供授权回退。raw schema/存储的 import/restore 正向测试属于合成 Host 测试，不表示 UI 已放行。

### 运行时分离的实际进展

`native/toolkit-host/stage.mjs` 使用仓库 Node pin 核验 worker 可执行文件 SHA-256，编译 N-API addon，复制固定 Toolkit 配置依赖，生成 Host panel/preload 和闭合 manifest。输出仅为构建暂存目录。`ORCA_TOOLKIT_HOST_STAGE` 只由 electron-builder 在构建时读取并复制到 `Resources/toolkit-host`，运行时没有 env 指定的信任路径。

当固定资源包存在且 seal 校验成功，通用 worker 通过显式 `execPath=.../worker-node` 启动，并移除其 ELECTRON_RUN_AS_NODE 环境。包不存在时维持原兼容路径；损坏包不会静默回退。真实子进程 smoke 验证了独立 Node 路径、IPC/命令、无 RUN_AS_NODE env、private has=false。没有关闭全局 RunAsNode fuse，没有修改签名白名单。

这只迁移了普通插件 worker；CLI、daemon、终端 host 等通用执行入口尚未全部迁移。当前已安装/默认构建的 Electron fuses 不满足 authority loader，仍拒绝；不可把当前签名 Electron 或新的通用 Node 加入配置服务允许名单。

## 本机验证记录

全部使用临时目录、临时数据库和测试二进制；无生产数据。Orca 使用 Node 24.22.1、pnpm 12.14.0，测试设置 ORCA_BACKGROUND_LAUNCH=1。

| 检查                                                        | 结果                                                                                       |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Toolkit Node 全量                                           | 104 tests：103 pass、1 Windows-only skip                                                   |
| Toolkit config-service Python/native                        | 19/19 pass，含真实 dual Security.framework、SCM_RIGHTS、SQLite                             |
| 原生跨仓库 authority harness                                | 12 并发请求、顺序重用、异步回复、撤销、旧 generation、异常退出、28 秒超时、FD 数量恢复通过 |
| 已安装 Orca SDK 隔离 worker probe                           | 普通插件命令拒绝，host-required，无 Bridge/JSON 回退                                       |
| Orca authority/seal、menu、真实 worker supervision 定向测试 | 58 pass、1 platform skip                                                                   |
| Orca 独立 worker Node smoke                                 | 实际 fork/IPC/命令通过；无 private discovery                                               |
| Toolkit scripts/check.py                                    | 通过                                                                                       |
| Orca tc:node                                                | 通过                                                                                       |

最终 `pnpm with 12.14.0 check:changed --base=baseline/installed-1.4.249 --maxWorkers=2`：**22/22 checks passed**，覆盖三个 TypeScript project、format、lint、changed-code quality、import boundaries、相关 unit tests 和全部选中的 ratchet。首次检查发现类型断言/宽泛 object 参数、同步主线程进程调用及导入规范问题；已改用 schema 校验、命名类型和异步 process-host，重跑通过，未放宽门禁。

**身份测试限制：**正向 C Host 使用 ad-hoc fixture；JS authority 正向仅在临时服务策略中 pin 测试 Node 的 CDHash。正式策略没有这个例外。负向同 UID Node/错误 CDHash、直接 Bridge/stdin、无/多/非 socket FD 均拒绝。合成 frame/session 测试不声称覆盖真实签名 Electron UI。所有成功均不能替代正式 Developer ID 包和真实跨 UID 服务验收。

复现核心命令（需固定 Node headers，路径按本机修改）：

```sh
# Orca
pnpm with 12.14.0 exec esbuild src/main/toolkit/toolkit-authority.ts --bundle --platform=node --format=cjs --outfile=/tmp/orca-authority.cjs
node native/toolkit-host/stage.mjs ../orca-tools /tmp/orca-stage/toolkit-host /path/to/pinned/bin/node /path/to/pinned/include/node
ORCA_BACKGROUND_LAUNCH=1 node native/toolkit-host/smoke-worker.mjs /tmp/orca-stage
ORCA_BACKGROUND_LAUNCH=1 pnpm with 12.14.0 test src/main/toolkit src/main/plugins/workers/plugin-worker-supervision.integration.test.ts src/main/menu/register-app-menu.test.ts --maxWorkers=2
# Toolkit
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
TOOLKIT_SOCKETPAIR_SOURCE=/absolute/orca/native/toolkit-host/socketpair.c TOOLKIT_AUTHORITY_BUNDLE=/tmp/orca-authority.cjs python3 -m unittest discover -s plugins/toolkit/config-service -p 'test_*.py'
node scripts/test-orca-host-probe.cjs /Applications/Orca.app
python3 scripts/check.py
```

## Remaining risks / RELEASE BLOCKED

1. 没有正式签名 Host 包、独立非 root 服务 UID 端到端、x64/Rosetta、notarization/staple 验收。主应用所有通用脚本入口、危险 entitlements、运行时 loader 的审查仍未完成。service enrollment 仍承担最终 code requirement/entitlement/整树权限检查；本轮 loader 的预检不是替代品。
2. staging manifest 必须在 nested binary 签名后重新生成再由 outer app 签名封印；自动签名顺序与 CI 发布管线尚未完成。fork 的独立 bundle ID、Developer ID、更新渠道和精确 CDHash enrollment 仍未落实。
3. CLI/daemon 等 runtime 分离及跨平台兼容性未完成。没有修改全局 RUN_AS_NODE；不能把此版本当作可 enrollment 的 Host。
4. main 的真实 task inventory/lease owner、迁移确认/恢复及目标修改保护未迁移，相关 Host 操作继续拒绝。未提供完整 Host-owned settings editor 或下载 UI，也未进行实际 Electron 窗口渲染验收。
5. 没有服务端事务前 generation 撤销检查协议；撤销可取消未派发请求、终止等待，不能撤销已提交事务。需保留 stable request_id 并查询结果。backup 仍非幂等。
6. 尚无持久化 Host authority 审计日志、升级时策略协调/新包 enrollment；当前重启会丢弃所有内存上下文。协议审计和发布运维仍需补齐。

后续应优先把可信任务记录和确认流程接入 main，迁移其余 Node-mode 入口、完成签名流程，再在隔离机器完成正式包与跨 UID 验收。上述门槛完成前，两仓库 PR 保持 Draft、**RELEASE BLOCKED**。

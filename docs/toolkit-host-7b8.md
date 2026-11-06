# Host configuration / task ownership — 7B-8

2026-11-06（Asia/Shanghai）。**RELEASE BLOCKED**。本阶段为两个独立 Draft PR 的增量开发，起点：Orca `22e9f7887db356140b5b478ca893447d6c71c92e`；Toolkit `4485add94457a7eb57fd087a9d1797f943e283b9`。未修改 main、合并、写入官方上游、安装生产应用、迁移真实配置或连接 VPS。

## 实现范围

### Main 与隔离服务的职责

Orca main 新增 `ToolkitTasks`、`toolkitDownloadLifecycle` 和维护能力判定。main 生成任务 ID/owner，私有 context 只允许 read/status/task-create/task-update。每个任务由对象引用句柄持有；复制同样的 ID 不获得所有权。普通面板 context 仍不允许 task/lease 操作，普通 worker 的私有 Host method 继续在 public dispatcher 前拒绝。没有新增 manifest capability、localhost token 或公开任务授权 API。

隔离配置服务新增 `host_tasks` 表和 `task_inventory` 元数据。task-create 在同一 SQLite 事务中检查 store/epoch、实际配置目标并建立任务与租约。task-update 绑定 owner/epoch/revision，以 CAS 状态机推进 created/running/paused/error/complete/cancelled；只有终态且 cleanup 已确认的内部调用可以释放租约。旧 lease-release 无法释放 Host task。任务更新和所有配置变更共用原有服务锁/事务；并发目标修改与创建任务只能一方成功，不能产生记录目标与配置目标分裂。

主进程队列串行处理任务请求。未知结果保留原 request_id；下一次更新先重试未决操作。create 未决请求也保留，目标不同的后续请求不能覆盖它。engine 创建时把实际配置目标交给 main 与新快照比较，防止旧 engine 配置借用新目标的租约。撤销使 context 失效并触发 engine abort/close；未确认清理的租约保留。服务重启保留任务、租约和幂等结果；Host 重启不会凭磁盘 JSON 自动取得旧 owner。

**接入边界：**现有 `DownloadManager` 已支持仅由 main 注入的生命周期适配器，创建、开始、暂停、恢复、失败、完成、取消和清理均有接点。V3Transport 在验证远端 REVOKED/DRAINED receipt 后更新 Host record，确认成功才删除私钥；失败保留私钥。跨仓库测试使用真实 main 实现与真实隔离服务代码驱动现有 engine。**生产 bundle 尚未纳入下载 engine 及其全部依赖，Controller/下载面板尚未切换为 main 自有入口**；普通插件不会获得此适配器，不能把这个接点当作已部署的完整下载产品。

### 旧任务、迁移和恢复

- 新建空服务显式初始化 `task_inventory=complete`；已有数据库升级为 `legacy-review-required`，即使旧 leases 为空也不推断“没有旧任务”。没有自动扫描后清空保护、自动接管、忽略未知记录或 UI 强制放行。
- main 检查固定 legacy jobs 目录；任何无法验证的目录或没有 Host record 的文件都阻止目标变更，磁盘声称 complete/cleanup_verified 不构成证明。这只是附加阻塞条件，信任根仍是隔离服务记录。
- 旧数据库的清理证明审查/接管工具尚未实现，相关按钮会明确禁用。不得通过删除 jobs 文件或直接改 meta 来解除阻塞。需要后续管理员离线审查流程；本轮不操作真实数据。
- import/restore/下载目标 patch 在主进程确认前检查库存和 epoch；服务执行事务时再次检查 leases。import 绑定该面板观察到的 preview ID/hash；restore 绑定 backup ID 和当前 store/epoch。对并发恢复，旧 epoch 的新请求被拒绝；相同 request_id 只回放原结果。main 按面板保留最多 128 个已批准请求摘要，允许恢复提交后响应丢失时精确重试；相同 ID 不同参数拒绝。确认对话框返回后再检查库存/epoch。
- 恢复只替换配置文档并生成新 epoch，**不会恢复备份中的任务/租约/所有权或降低 task inventory 状态**。旧 lease/未知任务保持阻塞，旧编辑器必须重新读取。离线 recover 复制旧备份时也强制 legacy-review-required，不能从旧的空 inventory 推断当前没有任务。
- v1 的 import/restore 可选 store_id/epoch 保留既有底层调用兼容；新的 main 面板对这两字段强制检查。该兼容不是普通 worker 的授权通道。

### Host 维护 UI

维护面板读取 main 返回的 capabilities，显示具体阻塞原因。Confirm migration 只有已预览且服务允许迁移时可点；Restore backup 只有 ready 且 inventory 可确认静止时可点。初始、无 Host、未知/旧任务、活动租约时禁用相应操作。原来的无条件可点但必被 main 拒绝问题已修正。

增加已知 settings 字段编辑、原始配置只读查看、刷新、备份 ID、恢复和原 request_id 重试。只提交实际改变的字段；未知字段和大整数不经 JS 整文档覆盖。目标字段被租约锁住时，其他 settings 字段仍可维护。backup 非幂等，因此不提供“重试上一个备份”的误导按钮。确认与恢复由 main 原生对话框授权；不使用浏览器 confirm 作为授权，也不访问 HTTP 配置端点。

当前 UI 仍没有 downloads-preferences 专用编辑器、备份列表/删除、旧任务接管和下载任务控制面板；完整 UI 验收未通过。生产 loader 的签名/fuse/权限门禁保持不变，所以当前安装的普通 Orca 仍不能打开受信维护功能。

## 签名构建流程

新增 `native/toolkit-host/seal.mjs`：先验证闭合暂存清单，再分别签 worker-node/socketpair.node，再按签名后的真实字节生成 manifest，原子替换并复核。独立 worker 使用仅 allow-jit 的专用 entitlement，未修改当前主应用 entitlement。Developer ID 路径要求对应证书链并使用 secure timestamp；临时 ad-hoc 必须显式 `--test-only`。

macOS 自定义 signer 在发现 Toolkit bundle 时执行上述流程，outer signer 忽略已签名的 Toolkit 子树，最后再检查 manifest，防止 outer signing 重签嵌套二进制导致封印失效。缺失 bundle 的现有构建路径不变。新增流程的临时签名 smoke 已执行；完整 electron-builder + Developer ID + notarization 尚未运行。

发布顺序必须是：固定两仓库 SHA、Node pin、工具版本 → stage → 完成其余 runtime 分离/entitlements 审查 → 对主 Electron 翻转限制性 fuses/生成 ASAR integrity → 签嵌套二进制 → 生成 Toolkit manifest → outer app 签名 → strict/deep 校验、各架构 entitlement/fuse/资源校验 → notarytool submit/wait/log → stapler staple/validate → 最终分发包验证 → 收集各架构主应用和 Bridge 精确 CDHash → 单独审核 enrollment。worker-node 永远不 enrollment；不依靠应用名字或泛 Team ID 放行。notarization 凭据不进入仓库。

官方依据：[Electron fuses](https://www.electronjs.org/docs/latest/tutorial/fuses) 要求在签名前修改 fuse；[Apple nested signing](https://developer.apple.com/documentation/xcode/using-the-latest-code-signature-format) 要求由内向外签名；[Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) 和 [custom workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow) 说明 Developer ID、hardened runtime、timestamp、notarytool 与 staple 流程。正式构建必须使用 fork 自己的身份和更新通道，不能使用官方上游身份。

## 验证分类与复现

详见 [测试证据](toolkit-host-7b8-validation.txt)。Toolkit Node 103 pass / 1 Windows-only skip；Python/native 23/23；Orca 定向测试 70 pass / 1 platform skip；Orca baseline check:changed 22/22。所有状态/文件均在临时目录。

1. **合成身份/合成 UI：**main/session/frame、真实恶意 worker、headless Chromium 面板 + 合成 Host binding、真实临时 SQLite；不能冒充签名 Electron UI 验收。
2. **临时签名/原生协议：**ad-hoc C Host、仅测试策略 pin Node CDHash，真实 socketpair/FD3/SCM_RIGHTS/双 Security.framework/SQLite。涵盖普通同 UID Node 拒绝、旧 context、撤销、并发、异常退出、超时与 FD 回收；新增 Host task + 现有 engine 的合成传输与清理证明。临时 worker 签名和独立 Node smoke 单独记录。
3. **真实生产签名：未执行。**无 Developer ID Orca 包、无生产 enrollment、无真正独立服务 UID 端到端、无 x64/Rosetta 或 Windows 实机验收。

核心命令：

```sh
# Toolkit
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
node scripts/test-host-panel.cjs ../orca
TOOLKIT_SOCKETPAIR_SOURCE=/absolute/orca/native/toolkit-host/socketpair.c TOOLKIT_AUTHORITY_BUNDLE=/tmp/orca-7b8-authority.cjs python3 -m unittest discover -s plugins/toolkit/config-service -p 'test_*.py'
python3 scripts/check.py
# Orca, pinned Node 24.22.1 / pnpm 12.14.0
pnpm exec esbuild native/toolkit-host/authority-test-entry.mjs --bundle --platform=node --format=cjs --outfile=/tmp/orca-7b8-authority.cjs
node native/toolkit-host/stage.mjs ../orca-tools /tmp/NEW_STAGE/toolkit-host /path/to/pinned/bin/node /path/to/pinned/include/node
node native/toolkit-host/seal.mjs /tmp/NEW_STAGE/toolkit-host - --test-only
ORCA_BACKGROUND_LAUNCH=1 node native/toolkit-host/smoke-worker.mjs /tmp/NEW_STAGE
ORCA_BACKGROUND_LAUNCH=1 pnpm check:changed --base=baseline/installed-1.4.249 --maxWorkers=2
```

## 剩余风险 / RELEASE BLOCKED

- 生产下载入口与所有 sealed 依赖迁移、旧任务安全接管、Host 重启后收据复核恢复尚未完成。当前正确行为是保留 unknown lease、停止新授权，不假装任务已安全清理。
- CLI/daemon 等通用 runtime 入口、主应用危险 entitlements/fuses 审查仍未完成；未启用生产授权。正式 signer hook 的整包行为、ASAR/fuse顺序、notarization、各架构 CDHash 和跨 UID 均须实机验收。
- 维护窗口真实 Electron senderFrame/native-dialog 行为未做完整渲染验收；本机没有仓库指定的 `$electron` skill，未以 Chromium fixture 替代该验收结论。
- 服务事务已提交之后，撤销只能停止等待，不能声称回滚；unknown 保留 lease。没有事务前远程 generation 撤销协议。
- inventory 尚未分页/归档；大量历史任务可能使状态响应达到帧上限并 fail closed，需要后续有界分页。任务 owner 的持久化只用于拒绝旧会话，不是可导出的 bearer token。面板批准重试缓存只在该窗口有效；关闭/导航/进程退出后未决操作须先检查服务结果，不能用旧 session 重发。
- Windows 原有 JSON/transport 路径和测试保留；本轮只有 Windows-only 实机测试跳过，不能声明 Windows 实机验收完成。

以上门槛未齐，两个 PR 保持 Draft，**RELEASE BLOCKED**。

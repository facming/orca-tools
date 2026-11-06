# 7B-16 测试分类与验收边界

**Draft / RELEASE BLOCKED。** 以下按“实际验证的对象”拆为六类，而非按脚本名推断真实性。一个联合脚本可贡献多个类别的证据；同一条测试不得重复累计总数。旧阶段记录保留，7B-16 实际重跑情况以 [执行记录](toolkit-host-7b16-validation.txt) 为准。

| 类别                | 现有入口（Toolkit 相对路径，另注明 Orca）                                                                                                                                                                                                        | 真实部分                                                                                                              | 注入/未覆盖部分                                                                                                                                  |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1 真实本地测试      | `scripts/test-download-history.cjs [--large]`；`scripts/test-download-large.cjs`；`scripts/test-host-recovery.cjs <authority-bundle>`；`modules/downloads/test-v3.cjs`（位于 plugins/toolkit）；Python reader/authority；Orca `src/main/toolkit` | macOS 文件、全量 SHA-256、排他发布、进程退出/重启/SIGKILL、SQLite、Ed25519/SSHSIG；隐藏 Electron 主进程/preload/panel | 历史 JSON 合成，Host 测试身份/管理回调注入；本地真实文件不等于真实远程字节传输。原生选择框/Finder/TCC 未人工操作                                 |
| 2 模拟账户 RPC      | `plugins/toolkit/test-macos-accounts.cjs`、`test-accounts.cjs`、`test-host-tools.cjs`；`scripts/test-host-accounts-downloads.cjs` 账户部分                                                                                                       | 真实账户规范化、provenance 拒显、禁用偏好落盘、UI、main IPC                                                           | accounts.list/selectClaude、runtime identity、额度/失效/超时均 fixture；没有真实 Claude 登录或服务用量                                           |
| 3 模拟远程浏览      | `plugins/toolkit/test-remote-browser.cjs`；`modules/downloads/test-macos.cjs` 的浏览项；联合 Electron browser 部分                                                                                                                               | 真实路径校验、过滤/分页、目录记忆、本地 UI                                                                            | files.browseServerDir/repo list 为注入结果；无真实远程文件系统/配对网络。原生 folder-picker 返回值也被模拟                                       |
| 4 模拟 SSH 字节传输 | `plugins/toolkit/modules/downloads/test{,-macos,-v3}.cjs`；`scripts/test-download-large.cjs`；联合 Electron download 部分                                                                                                                        | 本地 16 B/32 MiB/1 GiB、range 调度、暂停恢复、校验、任务幂等、故障恢复与清理                                          | transport.read/manage 或 exec 被替换；本地 Python authority/真实签名也不能证明 SSH 握手、网络掉包、sshd 并发限制。8 workers ≠8 条已验收 SSH 连接 |
| 5 真实远程账户验收  | runbook A01–A09                                                                                                                                                                                                                                  | 需专用 A/B 身份、真实配对 runtime、远端独立选择与用量证据                                                             | **NOT RUN**：用户确认尚未提供非生产环境；不得将模拟过期当真实凭证到期                                                                            |
| 6 真实 VPS 下载验收 | runbook D01–D09、真实 H01–H04                                                                                                                                                                                                                    | 需专用 VPS、真实目录/SSH/多连接/网络中断、独立源/目标全量哈希、远端撤销与本地 key 清理                                | **NOT RUN**：缺环境。历史 Linux 容器 sshd/UID 隔离结果不是 macOS+VPS 联合验收                                                                    |

表中 `scripts/test-host-download-ui.cjs` 属于本地 renderer 交互/展示证据，其**整个任务 backend 为模拟**；不把按钮存在当成后端恢复通过。`test-settings.cjs` 只是目标格式校验。`test.cjs` 里的 Windows 安装环境检查保留原 skip，不属于 macOS 真实环境通过。

## 可复现命令

Toolkit P0 集合（覆盖类别 1–4，报告按上表解释；不扩大冻结模块测试）：

```sh
node --test plugins/toolkit/test-macos-accounts.cjs plugins/toolkit/test-accounts.cjs \
  plugins/toolkit/test-host-tools.cjs plugins/toolkit/test-remote-browser.cjs \
  plugins/toolkit/modules/downloads/test*.cjs
python3 -m unittest discover -s plugins/toolkit/modules/downloads -p 'test_*.py'
PYTHONPATH=plugins/toolkit/config-service python3 -m unittest discover \
  -s services/download-authorization -p test_authority.py
node scripts/test-download-history.cjs --large
node scripts/test-download-large.cjs
```

性能测量独立运行，避免与测试/构建争抢 IO；`--large` 自动生成并删除约 8 GiB 文件，需 workload+2 GiB 可用空间；默认仍是 7B-15 的 1,003 条/三个 64 MiB。记录 logical bytes 和计算速率，**不是物理磁盘 IO**。缓存冷热、真实 Host ownership/回执恢复、完整 Electron UI 延迟仍需人工测量。

Orca 中构建现有测试 bundle（不安装生产 app）：

```sh
ORCA_BACKGROUND_LAUNCH=1 pnpm exec esbuild src/main/toolkit/toolkit-window.ts \
  --bundle --platform=node --format=cjs --external:electron \
  --external:@orca/process-host --outfile=/tmp/orca-7b16-window.cjs
pnpm exec esbuild native/toolkit-host/authority-test-entry.mjs \
  --bundle --platform=node --format=cjs --outfile=/tmp/orca-7b16-authority.cjs
# PINNED_NODE 必须是仓库 stage.mjs 校验通过的原始 Node；HEADERS 为对应版本。
ORCA_BACKGROUND_LAUNCH=1 "$PINNED_NODE" native/toolkit-host/stage.mjs \
  ../orca-tools /tmp/orca-7b16-stage/toolkit-host "$PINNED_NODE" "$NODE_HEADERS"
node native/toolkit-host/seal.mjs /tmp/orca-7b16-stage/toolkit-host - --test-only
```

Toolkit 中执行，staging 目录须是本轮新建；ad-hoc seal 只用于测试：

```sh
node scripts/test-host-recovery.cjs /tmp/orca-7b16-authority.cjs
ORCA_BACKGROUND_LAUNCH=1 node scripts/test-host-accounts-downloads.cjs ../orca \
  /tmp/orca-7b16-stage/toolkit-host /tmp/orca-7b16-window.cjs /tmp/orca-7b16-authority.cjs
```

联合脚本保持隐藏，不抢焦点，自动清理专用 HOME/profile/SQLite；脚本沿用的 `/tmp/toolkit-accounts-7b15.png`、`/tmp/toolkit-downloads-7b15.png` 是输出文件名，不是证据运行日期。需要本轮留档时复制到本轮证据目录并注明来源。不得把临时目录当长期交付证据；核心结果记录到 validation。

## 本轮源码核对点

- Toolkit `modules/accounts.cjs`：选择前 expectedActiveAccountId、全过程 runtimeId、选择后 refreshUsage=true 与远程确认；macOS 用量必须有对应 ID provenance。`accounts.html`：重新登录仅提示远端处理，Selected 只用于新会话。
- Toolkit `account-preferences.cjs`、`host-tools.cjs`：禁用按环境本地保存；远程每次被动读取重取列表，禁用不等于退出登录。
- Toolkit `remote-browser.cjs`：只读目录 RPC、项目根约束/逐段 symlink 拒绝；`modules/downloads/{engine,v3}.cjs`：全量 SHA-256、发布防覆盖、签名撤销和本地 key 清理。
- Orca `src/main/toolkit/{toolkit-downloads,toolkit-window}.ts`：窗口关闭/应用 before-quit 撤销 context 并触发异步 close；不会阻止进程退出等待全部清理。重启恢复仍是必要验收项。

这些是代码行为核对，不是已经操作真实远端的结论。产品代码沿用 7B-15；只有发现实证问题才修复，不为该阶段重建功能。

# 7B-16 独立非生产账户与下载验收 runbook

2026-11-06/07，Asia/Shanghai。**准备完成；真实远程账户、真实 VPS 下载均 NOT RUN；Draft / RELEASE BLOCKED。**
用户已确认尚未提供独立测试账户/VPS。本文件的配置、步骤、预期均不是实际部署或通过记录。
基线：Orca `c1e32d580f8fbe52e71d696a4bade30dca1a27fb`，Toolkit `d5e4b7e5259ea4d0babedfc97d276e7a423c48a8`。
沿用 7B-15 实现，不重建后台 SHA-256、request_id、账户切换或恢复架构。

## 1. 开始前填写环境卡

所有值由测试管理员在私有记录中填写；仓库只留脱敏别名、公钥指纹、结果。任一未确认项使对应真实验收保持 BLOCKED，不通过扫描个人 SSH 配置或凭证寻找替代环境。

| 资源             | 必需值与隔离要求                                                                                                            | 当前状态                             |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| macOS            | 专用测试机/VM、独立 OS 用户、空 HOME/profile/Keychain；两仓库完整 SHA、测试构建路径/哈希、系统版本、CPU/RAM/卷格式/可用空间 | 未提供；本机仅临时文件与隐藏测试进程 |
| Claude A/B       | 两个授权测试身份；记录 alias→脱敏 account ID、runtime ID 对应；至少一个有可核对的 Session/Weekly/更新时间                   | 未提供                               |
| 登录故障身份     | 使用 A/B 中专用可撤销账户或第三个测试身份 C；允许在测试端退出/重新登录、等待真实凭证失效                                    | 未提供                               |
| VPS              | 独立可销毁 Linux VM、VM ID/快照、管理员、独立管理地址/端口、下载私网地址/22222、防火墙来源 allowlist、断网恢复控制台        | 未提供                               |
| Orca environment | 独立配对 runtime；accounts-admin、accounts.list/selectClaude、只读 files.browseServerDir；只登记测试项目                    | 未提供                               |
| SSH/authority    | 独立 host key、receipt signing key/epoch，server/endpoint/account/signing key ID；四个独立非 root UID                       | 未提供                               |
| 本地目录         | 专用测试用户下载目录、jobs/keys、测试 profile；容量 ≥12 GiB（8 GiB 历史压力 + 下载/临时空间）                               | 临时测试可用；验收目录未提供         |
| 远端目录         | 新目录 `/srv/orca-7b16/fixture/project`，仅批准此 project 为导出根；state/staging 与源目录分离，VPS 建议空闲 ≥16 GiB        | 未提供                               |

`--user-data-dir` 或改 HOME 环境变量不足以隔离所有系统路径；正式人工验收必须用独立 OS 用户/VM。
测试构建单独放置，不安装到个人 `/Applications`，不复制现用配置。Host admission 不满足时记录阻塞，不能以关闭签名/host-key 检查换取“端到端通过”。现有 ad-hoc fixture 仅可计入模拟测试。

## 2. 用户、密钥、权限与部署核对

四角色建议名称与示例 UID（部署前检查冲突，可调整数字并同步 policy）：

| 角色            | 示例                    | 权限                                                                                                                 |
| --------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------- |
| authority       | orca-authority / 21001  | 非登录；独占 `/var/lib/orca-download` 0700、SQLite/receipt 私钥 0600、staging 0400；owner/reader/lookup 不可写       |
| workspace owner | orca-test-owner / 21002 | Orca 配对管理端的实际 Linux UID；拥有 fixture project、测试 Claude 凭证目录 0700/文件 0600；无 sudo                  |
| reader          | orca-download / 21003   | 无密码/sudo；仅专用 sshd forced command，不能在系统默认 sshd 登录；禁止 PTY、转发、rc、SFTP；不得访问 authority 私钥 |
| lookup          | orca-lookup / 21004     | 非登录，固定 AuthorizedKeysCommand，通过 authority lookup 查询；不能修改 code/state                                  |

管理员在**可销毁测试 VM**内按 [现有部署规范](https://github.com/facming/orca-tools/blob/feature/macos-orca-host-integration/docs/macos-security-deployment.md#dedicated-linux-download-authority-and-ssh-instance) 和 Toolkit `services/download-authorization/{policy.example.json,sshd_config.example,install.py}` 准备：

1. 安装 root 管理且不可被上述角色改写的 Python/cryptography 与 `/opt/orca-download`；不要运行旧 Windows 部署脚本。
2. policy owners 将实际 owner UID 映射到 `/srv/orca-7b16/fixture/project`，固定 socket `/run/orca-download/authority.sock`、dispatcher `/opt/orca-download/dispatch`；额度需容纳每个 1 GiB 源快照及并发任务。socket 父目录按规范 0755；这不授予 DB/私钥权限。
3. `install.py --policy <审核后的绝对路径> --authority-user orca-authority --python <root管理解释器>` 仅由测试 VM 管理员执行。它生成服务但不启动 sshd，也不配置防火墙。独立 sshd 的 host key 0600/root；ListenAddress 改为已批准测试私网地址。
4. `sshd -t -f <测试配置>`、`sshd -T -f <测试配置>` 检查；先 authority 再专用 sshd，unit 使用 Requires/After/BindsTo 和 KillMode=control-group。默认 sshd 必须拒绝 reader，owner 管理入口与下载入口分离。记录 `id`、关键目录 `stat` 和 sshd 有效配置，勿记录密钥内容。
5. 在管理员可信通道核验 SSH host 公钥指纹、receipt 公钥、epoch 和所有 ID，再写入**隔离 macOS** 的 root-owned `/Library/Application Support/OrcaToolkitState/endpoints.json`。worktree 为 `path:/srv/orca-7b16/fixture/project`；Toolkit 的 environment/host/user/port 必须完全一致。不能从待测 RPC 输出自动信任新 key。
6. 下载 task Ed25519 key 由现有 Host 流程按任务生成，存于测试 HOME 的 `Library/Application Support/OrcaTools/downloads/keys/<task-id>/`（目录 0700、私钥 0600）；不要手工复用管理 SSH key。管理 SSH key、host key、receipt key、任务 key 四类分开，均不提交到仓库、不贴入日志。
7. 测试人员仅在远端测试 owner 下通过 Claude 支持的交互登录完成 A/B 录入。Toolkit 没有重新登录按钮；不要直接编辑 token、把 `needsSignIn` 模拟值当真实过期，或用个人账户替代。

## 3. 可复现数据准备（无网络）

Toolkit 仓库脚本 `scripts/prepare-7b16-fixtures.py` 仅创建新目录和生成数据，无 SSH/部署/删除动作：

```sh
# 在独立 VPS 上，由 owner 执行；管理员预先建立 owner 独占的 /srv/orca-7b16（0700）；fixture 尚不存在。
python3 scripts/prepare-7b16-fixtures.py --output /srv/orca-7b16/fixture
# 本地演练用 /tmp 下未存在的绝对路径；不能复用或覆盖旧目录。
```

生成 0 B、16 B、32 MiB、1 GiB、中文/空格、隐藏/只读文件、空目录、205 项分页目录、根外 symlink；manifest 保存每个数据文件的大小与完整 SHA-256。普通文件为 owner 独占，非稀疏、无硬链接。manifest 位于 project 外，复制到私有证据目录。随机块重复写入用于可校验字节，不代表真实数据压缩特性；记录 SSH 是否启用压缩。

下载完成后在 Mac 对**实际目标路径**逐个 `shasum -a 256 '<目标文件>'`，与 manifest 比对，并核对字节数。人工选择实际输出路径，不能只按源文件名推断（冲突可能生成后缀）。防覆盖测试在目标目录预先创建同名哨兵，保存其 SHA-256；下载后原路径、哈希均须不变，另检查下载成品完整哈希。

## 4. 账户人工验收（真实远程账户类）

每步保存脱敏 UI、远程权威账户选择/用量记录与同次 UTC 时间；仅看 Toolkit Selected 标签不构成独立确认。新会话使用选中账户，已有会话不迁移。

| ID  | 操作                                                                                       | 通过标准/必须留存的证据                                                                                                                                             |
| --- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A01 | 冷启动，进入 Accounts；远程 Orca 账户管理核对选择 A，再在 A 启动新 Claude 测试会话核对身份 | Toolkit active ID、远端选择、新会话身份映射一致；邮箱脱敏，无自动改选                                                                                               |
| A02 | Refresh usage；核对 A/B Session、Weekly、reset、updatedAt 和 provenance                    | 仅接受与该 ID 匹配的 managed:/profile: 来源；A 数据不得出现在 B；缺来源显示 unknown，陈旧值明确 stale，不误报 0/满额                                                |
| A03 | A→B→A；每次切换后再 Refresh usage，远端独立核对并新建会话                                  | 选择先远程确认，切换后刷新属于目标 ID；B 无最新额度可为 unknown/fetching，不能复用 A 的额度；旧会话仍用原身份                                                       |
| A04 | 禁用 B→尝试选择→启用 B→选择；再禁用当前账户；重启                                          | 禁用阻止 Toolkit 后续选择，启用恢复；禁用当前不退出远程会话；偏好按 environment 持久保存；外部选择禁用账户仍如实显示当前                                            |
| A05 | 远端对测试账户退出/撤销登录，刷新；再通过远端交互重新登录并刷新                            | 明确 sign-in required / missing credentials / stale token，不报切换成功；新有效凭证的身份、用量来源相符。真实过期需服务确认或等待；未实际发生的 expiry 单列 NOT RUN |
| A06 | 仅中断测试管理连接：读取中、切换前、请求已发但确认未回三处；重新连接                       | 隐藏未确认旧卡片，显示可恢复错误，无自动重放切换。重连后先读远程实际选择再人工决定下一步，不假定超时意味着没切换                                                    |
| A07 | 远程 runtime 重启/重新配对，或在远端外部改选 B，然后刷新                                   | 同一次切换 runtime 身份不一致必须失败；重读发现真实选中 B；用量仍属于 B                                                                                             |
| A08 | 关闭并重启独立 Orca 测试进程，保留测试 profile；再读账户                                   | 远端选中与本地禁用偏好恢复；不开新会话、不自动选择；断网重启显示不可用，重连刷新恢复                                                                                |
| A09 | 1 GiB 下载在途期间切换 A/B                                                                 | 下载 task/target/key/lease 不变；当前账户选择变化不重绑下载；记录两类身份，Claude ID 不是 SSH reader ID                                                             |

本地已有模拟测试验证缺失/冲突 provenance、并发 mutation、超时、失效与 runtime 变化分支。真实服务无法自然产生的分支保留在模拟类，不人为篡改真实服务输出冒充远程结果。

## 5. 下载人工验收（真实 VPS 类）

| ID  | 操作                                                                                            | 通过标准/证据                                                                                                                                                        |
| --- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D01 | Remote browser 进入真实 project，隐藏开关、中文/空格、搜索、205 项翻页、返回根、重开/重启       | 与 VPS 实际目录一致；按 environment 记忆；根外、根外 symlink/子目录 symlink 拒绝；原生选目录取消保留原值                                                             |
| D02 | 下载 16 B、32 MiB、1 GiB；1/4/8 连接组合，至少一次两文件并行                                    | 每个目标大小与完整 SHA-256 一致；记录实际活跃 SSH 连接/range 日志和任务进度。16 B 可能仅一 range，不能要求它产生 8 条连接；全局并发上限 16                           |
| D03 | 在 1 GiB 有已完成 range 时暂停，记录 bytes，再恢复；再全部暂停                                  | 停止活动 range，恢复复用已验证片段，允许重下未完成 range；暂停不等于远端撤销；最终完整哈希通过                                                                       |
| D04 | 用测试 VPS 控制台/专用网络规则阻断下载端口，分别短暂中断和超过重试窗口；恢复规则、必要时 Resume | 记录规则 ID、开始/结束 UTC、真实连接错误；无假完成、重试有界、恢复后哈希正确。规则只覆盖测试来源/地址/端口，不能全局关本机网络或影响管理控制台                       |
| D05 | 同一 UI 请求重试、页面刷新；在未知结果后重启；复用新 ID 的既有任务完成后再重放原 ID             | 用真实 Host 审计/受控测试驱动记录 request ID→task ID，不手改 jobs JSON；同输入一任务，相同 ID 异输入拒绝。没有可观察原 ID 的驱动时该子项 NOT RUN，不用“点了两次”替代 |
| D06 | 下载中 Quit，重开；另用记录 PID 的测试进程 SIGKILL，重开并 Resume                               | 任务暂停/待恢复而非丢失，成品不覆盖，partial 重新校验，最终 SHA-256 正确；区分完整 app 退出与只关闭 Toolkit 窗口                                                     |
| D07 | 已有同名哨兵；传输中另创建目标；源文件在重新注册前变化                                          | 哨兵哈希不变，冲突安全拒绝/新后缀；不可静默把新源续接旧任务。授权快照已固定时原任务仍按原签名哈希验收，不能要求随原文件变化                                          |
| D08 | 完成/取消后 Clear access；阻断管理 revoke 回复，再恢复并重试                                    | 远端 REVOKED+DRAINED 签名回执→本地持久验证→Host release→本地私钥删除；远端 inventory 无活动授权/read lease；用保留公钥 hash/task ID 查证，不在证据中保留私钥         |
| D09 | 在受限测试目录制造本地 key/partial 删除失败，恢复权限后 Clear access；重启离线重试已保存回执    | 欠项可见且保留证据，不把 UI complete 或 Host released 当撤销成功；已有可信回执可离线完成本地清理，没有回执必须恢复管理连接                                           |

断网不是杀死 mock transport。停止 sshd unit 是服务退出，可补测，但不能替代真实网络 DROP；丢弃连接包后也应记录是否存活的 range 最终排空。工具没有可安全控制的隔离网络时 D04 保持 NOT RUN。下载完成时密钥可能已自动清理，需在故障用例中检查保留/重试；不应为了截图人为重造 key。

## 6. 历史全量校验与用户体验

现有实现每次历史加载取消 verified 标志，串行按 256 KiB/8 ms 复验；暂停下载不会暂停历史复验，且没有只重验变化文件的缓存。每个 1 GiB 文件理论至少约 32.8 秒（100 GiB 至少约 54.6 分钟），实际更慢。不能将这段 pending 解释为已验证或下载失败。

先用 Toolkit `node scripts/test-download-history.cjs --large`：真实 8×1 GiB + 1,000×1 KiB、合成历史，无 Host lease；记录 ready、全量耗时、logical MiB/s、CPU、RSS、事件循环、反复 list 延迟、正常 close/子进程退出、实际 SIGKILL 恢复。该负载不会建真实远程任务，不能证明完整 app 启动性能或物理盘读取速率。

人工 H01：准备至少 8 个真实 1 GiB 完成任务（更大历史按空间逐步增加），保留真实 Host 记录，Quit/重启；测首列表和 Accounts Refresh、滚动/切换页面响应、待验数变化。H02：一边复验一边启动 32 MiB/1 GiB 新下载，对比无复验时速度和交互；Activity Monitor 观察测试 Orca 及 worker 的 CPU/RSS/读取增量，`iostat -d -w 1 -c 60` 只辅助观察整盘，不能归因单进程。记录 APFS、内/外置盘、缓存冷热、其他负载，不强制清系统缓存。

H03：复验期间 Quit、关窗口、SIGKILL 三种操作分别测退出延迟，重开必须从 pending 重验。Manager.close 会取消历史读取；Orca before-quit 触发异步 close，**不会等待全部清理完成**，因此退出成功不等于撤销成功。未完成任务/回执/租约在下次恢复后复核。慢盘上单次在途 read 可能延迟取消，需实测。

H04：备份一份生成成品后改动同大小字节并恢复 mtime，或移走成品；重启/Recheck checksum 显示 failed，保留文件；恢复正确内容后 Recheck 可通过。校验通过后再次外部修改不会被实时监控，要显式复验或重启才能发现。

建议本次试验观察线（须记录硬件和样本，非 SLA）：首次列表 ≤2 s、交互无 >500 ms 停顿、普通 Quit ≤5 s。超线必须记录并判断是否阻碍日常使用；任何假 verified、覆盖、串账户均直接 FAIL。即使本地压力样本低于这些值，也不能预签真实 H01–H04。

## 7. 清理顺序与结果记录

1. 恢复所有测试网络规则，确认管理与下载通道可达，暂停/取消仍在运行的任务。
2. 对**本轮 task ID**执行 Clear access，逐个核对签名 REVOKED/DRAINED 回执、remote inventory、Host released、keys 目录无私钥、partial 欠项归零；保留脱敏 task/request/key hash、时间、证据链接。不运行无范围的删库/删 key 命令掩盖失败。
3. 欠项不能收敛则标 FAIL/BLOCKED，保留隔离环境与审计，不先销毁 VM。先验证远端撤销，再清本地 key；不能把网络不可达视为撤销。
4. 全部清理验证后，在测试远端登出 A/B/C，管理员撤销测试授权，清理测试管理 key/配对，停止专用 sshd 后停止 authority。销毁专用 VM/快照、测试用户/Keychain/profile/新 fixture 目录；只对环境卡中列明对象操作，不触及生产 sshd/authorized_keys/个人配置。
5. 公钥指纹、哈希、脱敏结果按约定保留；截图遮盖邮箱、路径中的个人名及 token。原始凭证不进入证据包。

每条记录：`case ID | 六类之一 | PASS/FAIL/BLOCKED/NOT RUN | 起止 UTC | Asia/Shanghai | 两仓库 SHA | build hash | 环境别名/runtime ID | 操作/实际结果 | task/request ID | 源/目标 SHA-256 | 截图/日志 | 清理结果 | 执行人/复核人`。

本轮 A01–A09、D01–D09、真实 H01–H04 全部 **NOT RUN（未提供独立非生产环境）**。源码检查和本地测试证据见 [测试分类](toolkit-host-7b16-tests.md) 与 [执行记录](toolkit-host-7b16-validation.txt)。签名/公证、完整包 admission/enrollment、独立安全审查仍是发布门槛；RELEASE BLOCKED 保留。

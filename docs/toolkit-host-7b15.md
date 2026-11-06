# 7B-15 — 账户与下载最终完善

2026-11-06，Asia/Shanghai。**Draft / RELEASE BLOCKED**。
起点：Orca `abb6bd5c9b277cc14f2160d3c48aa9cb6d384528`；Toolkit `910a61fb1c13b094b4b020f44b391721b850036a`。
沿用 [Orca #1](https://github.com/facming/orca/pull/1)（`feature/toolkit-trusted-host` → `baseline/installed-1.4.249`）与 [Toolkit #15](https://github.com/facming/orca-tools/pull/15)（`feature/macos-orca-host-integration` → `feature/macos-security-finalization`）。没有修改 base、main 或官方上游。

## 本阶段变更

- macOS DownloadManager 异步读取历史任务元数据；`ready` 只等待加载与 Host ownership reconciliation，不再等待历史成品全量 SHA-256。完整校验在后台串行运行，逐文件让出事件循环；单次读取 256 KiB，每次读取后等待 8 ms，无预读，历史校验理论读取上限约 31.25 MiB/s。实际吞吐更低；此限制针对历史成品复验，原 range worker 与新下载发布校验保留。
- 成品重新加载后立即取消持久 JSON 中的信任标志，明确 `pending` / `verified` / `failed`；只有完整 SHA-256 匹配且打开期间/最终路径未变化才 verified。大小、mtime/ctime/inode 仅为附加变更检测，不能替代哈希。校验中退出可取消，重启重新开始，失败保留原文件。
- Host 页面显示后台待校验、已验证、失败；pending 不显示已验证或资源导入按钮。新增 `Recheck checksum`，仍由 main 管理器执行，renderer 不能提供预期哈希或任意校验路径。写入校验结果失败也不得报告 verified。
- 新 request_id 复用已有未完成任务时，关联 ID 先落盘再返回。任务随后完成、页面刷新和 Orca 重启后，该 ID 仍返回同一任务；不同输入拒绝。每任务最多 256 个额外重试 ID，超过后提示使用已有任务，不静默丢弃幂等记录。
- macOS 账户额度严格要求 provenance 与账户 ID 相符；旧版缺 provenance 的 active/inactive 用量显示 unknown。两次一致的 runtime/target 映射不能证明额度来源，macOS 不再使用该回退。Windows 兼容路径保留。
- Host 读取账户时重新获取远程列表并应用本地禁用偏好，不再无限复用上一次快照；这不会主动强制刷新用量。沿用切换前 expected active ID、切换全过程同一 runtime、切换后远程确认和并发 mutation 屏障；错误/超时不显示成功、不自动重试切换。

## 审核与回归矩阵

| 范围                                            | 本阶段结果与证据层级                                                                                                                                                                                                                             |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 账户列表、状态、Session/Weekly、重置时间        | 真实隐藏 Electron/main/preload 展示与交互；RPC 与用量为模拟。脱敏、时间、unknown/stale、限流/失效凭证回归通过。新增缺失/冲突来源拒显用量测试。                                                                                                   |
| 连续/并发切换、禁用当前账户、设置变化、RPC 超时 | 模拟 RPC 下确认 runtime/active ID 与错误行为；真实 Host 并发拒绝、禁用恢复和进程重启通过。禁用当前账户只限制后续选择，不退出当前会话。外部变更后的被动读取重新读取远端，失败不回显旧快照。                                                       |
| 远程浏览、目录记忆、选择目录                    | 现有根授权、symlink/越界拒绝、搜索/隐藏项/分页/环境偏好测试通过。真实临时文件保存；远程目录与原生选择结果模拟，未操作实际系统弹窗。                                                                                                              |
| 大文件、暂停/恢复/取消、断线/重试               | 真实本地 1 GiB、32 MiB 文件与完整校验；字节 transport 和断线模拟。关闭/重开真实 Electron；SIGKILL 恢复、range 篡改、ENOSPC/权限注入回归通过。                                                                                                    |
| 历史性能与恢复                                  | 1,003 条合成历史元数据 + 真实文件（1,000×1 KiB、3×64 MiB）：列表 ready 99 ms；全部校验 37,068 ms；事件循环 P99 49 ms、max 78 ms、RSS 147 MiB。校验 pending 时实际 SIGKILL，重开重新校验全部通过。单次本机结果，非 SLA，非 1,003 次真实远程下载。 |
| 完整性与发布                                    | 完整 SHA-256、排他发布、防覆盖、发布中断恢复保留通过。新增同大小/恢复 mtime 的篡改、删除成品、校验中止重启和重新校验成功路径。                                                                                                                   |
| request_id                                      | 真实 renderer 刷新、持久 partition 进程重启及 main 任务复用通过；新增复用 ID 在任务完成后的重启去重测试。重试 ID 关联落盘失败不会返回成功。                                                                                                      |
| 撤销、租约、密钥                                | 真实 Ed25519/SSHSIG、临时 SQLite、Host recovery 9/9；远端回复丢失、Host release 失败、本地 key/partial 删除失败与离线回执重试继续通过。不把 UI 完成或 Host released 当作签名回执。                                                               |
| 账户与下载隔离                                  | 活动任务期间 A/B 切换、下载保持绑定原任务、下载目标租约不变；真实 Host 加模拟 RPC/byte transport 通过。                                                                                                                                          |

完整命令、数量、失败修正与边界见 [测试记录](toolkit-host-7b15-validation.txt)。独立测试环境要求、执行顺序、清理与通过标准见 [非生产验收流程](toolkit-host-7b15-acceptance.md)。7B-6 至 7B-14 的成果保留在 [阶段总览](toolkit-status.md)，本阶段没有重复开发。

## 仍未通过的验收

没有提供专用非生产 Claude 账户与远程环境，**真实远程验收未执行**。没有读取真实 Claude 凭证、连接或修改生产 VPS、迁移真实配置、安装生产应用、合并 PR。真实远程 SSH 网络长期断线、真实限流/额度、TCC/原生窗口操作、完整包 admission/enrollment、签名/公证与独立安全审查仍待验收。后台复验不会使成品永久可信：在成功校验后再被外部修改，需重新校验或重启才能发现。历史 Host ownership/回执重建仍有自己的开销，99 ms 合成历史结果不代表含大量真实租约的冷启动时延。

Blender、Godot、Unity、ComfyUI、项目工作台、TextEdit 保留现有实现与测试，暂停新增功能。Windows 不作为开发或验收目标。RELEASE BLOCKED 不变。

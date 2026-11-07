# 7B-15 独立非生产验收流程

状态：**未执行真实远程验收 / RELEASE BLOCKED**。本阶段未提供独立环境。下列是准备清单和执行顺序，不是部署完成证明。禁止使用生产 VPS、个人真实 Claude 凭证、已有真实配置或个人 authorized_keys。历史 Windows 部署步骤不能替代 macOS v3 Host 验收。

## 环境与权限

- 独立 macOS 测试机或 VM，独立 OS 用户、空 HOME/Orca profile、空 Toolkit 数据库；只使用专门测试构建和默认配置。不得复制个人 Library、Keychain 或 Claude 目录。生产签名、fuses、资源清单、Host admission/enrollment 单列验收，不以 ad-hoc 测试包替代。
- 独立 Linux VM、独立私网地址与 Orca environment，测试项目根 `/srv/orca-7b15-fixture`；记录 VM ID、快照和允许连接的端点。SSH 仅开放给测试客户端，不复用生产 sshd、主机密钥、数据库、authority epoch 或回执签名密钥。
- 至少两个经管理员授权的专用 Claude 测试账户 A/B，由测试人员在隔离远程环境内自行登录；准备有 Session/Weekly 数据的账户。第三个未登录或失效状态用专用测试身份，不能注销真实账户。测试记录只留脱敏 ID、状态、额度和时间，不输出凭证。
- Orca 配对远程 runtime，具有 accounts-admin 权限、账户 list/select RPC 和只读 `files.browseServerDir`；确认每次回复有 runtime identity。macOS 要求用量包含匹配账户的 `managed:<id>` 或 `profile:<id>` provenance；缺失时验收结果应为 unknown，无额度。
- Linux v3 授权服务使用彼此不同的非 root UID：authority、owner、reader、lookup；owner 只导出测试根。authority 数据库、staging 与 receipt-key 为独占私有目录/文件，reader/owner 不可写；程序、策略和解释器由 root 管理。参照 `services/download-authorization/policy.example.json`、`sshd_config.example` 和 [部署规范](macos-security-deployment.md)。安装器不自动启动 sshd；管理员需审查独立服务、端口、防火墙和目录权限后在测试 VM 内执行。
- reader 使用独立受限 sshd，禁用任意 shell、PTY、转发，使用 authority lookup 与 forced command；管理通道通过已配对 Orca。固定测试 SSH host key 与 receipt public key，记录 server/endpoint/account/key ID 与 epoch。客户端不能以关闭 host-key 检查完成验收。
- 本地临时目录至少 12 GiB 可用；为权限/ENOSPC 测试准备专用受限目录或限额测试卷，不填满真实系统盘。TCC、原生选择框、Finder 验收由测试人员在隔离桌面执行，自动测试保持隐藏不抢焦点。

## 数据与操作顺序

1. 在测试根创建：空文件、16 B、32 MiB、1 GiB、三个 64 MiB 文件；中文/空格名称、隐藏文件、205 个目录项、空目录、指向根外的 symlink、只读文件。内容为生成数据，保存每个源文件的完整 SHA-256。准备同名本地哨兵文件，并记录其哈希。为历史恢复准备 1,000 条真实已完成的测试记录；不得把伪造 JSON 当作真实 Host 完成记录。
2. 账户：冷启动列表、脱敏、Session/Weekly、重置时间（秒/毫秒/ISO 与本地时区）、过期/限流/未知状态；A→B→A 连续切换；同时提交两个切换及切换与禁用请求；禁用当前 A 应保持远程当前 A，但阻止再次选择禁用账户。重启确认禁用偏好和远程当前选择。
3. 在切换前检查与切换后确认之间中断 RPC、重启远程 runtime、修改 environment，分别记录故障阶段。确认未经匹配 runtime 与 activeAccountId 复核不会显示成功；超时不自动重发 mutation。用旧 A 的缓存回到 B 的响应，验证 B 不展示 A 用量。无 provenance 必须显示 unknown。
4. 浏览允许根，测试搜索、隐藏项、205 项分页、目录/文件记忆；拒绝根外路径与 symlink。原生选择取消保留原目录；不同 environment 使用各自目录记忆。
5. 下载大小梯度与多文件，检查进度、速度、暂停/恢复/全部暂停/取消。活动下载期间切换 Claude 账户，确认下载仍绑定原任务与目标，目标租约继续阻止修改下载服务器。恢复后对目标文件重新计算完整 SHA-256，与源清单比对。
6. 仅在测试 VM 的隔离网络中断传输及 revoke 回复；测试有界 range 重试、退出重启后的暂停、损坏 partial 的重下、源文件变化拒绝续传。原 request_id、新 ID 复用已有任务，以及丢回执后页面重载/进程重启/任务完成后的重复请求，都只对应同一个任务。相同 ID 不同输入必须拒绝。
7. 关闭时、发布前后、历史校验中执行测试进程 SIGKILL。重启列表无需等待全部历史哈希，全部历史成品从 pending 开始；SHA-256 通过才 verified。同大小并恢复 mtime 的内容篡改、文件删除、不可读、路径替换都不得显示 verified。恢复正确文件后 Recheck checksum 可恢复 verified，原文件不被删除或覆盖。
8. 撤销依次核对：远端 DRAINED/REVOKED 签名回执 → 本地持久保存并验证回执 → Host lease release → 本地私钥删除。分别注入各步失败，检查清理欠项可见；远端已撤销时使用保存的回执离线重试，不把 Host 终态或 UI 完成当撤销证据。取消时 partial 删除失败也保持欠项。
9. 清理仅针对这些测试任务：恢复连接并 Clear access，确认所有测试租约已释放、测试 key 已删除、无在读 range，再卸载独立服务、销毁 VM/测试用户。保留未清理任务的脱敏审计记录，不能直接删库掩盖欠项。

## 记录与判定

每项记录两个 commit、构建签名/清单、macOS/Linux/Orca 版本、测试端点 ID、预期与实际、UTC 时间及 Asia/Shanghai 显示时间、脱敏截图、源/目标 SHA-256、Host task/request ID、清理欠项。记录初次列表耗时、全量校验耗时、事件循环延迟、CPU/RSS/磁盘读取；本地实测值仅是该机器结果，不承诺远程性能。

报告分三栏：真实本地（进程/文件/签名/SQLite）、模拟远程（注入 RPC/transport/对话框）、真实非生产远程（实际测试账户、配对 runtime、网络 SSH）。只将实际跑过的第三栏标为通过。任一来源未确认、错误成功提示、重复任务、覆盖哨兵、撤销顺序错误均阻断验收。完整生产包 admission/enrollment、安全审查、签名/公证仍独立阻断发布。

## 无远程环境时可复现的替代证据

在 Toolkit 仓库执行，全部使用临时目录和生成数据：

```sh
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
node scripts/test-download-history.cjs
node scripts/test-download-large.cjs
python3 -m unittest discover -s plugins/toolkit/modules/downloads -p 'test_*.py'
PYTHONPATH=plugins/toolkit/config-service python3 -m unittest discover -s services/download-authorization -p test_authority.py
```

隐藏 Electron 测试的 staging、seal、bundle 与执行方式见 [本阶段测试记录](toolkit-host-7b15-validation.txt)。`services/download-authorization/test_isolation.py` 仅在可销毁 Linux 容器内以 root 执行，用于真实 UID/sshd 隔离证据，不可直接在桌面/生产服务器执行；它也不能替代真实 Claude 账户与 macOS Host 联合验收。本阶段未重新运行该容器测试。

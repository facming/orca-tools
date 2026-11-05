# macOS 发布前 HIGH 修复记录（阶段 7B-1）

日期：2026-11-05（Asia/Shanghai）。**交付状态：Draft / BLOCKED，不能发布或合并。**

本分支从 PR #8 / `feature/macos-unity` 的 `fe0a0157c1e0d686fe06fe9eed6846abc715cd06` 创建，名称为 `feature/macos-release-hardening`，PR base 保持 `feature/macos-unity`。没有开始 PR 整合，也没有修改 main、PR #9 或 #10。下面保留的阶段 7A 原文来自 #10 的 `7616003cc0709952fbc35155b3993fa9a0e1885b`，仅复制这份依据文档；未合并或 cherry-pick #10。

## 7B-1 当前结论

| 项目 | 原始问题及修复前复现 | 本次实现与验证 | 当前判定 |
| --- | --- | --- | --- |
| H-01 | 默认 urllib redirect 将合成 Bearer 从 A 转给另一端口 B；新测试用原默认 opener 重现 | 全部调用共用 `NoRedirect`，拒绝 301/302/303/307/308，包括同 origin；HTTP 错误不反射 body、reason、Location。双独立回环 HTTP 服务，GET/POST × JSON/stream × 跨端口/主机/同 origin/相对地址/多跳入口/协议切换，共 160 个拒绝组合，B 没有收到请求；正常认证与错误返回另测 | **实现及离线回归通过，待审查** |
| H-02 | 直接执行 #8 两个 CJS 文件，在临时目录用固定 `.tmp` symlink 改写合成 victim | macOS 使用 CPython 原生 POSIX 绑定逐级 `openat(O_DIRECTORY/O_NOFOLLOW)`，目录 0700、文件 0600、属主/单硬链接校验；固定 lock inode + flock、随机临时名、O_EXCL/O_NOFOLLOW、fsync、renameat；替换前重读身份和内容 hash。Controller 保留每个消费者的读取版本，偏好合并使用单一快照，外部修改/损坏报错保留；Windows 分支不调用 Python | **部分修复；最终比较与替换之间仍有竞态，HIGH 未关闭** |
| H-03 | 直接执行 #8 reader，在 cleanup 读取后插入人工 key，仍输出 KEY_REMOVED 且人工 key 丢失 | register、SSH done、Orca fallback 都使用同一 `PrivateDirectory`、同一 `.ssh/.orca-authorized-keys.lock`。精确完整授权行归属；保留清理 tombstone 支持重复确认；拒绝相同公钥出现在非自有行；带任务 ID 的 SSH 回执和带随机 nonce 的 fallback 回执。远端 reader 按内容摘要安装到独立版本目录，无固定可跟随链接临时文件 | **合作写入者并发已修复；不遵守锁的人工写入仍可丢失，HIGH 未关闭** |

H-01 的 MCP JSON 信封、工具名称、参数和成功响应未变。HTTP 失败的自由文本改为固定消息（403 保留认证诊断，404 标明 unsupported/unavailable）；这是有意减少错误细节，避免远端反射任何敏感请求头。ComfyUI、Godot、Blender、Unity、Tripo 仍走同一个 request/opener；stream 和各产物下载均无独立重定向通道。未建立真实 HTTPS 服务来测 TLS 握手；禁止任何 redirect 的策略在建立下一跳连接前生效。

## 未关闭的安全边界（不能当作 PASS）

`node:fs` 没有足够的目录描述符相对操作和 flock 接口。本次没有把多次 lstat/rename 包装成安全保证，而是增加小型 `private_files.py` / `private-json.py`，通过 CPython 的 POSIX 系统调用绑定及 libc fstatfs 查询实现路径固定和锁定。没有新增自行编译的 native 二进制，也没有声称已经获得独立原生代码审查；这些新增边界仍须 ChatGPT 审查和人工批准。macOS 运行环境须有可信 Python 3；缺失时明确失败，不退回旧的不安全写入。新文件随 Toolkit 源码分发，不自动安装解释器。

**标准 advisory flock + renameat 不是原子 compare-and-swap。** 不遵守锁的同 UID 进程可以在最后一次身份/hash 比较后、renameat 之前改写目标，仍被覆盖；同 UID 攻击者还可能在最后路径/临时文件检查之后替换名字。单靠改用 C/native 编写相同调用不能消除这个窗口。本轮没有宣称存在通用原子条件替换 API，也没有在后台授予权限或更改 SSH 服务设置。

明确的失败证据命令：

```sh
python3 plugins/toolkit/reproduce-unresolved-races.py
```

本轮结果：H-02 final check/rename race reproduced=True；H-03 non-cooperating writer lost update reproduced=True；**退出码 1，BLOCKED**。该程序在系统调用边界注入真实临时文件修改，不计为通过测试，也没有用 skip/expectedFailure 隐藏。H-03 在这个剩余窗口仍可能发出 KEY_REMOVED 回执，因此不能声称“绝不误确认”；自动删除本机私钥的强保证也尚未满足。本 Draft 禁止发布。要关闭这些 HIGH，需独立审查能强制所有写入经过同一授权服务的设计，或调整存储/SSH 授权模型；仅要求人工编辑器自愿使用 flock 不能满足原需求。

其他限制与失败恢复：

- 仅允许 APFS/HFS（本机 local 标记）以及 Linux ext 系列/XFS/Btrfs/tmpfs；NFS/SMB/FUSE/overlay/未知类型拒绝。类型 allowlist 不是对损坏内核、存储设备或全部挂载选项的可靠性证明；本轮实测是 macOS APFS，Linux libc 分支未在真实 Linux 主机验收。
- 权限、链接、锁超时/锁身份变化、已检测的外部改动、原子替换失败、文件系统未知、远端状态缺失/损坏/旧协议，都不会输出新协议的清理成功回执。Transport 失败保留任务与本机私钥，要求人工处理。fsync 在 rename 之后失败可能留下已提交的新文件，报错并要求重新读取，不能把这种断电/磁盘故障称作完整事务回滚。
- 旧 v1 reader 不使用新锁，不能与新版本并行运行；旧任务没有精确归属记录时不做猜测性清理，也不接受旧的无任务 ID `KEY_REMOVED` 回执。升级前必须人工核对旧任务/授权，在专门授权的维护窗口处理；本轮未连接真实 VPS。
- 清理 tombstone、内容寻址 reader 目录和异常终止遗留的随机临时文件保留供恢复，不自动清除未知文件。权限异常不会被自动 chmod 修复。人工应先备份合成验收以外的真实配置，然后检查归属、权限和遗留任务；不能直接删除本机 key 来消除告警。
- Windows 设置文件逻辑原样保留，故本次没有声称 Windows 文件系统也获得了 H-02 防护。共享远端 SSH 协议有明确 v1→v2 迁移限制，不能声称旧任务无缝升级。

## 实际测试记录

所有文件安全测试使用临时目录、合成配置/公钥和隔离 HOME；仅 H-01 使用合成回环 HTTP 服务。没有启动真实引擎/应用服务、连接真实 VPS、读取真实私钥/账号凭据或 SSH authorized_keys。

| 命令/检查 | 本轮结果 |
| --- | --- |
| `node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs` | 98 项：97 通过，1 既有 Windows 原生探针跳过，0 失败；其中顶层 Toolkit 76 项、Downloads 22 项 |
| `node --test plugins/toolkit/modules/downloads/test*.cjs` | 22 项：21 通过，1 跳过；下载、暂停、续传、恢复、清理失败保留 key、macOS/Windows 参数通过 |
| `python3 plugins/toolkit/modules/downloads/test_remote.py` | 原始 range/命令边界/源变更/注册 hash 回归通过；清理断言升级为 v2 tombstone，未删除原有读取测试 |
| `python3 plugins/toolkit/modules/downloads/test_hardening.py` | 13 项通过：并发 register、并发 cleanup、register+cleanup、人工写入检测、锁等待/超时/替换、原子替换失败、链接、重复 cleanup、实际生成的 Orca fallback 与 SSH done 两个本地进程并发 |
| APFS 并发压力复验 | 双 register + 双 cleanup 用例重复 100 轮通过。初稿非独占 O_CREAT 初始化曾有 6/100 ENOENT；改为 O_CREAT/O_EXCL 初始化后打开稳定 inode，100/100 通过。没有忽略初稿失败 |
| `python3 -m unittest discover -s services/local-gpu-bridge -p 'test*.py'` | 301 项：298 通过，3 既有 Windows 专属跳过，0 失败；包含全部引擎/MCP/ComfyUI Bearer/下载回归及 3 项新增 redirect 测试（含 160 组合） |
| `python3 scripts/check.py` / `git diff --check` | PASS：189 个源码/文档文件；diff 无空白错误。扫描只覆盖仓库源码与文档，未扫描真实凭据 |
| `python3 plugins/toolkit/reproduce-unresolved-races.py` | **FAIL / exit 1：H-02 与 H-03 最终窗口均仍可复现；发布阻塞** |

新增 Node 攻击测试还覆盖两个固定 tmp 链接、目标 symlink/hardlink、父目录链接、损坏 JSON/错误 schema、偏好 read→write 外部修改、多个 Controller 快照冲突、恢复、Windows 不调用 POSIX helper。共享 syscall 测试覆盖随机名碰撞（普通文件和 symlink）、写入中断、目标 inode 替换、单链接/权限及锁错误。原先允许 macOS 静默忽略偏好错误的测试改为断言明确错误；Windows 的原断言保留。旧 Windows UI fixture 显式标注 win32，防止在 Mac 宿主上误用新权限路径，未删掉业务断言。

Accounts 专用偏好源码未改。现有实际 Mac/VPS、Unity C#、Metal、TCC、LaunchAgent、Windows 原生验收缺口继续有效。本轮不处理 MEDIUM/LOW，不启动 PR 整合。

---

以下为阶段 7A 历史审计原文；“本轮未修复”等表述指 7A，当前状态以上面的 7B-1 记录为准。

# macOS 最终集成前审计（阶段 7A）

审计日期：2026-11-05（Asia/Shanghai）。仓库：`facming/orca-tools`。

**结论：Git 依赖完整，可以规划集成；当前不具备发布条件。** PR #5 的两次网络安全修复均已进入 #6/#7/#8，未发现当前分支基线错误或重复补丁。发现 3 项已用合成数据复现的 HIGH 问题，真实 Mac 应用、Unity C# 编译和 VPS 链路尚未验收。Mock 通过不能代替实机通过。

本次只增加/修改文档；没有合并 PR、修改 main 或业务源码，也没有开始 7B。没有执行真实账号 RPC、启动真实 GPU/引擎、连接 VPS 执行任务、创建 LaunchAgent、修改 Tailscale/PF/防火墙、安装插件/扩展、读取真实 Keychain/Cookie/OAuth/Token/SSH 私钥或覆盖用户文件。GitHub PR/分支查询及提交发布使用既有 Git/gh 认证；不导出认证材料。漏洞复现只使用自行创建的临时文件、合成字符串和回环 HTTP。

## 1. 审计方法与证据范围

- `git fetch origin --prune`、`git ls-remote --heads origin`、`gh pr list --state all`、逐 PR `gh pr view` 获取实际远程快照；共 9 个既有 PR、10 个远程分支（含 main）。当时没有 `feature/macos-release`。
- 对每个 PR 执行 `git merge-base --is-ancestor origin/<base> origin/<head>` 和 `git rev-list --left-right --count origin/<base>...origin/<head>`。使用 `git patch-id --stable` 检查 main 之后整条链的重复补丁；没有执行 merge、rebase 或 cherry-pick。
- 业务源码审计基线为 #8 `fe0a0157c1e0d686fe06fe9eed6846abc715cd06`。#9 通过远程提交及其文档审阅；本次新分支从 #8 创建，不继承或复制 #9 的提交。
- 代码位置均指该基线的仓库相对路径。实机记录来自既有 `docs/macos-compatibility-audit.md`、`docs/macos-port-implementation.md`、CHANGELOG 和 #9 的审计文档；不是本轮重新进行的实机验收。
- `reviewDecision` 均为空，`statusCheckRollup` 均为空：没有 GitHub 上的审批决定或 CI 成功记录可作为放行依据。这不证明仓库外从未进行 ChatGPT 审查。
- 审计为定点代码检查及离线回归，不是对所有依赖或任意可信脚本的安全证明。远程分支后续变化必须重新审计。

## 2. PR 和远程分支状态

表中的冲突状态结合 GitHub 和真实祖先关系；`0/N` 表示 base 独有 0、head 独有 N 个提交。所有 head SHA 与远程分支一致。

| PR | 状态 / Draft | head → base | head 完整 SHA | 冲突 / base-only:head-only | 包含其他 PR 的提交 |
| --- | --- | --- | --- | --- | --- |
| [#1 技术方案](https://github.com/facming/orca-tools/pull/1) | Open / 是 | `docs/macos-port-plan` → `main` | `e20df278c5699fe6c1df01bed0779f2abc8ec1e8` | 无；CLEAN / 0:7 | 无 |
| [#2 Toolkit](https://github.com/facming/orca-tools/pull/2) | Open / 是 | `feature/macos-platform-runtime` → `docs/macos-port-plan` | `a2ad0c36e47e1192a97f7f4bc6cf9e6ee908131c` | 无；CLEAN / 0:8 | #1 |
| [#3 Accounts](https://github.com/facming/orca-tools/pull/3) | Open / 是 | `feature/macos-accounts` → `feature/macos-platform-runtime` | `c68b6e5c04deb97311ef07fe5c0ca6eceb353519` | 无；CLEAN / 0:1 | #1–2 |
| [#4 Downloads](https://github.com/facming/orca-tools/pull/4) | Open / 是 | `feature/macos-downloads` → `feature/macos-accounts` | `872b7508ad4d7eab0c4e4d6e73c9e7910516d454` | 无；CLEAN / 0:1 | #1–3 |
| [#5 ComfyUI](https://github.com/facming/orca-tools/pull/5) | Open / 是 | `feature/macos-gpu-comfyui` → `feature/macos-downloads` | `1441fd5986951f3b392993d6945b6b8ce370be2a` | 无；CLEAN / 0:3 | #1–4 |
| [#6 Godot](https://github.com/facming/orca-tools/pull/6) | Open / 是 | `feature/macos-godot` → `feature/macos-gpu-comfyui` | `f09aa4d8ca128a8ebdc172017c4ac22fedea5b8f` | 无；CLEAN / 0:4 | #1–5，含 #5 全部安全修复 |
| [#7 Blender](https://github.com/facming/orca-tools/pull/7) | Open / 是 | `feature/macos-blender` → `feature/macos-godot` | `402f36f11fdc6a2f975d2d9474c3606ea1894d9c` | 无；CLEAN / 0:1 | #1–6，含 Godot 必填授权修复 |
| [#8 Unity](https://github.com/facming/orca-tools/pull/8) | Open / 是 | `feature/macos-unity` → `feature/macos-blender` | `fe0a0157c1e0d686fe06fe9eed6846abc715cd06` | 无；CLEAN / 0:1 | #1–7 |
| [#9 VRoid / Tripo 文档](https://github.com/facming/orca-tools/pull/9) | Open / 是 | `research/macos-vroid-tripo-audit` → `feature/macos-unity` | `db71cd7a52b56df2be35697bddd2316333ca5bbd` | 无；CLEAN / 0:1 | #1–8；自身只增加/更新审计文档 |

`origin/main = a729805a60c475be037fffb52b6fb746cde71156`。没有既有 PR 是 Merged 或 Closed。所有功能及安全修复都**尚未进入 main**；“已传递到下游”不等于“已合并发布”。各分支继承上游提交是正常堆叠依赖，不应作为重复功能再 cherry-pick。

### PR #5 安全修复传递

| 修复 | #6 | #7 | #8 | 当前 main |
| --- | --- | --- | --- | --- |
| `b2fdd22204af25e0cecf8e73f87a7fc684267b83` | 是，祖先 | 是，祖先 | 是，祖先 | 否 |
| `1441fd5986951f3b392993d6945b6b8ce370be2a` | 是，祖先 | 是，祖先 | 是，祖先 | 否 |
| #6 授权修复 `f09aa4d8ca128a8ebdc172017c4ac22fedea5b8f` | HEAD | 是，祖先 | 是，祖先 | 否 |

两次 #5 修复标题相同，但 patch-id 不同；整条 main..#9 链无重复 patch-id。#6 的 `1372136`、`87841d1` 是 rebase 后安全防护及验证记录，不能删作“重复提交”。

不只检查祖先：比较 #5 最新提交和 #8 的 AST，`RuntimeSecurity`、`ClosingServer`、`ComfyProcess`、Token/私有路径检查、Tailscale/peer 校验、LaunchAgent 模板/控制/校验，以及 HTTP `authorized/body/send_json` 均未改变。`test_macos_security.py` 和 `test_macos_security_edges.py` 逐字不变，33 项回归在当前全集中通过。后续扩展集中在受限 native 分发、引擎 health 和 finally 清理，没有观察到这些既有安全修复被回退。新增接口仍需独立审计，不能由此推论没有新风险。

## 3. 实际 commit DAG 与最终整合路径

```text
main a729805
  └─ #1 [7 commits] e20df27
      └─ #2 [8 commits] a2ad0c3
          └─ #3 c68b6e5
              └─ #4 872b750
                  └─ #5 0cd7fcb → b2fdd22 → 1441fd5
                      └─ #6 72d1e23 → 1372136 → 87841d1 → f09aa4d
                          └─ #7 402f36f
                              └─ #8 fe0a015
                                  ├─ #9 db71cd7（文档）
                                  └─ research/macos-release-readiness（本次文档）
```

**推荐顺序：#1 → #2 → #3 → #4 → #5 → #6 → #7 → #8 → #9 → 本次 7A 文档 PR。** 先解决本报告 HIGH，再按实际修复落点更新所有受影响下游；新提交改变 DAG 后须重跑基线和安全继承检查。

### 3.1 创建方式（仅供后续人工批准，本轮未执行）

建议从核验后的 `origin/main` 创建 `feature/macos-release`，在这条专用分支保留原始提交并逐层集成。不要从旧的 ComfyUI/Godot commit 猜测基线，也不要对整条堆叠链逐 PR squash/rebase merge，否则后续 PR 的原始祖先不在目标分支中，会出现重复 diff 或需要重建堆叠。

```sh
# 仅为下一阶段方案；须先完成审查/批准并再次核验远程 SHA
git fetch origin --prune
git switch -c feature/macos-release origin/main
# 为每一步记录 before SHA；在批准后将对应 PR 的 base 改为 release，
# 逐个使用保留提交的 merge commit 策略。这里不执行合并命令。
```

现有 PR base 是上游功能分支，**不能按现有 base 点 Merge 并以为已经进入 release**。推荐每步将待整合 PR retarget 到 release，重新查看 diff/冲突/测试并人工批准后合并。仓库当前允许 merge commit，且不会自动删除源分支；审计没有改变这些设置。main 保持不动，最终 release → main 另开 PR，不属于 7A。

从 #8 直接创建 release 在树内容上也能包含 #1–8，但会绕过逐 PR 集成审查和独立回滚点，故不作为首选。若另行批准采用这种方式，必须以完整 #8 SHA 为起点，并在流程上分别验收 #1–8，不能声称这些 Draft PR 已合并。

### 3.2 每个 PR 的动作与回滚

“可直接”仅表示当前 DAG 无需整理，**不是授权现在合并或发布**。

| 顺序 | PR | 当前技术动作 | 基线 / rebase / 冲突 | 回滚范围 |
| --- | --- | --- | --- | --- |
| 1 | #1 | 可直接进入 release | 无需同步/rebase；retarget 后重查 | 回退其文档集成 commit |
| 2 | #2 | 可直接；H-02 修复后才可放行相关设置路径 | 当前无冲突；修复须向 #3–8 传递 | 回退 Toolkit 集成 commit；上层若已集成，先逆序撤销 |
| 3 | #3 | 可直接 | 当前无需同步/rebase | 回退 Accounts 集成 commit；运行状态另行人工核对 |
| 4 | #4 | DAG 可直接；H-03 未解决不能放行 | 修复后同步 #5–8 | 回退 Downloads 集成 commit；先暂停任务、确认临时远程授权已清理 |
| 5 | #5 | DAG 可直接；保留全部 3 commits；H-01 须修复 | 不重做已完成的安全 rebase；修复后同步 #6–8 | 回退 ComfyUI 集成 commit；先停止已部署的受管服务，保留配置/任务 |
| 6 | #6 | 可直接，必须保留 f09aa4d | 当前无 rebase/代码冲突 | 回退 Godot 集成 commit，先停止受管 Godot |
| 7 | #7 | 可直接 | 当前无 rebase/代码冲突 | 回退 Blender 集成 commit，先保存并停止受管 Blender |
| 8 | #8 | DAG 可直接；真实 C# / Metal 尚未验收 | 当前无 rebase/代码冲突 | 回退 Unity 集成 commit；先保存并停止 Editor，项目副本恢复另行批准 |
| 9 | #9 | 只整合文档，不启用 VRoid/Tripo | 相对 #8 可直接；与本次 PR 是兄弟分支 | 只撤销文档集成 commit |
| 10 | 7A PR | 最后整合本报告及记录 | #9 和本次都在 CHANGELOG 顶部插入内容，需人工保留两段；其他上下文也重新检查 | 只撤销审计文档集成 commit |

每步合并前记录 `before_i` 和合并 commit `M_i`。已共享分支出现问题时，用 **`git revert -m 1 M_i` 新建回滚提交**，不要 force-push/reset 共享历史。下游已依赖时按 #8→#2 逆序撤销或整体回到最后已验收的内容；不可只删掉底层留下依赖。冲突处理在独立工作分支进行；未提交的冲突可 `git merge --abort` 回到该步开始状态。所有这些命令均为未来方案。

当前 #1–9 **没有必须 rebase 的 PR，也没有已确认的代码冲突**。未来若 main 或上游变更，优先用保留提交的同步方式更新下游；只有审查明确要求线性历史且协调所有下游时才采用 `rebase --onto`，记录旧/新 SHA 并 `--force-with-lease`，随后重验全部祖先和安全测试。不能预先宣称未来冲突不存在。回滚 Git 不会回滚账号选择、SSH 授权、项目编辑或 LaunchAgent，必须采用对应运行态恢复流程。

## 4. 架构完整性

### 4.1 Toolkit

| 检查 | 结论与源码证据 |
| --- | --- |
| 统一 runtime | `platform/index.cjs` 统一构造 macos/windows adapter；入口 `main.mjs`、Controller、Accounts、Downloads 使用同一 runtime。Mac 从运行宿主核验 CLI/SDK/profile；不读取 OAuth 文件。 |
| 初始化 | `controller.cjs:initialize/configureDownloads/readAccounts`：Accounts 被动访问不创建 RuntimeClient/RPC；Downloads 本地依赖检查和恢复不自动远程传输；Mac GPU 只读 health，不因 activation 启动引擎。 |
| 数据目录 | Toolkit 使用 `~/Library/Application Support/OrcaTools`，Downloads 在其 downloads 子目录；Bridge 使用独立 `LocalGpuBridge`，logs/cache 各归 Library 标准目录。两个产品目录是刻意分工，未发现混用 Windows LOCALAPPDATA。 |
| 状态 | Accounts ready 是最近成功快照；Downloads configured/ready/offline 区分本地配置与远程请求；GPU ready 仍以 ComfyUI 六项为准，原生引擎有独立状态。Controller 总览仅携带 Godot 摘要，Blender/Unity 由 GPU health 面板读取，见 M-02。 |
| Windows | 保留 windows adapter、原原生控制及 Job Objects/Firewall 路径；共享 MCP schema、Blender 参数验证和安装清单有变化，不能说 Windows 完全未改。现有 Mock 回归通过；原生 Windows 跳过不算通过。 |
| 插件停用 | `main.mjs:deactivate` → `server.cjs:close` 关闭 picker、等待 Downloads.close、关闭 HTTP。独立 LaunchAgent/引擎不归插件进程所有，不会随 Toolkit 停用而停止。Accounts 在途 RPC 没有显式取消/客户端销毁接口；不保证所有资源瞬时释放，见 M-03。 |
| 私有设置 | Accounts 偏好、Downloads job/key/partial 已有更严格策略，但通用 settings/downloads-preferences 未统一到同级别实现，见 H-02。 |

### 4.2 Python Bridge 与四引擎

- `Bridge` 管理 ComfyUI jobs；`bridge_platform/macos_native.py:NativeControl` 聚合独立 Godot/Blender/Unity control/store/锁/进程。单个存储初始化失败降级为该引擎 unavailable，不阻断其他引擎；native.close 使用嵌套 finally 尝试全部清理，service.finally 最后仍停止 ComfyUI。
- `/v1/native/jobs` 先校验 engine 并拒绝同一显式 request_id 跨引擎复用；查询 owner 必须唯一，否则拒绝歧义。各引擎校验同 ID 指纹。Unity `/v1/unity/start|call` 自行生成 UUID，不接受调用方 request_id；当前没有可复现的确定性 ID 冲突，但需要认清不是统一的全局幂等接口。
- ComfyUI 提交未确认保留 unknown、不自动重投；原生启动时未终结任务改 failed（Unity 另标 outcome_unknown），不接管存盘 PID；Downloads 重启暂停。不同状态模型符合不同执行语义，不能把 failed 一律当作“未发生副作用”。
- 普通 ComfyUI 离线、某引擎异常或损坏任务库可独立反映；**共享安全面不是故障隔离域**：Tailscale/config/token 或 ComfyUI 监听所有权异常会撤销整个服务并关闭全部受管引擎，这是主动的失败关闭。GPU 内存耗尽、系统进程限制及授权脚本也没有跨引擎硬隔离保证。
- Godot 默认总任务上限 120 秒；Unity start 默认总会话上限 300 秒（含导入），编辑默认 60 秒；Blender 空闲会话保留至关闭，执行任务有自己的超时。Unity 300 秒是已有明确设计，不在本报告误记为新 bug；MCP 未暴露调整入口，见 M-01。
- 正常退出、取消、安全撤销使用当前内存 Popen 的 session 进程组 TERM/KILL；SIGKILL/断电或脱离 session 的子孙进程仍可能遗留。不得用磁盘 PID 自动清理。Downloads SSH abort 目前只有 TERM，无超时 KILL 升级，见 M-03。

### 4.3 MCP 协议完整性

| 模块 | 已连接的工具/协议 | 授权与限制 |
| --- | --- | --- |
| ComfyUI | `gpu_status/list_workflows/run_workflow/list_jobs/job_status/cancel_pending_job/upload_input/fetch_artifact` | 名称及 request_id 协议保留；只能取消排队任务，不提供全局 interrupt |
| Godot | `godot_operation/godot_stop/godot_cancel/godot_logs/capture_scene/run_project_script` + 通用 status/wait/artifact | 执行工具要求 boolean authorized + 本地 enabled/allow_scripts；缺失/false 在 Mac 后端也拒绝 |
| Blender | `blender_operation/blender_tasks/cancel_blender_task` + 通用 status/wait/artifact | execute_code/run_script（包括 batch 内）必须 authorized=true + allow_scripts；普通白名单操作不需要该授权，不应误写成所有工具都相同 |
| Unity | `unity_start/unity_stop/unity_tools/unity_call` + 通用 status/wait/artifact | 四个工具全部要求 authorized；Mac 返回异步任务；必须从当前 unity_tools 取得操作名，不能沿用 Windows 动态 Pipeline 名称 |
| VRoid / Tripo | 共用 VPS 客户端仍有 Windows catalog | Mac restricted_native 路由不放行；Tripo 配置直接拒绝。不得在发布配置启用这两个 MCP group |

`plugin/client.py` 的 NATIVE_COMMON 仍暴露 Mac 不支持的项目文件/Git/sync 等入口，Mac 返回 404/unsupported；没有按平台过滤的能力协商。已经实现但 MCP 未完整开放的包括 native 通用 cancel/logs（只有 godot_* 命名和 Blender 专用取消；Unity 只能借用通用 HTTP 或停止会话）、Unity start 的 timeout_seconds，以及 Unity task 的显式 request_id。见 M-01。

HTTP 远程鉴权统一为 403 固定消息；native unsupported 为 404 + code，native 参数错误为 400 固定消息；ComfyUI 的 400 没有同样的 machine-readable code。MCP 客户端把 HTTP 错误转为文本 ValueError/isError，会丢失 native code；并非完整统一错误协议。

产物统一采用 job/index + `fetch[_native]_artifact`，客户端独占 partial、校验 Content-Length、计算 SHA-256、硬链接排他发布并避让重名。原生 adapter 在服务端复核已登记 SHA-256 和链接；客户端计算的 receipt hash 不等于独立比对服务端宣告 hash。ComfyUI 取后端产物，原生产物多为 32 MiB 上限，客户端整体上限 2 GiB；这是不同的限制，文档/UI应说明。MCP 下载目标是 VPS 文件系统，Toolkit Downloads 目标是 Mac，不能混为一个下载通道。

## 5. 本轮测试与真实验收矩阵

执行宿主：macOS 15.7.5 arm64；使用临时项目、合成 Mach-O/PNG/账号、Mock SDK/Popen/Tailscale/ifconfig/lsof/launchctl/信号与真实回环 HTTP。宿主是 Mac **不意味着真实 GPU、应用或 VPS 通过**。C# 只进行源码相关夹具检查，没有真实编译。

| 模块 | Mock / 本地离线测试 | 真实 Mac 测试 | VPS 联调 |
| --- | --- | --- | --- |
| Accounts | 本轮 Toolkit 全集通过；覆盖被动无 RPC、身份、用量归属、遮罩、切换并发/超时、偏好权限及 UI | 仅历史 Toolkit 管理页/目录选择记录；真实账号读取、切换均未验收 | 未执行/未有通过记录 |
| Downloads | 本轮全集及 Python reader 通过；合成并行传输/hash/续传、实际临时文件发布/锁/子进程恢复；SSH/keygen 为 Mock | 历史目录选择保存/取消已人工通过；本地文件测试不是实际下载。真实 TCC、Finder 传输链未验收 | 未执行；SDK 回包、真实 host pin 及清理未验收 |
| ComfyUI | Mac bridge 23 + security 21 + edges 12 项纳入全集并通过；其他共用任务测试亦通过 | 未启动真实 ComfyUI/MPS/CPU workflow；LaunchAgent 为 Mock | 未执行 |
| Godot | Mac 专项 32 + MCP schema 5 项通过（全集内） | 未运行真实 Godot/场景/截图/信号 | 未执行 |
| Blender | Mac 专项 30 项通过（全集内）；另含共用 worker/Metal 合成测试 | 未运行真实 Blender/Metal/CPU/GUI/VRM 扩展 | 未执行 |
| Unity | Mac 专项 34 项通过（全集内）；另含原 Windows Mock | 未编译 C#；未验证 Editor API、许可、Metal、domain reload | 未执行 |

### 5.1 本轮实际执行命令与结果

从仓库根目录运行：

```sh
node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_*.py'
python3 plugins/toolkit/modules/downloads/test_remote.py
python3 -m unittest discover -s scripts -p 'test_*.py'
git diff --check
python3 scripts/check.py
```

- Toolkit：82 项，81 通过，0 失败，1 项 Windows 原生探针跳过。
- Python Bridge：298 项，295 通过，0 失败，3 项 Windows 专属跳过；41.848 秒。已有 malformed-workflow 夹具的 JSONDecodeError 日志不代表测试失败。
- 下载 reader：两组 PASS（精确范围/命令拒绝/源变化/限定清理；隔离注册/forced-command/hash/权限）。只在临时 HOME 操作合成 authorized_keys，不生成真实 SSH 密钥。
- scripts：6 项，3 通过、3 项 Windows 安装测试跳过；未运行真实安装器安装动作。
- diff/check 与敏感检查结果见第 10 节。额外 3 个审计复现**证实存在缺陷**，不能计入“安全通过”；没有将其写入业务或测试源码。

便于后续修复后定向回归的命令（本轮已经包含在上述全集；不要重复计数）：

```sh
node --test plugins/toolkit/test-accounts.cjs plugins/toolkit/test-macos-accounts.cjs
node --test plugins/toolkit/modules/downloads/test*.cjs
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_macos_bridge.py'
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_macos_security*.py'
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_macos_godot.py'
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_macos_blender.py'
python3 -m unittest discover -s services/local-gpu-bridge -p 'test_macos_unity.py'
```

### 5.2 人工验收缺口与恢复步骤（未执行）

执行以下步骤必须在 ChatGPT 审查、人工批准后，在专用测试账户/项目副本进行。优先修复 HIGH 后再向真实 VPS 下发客户端。不得把生产 Token 放入命令行、截图或审计记录。

| 模块 | 待执行动作 / 工具或命令 | 人工事项与通过标准 | 失败恢复 |
| --- | --- | --- | --- |
| Accounts | Toolkit 点击 Refresh usage；明确选择另一个账号；新建远程 Claude 会话验证 | 人工配对并指定测试 environment/两个账号；过期/未知用量如实显示；新会话使用目标账号；已有会话不迁移 | 停止重复切换；在远程官方界面核对当前账号，确认后手动恢复原账号；不用超时自动重试 |
| Downloads | Toolkit 下载合成大文件、Pause/Resume/Cancel/Clear access；`shasum -a 256 <测试文件>` 比较两端 | 人工选择测试目录、确认 SSH host key；验证大/小文件、中文路径、磁盘不足、TCC 拒绝、断网、重启、目标重名、授权清理 | Pause；保留 partial/job；先修复容量/权限再 Resume；源变化新建任务；清理失败保留本机任务密钥并核实撤销，禁止直接删 key 掩盖远程残留 |
| ComfyUI | 审阅私有 settings 后预览 `python3 scripts/install-local-gpu-macos.py install --settings <私有设置文件>`；批准后另执行确认安装；管理页 Start；MCP list/run/status/cancel/fetch | 人工选可信环境/模型、核验 MPS 与显式 CPU fallback；实际 Tailscale 在线/离线/IP 变化、错 Token/peer、长连接撤销、轮换及恢复；`lsof -nP -iTCP:8188 -sTCP:LISTEN` 核对回环 | 管理页停止；`python3 scripts/install-local-gpu-macos.py stop`；保留配置/任务/模型；核对安全错误后显式 start，不换公网监听地址 |
| Godot | MCP `godot_operation` 的 import/open/run、capture_scene、run_project_script、godot_cancel/logs、fetch_native_artifact | 人工选可信 Godot 4.5+ stable major 4 arm64 和副本、设置 allow_scripts；逐请求真实授权；图形/PNG/超时/取消、外部实例无影响 | 停止受管任务，未知结果先检查项目；不自动重投；不按历史 PID 杀进程；人工核验后恢复副本 |
| Blender | blender_operation 连续 create/transform/material/render/save/export、batch、viewport_capture、受权脚本、cancel_blender_task | 人工选择实际版本及 Metal/CPU，核验 GLB/OBJ 回读；VRM 缺扩展应 unsupported；不自动安装扩展；检查私有 Preferences 和其他引擎 | 先保存新文件，停止受管会话；检查 partial_success/已发布文件；不删除未跟踪产物或覆盖已有用户文件 |
| Unity | 人工在副本安装已审查 OrcaUnityBridge.cs 并编译；MCP unity_start/tools/call/stop、native_job_status、fetch_native_artifact | 精确 Unity 6000.0 版本、许可、Builtin/URP/HDRP 17.0.x、Metal、Play/domain reload、Game View 异步 PNG、Scene 暂存与 .meta、外部 UnityLockfile；在 300 秒会话截止前保存 | 停止受管 Editor；unknown 不重试编辑；检查暂存 Scene/.meta 和项目修改，再从副本恢复；不删外部锁、不接管历史 PID |

公共 Mac 验收还缺：真实 LaunchAgent 登录时网络未就绪、bootstrap/kickstart/bootout、安装残留/卸载保留数据；TCC Files and Folders、Screen Recording（如实际引擎功能需要）、Gatekeeper；四引擎并发和单引擎失败；SIGTERM/SIGKILL 后遗留进程人工识别。由用户在系统界面处理权限，不自动授予 Full Disk Access、关闭 Gatekeeper 或安装系统扩展。

Windows 原生能力本轮未测试；历史阶段 1 明确排除了 Windows 实机。若最终发布同时承诺 Windows 不回归，应补充共享 schema/worker/安装清单变更的 Windows 验证，或明确这次发布验收范围，不把跳过记录改写为通过。Intel/Rosetta 也不在已支持范围。

## 6. 安全审计

| 安全面 | 当前实现 / 证据 | 结论、限制 |
| --- | --- | --- |
| 监听地址 | Toolkit `127.0.0.1:18774`；管理 `127.0.0.1:18773`；ComfyUI `127.0.0.1:8188`；Bridge 经核验的 Tailscale IPv4 `:18772`。`server.cjs`、`macos_service.py`、`macos.py` | 没有 Mac wildcard/LAN/IPv6 回退；ComfyUI lsof 检查监听地址及持有 PID。Godot/Blender/Unity adapter 不新增远程 HTTP；Unity/Blender 使用私有 spool。可信引擎/脚本自行开端口不受此保证约束 |
| Tailscale | Self ID/IP/Running/Online、唯一 UP/RUNNING utun；绑定后重验；请求和后台监测 | 离线/变化/探测失败锁定撤销并关闭监听及已接受连接，恢复须显式重启。后台间隔 1 秒另加命令超时（各 4 秒），不是内核原子撤销，已发送数据不可追回 |
| Bearer 生命周期 | 随机 64 hex，文件 0600/属主/父目录检查，逐请求读取且慢探测/请求体后重验 | 可原子轮换，旧值下一次校验失效；client export 不自动更新，需私密同步。没有自动 TTL；有效本机 admin session 独立于 Bearer。VPS HTTP redirect 存在 H-01 |
| peer allowlist | IP + 稳定节点 ID + 在线；不信 X-Forwarded-For | 已有 Mock 反例；仍待真实 tailnet 联调。所有获准 peer 共用凭据权限，不是按工具细分角色 |
| 管理页 | 仅回环、严格 Host、session token、POST Origin、CSP、固定路由；远程 Bearer 不嵌入 HTML | 是本机访问边界，不是本地 OS 用户鉴权：能访问回环页面的本机进程可取得 session。需可信本机用户/受控测试账户 |
| SSH pinning | 每任务 known_hosts/HostKeyAlias；StrictHostKeyChecking=yes、-F /dev/null、IdentityAgent=none、禁转发；pin 从 Orca 通道取得 | 同任务 key 变化拒绝；须真实核验 Orca 身份/回包。清理确认后才删本机 key，但远端 authorized_keys 并发更新存在 H-03 |
| OAuth/账号 | 被动无 RPC，返回邮箱遮罩/用量/ID；远程凭据不复制；本地偏好 0700/0600 | 未读取凭据；真实 RPC 及 SDK 副作用待授权验收。通用设置文件保护弱于专用偏好，见 H-02 |
| 项目路径 | native root canonical + inode、相对路径/符号链接拒绝、openat/O_NOFOLLOW、单硬链接和大小校验；Downloads 项目根/目录类型检查 | 约束 Bridge 文件接口；不约束有用户权限的已授权脚本。不能将路径预检当作执行沙箱 |
| 文件覆盖/产物 | native 暂存 + 排他发布；已有目标不覆盖；原生 hash 复验；客户端 partial/硬链接避让 | H-02 是设置路径的例外。VPS fetch 允许用户指定任意绝对目标目录，并不具有 Mac 项目根约束；只应用在受信 VPS 账户/目录 |
| 子进程环境 | native 固定系统 PATH、私有 HOME/TMPDIR；ComfyUI 启动只继承少量环境、禁 custom/API nodes | 无 shell；ComfyUI dependency probe 使用 -I 但未显式传入相同 env 白名单，见 M-04。可信 Python/应用包仍可执行用户权限代码 |
| 取消/进程回收 | 内存 Popen、独立 session、TERM/KILL、正常退出 finally；不接管旧 PID | 不是 Job Objects。脱离 session、SIGKILL/断电及 Downloads TERM 无升级限制需人工恢复；不宣称全部后代必被回收 |
| LaunchAgent | 当前普通用户 GUI domain、固定参数数组、-I、umask 077、完整模板核验、KeepAlive=false | 无 root daemon；不自动重装/覆盖残留；卸载只删自有注册/launcher并保留数据。真实行为未验收 |
| 脚本授权 | Godot 全项目执行/Unity 四接口：本地 allow_scripts + authorized=true；Blender Python/batch 脚本同样要求 | `authorized` 是调用方传入的显式声明，不是独立系统授权弹窗；代理流程仍须取得用户真实许可。共享 Windows MCP 必填字段变更需迁移 |
| 日志脱敏 | Mac HTTP 不打印请求；固定异常消息；原始引擎 stdout/stderr 丢弃；remote_privacy 过滤 JSON/路径；Unity Console 固定过滤 | 不保证图片/产物内容无秘密；账户 ID/部分邮箱域名仍属于可见数据，测试记录应合成化。没有读取本机真实应用日志来证明生产脱敏 |

**macOS 没有本项目中 Windows Job Objects 和 Defender Firewall 的完全等价实现。** POSIX 进程组、最小环境和路径校验不提供等价进程树约束或出站网络隔离；PF 也不是按应用路径控制的直接等价物。本次不启用 PF，不新增防火墙/系统扩展，不显示 Network privacy ready。仅允许可信项目；若产品需求必须约束不可信代码，此架构不能满足发布条件。

## 7. 阻塞项与风险登记

### BLOCKER：发布必须先解决

**B-01：真实验收证据不足。** 六模块真实业务/VPS 链路没有通过记录，Unity 固定扩展尚未真实编译，真实 Metal/MPS/TCC/LaunchAgent/进程行为未验证。关闭标准：完成第 5.2 节矩阵，记录准确版本、测试项目、预期/实际、恢复验证及人工签字；发布宣称严格匹配已验收范围。历史 Toolkit 管理页/目录选择通过不能覆盖这些缺口。

### HIGH：发布前必须解决

**H-01：MCP 客户端跨 origin 重定向会转发 Bearer。**

- 位置：`services/local-gpu-bridge/plugin/client.py:Client.__init__/request`（约 141–171 行）。仅首次 config URL 验证 Tailscale/loopback；`build_opener(ProxyHandler({}))` 保留默认 redirect handler，Authorization 是普通请求 header。
- 临时双回环服务复现：A 返回 302 到不同端口 B，使用合成 64 字符 credential 的 `Client.request('/v1/status')`；结果 `Different-origin redirect accepted: True; Synthetic bearer forwarded: True`。未输出 credential、未接触真实 token。
- 影响条件：获准端点或其响应链被劫持/替换并发出重定向。现有 Bridge 正常路由不主动 redirect，但客户端没有维持“仅向配置 origin 发凭据”的边界；可绕过首次 URL 限制。不是证明当前真实凭据已泄露。
- 修复要求：默认拒绝 redirect，或逐跳验证同 scheme/host/port 且绝不向其他 origin 转发授权；覆盖 GET/POST/stream、降级和多跳回归。修复后重新验证 ComfyUI/native 下载与错误返回。Windows 共用客户端同样受影响。

**H-02：Toolkit 通用设置临时文件可跟随符号链接覆盖其他文件。**

- 位置：`plugins/toolkit/settings.cjs:save`、`plugins/toolkit/download-preferences.cjs:write`。固定 `<file>.tmp` 用 `writeFileSync` 打开，没有独占/O_NOFOLLOW、目标身份或父目录链接校验；read 也不采取 AccountPreferences 同级保护。
- 临时目录复现：为 `settings.json.tmp` 和 `downloads-preferences.json.tmp` 各建一条指向合成“保留文件”的 symlink，然后分别调用 settings.save/defaults 和 rememberFolder。两次目标均被截断改写，rename 后最终设置文件仍为 symlink。
- 影响条件：同用户进程、损坏/恢复数据或不安全父路径事先布置该链接；0700 降低其他用户攻击面，但不满足本项目“拒绝链接、不覆盖非自有文件”的约定。没有声称这是跨用户提权。
- 修复要求：复用专用偏好的属主/权限/父路径检查、随机独占临时文件、链接拒绝和替换前身份验证；覆盖两个文件及损坏/外部改动/失败保留测试。此项并非 Accounts 偏好文件漏洞。

**H-03：下载远端授权清理未串行化，可能覆盖并发 authorized_keys 更新。**

- 位置：`plugins/toolkit/modules/downloads/remote-reader.py:register/main(done)` 及 `transport.cjs:cleanupViaOrca`。注册 append 与清理 read/filter/temp.replace，没有共同锁；两个清理也可能以旧快照互相覆盖。
- 隔离 HOME 复现：done 读取合成 authorized_keys 后、替换前插入一条模拟其他任务/人工新增的无关行；命令仍输出 `KEY_REMOVED`，新行丢失。只操作临时 HOME，不读取或写入真实 SSH 文件。
- 多任务完成/注册/清理并发是实际可达流程。可能删除无关新增授权、恢复另一任务已删行，破坏“只清理自己的授权”的承诺；恢复的行是否仍可下载还取决于任务元数据，不应直接等同已恢复数据访问。
- 修复要求：注册、SSH done 和 Orca fallback 使用一致的锁及原子更新协议；保护远端文件/临时路径免于链接和意外覆盖；测试两次 cleanup、register+cleanup 和失败重试的交错，确认无关行保留。只有收到正确清理回执且状态一致才删本机任务 key。

以上 3 项均在 #8 实际代码上验证，本轮没有修复提交。H-01/H-03 涉及已有共享逻辑，也不能因不是本轮新增就从最终发布风险中排除。

### MEDIUM：仅可在明确受控条件下延期

| ID | 问题 / 证据 | 延期条件与后续工作 |
| --- | --- | --- |
| M-01 | MCP 能力目录/错误码/取消接口未统一；Unity timeout/request_id 不完整开放；Godot 命名的 cancel/logs 实际按 owner 路由，可作用于其他 native 引擎 ID | 人工固定工具集、按 engine 检查 job ID；禁用 Mac 不支持的 catalog/group；文档说明 300 秒上限；后续增能力协商、明确通用取消和幂等语义，不能要求用户借用误导名称 |
| M-02 | Controller GPU 总览用 ComfyUI online，可能四引擎中仅 Godot/Blender/Unity运行却显示 GPU offline；总览原生摘要只含 Godot | 受控使用专用四引擎 health 面板判断，不把总览当全体健康；后续统一聚合/部分可用状态 |
| M-03 | Controller.close 未取消 Accounts 在途 RPC；Downloads SSH abort 仅 TERM，close 等待 pauseAll 可能受未退出 child 影响 | 受控测试不在挂起 RPC/SSH 期间强行卸载；人工处理异常子进程，保留任务；补充有界关闭/升级终止及端口释放回归，不把进程组宣传为 Job Objects |
| M-04 | `macos.py:probe_python` 执行可信 Python 依赖探针使用 -I，但未传 env 白名单；与实际 ComfyUI/native 启动环境约定不一致 | 仅在干净专用账户与已审阅环境进行安装；统一 probe env 并加合成敏感变量测试。-I 不是清除全部进程环境 |
| M-05 | 安装器拒绝覆盖旧安装，升级/回滚需要人工；Unity Scene 暂存/.meta保留；私有 HOME 的实际许可/Preferences影响未验收 | 不推广自动升级；先在副本上完成备份、版本核对、停止/恢复演练；不自动删除用户残留 |

### LOW：文档或体验

- L-01：部分阶段文案仍为 “Only ComfyUI” 或只提 Godot；UI/错误提示应同步为当前四引擎能力，同时保留阶段历史上下文。
- L-02：需明确 native 32 MiB、客户端 2 GiB 和不同 PNG/格式限制；固定日志有意丢弃原始输出，应给出受控本机诊断指引，不将应用完整日志发远程。

## 8. 发布前工作清单

- [ ] ChatGPT 审查本报告，人工确认发现项、风险接受范围和下一阶段任务；7A 不代表批准 7B。
- [ ] 为 H-01/H-02/H-03 分别完成修复、独立回归和审查；不要在本次文档 PR 混入修复。
- [ ] 对所有修复同步下游，重新取 PR/分支 SHA、祖先/patch-id、安全 AST/回归证据；保持 #5 两次安全修复及 #6 授权修复完整。
- [ ] 完成六模块真实 Mac + VPS 矩阵，尤其 Unity C# 编译/Editor 交互及 GPU 后端；写入实际通过/失败/未测，不填写推定通过。
- [ ] 人工确定 M-01–M-05 的修复或受控延期；明确共享 Windows MCP 必填授权迁移，不宣称旧调用无需调整。
- [ ] 从核验 main 创建 release，逐层 retarget/审阅/批准集成；保留每步回滚 SHA，解决 #9/7A 文档交叠。
- [ ] 仅保留 VRoid/Tripo 审计文档和 Mac unsupported 防线；不注册这两个 Mac MCP group、不部署登录/profile/注入代码。
- [ ] 复验最终 release tree 的 diff/check/敏感扫描及全套离线回归；检查没有私有配置、真实用户路径或凭据进入 Git。
- [ ] 在单独批准的实机步骤检查 token 分发/轮换、peer/host pin、日志和服务生命周期；不借移植启用 PF 或系统扩展。
- [ ] 发布文档写明 arm64/版本范围、非沙箱/非出站隔离、300 秒 Unity 会话上限、故障恢复和安装升级限制。
- [ ] 最终 release → main PR 单独审查、人工批准；本阶段所有既有 PR 保持 Draft，不合并。

## 9. 本次文档分支与 #9 的关系

本次 `research/macos-release-readiness` 从 `origin/feature/macos-unity` 的完整 SHA 创建，Draft PR base 为 `feature/macos-unity`。仅变更本报告、实施文档和 CHANGELOG 三个文件，避免重复引入 #9。#9 的 `docs/macos-vroid-tripo-audit.md` 当前尚不在本分支，最终按第 3 节另行整合；可在 [#9 提交的审计文档](https://github.com/facming/orca-tools/blob/db71cd7a52b56df2be35697bddd2316333ca5bbd/docs/macos-vroid-tripo-audit.md) 审阅。其“审计优先、不开放功能”结论保持。

## 10. 提交前检查记录

提交说明：`docs(macos): audit release integration readiness`。检查最终三文件 diff：无业务源码变更。

| 检查 | 本轮结果 |
| --- | --- |
| `git diff --check`（并对暂存的新报告执行 `git diff --cached --check`） | PASS |
| `python3 scripts/check.py` | PASS，182 个源码/文档文件，无私有运行态配置或已识别凭据 |
| 独立敏感模式扫描 | 181 个既有跟踪文件 + 本次 3 个新增/修改文档检查，0 命中；新增报告亦由 check.py 全量扫描 |
| 变更白名单 | 仅本报告、`docs/macos-port-implementation.md`、`CHANGELOG.md` |

扫描只报告文件/类别和数量，不打印命中内容；不扫描用户 Keychain、浏览器 profile 或 SSH 目录。规则包括已知凭据格式、私钥边界、真实本机用户路径、可疑长 token 赋值及运行态私有配置路径；测试中合成值不当作真实泄露，也不把规则未命中当作不存在任何秘密的证明。

本轮审计可提交，发布不可放行。所有合并、系统配置、实机任务和源码修复均留在后续明确授权阶段。

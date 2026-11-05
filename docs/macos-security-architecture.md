# macOS 安全架构重设计：H-02 / H-03

状态：**7B-3 已有可运行实现与隔离测试，Draft / 待独立审查及人工批准。下文设计与第一步记录保留；最新验收见第 7 节。**

基线：`facming/orca-tools` PR #11，`feature/macos-release-hardening`，HEAD `67cac0913f0223e3e84569e982d369f39d4fc6ce`。设计分支：`feature/macos-security-architecture`；Draft PR base 为 `feature/macos-release-hardening`。PR #11 保持原样，**H-02、H-03 两个 HIGH 均未关闭，仍禁止合并及发布**。本文不改变既有审计结论。

本文推荐：H-02 使用**隔离服务身份的单写入服务 + SQLite 事务**；H-03 使用**专用下载账户 + 单一授权代理 + 独立 SSH 入口**。纯同 UID SQLite、同 UID 后台进程、更多最终检查均不能满足“任意同 UID 非合作写入者不能覆盖有效状态”的要求。若不能批准身份隔离和安装条件，则保留阻塞，不能以弱化威胁模型宣称关闭 HIGH。

## 1. 范围、基线事实与威胁模型

### 1.1 已核实的现状

代码依据均来自上述 HEAD：

| 位置 | 当前行为及设计影响 |
| --- | --- |
| `plugins/toolkit/private_files.py` | `flock`、文件身份/hash 检查、`fsync`、`renameat`；最后比较与替换不是原子 CAS。临时文件安全不等于非合作更新安全。 |
| `plugins/toolkit/settings.cjs` | macOS 经 `private-json.cjs` 读写；版本保存在进程内 Map/WeakMap；`validate()` 拒绝未知节及字段，返回 defaults 合并对象，不能直接用作无损迁移器。 |
| `plugins/toolkit/download-preferences.cjs` | folder 和 remoteLocations 先读后写；顶层展开保留部分未知字段，但 `location()` 只提取 source/directory，重写单环境条目可能丢失未知字段。 |
| `plugins/toolkit/controller.cjs` | 每个 Controller 的 `loading` 只保护自身；整对象保存；远端目标切换前检查本机任务。跨 Controller 的版本及目标切换约束需要服务端协调。 |
| `plugins/toolkit/modules/downloads/remote-reader.py` | register/cleanup 改写个人 `authorized_keys`；精确授权行和 tombstone 不能消除非合作编辑器的最后窗口。 |
| `plugins/toolkit/modules/downloads/transport.cjs` | SSH done 比较 `KEY_REMOVED:<id>`，Orca fallback 在终端 tail 中寻找带 nonce 标记；之后删除本机 key。这些标记不是持久撤销的独立可信证明。 |

现有 `reproduce-unresolved-races.py` 使用临时目录复现两个竞态，退出 1 是发布阻塞证据。不得改成 skip、expectedFailure 或通过测试。本文不更改上述文件。

### 1.2 对手、信任域与承诺

| 主体/事件 | 范围 | 承诺或限制 |
| --- | --- | --- |
| 两个正常 Controller、旧版本 UI、后台偏好更新 | 范围内 | 无关字段不丢失；冲突可见；请求重试幂等；不会使用过期整对象覆盖。 |
| 与桌面用户同 UID 的编辑器/脚本，忽略应用锁，直接写、截断、rename、换目录、放 symlink/hardlink | 范围内 | 不能写入隔离服务拥有的权威配置；旧 JSON 编辑不覆盖新数据库；不是依靠它们合作。 |
| VPS workspace 用户、人工 SSH 编辑器、并发下载客户端 | 范围内 | 可编辑自己的个人 SSH 文件，但新下载协议不打开、不改写该文件；不能写授权代理数据库或更换受管程序。 |
| IPC 伪造、跨用户/跨任务请求、stdout 注入、回执重放 | 范围内 | 操作身份、授权范围、任务绑定、消息大小与版本都在可信端验证，失败不删本机私钥。 |
| 进程被杀、请求/回复丢失、重启、ENOSPC/EIO、备份恢复 | 范围内 | 明确提交点；不把不确定结果报为成功；恢复不复活已撤销任务；保留原件及核查材料。 |
| root/管理员恶意修改、内核或文件系统失信、服务 UID/签名程序被攻陷 | 信任边界外 | 它们能绕过 DAC、修改状态或签名回执；不宣称绝对防篡改或不可回滚。管理员恢复须执行隔离流程。 |
| 任意同 UID 进程注入已授权客户端、窃取现有任务私钥 | 残余风险 | 不能据此证明“用户真实意图”；任务 key 只限该任务下载，不提供 shell。服务仍强制字段、版本、文件范围及撤销规则。 |

这里的“同 UID”是原桌面用户或 VPS workspace 用户，不是新隔离服务身份。若要求防御拥有服务 UID 的任意进程，还需要另一信任域；把服务也放在该 UID 下不构成隔离。0700/0600 只隔离其他身份，同 UID 进程可 chmod 自己的文件。文件 hash、watcher、mtime、`PRAGMA data_version` 都不是防任意恶意写入的原子安全边界。

SQLite 的 Unix 锁本身也属于合作式锁；绕过 SQLite 改写文件可破坏数据库及日志，不能以事务替代权限隔离。[SQLite 损坏原因](https://www.sqlite.org/howtocorrupt.html)

## 2. H-02：方案比较与选择

| 方案 | 现有语义适配 | 非合作写入边界 | 崩溃/运维/权限成本 | 结论 |
| --- | --- | --- | --- | --- |
| A. 同桌面 UID 的 SQLite 单写事务 | 支持版本、字段合并、跨文档事务；需要替换读写 API | 合法 SQLite 连接之间隔离；同 UID 原生写可绕开锁，损坏 DB/journal/WAL/SHM | 低部署成本；需 SQLite 备份及恢复 | **单独不够**；可作为存储引擎，不能关闭当前 H-02。 |
| B. 单写入服务 | 请求中携带 base revision 和字段 patch；所有读取亦走 IPC | 同 UID daemon 仍不够；专用非登录 UID + 不可写父目录形成 DAC 边界 | 安装需管理员批准；需身份认证、版本协商、生命周期、恢复及限流 | **选用隔离身份版本，并用 SQLite 存状态**。 |
| C. macOS 原生机制 | NSFileCoordinator 适合参与协调的文件；UserDefaults 缺少所需跨文档 CAS 合约；Keychain 更适合小型凭据 | 不假定 NSFileCoordinator 拦截任意 POSIX 写；Keychain 访问控制也不能直接提供配置 patch/迁移事务 | 桥接、签名、权限提示与兼容测试成本；不能仅换 API 就获得所有语义 | 协调器不是安全边界；原生服务管理/IPC 可作为 B 的宿主，Keychain 可用于独立凭据。 |

Apple 的文件协调模型围绕 coordinator 与 presenter 协作；本设计不从该 API 推导强制文件锁。[Apple 文件协调说明](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileCoordinators/FileCoordinators.html)

### 2.1 推荐拓扑与安装门槛

```text
Controller A/B、设置 UI、偏好模块（桌面 UID）
        │ 经过认证的受限 IPC：read / patch / import / export / status
        ▼
签名配置服务（专用非登录 UID；唯一写入者）
        │ 一个受控写队列 + SQLite 事务
        ▼
管理员拥有且桌面用户不可替换的父目录 / 服务私有数据库
```

建议位置为 `/Library/Application Support/OrcaToolkitState/<uid>/`，这是待批准的部署路径，不是本阶段创建目标。顶层与所有可替换祖先由管理员拥有、普通用户无写权限；租户目录服务 UID 拥有、0700；DB、journal、备份及迁移原始快照服务 UID 拥有、0600，umask 077，禁止额外 ACL 授予客户端写权限。不用桌面用户可 rename 的 home 子目录承载权威状态。

服务在安装后以非 root 身份常驻，使用受管 launchd 生命周期。只有安装/卸载阶段需要管理员权限，运行期不持有任意提权能力。服务与客户端二进制、配置和启动项不可由普通用户改写；不运行来自用户可写目录的 JS/Python、动态插件、环境注入或 shell 命令。当前源码树直接运行的 Node 客户端不能仅凭“Node 签名”获得信任：第二步必须交付受保护的最小客户端桥接及明确的身份策略，未完成前不启用服务模式。

IPC 应基于系统提供的 peer/audit 身份验证用户，并校验客户端 designated requirement/签名身份；不信任请求中的 UID、PID、路径或 bundle 名字。每个租户独立命名空间。若桥接提供本地 HTTP，仍保留现有 token、Origin、回环限制；不能把身份验证退化为所有同 UID 程序可调用的任意写入 RPC。服务端仅允许固定配置命名空间和白名单操作，不接受 SQL、任意文件路径、命令或客户端自报的服务身份。身份验证方案需原生安全审查，不能靠本设计断言已实现。

服务不可用时显示配置不可用并停止写入；不退回 JSON、不自动建空库、不用 defaults 掩盖原有效状态。launchd 单实例管理用于运行协调，真正的直接写入边界是不同 UID 与受保护目录。服务重启先恢复、验证，再接受请求。

### 2.2 SQLite 事务与版本合约

推荐首版 **`journal_mode=DELETE` + `synchronous=EXTRA`**：配置很小，服务按队列串行处理，WAL 的读写吞吐优势不足以抵消 sidecar 生命周期复杂度。macOS 再设置 `fullfsync=ON`，禁用 shared cache，`foreign_keys=ON`，有界 busy timeout（建议 3 秒）和事务大小；启动读取实际 pragma 结果，不忽略不支持或误拼的选项。SQLite 隔离适用于合法连接；耐久性仍依赖底层存储正确实现同步。[SQLite 隔离](https://www.sqlite.org/isolation.html)、[同步选项](https://www.sqlite.org/pragma.html#pragma_synchronous)、[fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync)

WAL 是备选而非第二套写机制：若测量证明需要，整体切换为 WAL + FULL，配套受控 checkpoint、0600 WAL/SHM、原子一致备份及故障测试。不得使用 WAL + NORMAL 声称每次确认提交均耐断电。WAL 仅允许支持的本地文件系统；实现时固定并核验 SQLite 版本及安全修复，特别是官方记录的 WAL-reset 修复（3.51.3 或相应已验证回移版本），不能直接假设系统 Python 内置 SQLite 足够新。[SQLite WAL](https://www.sqlite.org/wal.html)

拟议逻辑模型（不是本阶段数据库实现）：

| 表/实体 | 必需内容 |
| --- | --- |
| store_meta | schema_version、store_id、store_epoch、全局 commit_seq、迁移状态；与业务改动同事务递增 |
| documents | namespace = settings / downloads-preferences；完整 JSON 树、文档 revision、内容摘要 |
| revisions | 前后版本、改变路径、调用身份、request_id、摘要；保留可审查的上一个有效版本 |
| requests | 幂等键、请求摘要及最终结果；相同 ID 不同内容拒绝 |
| migration_sources | 来源文件名、原始 bytes、摘要、导入版本及人工确认记录；不含其他真实凭据 |
| target_leases | 当前下载目标 generation、活动/可恢复任务引用；配合目标切换事务 |

所有版本号由数据库事务管理，Map/WeakMap 不再是权威。revision 为不溢出 JavaScript safe integer 的传输形式（十进制字符串）；store_epoch 变化使旧 token 全部失效。保留窗口及容量需明确配置，历史被回收时拒绝过旧 base，不能猜测合并。

读操作返回完整快照、已知字段投影和 `(store_id, epoch, revision)`。写操作提交 `request_id + base revision + 显式字段操作`：

1. `BEGIN IMMEDIATE` 后读取当前文档、已保存的 base 和目标约束。
2. patch 只能修改明确指定的路径；删除是显式操作，不能把缺失字段当删除。数组作为整体冲突单元；父子路径重叠算冲突；动态 environment 名作为字面 map key，拒绝原型污染路径。
3. base 之后若仅有无关字段变化，在最新树上应用 patch。相同路径即使先改后还原也按修改记录判冲突，避免 ABA；同字段变化要求重载并重新确认。整对象请求须由 base 对比得出实际编辑路径，不能拿“所有表单值”覆盖当前对象。
4. 在完整树上只验证本版本认识的字段和跨字段约束；未知字段原样保留、不执行。版本不支持的语义显示不可编辑/导出提示，不能默认清空。
5. CAS 更新 `WHERE revision = current_revision`，同时写历史、请求结果和新版本；只在 COMMIT 成功后确认。序列化事务不能代替乐观版本检查。
6. COMMIT 结果因断连/EIO 不确定时返回 `UNKNOWN`，客户端以相同 request_id 查询结果；不能宣布失败后用新 ID 盲重试，也不能宣布成功。恢复后只能读到完整的旧版本或完整的新版本。

例：A、B 都读 v12，A 修改 `gpu.enabled` 到 v13，B 修改 `downloads.sshPort` 可合并到 v14；A/B 都改 `downloads.sshPort` 则第二个冲突。`remoteLocations[env].source` 的更新保留同条目未知字段，以及其他环境和 folder。两文档需要一起变更时使用同一 DB 事务。

目标切换不能只复用当前 Controller 的任务扫描：每个新任务先取得服务端 target generation/lease，设置切换与 lease 获取同事务串行；存在活动、暂停、可恢复或 cleanup 未确认的 lease 时拒绝目标切换。任务注册失败也需明确解除 lease；客户端崩溃留下 lease，恢复核对后才释放，禁止超时自动放行。第二步要覆盖 engine、Controller 初始化和恢复，不能仅替换 settings.save。

### 2.3 外部修改、文件边界与恢复

数据库只能由服务经 SQLite API 打开；其他模块及管理工具也走受限 API。普通用户对数据库目录的写、truncate、unlink、rename、hardlink、symlink 均应被 OS 拒绝。受保护祖先保证 SQLite 的路径解析不处于可由对手换名的目录内；单靠打开前 lstat 不够。

启动/重启检查目录、属主、mode、ACL、文件类型、硬链接数及符号链接；拒绝不支持的远程/同步文件系统。DELETE 模式允许 SQLite 正常处理受保护的 hot `-journal`；不手动删除。`-wal`、`-shm` 应不存在，遇到遗留 sidecar 视为模式不符，停止并保留现场，不擅自移除。若批准 WAL 模式，DB/WAL/SHM 全部须处于上述目录、0600、正确属主、无链接，含自动创建、重开及 checkpoint 后状态。应用不得在 SQLite 活跃连接外随意打开/关闭 DB 文件做 hash 备份，以免破坏底层锁语义。[SQLite 文件及日志边界](https://www.sqlite.org/howtocorrupt.html)

服务内其他正常请求的修改由 revision/history 可检测；未授权原生写入由权限拒绝。检测到身份、模式、schema、完整性异常则停止写入，保留数据库和日志，提供只读诊断。`integrity_check` 只能查结构完整性，不能证明内容真实；历史摘要不防 root 同时重写历史。受信管理员维护必须停服务，通过维护 API 导入并递增 epoch；静默原生篡改的绝对检测不在承诺内。

保持上一个有效版本的条件是受保护状态未遭管理员/硬件破坏。正常写失败由 SQLite 回滚；崩溃使用 journal 恢复；不得用默认配置“修复”。备份用 SQLite 一致备份接口，关闭并校验备份后发布，保留多个有效代次；不直接复制活跃 DB 单文件。完整性失败时冻结现场，人工选择校验通过的备份恢复到新 epoch，明确可能丢失的时间范围。[SQLite 原子提交和恢复](https://www.sqlite.org/atomiccommit.html)

### 2.4 迁移、单一写入路径与回滚

**不自动迁移。** 本阶段不读取、导入、覆盖用户真实配置。后续也须用户主动选择“预览迁移”，确认来源摘要、未知字段及差异后才导入。

1. 停止参与迁移的 Controller、偏好写入和下载配置操作；要求旧版退出。确认服务/客户端兼容版本和安装身份。导入工具只处理用户选定的 `settings.json`、`downloads-preferences.json`，不扫描整个 home。
2. 从 no-follow 文件描述符读取有大小上限的原始 bytes，检查读取前后身份/摘要；显示这些确切 bytes 的预览。检测变化则要求重新预览，不声称“检查后源路径永远不变”。确认绑定捕获的摘要，服务导入该快照；后续源文件变化不影响导入内容，也不能无声成为新配置。
3. BOM、嵌套对象、未知节/字段、未知数组及数值无损保留；使用可保留数值精度的 JSON 表示，不能经 JS Number 悄悄改写大整数。原始 bytes 同时存档。重复 key、非法编码、截断 JSON、非法已知字段或顶层非对象停止迁移并保留原件，不降级为空对象。未知字段不是损坏。
4. 两个文档和迁移状态同事务导入；缺失文件经用户确认才使用默认初值。记录缺失与显式 null 的区别。旧 downloads 模块 `settings.json` 若存在，单独列为可选来源并提示冲突，不自动覆盖主设置。
5. 提交后新 macOS 客户端只使用服务；旧 JSON 留作原始档案，不双写、不读回、不作为运行配置。服务端迁移状态是唯一切换依据，用户目录 marker 仅用于提示，不是安全边界。迁移中断可按 request_id 查询并续办，不能建立第二个空库。
6. 对旧来源提供显式“检测旧文件修改/重新导入预览”：比较捕获摘要，运行时可提示旧文件编辑不再生效；watcher 仅作通知。无法保证捕获恶意进程写后还原的所有瞬态操作，但这不影响新权威状态。不要把旧文件的修改吞成配置更新。
7. 新客户端移除该配置的 JSON 写入口；旧程序不支持服务模式，禁止混跑/自动降级。受管启动器拒绝不兼容版本。不能阻止用户绕开启动器运行旧源码，但其写入仅影响已退役档案，不影响服务 DB；检测到时提示冲突，不重新启用 JSON。

回滚服务代码须保持向前兼容，否则停止配置写入。数据回滚优先在服务内恢复历史版本并产生新 revision/epoch。确需回到旧 JSON 版本时：停所有客户端和服务写入，显式导出新文件供人工核对，保留 DB/journal/备份及旧原件；确认旧程序能表示所有字段后才能人工切换。旧 validator 无法容纳未知字段时拒绝降级，不能为兼容而丢字段。禁止自动覆盖旧 JSON；旧 H-02 重新暴露，降级后不能沿用 HIGH 已关闭的结论。

Windows 保持原有 JSON、路径、同步函数及无 Python/服务依赖行为；macOS 的新异步加载、patch、错误展示通过平台适配器引入。Accounts 独立偏好、任务 key 文件不自动纳入本迁移。它们不因此获得新安全保证。

## 3. H-03：三种架构比较

| 维度 | A. 受管下载账户 | B. SSH 用户 CA / 短期证书 | C. 单一授权代理 |
| --- | --- | --- | --- |
| VPS 改造 | 专用非 root 登录身份、受保护程序和导出目录 | 配置用户 CA 信任、签发服务、时钟与撤销分发 | 安装常驻服务、持久状态及 SSH 查询入口 |
| root 要求 | 新账户/权限通常需管理员 | 已有 CA 服务可不需客户端 root；首次服务器信任通常需管理员；写个人 cert-authority 行仍触及个人文件 | 与 workspace UID 隔离、接入 sshd 通常需管理员；同 UID 无强边界 |
| 原 SSH 登录 | 独立入口可保持原配置/个人 key 不变 | 写共享信任可能扩大影响面，应限专用入口 | 独立入口可隔离；不挂接个人 key 修改路径 |
| 并发正确性 | 单账户仍需事务权威，不能只把 JSON/flock 换个目录 | 签发无需每任务改 key 文件；撤销及已有会话仍需状态协调 | 事务管理注册/撤销和读许可，无客户端改文件 |
| 单任务撤销 | 需要按任务授权表及会话栅栏 | 仅等过期不等于立即撤销；需 serial/key KRL 或在线查询，加会话控制 | 原子 tombstone + 拒绝新读 + 排空已有读后回执 |
| VPS 重启 | 持久状态、拒绝未恢复任务 | 证书仍有效，重启不会撤销；需恢复 KRL/签发记录与时钟 | 先恢复已提交状态、处理撤销中任务，再开放入口 |
| 安全边界 | 账户隔离、forced command、文件范围；账户仍不能写自身入口规则 | CA 私钥、principal/期限/critical options、信任配置、撤销信息 | 代理身份、DB 目录、调用身份和回执签名 key |
| 运维复杂度 | 中；源文件跨账户读取需处理 | 高；CA 托管、续签、轮换、时钟、撤销与审计 | 中高；服务可用性、恢复、幂等、审计和协议版本 |

推荐 **A + C**。单独 A 不保证事务；单独 C 若与 workspace 共用 UID 也不安全。B 适合已有受管 SSH CA 的组织作为未来适配，首版不自建 CA。零管理员、直接改个人 `authorized_keys` 的路径不能满足当前要求，不提供自动降级。

用户 CA 认证“谁可登录服务器”，Host CA 认证“连接的是哪台服务器”，两者不可互换。用户证书应绑定唯一任务 principal、公钥、serial、有限有效期、forced command，清除默认转发/PTY 等扩展；证书与授权行同时指定强制命令时必须验证兼容性。稳定 CA 信任可设在受管入口，不必逐任务改个人文件。单任务 KRL 撤销需可靠分发和加载，证书到期/KRL 都不会自动终止已有会话；必须另有读许可检查。CA key 泄漏、时钟回退与备份恢复都是独立风险。[OpenSSH 证书与 KRL](https://man.openbsd.org/ssh-keygen)、[授权行限制](https://man.openbsd.org/sshd)

### 3.1 推荐部署：独立入口与最小权限

```text
Mac（每任务私钥，只授权任务读取）──严格 host pin──专用 SSH 下载入口
                                                       │ 受保护查询程序
workspace 的受限管理客户端 ── peer 身份验证 IPC ── 授权代理（独立 UID）
                                                       │ SQLite 任务/撤销/审计
                                                       ▼
                           固定 reader / 只读导出对象 / 逐任务读许可
```

建议由管理员**人工**部署独立 sshd 实例，独立配置文件、监听 Tailscale 地址与单独端口，仅允许专用下载账户；不修改现有 `/etc/ssh/sshd_config`，不重载个人登录实例。独立配置同样属于需要审核的 SSH 配置，本工具不得自动写入。若不能保证原 SSH 实例不会允许该新账户绕开限制，安装验收失败：需管理员另行限制账户可登录范围，或提供独立容器/主机，不能暗中改原实例配置。

专用入口使用 `AuthorizedKeysFile none`，通过 root 拥有、不可由组/其他用户改写的 `AuthorizedKeysCommand` 查询代理，指定独立最小权限 `AuthorizedKeysCommandUser`。查询程序只查询，不拥有授权数据库写权；查不到、超时、代理恢复中一律不给 key。OpenSSH 官方要求命令属主/路径并支持关闭文件查询。[OpenSSH 配置手册](https://man.openbsd.org/sshd_config#AuthorizedKeysCommand)

不使用个人 `authorized_keys`，也不维护每任务实体授权行。查询输出是 sshd 所需的公钥授权格式（不是清理回执），由可信查询程序从已提交任务记录构造，含固定 forced-command 和服务端选择的任务 ID/授权 generation。禁止客户端注入选项、命令或 shell 片段；同一公钥不能同时绑定多个任务。

专用账户无 sudo、无密码/交互 shell 通道、无 PTY、转发、agent/X11、用户 rc、用户环境注入、SFTP/SCP 或任意命令执行。forced command 只能接受结构化 `read(offset,length)`、`revoke/status`，继承当前单次最多 8 MiB 等边界。账户 shell/forced-command 组合必须实际验收，不能简单设 nologin 后假设强制命令还能工作；任何 shell 只作为 root 拥有固定 dispatcher 的必要启动机制，不接受客户端 shell 文本。

代理使用专用非登录 UID，DB/日志/回执签名私钥为 0600、目录 0700，祖先及安装程序 root 拥有且不可替换。下载账户、lookup 账户、workspace 均不能改 DB、启动项或 dispatcher；不得把代理 DB 放在 workspace home。代理不以 root 运行，不提供任意命令、任意文件读写 RPC。root 仅用于管理员部署账户、权限及 SSH 服务，不创建 root 账户。

文件范围采用管理员明确批准的只读导出区，首版优先使用每任务不可变 staging 副本（需要额外空间），而不是给下载账户整个 workspace/home 的权限。导入时来源必须属于该 workspace 身份的批准项目根，服务端重新校验；用户提交的路径/客户端 repo list 不是授权依据。逐级 no-follow 打开、拒绝目录逃逸/特殊文件/不可信硬链接；导入期间源变化则拒绝；staging 只有受管组件可写，reader 仅拿只读句柄。重启重新校验对象摘要/身份，源变更不扩大读取范围。若 staging 容量不足，注册失败，不改成任意绝对路径读取。

### 3.2 注册、读取、撤销与会话竞态

代理是唯一事务写入者；状态与 audit/request 结果同事务。建议采用与 H-02 相同的 SQLite 单连接写队列和 DELETE/EXTRA 策略，Linux 同步能力单独验证，不套用 macOS fullfsync。schema 至少包含：`server_id, authority_epoch, owner, task_id, key_fingerprint, export_object_digest, auth_generation, state, expires_at, request_id, commit_seq`。task_id 使用足够随机熵（现有 128 bit ID 可保留），同一 epoch 中永久不复用已撤销 task/key。

注册通过受限 IPC 验证 workspace peer UID，绑定其批准导出根；不凭传入用户名认定身份。普通 workspace 客户端只能注册/撤销自己的任务。跨网络管理另需经批准的受限认证通道；首版可由已认证 Orca 通道调用固定管理客户端，但不再上传执行可修改授权状态的 Python 代码。代理端验证所有参数及 task key 的持有证明。私钥不离开 Mac。

状态机：`PREPARING → ACTIVE → REVOKING → REVOKED`，失败可进入待核查状态；禁止 REVOKED 回到 ACTIVE。注册 idempotency key 与规范化请求摘要绑定；重复同请求返回原任务，不同内容冲突。staging 完成并校验后才提交 ACTIVE，崩溃留下的未归属 staging 仅列为待核查，不靠文件名猜测自动删除。

SSH key 查询不是最终读许可：认证通过到 forced-command 启动之间仍可能撤销。每次 read 在代理处检查 ACTIVE、generation、期限、对象及字节范围，取得受管读 lease；验证与 lease 登记在同一串行临界区。代理生成的绑定信息由可信 dispatcher 使用，不能信任 `SSH_ORIGINAL_COMMAND` 中自报 task_id。reader 账户不对外提供任意执行环境；内部 IPC 按角色分权，不允许 lookup 调用管理写操作。

撤销步骤：

1. 校验请求身份（任务 key 持有者仅能撤销自己；workspace 管理身份只处理所属任务）、task_id、公钥指纹、generation、nonce 和 request_id。
2. 事务写 REVOKING；停止发放新认证和读 lease。已经完成 SSH 认证的会话也不能获得新读。
3. 取消或等待该任务全部活跃读 lease 结束，确保相关 reader 不再产生新数据；进程失联或取消无法确认则停留 REVOKING，不发成功回执。栅栏同时覆盖所有工作进程，不能只清空内存计数。
4. 栅栏完成后事务写 REVOKED tombstone、commit_seq 和审计事件，确认持久提交；随后生成签名回执。已发送到网络/客户端缓冲的数据不能追回，回执不声称抹除已下载数据。
5. 重复撤销或查状态从 tombstone 返回相同撤销事实，并为本次 challenge 生成新签名；不存在任务、归属不明、存储损坏均返回 UNKNOWN，不等同于 REVOKED。

代理/reader/sshd 作为受管进程组启动。代理崩溃时 reader 必须关闭租约并停止输出；重启先阻断入口并确认旧工作进程退出，再恢复 DB 中 REVOKING/REVOKED 状态。不能仅重置内存 lease 后宣布排空。自动重启不能将数据库丢失解释为“无任务所以清理完成”。普通重启保留 authority_epoch；备份恢复按下一节隔离处理。

### 3.3 可信清理回执及本机私钥删除条件

清理协议 v3 使用有长度边界、严格 schema 的结构化信封，并对规范化 payload bytes 做独立签名（建议成熟 Ed25519 实现；禁止自定义密码算法）。最低字段：

```text
protocol, action="revoke", server_id, endpoint_id, account_id,
authority_epoch, task_id, task_public_key_sha256, auth_generation,
request_id, client_nonce, state="REVOKED", revocation_commit_seq,
read_barrier="DRAINED", issued_at, signing_key_id, signature
```

每次请求 nonce 至少 256 bit，不复用；签名含域分隔符（例如 `orca-download-revocation-v3`）。拒绝重复 JSON key、非法编码、未知协议版本、截断、超长和多义序列化；必须验证签名再解释成功。摘要绑定标准化公钥 bytes，不绑定可变 comment。终端 stdout 仅可作为不可信信封载体，substring、exit 0、任务 ID 和 nonce 本身都不证明撤销。

回执验签公钥与 server/endpoint/account 在部署时通过管理员核验的通道预置；不能从同一次未经验证的 stdout 收取新信任 key。host key 与回执 key 分离；host pin 保护 SSH 连接，回执 key 证明代理所确认的状态。轮换要求旧 key 签名的过渡声明或人工重新固定，任务保存对应信任版本；不能因验签失败自动接受新 key。

Mac 只有在全部字段与持久化本地任务匹配、签名正确、请求 nonce 匹配、撤销序号不倒退、DRAINED 且 epoch 可信后，才记录 `cleanup_verified` 和完整回执，并同步到磁盘。之后才能删除该任务专用本机私钥。私钥路径须依旧遵循 no-follow/身份校验，不按远端响应给出的路径删除；本机状态写入或删除失败也保留可重试记录。清理失败、超时、签名失败、UNKNOWN、旧字符串回执全部保留 key，并显示核查状态。

SSH done 回复丢失后该 key 已不能登录，这是正常情况：使用经身份验证的管理 status 通道（Orca 固定客户端或人工管理工具）读取同任务 tombstone，获取新 nonce 的签名证明。fallback 不得绕过验签，不得使用旧 Python 清理脚本。重新注册同 ID 不是恢复办法。

回执证明的是指定受管入口、epoch、任务的持久撤销及读栅栏，不证明管理员没有复制 key 到其他主机/入口。对未知副本不做全局“已删除”承诺，也不扫描或删除人工 SSH key。

### 3.4 崩溃、恢复、审计与回滚

| 故障点 | 恢复行为 |
| --- | --- |
| 注册提交前崩溃 | 无 ACTIVE 授权；按相同 request_id 查询/重试；未归属导出对象保留待核查。 |
| 注册已提交、回复丢失 | 返回原注册结果；拒绝换 key、换 source 的同 ID 重试。 |
| REVOKING 后崩溃 | 重启先确认旧 reader 已退出，再完成持久撤销；不直接回 ACTIVE。 |
| REVOKED 已提交、签名/网络失败 | 本机保留 key；status 从 tombstone 生成新 challenge 回执。 |
| DB/journal 磁盘失败 | 拒绝新授权/读取，停止确认成功；保存现场、审计、任务 key，恢复后核查提交状态。 |
| VPS 重启 | 服务依赖顺序保证恢复先于 SSH 接入；已撤销任务仍拒绝；ACTIVE 仅在对象和期限验证通过后恢复。 |
| 备份恢复/状态丢失 | 禁止直接启动旧 ACTIVE 表；管理员隔离入口，生成新 authority_epoch，旧 epoch 所有任务拒绝。客户端需重新核验；不能从旧备份补发虚假的历史撤销证明。 |

审计记录 actor、task/key fingerprint、request_id、操作、前后状态、commit_seq、结果、启动/恢复事件，不记录私钥或文件内容。日志不得由 workspace 修改；DB 内审计与状态同事务，外部日志仅辅助。人工工具提供只读任务状态、回执验签、服务身份/版本/epoch、待核查项及读 lease 列表；没有“按注释批量删 key”的命令。

回滚先停止新注册和专用入口，保留代理状态、tombstone、回执及客户端 key；旧会话必须停止。兼容的旧代理版本可读取当前 schema 时才允许代码降级，否则保持停用。不得恢复旧授权快照并开放入口，不自动恢复个人 authorized_keys 写入模式。移除独立服务/账户/ACL/导出区是管理员核对后的独立操作，卸载不自动删归属不明数据。

### 3.5 旧任务及 Windows 兼容

新 macOS 任务显式选择 v3 端点，持久保存 endpoint/account/host pin/receipt key/epoch；不把现有任务的 sshUser/port 原地改到新账户。旧 v1/v2 任务保留其状态、私钥、原端点及人工清理提示；**停止使用旧字符串回执触发自动删 key**。这是第二步需明确实施的安全行为变化。

不自动迁移、注册或撤销任何旧任务。旧授权的存在、注释相似或 key 相同都不能证明精确归属；仅提供人工核查材料，管理员决定具体处理，工具不扫描或清理个人 key。旧授权仍在时，不能宣称该 VPS 所有历史任务均安全撤销。

Windows 的现有设置存储和默认下载协议路径保持；v3 作为显式版本/端点适配，不能无通知修改 Windows 目标或产生新依赖。共享旧 SSH 模型的 H-03 风险不会因 macOS v3 而消失。若后续要让 Windows 也停止旧回执自动删 key，须独立说明兼容行为变更并审查；在此之前整体共享旧路径仍是发布阻塞，不把“Windows 未改”解释为“Windows 已安全”。

## 4. 需要人工批准的实际操作

以下为后续部署清单，不是本阶段执行指令；不能通过读取这份文档取得自动安装授权。

| 环境 | 待批准操作 | 交付时的核查材料 |
| --- | --- | --- |
| Mac | 安装签名服务/桥接、专用非登录 UID、受保护目录及 launchd 配置 | 可审查安装清单、代码签名、权限树、卸载方案；拒绝授权时明确不可启用 |
| Mac | 用户选择两个配置文件并确认迁移/导出/回滚 | 原始摘要、完整差异、未知字段报告；本阶段不读取真实数据 |
| VPS | 创建专用非 root 下载/代理/lookup 身份，安装固定程序和独立 sshd 服务 | 账户权限矩阵、独立配置 diff、有效配置检查、回滚步骤 |
| VPS | 选择监听端口、Tailscale/防火墙范围，确认原登录入口不授予新账户非受限访问 | 独立实例配置测试和原 SSH 登录回归；不自动编辑 sshd_config |
| VPS | 批准项目导出根、staging 容量、只读权限及服务启动顺序 | 文件范围负例、磁盘配额、重启与进程组恢复验证 |
| 双端 | 固定 host key、回执验签 key、server_id、endpoint_id、轮换/恢复策略 | 经人工核验的公钥指纹和配置清单；不读取现有真实私钥 |
| VPS | 人工检查历史未知授权、批准状态备份恢复或最终卸载 | 精确任务归属及审计记录；无法确认则保持原状 |

不自动创建 root 账户，不自动修改任何 sshd 配置，不扩大远端 shell 权限，不降低 `StrictHostKeyChecking=yes`。保留 `IdentitiesOnly=yes`、`IdentityAgent=none`、禁转发、禁自动 host key 更新及独立 known_hosts；host/签名 key 变化必须停止并核查。

## 5. 第二步验收矩阵：关闭 HIGH 的必要条件

本节是**尚未执行的新架构验收计划**。第一步既有测试通过不代表这些测试通过。所有数据为合成 fixture，跨 UID/真实 sshd/断电测试在专用测试 VM 中经批准运行，不连接生产 VPS，不读取真实用户配置或 SSH 文件。

| ID | 测试 | 必须观察的结果 |
| --- | --- | --- |
| S01 | 原桌面 UID 的独立进程无 flock，分别 write/truncate/rename DB、journal、父目录；伪造 WAL/SHM、symlink/hardlink；在各提交窗口重复 | OS 权限拒绝；有效版本/无关文件不变。必须是独立进程与真实不同 UID 的服务，不能用 mock 权限假装通过。 |
| S02 | 同桌面 UID SQLite 对照实验：一进程事务，另一进程原生篡改文件；与隔离部署比较 | 对照证明 SQLite 单独不是强锁；隔离部署拒绝写入。对照数据损坏只能发生在临时副本。 |
| S03 | 两个真实 Controller/进程同时改无关字段、同字段、父子字段、ABA、两个文档和不同环境条目 | 无关项均保留；同项冲突；未知字段和数值无损；请求幂等；历史过期明确拒绝。 |
| S04 | 迁移含 BOM/未知字段/大整数/重复 key/损坏 JSON，预览期间及确认后外部改源文件 | 只导入用户确认 bytes；变化可提示；坏原件不变；不静默默认或截断；无 JSON 双写和自动迁移。 |
| S05 | DB/journal/WAL/SHM owner/mode/ACL/父目录/硬链接的正反例，启动、运行、恢复、备份阶段 | 合法状态正确；异常失败关闭并保留现场；DELETE 模式异常 WAL/SHM 不被盲删。 |
| S06 | BEGIN/更新/journal sync/COMMIT/回复前后 SIGKILL；重启并以同 ID 重试 | 只有完整旧/新版本；已确认提交可恢复；不确定结果可查询；版本及 request 结果一致。 |
| S07 | 有限容量测试磁盘 ENOSPC、只读卷、VFS 注入短写/EIO/fsync 失败；备份写失败 | 上个有效状态可恢复；未确认不报成功；不触发 JSON/defaults fallback。kill 不等于断电测试，两者分别记录。 |
| S08 | 同时创建任务、切换目标、暂停恢复、Controller 崩溃/重启 | target lease 阻止任务被迁到其他服务器；陈旧/未知 lease 不自动释放。 |
| S09 | 伪造 IPC UID/PID、未授权二进制、跨租户、超大 patch、注入 SQL/路径；服务死循环/重复实例 | 拒绝未授权操作；有界资源/超时；不开放新写路径。 |
| R01 | workspace UID 独立编辑合成个人 authorized_keys，同时 register/revoke；尝试写代理 DB、替换程序 | 个人文件只出现编辑器自己的变动；下载组件从不打开它；代理状态写入被 DAC 拒绝。 |
| R02 | 双 register、双 revoke、register/revoke 交叉、相同 ID 不同 key、跨任务/跨 owner | 事务顺序明确，幂等/冲突正确；不清理其他任务，不允许撤销后复活。 |
| R03 | key 查询之后、forced command 前撤销；长读中撤销；reader 卡死/代理重启；已有 SSH 会话重发 read | 新读拒绝；只有真实排空后才签 DRAINED；不能确认时保留本机 key。 |
| R04 | 伪造旧 stdout、正确任务错 key、正确 key 错 epoch、重放 nonce、截断/重复 key JSON、错签名/签名 key 轮换 | 所有负例拒绝；没有字符串成功路径；fallback 使用同一验证器。 |
| R05 | 注册/撤销各提交点杀进程；回复丢失；VPS 重启；磁盘满、fsync 失败 | tombstone 持久；已撤销 key 拒绝；status 可恢复；未知不冒充已清理。 |
| R06 | 旧备份恢复、丢 DB、回执序号倒退、服务签名 key 丢失 | 停入口、新 epoch/人工核验；旧 ACTIVE 不复活，不补造成功。 |
| R07 | shell、PTY、转发、SFTP、环境/命令注入、跨项目路径、symlink/hardlink 逃逸、源文件变化 | 拒绝；读取只限批准导出对象和边界；个人 SSH 登录保持原样。 |
| R08 | 撤销提交后本地回执落盘失败、key 删除失败、主机 pin 变化、旧任务归属不明 | key 保留或明确可重试；不删个人/未知 key；人工核查路径可用。 |
| C01 | Windows 既有测试、macOS UI/下载暂停续传/恢复/偏好与目标切换回归 | Windows 原行为/依赖无改变；平台与协议边界明确；旧路径风险仍单独标示。 |

每个 HIGH 关闭前须有上述测试日志、权限实测、故障恢复证据、独立原生安全审查及人工批准。若跨 UID 安装、客户端身份绑定或远端读栅栏尚未完成，就继续标为 HIGH OPEN，不能以增加 flock、mtime 检查或只测试合作进程替代。

## 6. 第一步交付与检查记录

本阶段仅新增本文，未改业务代码；工作区初始干净，fetch 后本地基线、远端分支及 PR #11 HEAD 一致。2026-11-05 在 macOS 上完成以下检查，均仅使用仓库文件和隔离合成数据：

| 检查 | 实际结果 |
| --- | --- |
| `node --test plugins/toolkit/test*.cjs plugins/toolkit/modules/downloads/test*.cjs` | 98 项，97 通过，1 项既有 Windows 原生探针跳过，0 失败。包含旧实现的设置冲突、Windows 无 Python 路径、下载恢复及失败保留 key 回归。 |
| `python3 plugins/toolkit/modules/downloads/test_hardening.py` | 13 项通过；隔离 HOME 与合成公钥，未连接 VPS。 |
| `python3 plugins/toolkit/modules/downloads/test_remote.py` | bounded read、命令拒绝、源变动、精确清理与隔离注册检查通过。 |
| `python3 scripts/check.py` | 190 个源码/文档文件通过语法、私有运行文件及凭据模式检查。 |
| `python3 plugins/toolkit/reproduce-unresolved-races.py` | **FAIL，exit 1**：H-02 / H-03 均 `reproduced: True`；如实保留发布阻塞，不计入通过项。 |

文档另作 whitespace、相对基线仅一个新增 Markdown 文件、链接和需求覆盖检查；提交前执行 `git diff --cached --check`。新架构的跨 UID、崩溃恢复、磁盘故障、独立 sshd 集成测试尚未实施，不能列作通过。本次仅做架构语义评估和既有回归，不宣称新的 SQLite/IPC/v3 协议已有运行验证。

交付后保持 Draft，不合并、不关闭 PR #11、不修改 main、不执行部署或迁移。按“ChatGPT Brain → Codex 执行 → ChatGPT 审查 → 人工批准”进入下一步；日常文档、测试与 Git 小问题自行处理，审查边界不阻止第一步完整发布。


## 7. 2026-11-05：7B-3 实现与实际验收

实现基线是 PR #12 `919480467d692da4fca21eff5acdbf721688ad4f`；实现分支
`feature/macos-security-implementation`，base 保持 `feature/macos-security-architecture`。
未改 main，未部署生产服务/VPS，未读取或迁移真实用户配置，未删除个人 SSH 授权。

### 7.1 已实现的路径

- `plugins/toolkit/config-service/`：独立非 root UID 服务、原生 C bridge、内核 peer
  UID/audit token 与 Security.framework designated requirement；SQLite 单写连接及
  lifetime lock，DELETE/EXTRA/fullfsync；完整 JSON 树、字符串 revision、字段/父子/ABA
  冲突、同事务幂等请求与审计、跨文档 patch-many、未知精确数值保留、双来源预览确认、
  原始 bytes 存档、一致备份、新 epoch 恢复、持久 target lease。
- macOS settings/preferences 运行读写只走服务，不再用 private-json；缺服务明确报错，
  迁移待确认时拒绝保存。Windows 继续 JSON。其他专用偏好未纳入本次迁移。
- `services/download-authorization/`：独立 authority/lookup/reader/workspace 角色、
  管理 IPC、任务 SSHSIG 持有证明、逐级 no-follow 导入、0400 staging、受限范围读取、
  数据库唯一任务/公钥绑定、永久 tombstone、nonce 防重放、签名注册元数据和撤销回执。
- 独立 sshd 配置使用 AuthorizedKeysFile none、AuthorizedKeysCommand、固定 per-key
  forced command。新增 macOS v3 任务从管理员固定的 host key/receipt key/epoch 取信任，
  不从同次 stdout 建立信任。所有撤销走同一验签器，回执落盘及 lease 释放成功后删任务 key。
- 旧 macOS 任务显示 legacy/manual cleanup，旧 register/字符串 cleanup/fallback 被拒绝；
  不迁移旧任务，不改个人 authorized_keys。Windows 旧协议保留，其风险没有因此消失。
- 提供显式 bundle 构建、macOS 安装/卸载停用、Linux 安装准备、sshd 模板、恢复命令和
  [部署手册](macos-security-deployment.md)。生产 Developer ID、部署权限和信任固定必须由管理员审核。

### 7.2 实际证据（不能扩大解释）

| 项目 | 实际结果 |
| --- | --- |
| macOS 原生跨 UID | 用户在本机终端以 sudo 执行 `scripts/test-macos-security-isolation.py --client-uid 501`；真实 `_www` 服务 UID 与 501 客户端 UID。签名桥接接受，未批准 Python 客户端拒绝；DB/journal/WAL/SHM 写、unlink/rename/hardlink/symlink 拒绝；显式合成迁移读取通过。用户返回完整 PASS 截图，临时目录/进程自动清理。 |
| native 首次失败与修正 | 首次测试因 CDHash requirement 表达式格式错误拒绝合法 bridge，未计通过。改为 `cdhash H"…"` 并在启动前 codesign 校验；同时改用内核 audit token。之后用户两次运行全部 PASS。生产签名证书未使用。 |
| Linux 真隔离 | 无网络 Docker 临时环境中真实六个 UID；workspace/reader/lookup/stranger 对代理 DB、journal、签名 key、受管代码操作均被 DAC 拒绝；配置服务 UID 与客户端不同，多独立进程修改无关字段均保留。 |
| 独立真实 sshd | 实际公钥认证/10-byte 读取通过；shell、PTY、SFTP 命令及越界读取拒绝；错误 host pin 拒绝；合成个人 authorized_keys 独立编辑不影响下载。 |
| 读 lease | 真实 reader 进程持有 lease 时 revoke 返回 REVOKING/PENDING、无签名且禁止新 SSH；reader ACK 后新 challenge 才产生 DRAINED。异常读进程的 PID/start-time/boot-id 栅栏只在确认退出后释放；不能确认的 lease 保留。 |
| 并发/重启/恢复 | 双任务 register/revoke、永久任务/key 唯一性、服务 SIGKILL 后 tombstone、被撤销 key 拒绝；配置 hot-journal SIGKILL 恢复、request/document 一致；损坏数据库从备份恢复到新目录、新 epoch，原损坏 bytes 保留。 |
| 新单元/协议测试 | config 7 项、authority 5 项、Node v3 6 项；含 request_id 重试/冲突、同字段/父子/ABA、未知大整数/高精度小数、重复 key/坏 JSON、迁移预览重启、target lease、SQLite allocation FULL 回滚、只读写入拒绝、伪造/nonce/epoch/字段绑定/seq 回退、失败保留 key，以及真实 SSHSIG 注册→签名元数据→撤销闭环。 |
| Toolkit/Bridge | 最终 Node 与 Python 回归数量见发布审计同日附录；Accounts、ComfyUI、Godot、Blender、Unity 原业务代码未修改。旧测试对 JSON 落盘的断言改为 SQLite API fixture/旧 JSON 不再有效断言；Windows legacy 合约继续测试。 |
| 旧竞态复现 | `reproduce-unresolved-races.py` **仍 exit 1**，两个 reproduced=True。该脚本直接调用保留的旧 helper/reader，是旧协议风险证据，未改成 skip/pass。不能据此把 Windows 或历史授权宣称安全。 |

### 7.3 实现细化与未验收项

原设计的重启栅栏落地为“durable lease + stdout flush ACK + Linux 内核 peer PID、
start-time、boot-id 的退出证明”。仅在确认旧 reader 不再产生数据时清除该 lease；
普通 daemon 重启不会整体清空计数。未知状态保持 REVOKING。注册 staging 在同一
注册事务内准备，ACTIVE 提交前没有可用授权；失败对象保留，不猜测清除。历史和请求
目前不回收，长期运行需监测容量；DB 错误不产生成功确认。

本轮没有验证真实整机掉电、VFS 短写/EIO/fsync 注入、实际 VPS 全机重启、真实
Developer ID/notarization/LaunchDaemon 开机流程、生产 SSH 全入口策略或真实引擎 GUI。
SQLite max_page_count 引发的 FULL 和 query_only 失败不能冒充这些测试。服务版 Python
源码由 root 保护，最终签名发行包/解释器及依赖仍需原生安全审查。IPC 认证已安装桥接
与 tenant，不证明同 UID 调用者的人类意图。旧 JSON 降级和旧授权恢复仍不允许自动执行。

**H-02/H-03：功能实现与上述临时环境测试已交付，但完整验收矩阵、独立审查和人工批准
尚未完成，继续 HIGH OPEN / RELEASE BLOCKED；不得把此 Draft 当作生产放行证明。**

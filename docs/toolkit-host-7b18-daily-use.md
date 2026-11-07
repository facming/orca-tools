# 7B-18 独立开发版：固定构建与日常使用

2026-11-07（Asia/Shanghai）。**账户和下载核心功能真实环境验收通过；保留 Draft，正式发布仍未执行。** 依据为已有 Mac、System Default Claude 账户、实际 VPS 下载的 [7B-18 记录](toolkit-host-7b18.md) 与 [执行证据](toolkit-host-7b18-validation.txt)。现有单账户刷新与用量已通过；不据此宣称多账户切换或所有旧验收矩阵均已实测。

## 固定构建

本次原样保存已验证字节，没有重新编译、安装应用或改动运行时代码。后续文档提交不改变以下产品代码基线。

| 项目                         | 固定值                                                             |
| ---------------------------- | ------------------------------------------------------------------ |
| Orca 代码基线（完整 40 位）  | `9cf8fc8158398aeb863e0f642d91b41629d06e5e`                         |
| Toolkit 代码基线             | `4df60d2cd711d2ccc5fadeb3589747a13b5651d5`                         |
| 构建名称                     | `orca-toolkit-dev-7B-18-20261107-arm64`                            |
| 实测系统 / 运行时            | macOS 15.7.5 / arm64 / Electron 43.8.6 / 现有 Orca 1.4.249         |
| 当前入口 / Host bundle       | `main.cjs` / `toolkit-host-7b18-r4`                                |
| 压缩快照大小                 | 39,649,399 bytes                                                   |
| 压缩快照 SHA-256             | `4e58af6e2b62b078daa7a0ff677ad77c40ea09e3bdb8b0569495740884a576c0` |
| `main.cjs` SHA-256           | `a691bcda28e388a341d4630fc9bb088eeb309c9c24ddc14079db776beac7ff5d` |
| Host `manifest.json` SHA-256 | `a8a9ae0b5aa11af02fa239425ca6cd3152509ba8148041b978a0faa63c5414bb` |
| `launch.command` SHA-256     | `1b0e974347e7ef427d2b7c630dd456fe6b9c70688298f97be67458a791025eeb` |
| `stop.command` SHA-256       | `fc1ed94f280e41a5fa2b253cb51cd4c7cc52dff9b0059b00ed71696b34a835a5` |

快照目录：`~/Downloads/OrcaToolkitDevelopment-builds/orca-toolkit-dev-7B-18-20261107-arm64/`。包含同名 `.tar.gz`、`SHA256SUMS`、`BUILD-MANIFEST.json`、`PAYLOAD-SHA256SUMS` 和 `README.txt`。34 个运行文件/支持源码逐个固定哈希；Host 内置清单的 25 项全部核验通过。`BUILD-MANIFEST.json` 另记录外部 Electron 可执行文件、应用文件树及现有 Orca 应用文件树的哈希。

这是**当前 Mac 的本地构建归档**。入口含固定绝对路径，仍依赖现有 Electron、Orca CLI/runtime/profile、SSH 连接和 VPS 服务；不能将压缩包当作可迁移安装包直接在另一台机器启动。归档不含凭证、连接文件、信任配置、数据库、下载文件或运行日志；这些现有私有数据保留在原处。归档内 `repository-support` 保存基线 Python 启动器和存储模块，当前入口仍使用原仓库路径。不要移动/删除原开发目录和这些依赖，或用后续构建覆盖本快照。

校验归档：

```sh
cd "$HOME/Downloads/OrcaToolkitDevelopment-builds/orca-toolkit-dev-7B-18-20261107-arm64"
shasum -a 256 -c SHA256SUMS
```

## 启动

Finder 用“前往文件夹”打开 `~/Library/Application Support/OrcaToolkitDevelopment/live-20261107`，双击 `launch.command`。也可从任意工作目录执行：

```sh
"$HOME/Library/Application Support/OrcaToolkitDevelopment/live-20261107/launch.command"
```

保持启动终端打开；出现 `Live Toolkit ready` 后可使用 `Orca Toolkit — Development` 窗口。该入口自行启动并监督专用 SSH 隧道（本机 `127.0.0.1:22468`），无需另开测试脚本、开发服务器或手工建立隧道。正常启动不需要调试端口或 `ORCA_BACKGROUND_LAUNCH`。不要设置 `ORCA_BACKGROUND_LAUNCH=1`，该变量会隐藏窗口。

重复启动输出 `Development Host is already running.`，直接使用已打开的开发窗口。Accounts 显示现有 System Default 账户；Downloads 保留历史任务。重启后未完成任务保持暂停，确认任务后由用户点击 Resume。已完成文件会在后台重新校验，短暂显示 `Checksum verification pending` 是正常状态。

## 关闭

双击同目录 `stop.command`，或执行：

```sh
"$HOME/Library/Application Support/OrcaToolkitDevelopment/live-20261107/stop.command"
```

`Saving downloads and closing the development Host.` 表示已发送关闭请求；等待开发窗口退出、启动终端命令返回后再重开。正常关闭会保存下载状态并释放本入口创建的隧道。也可关闭开发窗口或使用该开发应用的 Quit。任务/成品保留在独立目录；停止开发版不关闭已安装 Orca 或 VPS 服务。无运行实例时输出 `Development Host is not running.`，可安全重复执行。不要用全局 `pkill Electron` / `killall ssh` 关闭它。

## 常见故障

| 现象                                    | 处理                                                                                                                                                   |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 提示已运行但未看到窗口                  | 查找 `Orca Toolkit — Development` 窗口；如曾使用后台环境变量，运行 `stop.command` 等退出，再 `unset ORCA_BACKGROUND_LAUNCH` 后启动。                   |
| 一直 Starting / Reconnecting，没有窗口  | 检查既有 VPS 网络、SSH 密钥和已信任主机身份是否可用；保留终端提示反馈。启动器会继续重连，`stop.command` 可停止等待。不要关闭主机身份校验或重装服务。   |
| `Address already in use` / 22468 被占用 | 用 `lsof -nP -iTCP:22468 -sTCP:LISTEN` 只读确认占用者。启动器拒绝借用该端口；不要杀不明进程或改生产服务端口。                                          |
| 文件不存在、权限错误、模块加载失败      | 检查原开发目录、仓库支持文件、Electron 与现有 Orca 路径是否仍存在；核对本页哈希及本地构建清单。不要覆盖 `/Applications/Orca.app`。反馈具体错误后修复。 |
| 账户用量未知或刷新失败                  | 检查现有连接后点 Refresh usage。未知不等于用量为零；保留现有登录凭证，不执行退出、切换或重新登录来盲目排错。                                           |
| 下载暂停、断线或授权暂时超时            | 普通短暂断线自动重试；重启后的暂停任务需手动 Resume。保留任务与已有分片。明确权限拒绝、SSH 身份变化或签名错误需要核实原因。                            |
| 校验失败、旧文件已移走                  | 在原目标目录确认文件，使用 Recheck checksum 重验。不要把 pending/failed 当成 verified；保留错误与任务记录反馈。                                        |
| 关闭后马上重开提示已运行                | 关闭请求异步处理，等待启动终端返回再启动。若持续不退出，保留日志和现象反馈，不强杀所有 Orca/Claude/SSH 进程。                                          |

实际数据目录：开发状态 `~/Library/Application Support/OrcaToolkitDevelopment/live-20261107`；本轮下载成品 `~/Downloads/OrcaToolkitDevelopment`。之前历史任务可指向原有其他下载目录。备份私有状态前先正常关闭开发版；不要把整个状态目录上传到 GitHub。反馈时提供现象、操作、任务状态及脱敏错误即可。

## 隔离核对与后续范围

本次最小环境、仓库外启动/重复启动/关闭/重复关闭实测通过，实际可见窗口正常加载。前后只读比对：已安装 Orca 的 8,038 个普通文件内容一致，既有 Orca 进程继续运行；现有 profile 的 settings、sshTargets 等选定域一致；远程 Claude 凭证在内的 29 个受检配置/程序文件一致；27 个原有运行服务的状态、PID、启动时间一致。本轮没有远程写入、重启服务、生产配置变更或账户操作。私有比对证据保存在开发目录，不提交 Git。

7B-18 原验收曾在既有下载服务内修复 authority 并调整下载额度，详情见原阶段记录；本次收尾以该已验收状态为基线，没有再次部署。正式应用签名/安装/发布不属于本次交付。

两个 PR 保持 Open / Draft、自动合并关闭，原 base 不变，不合并、不改 main、不推送官方上游：

- [Orca #1](https://github.com/facming/orca/pull/1)：`feature/toolkit-trusted-host` → `baseline/installed-1.4.249`。
- [Toolkit #15](https://github.com/facming/orca-tools/pull/15)：`feature/macos-orca-host-integration` → `feature/macos-security-finalization`。

后续仅根据用户日常使用反馈修复具体问题；不主动扩展技术架构、测试框架或功能范围。Blender、Godot、Unity、ComfyUI、工作台、TextEdit 及其他引擎已有代码、文档、产物完整保留并继续冻结。Orca 已有 `pnpm-lock.yaml` 本地修改保留且不纳入本次提交。

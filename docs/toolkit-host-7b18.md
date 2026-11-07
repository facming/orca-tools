# 阶段 7B-18：现有 Mac / Claude / VPS 日常使用

继续 Toolkit Draft PR #15（base `feature/macos-security-finalization`）及 Orca Draft PR #1（base `baseline/installed-1.4.249`）。起点分别为 `21e05f824b1135b674264e3ca929f6fcb2b67a62`、`8acd6e719fe4ae50583536e0d768b44052de7565`。未改 main、未合并、未推送官方上游、未覆盖 `/Applications/Orca.app`。

## 修复

- 授权请求最多 4 次，复用原始签名请求及 request ID；连接错误、超时和远端 TimeoutError 自动恢复。明确权限拒绝、无效响应及签名不匹配不作为暂时断线处理。暂停可中断等待。
- 开发启动器持续监督其自行创建的 SSH 隧道，退出时只关闭该隧道；持有单实例锁。已有端口被其他进程占用时拒绝借用。提供独立 `launch.command` / `stop.command`，关闭信号先交给 Electron 正常保存。
- 下载窗口关闭 / Quit 等待下载停止、状态落盘、Host 状态更新，然后撤销上下文。重启保留暂停任务及已校验分片；由用户 Resume，避免意外启动历史下载。
- 速度采用最近 5 秒接收数据，停顿不沿用整段平均速度；UI 显示重试阶段和等待时间。30 秒无数据重新连接；SSH 身份变化与授权拒绝有单独提示。
- 真实 1 GiB 测试发现 VPS 每个 4 MiB range 都在全局锁内重算整个源快照哈希，阻塞授权和其他读取。改为进程内最多缓存 8 个已完整验证快照的 1 MiB 分块摘要，返回每个 range 前仍校验涉及的实际字节，并检查 inode、size、mtime、ctime。元数据变化 / 服务重启重新完整校验，本地成品仍完整 SHA-256 验证。已测试跨块读取及篡改后恢复 mtime 的拒绝。

## 真实环境范围

使用已有 `vps01` 连接、已有独立下载 authority / loopback sshd 和已有 System Default Claude 账户。只更新 `/opt/orca-download/authority.py`（保留原文件备份），将现有下载服务 `max_export_bytes` 从 256 MiB 调至 2 GiB，并在原导出目录生成一个随机内容 1 GiB 文件。下载服务身份、数据库、签名密钥与信任固定值保留；未修改默认 SSHD、防火墙、现有 Claude 登录凭证或其他 Claude 进程。未创建第二账户或额外 VM。

独立开发状态位于 `~/Library/Application Support/OrcaToolkitDevelopment/live-20261107`；启动/关闭入口在该目录。成品位于 `~/Downloads/OrcaToolkitDevelopment`。开发版仍使用已有 Orca profile 的连接信息，配置和任务写入独立目录。此开发入口不是正式签名安装包。

## 实测与回归

实际操作、最终哈希和清理结果见同目录 `toolkit-host-7b18-validation.txt`。验证用的 Electron 窗口均隐藏，使用 Playwright CDP 读取真实 Host 状态；没有替换远程授权、SSH 字节传输或真实账户响应。

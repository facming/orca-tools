# 7B-16 — 账户与下载真实非生产环境验收准备

2026-11-06/07，Asia/Shanghai。**Draft / RELEASE BLOCKED**。
起点：Orca `c1e32d580f8fbe52e71d696a4bade30dca1a27fb`；Toolkit `d5e4b7e5259ea4d0babedfc97d276e7a423c48a8`。
沿用 [Orca #1](https://github.com/facming/orca/pull/1) 与 [Toolkit #15](https://github.com/facming/orca-tools/pull/15)，分支和 base 不变。

## 本阶段交付

- 用户确认尚未提供独立非生产测试账户和 VPS，因此本轮完成可复现本地证据与人工验收准备；真实远程账户、真实 VPS 下载均未执行。
- [非生产验收 runbook](toolkit-host-7b16-acceptance.md) 列明 A/B/C 身份、独立 macOS 用户/VM、VPS、四个 SSH/authority 角色、目录、权限、四类密钥、配对/固定信任、数据准备、故障操作、证据字段和有序清理；A01–A09、D01–D09、H01–H04 逐条保留待执行状态。
- [测试分类](toolkit-host-7b16-tests.md) 将原有混合测试明确拆为真实本地、模拟账户 RPC、模拟远程浏览、模拟 SSH 字节传输、真实远程账户、真实 VPS 六类。复用原有测试，无新建账户/下载架构。
- Toolkit 增加离线数据生成脚本 `scripts/prepare-7b16-fixtures.py`：拒绝已有目录，生成 16 B/32 MiB/1 GiB 等文件与完整 SHA-256 清单、分页/中文/隐藏/越界样本。不连接网络、不安装、不读取个人配置。
- 扩展现有 `scripts/test-download-history.cjs`，默认负载保留；`--large` 检查可用空间，生成 8×1 GiB 与 1,000 个小文件，覆盖正在校验时正常 close/进程退出及 SIGKILL、重新全量校验，记录列表/事件循环/CPU/RSS/逻辑读取速率。自动清理临时文件。

## 对日常使用的明确边界

禁用账户只限制 Toolkit 后续选择；切换仅影响新会话；重新登录在远端 Claude 环境操作。额度无法确认来源时 unknown 是正确的安全退化，真实 runtime 的 provenance 兼容性仍需验收。

后台复验不阻塞等待全量哈希的初始列表，但每次重启都会重新读全部历史成品。理论上 100 GiB 至少约 54.6 分钟，pending 期间不得声称 verified。暂停下载不暂停历史复验；普通 Quit 触发取消，不等待远端撤销和所有清理完成。慢磁盘、真实 Host 历史恢复、复验与新下载竞争 IO、完整 app 的交互/退出性能仍待人工验收。逻辑读取率不能冒充物理盘 IO。

本轮实测、命令、结果和未执行项见 [测试证据](toolkit-host-7b16-validation.txt)。未改产品运行时代码，没有重做 SHA-256、请求 ID、账户切换或任务恢复；没有安装生产应用、迁移真实配置、连接生产 VPS、修改 main、合并 PR 或修改官方上游。Blender、Godot、Unity、ComfyUI、工作台、TextEdit 的现有实现与文档全部保留并冻结。完整包 admission/enrollment、签名/公证及独立安全审查继续阻断发布。

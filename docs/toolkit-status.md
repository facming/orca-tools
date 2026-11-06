# Toolkit 阶段总览与功能状态

2026-11-06 更新，**Draft / RELEASE BLOCKED**。当前阶段：[7B-15 账户与下载最终完善](toolkit-host-7b15.md)。两个 P0 为 Claude 账户管理、远程文件下载管理；只验收 macOS。已有其他功能冻结保留，Windows 不作为开发验收目标。

| 阶段                          | 已实现成果                                                             | 真实测试、模拟测试与待验收边界                                                                                                                                           |
| ----------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 7B-6                          | Host API/身份及签名设计、版本溯源、显式 FD、独立 worker 基础           | 设计与本地底层测试，不等于已部署可信 Host。                                                                                                                              |
| [7B-7](toolkit-host-7b7.md)   | Host Authority、闭合资源清单、原生通信、精确窗口/上下文授权及撤销      | 本地原生通信、恶意 worker、临时签名测试；生产 admission/enrollment 未通过。                                                                                              |
| [7B-8](toolkit-host-7b8.md)   | 配置维护、Host Tasks 所有权、任务/目标租约事务、维护屏障               | 真实临时 SQLite、main/服务测试；历史任务离线审查仍待工具支持。                                                                                                           |
| [7B-9](toolkit-host-7b9.md)   | main 自有下载引擎和 Host UI、任务重建、v3 生命周期                     | 真实隐藏 Electron/preload 与本地文件；远程服务模拟；生产包未安装。                                                                                                       |
| [7B-10](toolkit-host-7b10.md) | 故障恢复、幂等重试、签名清理、目录浏览和记忆、打开目录入口             | 实际本地恢复/签名/SSH 隔离 fixture 测试；不是生产 VPS 验收。                                                                                                             |
| [7B-11](toolkit-host-7b11.md) | 账户、下载 UI、远程浏览、四引擎状态入口                                | 真实隐藏 Host 与 UI 交互；账户/目录/引擎状态模拟。                                                                                                                       |
| [7B-12](toolkit-host-7b12.md) | 原生项目工作台、ComfyUI 工作流、资源导入导出基础                       | 真实临时项目文件和 loopback；引擎执行/推理模拟。                                                                                                                         |
| [7B-13](toolkit-host-7b13.md) | TextEdit、Blender 5.2.3 LTS、Unity 6000.5.4f1 API 适配、工作流版本管理 | TextEdit 后台启动、Blender 场景/导入导出/CPU/Metal 真实通过；Unity 使用实际程序集编译通过，完整运行因 headless license 阻塞；Godot 未真实执行、ComfyUI 未真实推理。      |
| [7B-14](toolkit-host-7b14.md) | 账户恢复/刷新/并发/在途失效；下载持久重试身份、重启成品复验与清理欠项  | 真实 macOS Electron + 生产 preload、实际本地 1 GiB 与 32 MiB 文件、签名及数据库通过；账户 RPC/远程目录/网络 range 模拟。真实账户与非生产远程环境端到端验收仍待提供环境。 |

| [7B-15](toolkit-host-7b15.md) | 后台串行限速 SHA-256、三态与重试；持久重试 ID 关联；macOS 严格用量来源与远程列表刷新 | 真实本地 1 GiB、1,003 条合成历史/三个 64 MiB 文件、SIGKILL、隐藏 Electron 与签名/SQLite；远程 RPC/字节模拟。独立非生产验收流程已整理，真实远程验收未执行。 |

“已实现”不等于“真实生产验收通过”。旧阶段文档保留其当时边界，最新状态以此总览和 7B-15 清单为准。后续引擎任务：Unity 完整 Editor/Metal/Play、Godot 执行、ComfyUI 推理及 Blender 更广工作负载；本阶段不开展。发布还受完整包签名、保护目录、fuses、固定构建 enrollment 与独立安全审查约束。

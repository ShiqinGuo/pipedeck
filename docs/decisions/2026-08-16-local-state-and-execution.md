# 本地状态、Git 与执行所有权

类型：ARD（Architecture Rationale/Decision）
状态：Accepted
日期：2026-08-16
Accountable owner: TripGuru Engineering
当前契约：`src/tripguru_local/api.py`

## 背景与驱动

产品需要在桌面窗口刷新和 sidecar 重启后保留仓库登记、工作区配置与运行证据，同时执行真实 Git、质量门禁、构建和长期服务命令。Docker 与进程列表只能证明当前状态，不能拥有用户意图；Secret 也不能进入可查询的本地历史。

## 决策

控制服务使用 Python 标准库 SQLite 作为本机单写事实源，存储 Checkout、Workspace、Profile 和不可变 Run/Event 记录。Workspace 的嵌套配置以经过 Pydantic 校验的 JSON 快照保存；schema 版本由控制服务迁移。数据库路径默认位于用户本地应用数据目录，可通过进程环境变量为测试覆盖。

Secret 值不进入 API response、SQLite、命令参数或日志。Profile 只保存宿主机环境变量引用；执行器在创建子进程前解析引用并通过 environment 注入。

Git 导入、克隆和更新由独立 Repository service 负责。URL 禁止嵌入密码、Token 和非标准身份，允许标准 `git@` SSH 地址；认证委托给 Git credential helper 或 SSH agent。更新先检查 dirty、detached 和 upstream，再执行 `git pull --ff-only`，不提供 reset、clean、自动 stash 或强制 checkout。

执行器以 Run 为并发与取消边界，有限命令按计划顺序执行，长期服务以受控子进程运行并增量写入 Event。进程只由创建它的 sidecar 实例拥有；sidecar 重启时遗留的 `queued/running` Run 转为 `interrupted`。取消终止 Run 拥有的进程树；重试创建关联的新 Run。

Docker 清理使用 preview/apply 两阶段，但 preview 不是授权缓存。apply 针对准确容器 ID 重新读取状态，并逐项校验本地 ManagedResource 记录、`tripguru.local/managed=true`、MinIO 禁删和同类健康实例保留规则。

所有有副作用的 loopback API 要求本次 sidecar 生命周期的随机 token。浏览器开发模式只从显式环境变量获得 token；桌面壳生成 token、注入 sidecar，并通过 Tauri command 向同一窗口提供。

## 替代方案

- 单个 JSON 文件：实现更少，但并发事件追加、历史分页和崩溃一致性较差。
- Docker/Compose label 作为配置事实源：无法拥有本机命令、Git 与未启动工作区，也会把观察状态误作用户定义。
- 在 SQLite 加密保存 Secret：仍引入密钥生命周期与回读面，首版本机环境引用足以闭合需求。
- GitLab Runner 或通用流水线 DSL：能力远超本地开发测试目标，配置和执行语义更重。

## 后果与验证

控制服务需要明确的 schema 初始化、revision 乐观并发、终态不可变和事件顺序测试。运行日志首版采用增量轮询 API，避免同时引入 WebSocket 生命周期；客户端按事件序号恢复。长期进程在 sidecar 退出后不承诺接管，桌面壳必须先请求取消或由进程树清理兜底。

验证必须覆盖 SQLite 重启恢复、dirty/非 fast-forward Git 阻断、Secret 不落盘不进日志、取消进程树、重试新 Run、重复启动幂等、部分清理结果和 MinIO 永不删除。

## 复查条件

当需要多用户共享、跨主机执行、sidecar 热升级后接管运行、操作系统凭据存储写入，或日志规模超过本地 SQLite 轮询能力时，新增后续 ARD 重新评估 owner 与传输方式。

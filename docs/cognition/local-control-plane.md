# 本地控制面模型

Canonical record: `docs/cognition/local-control-plane.md`
Accountable owner: Pipedeck Engineering
Status: Current

## 术语、边界与粒度

- RepositorySource：一个不含内嵌凭据的远程 Git 地址；远端拥有 ref 事实。
- Checkout：一个已发现、导入或克隆的本机 Git 工作树；规范化绝对路径是本机身份，Git 拥有 HEAD、branch 和 dirty 事实。
- ProjectContract：Checkout 在某个源码快照中以 `.gitlab-ci.yml` 声明的可运行能力；解析语义为受支持的子集，不支持语义显式阻断。
- Pipeline：从 `.gitlab-ci.yml` 解析并展开后的 job DAG；解析结果与 yml SHA 一起进入 Plan 指纹。
- Environment：Workspace 绑定一个 ref（branch/tag）的 git worktree 并存实例；每个 Environment 拥有独立 Plan/Run/DeploymentRevision 与派生的 Compose project identity。
- Workspace：一个可保存的多 Checkout 本地集成定义。
- Service：Workspace 对某个 Checkout 的命令、ExecutionTarget、Endpoint、Readiness、环境变量和 ConnectionProfile 配置。
- ExecutionTarget：服务期望运行的位置；HostTarget 拥有宿主进程契约，ComposeTarget 拥有明确 Dockerfile 或 Compose services 契约。
- MiddlewareBinding：Workspace 为一种中间件选择的当前 RuntimeResource identity。
- ConnectionProfile：服务消费一种中间件时的类型化输出契约；它拥有账号、database/bucket 语义和 Secret metadata 引用，不拥有 Secret value。
- RuntimeResource：Docker inspect 的当前状态投影，只拥有容器 identity、health 与 published endpoint，不拥有连接语义。
- Plan：从当前 Workspace revision、源码和 Runtime 快照生成的不可变预检结果，包含配置与源码指纹。
- Run：一次 Plan 的执行记录；重试总是创建新 Run，并以 `retry_of` 关联旧记录。
- DeploymentRevision：一次 Compose target 的不可变 intent、artifact、previous revision、状态与恢复结果。
- ManagedResource：系统创建的本地中间件容器 intent 与 lifecycle；P0 只支持 PostgreSQL 18，状态为 planned、provisioning、active、failed、removed。
- Runtime：Docker 或本机进程中的实际状态投影。

## 事实源与生命周期

项目自动识别能力由仓库内容拥有；用户覆盖命令、机器路径、Workspace、Plan、Run 与 DeploymentRevision 历史由控制服务的本地 SQLite 拥有。Secret 值只存在于宿主机环境或 Windows Credential Manager，SQLite 只保存 metadata 和引用。Docker 和本机进程只提供实际运行状态。

客户端和未来 CLI 都是同一本地控制 API 的消费者，不拥有第二套配置或调度逻辑。

## 不变量

- 未通过计划校验的 Workspace 不可启动。
- 一次执行绑定明确的源码快照和配置指纹。
- Compose project identity 由 Workspace 与 target 稳定派生；用户输入不能接管任意外部 Compose project。
- 同一 Compose target 只有一个未决 revision 和一个 active revision；新 revision 激活时 previous 原子进入 superseded。
- dirty worktree 不会被更新、重置或覆盖。
- Git URL 不允许嵌入密码、Token 或非标准身份；允许 `git@host:path`/`ssh://git@host/path`，私有仓库认证委托给本机 Git credential helper 或 SSH agent。
- 命令参数和冻结 Compose 文件不承载 Secret；敏感配置只能通过环境引用在执行瞬间注入，日志永不回显 raw、URL-encoded 或组装后的值。
- `queued -> running -> succeeded | failed | cancelled | interrupted` 是 Run 的唯一状态前进方向；重试不回写旧 Run。
- 外部资源默认只读；平台只变更明确拥有的资源。
- ManagedResource 必须在 Docker mutation 前落盘；runtime ID、稳定名称、kind、workspace、image、port 与 labels 任一不匹配都不得接管或删除。
- 每类受保护中间件至少保留一个健康实例，MinIO 不得被自动清理。
- 容器 cleanup 不等于数据销毁；PostgreSQL volume 不属于批量 cleanup 的删除范围。
- 外部或共享数据库默认不执行自动 migration。

## 示例

开发者选择 Supplier 后端和管理端，后端绑定现有 PostgreSQL 18 与 MinIO，并用 PostgreSQL/MinIO ConnectionProfile 配置输出变量和 Credential Manager 引用。控制服务检查仓库、结构化命令、target、endpoint、readiness 和连接目标后生成计划；只有阻断项清零时客户端才允许创建 Run。Host target 在执行时得到 loopback URL，Compose target 在部署时得到 `host.docker.internal` URL，日志、Plan、Run 与 frozen Compose 都不保存 Secret 或完整连接值。

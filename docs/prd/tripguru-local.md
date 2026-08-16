# TripGuru Local 多项目开发控制台

Canonical record: `docs/prd/tripguru-local.md`
Accountable owner: TripGuru Engineering
Status: Current

## 问题与用户

TripGuru 开发者需要在大量独立前后端仓库之间切换，手工处理 Git、运行命令、端口、数据库连接和 Docker 容器。开发完成后的本地集成验证缺少统一入口、可重复配置和可观察结果。

## 结果与成功标准

开发者能够在一个只面向本机的客户端中：

1. 浏览有权限的 TripGuru 仓库或导入已有本地仓库。
2. 选择多个仓库组成工作区，并为每个服务配置 Host 或 Compose target、命名 endpoint 和本地中间件。
3. 在启动前看到完整计划、阻断原因和恢复动作。
4. 运行快速检查或完整本地集成流程，并看到逐阶段状态和日志。
5. 只清理平台拥有的资源，且不会删除最后一个健康的 PostgreSQL、Redis、Elasticsearch 或 MinIO。

完整本地纵向链路以下列可验收结果为成功标准：

1. 可从本地目录导入仓库，或把无凭据的 Git URL 克隆到用户选择的目录；私有仓库沿用本机 Git 凭据。
2. 可保存并重新打开多项目工作区；每个服务可覆盖结构化 argv、环境来源、endpoint、readiness 与执行 target。
3. 中间件绑定选择当前 Docker 实例；PostgreSQL/MinIO 的类型化 Profile 把连接输出注入宿主进程或 Compose 容器，Secret value 仅由 Windows Credential Manager 提供。
4. 预检通过后可真实运行；客户端持续显示阶段、服务状态和日志，并支持取消、失败重试和历史回看。
5. Git 更新遇到 dirty worktree 或非 fast-forward 时明确阻断，不重置、不覆盖。
6. 清理先预览并只作用于平台拥有的资源；执行瞬间重新校验保护规则，MinIO 永不进入可删除集合。
7. 集成 target 把当前源码和配置构建为不可变本机镜像；替换后验证显式 readiness，失败恢复 previous revision，恢复失败进入可操作的 degraded 状态。
8. 可由客户端为指定工作区创建一套托管 PostgreSQL 18；只有系统持久化 intent、Docker ownership 和健康检查完全一致时才可绑定或清理。

## 范围与非目标

范围包括独立桌面客户端、本地控制服务、本机 Git、GitLab/GitHub 等 Git URL、Host 进程、Docker Compose、工作区配置、运行与部署历史，以及 Windows Credential Manager Secret。

不包括 staging/production 发布、Kubernetes 管理、云开发环境、远程数据库迁移、自动删除外部容器，以及完整复刻 GitLab Runner。

## 约束与链接

- 所有控制接口只绑定 loopback。
- Docker 资源以 label 明确 ownership。
- 项目运行契约与机器本地配置分离。
- Docker 和进程只投影实际状态；Workspace、Plan、Run 与 DeploymentRevision 是期望配置和历史的事实源。
- 首版托管中间件只创建 PostgreSQL 18 容器；Redis、Elasticsearch 和 MinIO 使用外部实例，MinIO 永不进入创建或删除动作。
- 行为验收见 [../behavior/workspace-plan.md](../behavior/workspace-plan.md)。
- 视觉和交互验收见 [../design/visual-interaction.md](../design/visual-interaction.md)。

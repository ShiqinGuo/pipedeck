# Pipedeck — 本地多项目 CI/CD 控制台

Canonical record: `docs/prd/pipedeck.md`
Accountable owner: Pipedeck Engineering
Status: Current（2026-09-07 用户确认定位）

## 用户与核心价值

开发者需要把前端、后端及关联服务集成起来测试业务功能。Pipedeck 将多个项目的检查、构建、本地部署与依赖准备组织在一个控制台中，让本机成为可直接使用的测试环境，减少为验证功能而向服务器发布的等待和配置工作。

核心流程：选择项目与版本 → 配置服务和中间件连接 → 预检、构建、部署 → 检查环境就绪 → 打开应用测试功能 → 修改后更新并再次验证。

成功指标：首次得到可测环境的时间、一次修改到再次可测的时间、手工配置次数、失败恢复成本及能验证的业务链路范围。Pipeline 通过、进程存活和环境就绪分别展示，业务正确性由手工或自动化测试确认。

## 当前能力

- 保存多项目 Workspace，配置命令、环境变量、Host/Compose target、中间件连接与 HTTP/TCP readiness。
- 声明项目间 `depends_on`，预检阻断缺失或循环依赖；按依赖顺序部署或启动，并等待前置服务就绪。
- GitLab CI 子集用于复用仓库 pipeline；`.pipedeck.yml` 提供本地声明，工作区保留机器相关配置。
- 运行计划保存源码 HEAD、工作树指纹及服务目标；历史运行保持不可变。
- 本地 Compose 构建不可变镜像，记录 DeploymentRevision，执行就绪检查及已有 revision 的恢复流程。
- 工作区与运行详情显示每个服务的实时观测、运行版本和恢复信息；已移除但仍运行的目标保持可见。可通过 `application.endpoint/path` 指定 HTTP 测试页面。服务就绪并且该页面可访问后提供入口；未配置时只尝试 HTTP 就绪端口的根页面，TCP 检查不猜测网页协议。
- 为明确选定的项目创建 worktree。应用分支检出会更新工作区对应项目和依赖引用，再按新配置预检；被工作区引用的 checkout 不允许删除。
- GUI 与 CLI 使用同一控制 API。Secret 使用宿主环境或系统凭据引用，清理遵守 ownership 与中间件保护规则。
- 同一工作区同时只允许一个 queued/running；活动运行阻断分支应用。失败/取消回收此次拥有的 Host 进程，日志增量读取并保留终态尾日志。

## 范围与边界

只管理本机，不管理 staging/production 或远程部署。Docker 提供运行观测，工作区配置与运行历史由本地控制服务拥有。

工作区定义整套集成组合；Environment API 当前记录单个项目的分支 checkout。创建 checkout 本身不创建整套隔离环境。多个环境需要显式配置不同工作区、端口和数据绑定；整套环境复制、自动端口分配、数据快照与重置仍是后续设计事项。

GitLab CI 支持语义以实现与测试为准；不支持的语义明确阻断。扩展语法支持的优先级由真实项目的集成需要决定。

HTTP/TCP 就绪检查来自实际观测及应用提供的检查端点，不自动证明数据库迁移、测试账号、基础数据或全部业务依赖已准备完毕。项目可通过本地声明的任务准备数据与执行功能冒烟；共享数据库不自动迁移。

## 验收与依据

- [工作区与运行行为](../behavior/workspace-plan.md)、[交互规范](../design/interaction-model.md)
- [本次研究与优化依据](../research/2026-09-07-local-integration.md)
- `tests/test_integration_environment.py`：真实 Git 仓库、两项目构建和依赖启动、前端 HTTP → 后端 API → SQLite 写入查询、版本观测与取消。
- `apps/web/e2e/integration.spec.ts`：每服务状态、测试入口、分支应用顺序和编辑草稿冲突恢复。

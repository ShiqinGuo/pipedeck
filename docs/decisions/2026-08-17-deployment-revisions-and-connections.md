# 部署 Revision、连接投影与系统凭据

类型：ARD（Architecture Rationale/Decision）
状态：Accepted
日期：2026-08-17
Accountable owner: TripGuru Engineering
取代范围：`2026-08-16-local-state-and-execution.md` 中“首版仅使用宿主机环境引用”和“Run 只拥有宿主进程”的限制

## 背景与驱动

工作区选择 Docker 中间件后，选择必须实际改变子进程或容器收到的连接配置。集成模式还必须把当前源码构建为可识别 artifact、替换明确的本地应用目标、验证 readiness，并在失败或 sidecar 重启后有确定恢复状态。简单执行 `docker compose up` 无法拥有这些不变量。

现有 TripGuru 仓库并不统一：有些仓库有完整应用 Compose service，有些只有 Dockerfile，Supplier 后端的 `compose.yml` 只拥有 MinIO。因此应用部署不能由“仓库里是否碰巧存在 Compose 文件”推断。

## 决策

### 执行目标

每个 Workspace Service 明确选择一种 target：

- HostTarget：以结构化 argv 在宿主机运行，声明 endpoint injection 与 HTTP/TCP readiness。
- ComposeTarget：由已有 Compose service 或 Dockerfile 生成 service，最终都通过一个 TripGuru Local 专属 Compose project 运行。

Compose project name 由 `workspace_id + target_id` 稳定派生，不允许用户自由填写。source compose files、Dockerfile 和 build context 必须规范化后仍位于 checkout。已有 Compose service 必须通过 `docker compose config --services` 证明存在；Dockerfile target 由控制服务生成单 service Compose model。

### DeploymentRevision

一次 Compose 部署创建不可变 DeploymentRevision，拥有 source content fingerprint、当前 Git HEAD、Workspace revision、target config fingerprint、生成后的 Compose override、image tag/digest、previous revision、状态和恢复结果。dirty source fingerprint 必须包含 tracked diff 与 untracked 文件内容；只记录 `git status` 文件名不足以证明 artifact 输入。

状态只能沿以下方向前进：

```text
planned -> building -> applying -> verifying -> active -> superseded
                    \-> failed
                              \-> recovering -> rolled_back | degraded
```

同一 target 同时只能有一个 `building/applying/verifying/recovering` revision，且只有一个 current active revision。新 revision 激活时，previous active 在同一事务中进入 `superseded`。intent 和预期 ownership 在第一次 Docker mutation 前写入 SQLite。构建使用 SHA/config 派生的不可变本机 image tag；生成 override 注入固定 image、端口、连接环境和 `tripguru.local/managed=true`、workspace、target、revision labels。

build 失败不改变旧 active revision。apply 或 verify 失败时使用 previous revision 保存的 Compose config 与 image 恢复；恢复失败进入 `degraded`，客户端提供 reconcile/rollback，绝不把命令退出、取消或进程 kill 表述为已回滚。sidecar 启动时对未决 revision 与 Docker labels 做 reconcile。

### Readiness 与端口

端口是命名 ServiceEndpoint，而非仅用于冲突检查的整数。每个 endpoint 声明 protocol、host port、可选 container port和注入策略；Host target 可由 adapter 注入 env/argv，Compose target 由生成 override 写端口映射。

`running` 不等于 ready。每个长期 Host 服务和 Compose 应用 target 都必须声明 HTTP 或 TCP probe；Compose healthcheck 可以作为额外证据，但没有 healthcheck 时 `docker compose --wait` 只证明容器运行，不能代替应用 probe。

### 中间件连接

RuntimeResource 只投影 `docker inspect` 得到的 structured published endpoints，不拥有用户名、数据库、bucket 或 Secret 语义。Workspace Service 使用 kind-specific ConnectionProfile：

- PostgreSQL adapter 输出一个已正确编码的 URL env。
- MinIO adapter 输出 endpoint、access key、secret key和 bucket 等多个 env。
- Redis/Elasticsearch 以后以独立 adapter 扩展，不塞进通用 URL 模板。

Host consumer 使用 `127.0.0.1:published_port`；Compose consumer 使用 `host.docker.internal:published_port`。没有 published endpoint 时预检阻断，未来若支持 network attach 必须另立契约。profile 输出与显式 environment 同名时阻断，不能静默覆盖。

Plan 只展示 env 名和脱敏后的 host/port/database/bucket。执行或部署 mutation 前重新读取 Runtime、resource identity、health、endpoint 与 Secret；选择变化使旧 Plan 失效。

### Secret 所有权

Windows Credential Manager 独占 Secret value；SQLite 只保存 id、name、scope、presence、version 和时间。API 只提供 write/update/delete/list-presence，永不回读 value。Secret 只进入 child environment 或生成 Compose 环境的执行瞬间，不进入 argv、Plan、Event 或可提交文件。

Credential Manager 与 SQLite 不共享事务：写 credential 成功而 metadata 失败时 best-effort 删除；metadata 存在但 credential 缺失时预检或执行返回 `SECRET_MISSING`。冻结 Compose JSON 只保存 `${TGL_*:?}` 占位符，执行时才从临时父进程环境解析；raw、URL-encoded 和组装后的敏感连接值都必须注册到日志 redactor。

### 托管中间件

P0 只允许系统创建 PostgreSQL 18，不创建 Redis、Elasticsearch 或 MinIO。ManagedResource intent 在第一次 Docker mutation 前写入 SQLite，拥有 workspace、kind、固定 image、host/container port、账号/database 和 Secret metadata 引用；Secret value 仍只由 Credential Manager 拥有。

容器名称由 workspace 与 kind 稳定派生，并注入 managed、workspace、kind 和 resource labels。`planned -> provisioning -> active | failed -> removed` 由 SQLite CAS 推进；只有精确 ownership、image、端口与 healthy inspect 全部成立才进入 active。reconcile 可在 sidecar 中断后按稳定名称或已知 runtime ID 恢复，但同名外部容器永不被接管。

批量 cleanup 必须再次委托 ManagedResource lifecycle，以本地 intent、Docker ID 前缀、kind、workspace 和 labels 复核后删除容器并记录 removed。该动作只拥有容器进程生命周期，不自动删除 PostgreSQL data volume；未来若需要销毁数据，必须新增独立的显式确认契约。

## 替代方案

- 只运行原 Compose：无法为没有应用 service 的仓库部署，也不能稳定注入 ownership 和 immutable image。
- 把 Docker 容器当 target 配置：容器 ID 是可变 Runtime identity，不能拥有用户期望或替换关系。
- 使用 `latest` tag：无法证明当前源码，也无法可靠回到 previous artifact。
- 将 Secret 加密写 SQLite：引入第二个密钥生命周期和回读面；Windows Credential Manager 已是本机目标的正确 owner。
- 所有中间件使用一个 URL 模板：无法表达 MinIO 多输出、TLS/auth 差异和不同 consumer 的网络可达性。

## 后果与验证

需要增加 target/revision schema、kind adapter、Credential Manager boundary、Compose compiler/deployer、target lock和启动 reconcile。客户端必须把 target、endpoint、connection outputs、Secret presence、revision、verify 与 recovery 显示为一等状态。

验收至少包括：两套 PostgreSQL 选择产生不同 sentinel；Host/Compose 对同一实例生成不同可达 host；当前 SHA 镜像与 labels 可由 `docker inspect` 证明；verify 失败恢复 previous image；恢复失败显示 degraded；sidecar 在 apply 中断后能 reconcile；MinIO 和外部资源仍不可删除。

## 复查条件

当支持 Podman、容器直接加入中间件 network、远程 registry、Kubernetes、跨主机执行或多用户共享时，新增后续决策，不扩张本机 Docker Desktop P0 的隐含行为。

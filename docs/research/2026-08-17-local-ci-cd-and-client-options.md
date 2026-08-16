# 本地 CI/CD 与客户端方案调研

类型：Research
状态：Frozen input
日期：2026-08-17
Accountable owner: TripGuru Engineering

## 调研问题

TripGuru Local 只服务开发者本机。它需要把仓库接入、多项目配置、快速质量门禁、宿主机开发进程、Docker Compose 集成部署、中间件连接选择、运行证据和受控清理放在一个客户端中。本调研回答三个问题：

1. 是否应直接采用现成 CI、开发循环或容器 GUI。
2. 哪些能力应复用本机现有工具，哪些状态必须由 TripGuru Local 拥有。
3. Python、Node、Compose 与四类中间件怎样落到同一套本地契约。

## 评估结论

采用“薄桌面客户端 + 本地控制服务 + 原生工具适配器”的组合：

- React/Tauri 只负责安全的本机 GUI 和 sidecar 生命周期。
- Python 控制服务拥有 Workspace、Plan、Run、DeploymentRevision、Secret metadata 和恢复状态。
- 快速 CI 默认直接运行仓库锁定工具，例如 `uv`、`pnpm` 和项目自带脚本；未来可增加 Dagger adapter，但不要求项目迁移 DSL。
- 本地 CD 复用 Docker Compose/BuildKit，不另造容器编排器；每次集成部署使用明确 target、不可变源码 revision、生成的 override、ownership label、readiness 和恢复记录。
- Docker 与进程扫描只提供实际状态，不能反向成为期望配置的事实源。

现成工具都能覆盖问题的一部分，但没有一个同时拥有本产品的组合 Workspace、Host/Compose 双目标、中间件连接投影、Windows 凭据、历史证据和保守清理语义。

## 方案比较

| 方案 | 强项 | 与目标的主要错位 | 采用方式 |
| --- | --- | --- | --- |
| GitLab Runner Docker executor | 隔离 job、image/service、接近远端 CI | 需要 Runner 管理；面向 job 而非长期本地 Workspace；不能直接拥有桌面配置、现有 Compose target 和安全清理 | 不内嵌；项目仍可把 TripGuru Local 命令对齐 GitLab 脚本 |
| Dagger | local-first、类型化 pipeline、内容寻址缓存、服务与 Secret | 引入额外 engine/module；更适合 CI 图，不直接解决 GUI、既有 Compose 替换和本机资源治理 | 后续作为可选 CI adapter，不作为 P0 运行时依赖 |
| Earthly | 容器化构建与缓存，适合多项目 CI | 需要 Earthfile；官方也指出单语言紧密本地循环通常不占优；不拥有部署恢复 | 不内嵌；保留自定义命令接入能力 |
| Docker Compose Watch | 低额外依赖，能 sync/rebuild/recreate | 要求 Compose `develop.watch`；是开发循环，不是 revision、回滚、Secret 或历史控制面 | 后续给已有 `develop` 配置的项目提供 watch 模式 |
| Tilt + Compose | 多服务可视化、增量 build、Live Update | 要求 Tiltfile；偏持续 inner loop，不覆盖仓库接入、凭据、统一历史和保守 cleanup | 借鉴资源状态与日志交互，不设为必需依赖 |
| Skaffold | 完整 build/test/deploy/watch 顺序 | 核心目标是 Kubernetes；当前产品明确不管理 Kubernetes | 排除 |
| DevSpace | Kubernetes 开发、同步、端口转发、localhost UI | Kubernetes-first；比本地 Docker Compose 目标更重 | 排除 |
| DevPod | 从 Git/目录创建 Dev Container Workspace，有桌面 GUI | Workspace 粒度偏单开发容器；不负责多仓服务集成和现有中间件选择 | 借鉴仓库创建入口与 provider 思路 |
| Portainer | Docker stack、Git polling/webhook、容器管理 | 需要常驻管理服务；Git stack 更新不等于本地源码 CI；不管理 Host 服务和项目级命令 | 不嵌入；借鉴 stack/redeploy 状态表达 |
| Dockge | Compose stack-first GUI，操作与日志集中 | 只管理 Compose 文件和容器，不拥有 CI、Git、安全凭据和多项目 Host 流程 | 借鉴紧凑 stack 工作台 |
| Podman/Podman Desktop | daemonless/container GUI、Docker CLI 迁移路径 | Windows 仍需 VM；`podman compose` 委托外部 Compose provider，行为受 provider 影响 | P0 锁定 Docker Desktop；Runtime Protocol 保留未来 Podman adapter |
| 自建 Tauri + 本地 API | 能准确拥有产品 contract、仅 loopback、可离线打包 | 需要自己证明安全、恢复、安装和端到端行为 | 采用；严格限制为本机控制面 |

## 为什么不复刻 GitLab CI

GitLab Runner 的 Docker executor会把 job 分成 prepare、pre-job、job 和 post-job，并以 image/services 提供隔离。这适合远端共享 runner。TripGuru Local 的目标不同：它还要启动需要持续存在的开发服务、复用用户已有 checkout 和本机缓存、选择已有中间件、替换明确的本地 Compose service，并在桌面端解释和恢复状态。

因此本产品只保留 GitLab-like 的用户体验：阶段、日志、失败位置、重试和不可变历史；不解析完整 `.gitlab-ci.yml`，也不注册本机 Runner。项目命令仍由仓库声明，后续 adapter 可把同一脚本同时用于本机与 GitLab。

## 为什么以 Compose/BuildKit 实现本地 CD

Docker Compose 已经拥有服务依赖、网络、volume 和 recreate 语义；BuildKit 默认提供增量、并行与内容相关缓存。Compose Watch 还能在项目明确声明开发规则时执行 sync 或 rebuild。TripGuru Local 只补它们没有拥有的控制面：

- 从 Workspace target 稳定派生 Compose project identity，禁止任意名称接管外部项目。
- 校验 checkout 内的 compose files、profiles 和 service names。
- 把当前 Git SHA、配置指纹与镜像 digest 固化为 DeploymentRevision。
- 通过生成的、本机状态目录中的 override 注入 image reference、端口、连接环境和 ownership labels，不修改仓库文件。
- build 成功后才 apply；apply 后按 HTTP/TCP/Compose health contract 验证。
- 记录 previous revision；失败进入 rollback/reconcile，而不是把 CLI 退出或 kill 冒充恢复。
- sidecar 重启后以 SQLite intent 和 Docker label 对账未决 revision。

BuildKit 本地缓存默认可直接复用。首版不依赖远程 registry；镜像保存在本机 Docker image store。离线场景仅要求所需基础镜像与语言依赖已存在，Git clone/依赖首次下载仍可能需要网络。

## 常用技术栈适配

| 技术栈 | 快速 CI | Host 开发目标 | Compose 集成目标 | Readiness | 缓存与约束 |
| --- | --- | --- | --- | --- | --- |
| Python 3.12 + uv + FastAPI | `uv sync --frozen`、仓库 Ruff/Pyright/Pytest | argv 启动 Uvicorn；端口通过明确 adapter/env/argv；不隐式读取 TripGuru 配置之外的 `.env` | 使用项目 Dockerfile/Compose build；固定 SHA image | `/health` 证明进程；`/ready` 证明 PostgreSQL 等依赖 | 复用 uv cache；命令以 argv 保存 |
| React/Vue + Vite + pnpm | `pnpm install --frozen-lockfile`、lint/typecheck/test/build | `pnpm ... dev --host 127.0.0.1 --port N` adapter | 构建静态资源或项目 Compose service | HTTP probe | 复用 pnpm store；禁止同步宿主 `node_modules` 到 Linux 容器 |
| Nuxt + pnpm | install、lint/typecheck/test/build | 显式 host/port adapter | 项目 Dockerfile/Compose service | HTTP probe | 区分 dev server 与 production preview |
| Docker Compose | `docker compose config` 预检、可选 build | 不适用 | 指定 files/profiles/services，生成 override 后 build/apply/verify | Compose health 或显式 probe | 稳定 project identity；同 target 单写锁 |
| PostgreSQL 18 | 可选连接 probe，不自动迁移外部库 | published endpoint `127.0.0.1:host_port` | 默认 `host.docker.internal:host_port`；未发布则阻断或未来显式 network attach | Docker health + 可选 SQL sentinel | URL 由 adapter 组装；密码来自 Credential Manager |
| MinIO | endpoint/bucket probe | published HTTP endpoint | `host.docker.internal` published endpoint | Docker health + 可选 bucket probe | endpoint、access key、secret key、bucket 是多输出；MinIO 永不自动删除 |
| Redis | 后续 typed adapter | published endpoint | `host.docker.internal` published endpoint | PING/health | 不把所有中间件压成通用 URL |
| Elasticsearch | 后续 typed adapter | published HTTP endpoint | `host.docker.internal` published endpoint | cluster health/HTTP | 明确 TLS/auth 与 endpoint 输出 |
| Windows + Docker Desktop | 本机工具进程 | Job Object/进程树取消 | Linux container + Compose v2 | loopback/HTTP/TCP | Secret value 仅 Windows Credential Manager；API 只监听 `127.0.0.1` |

## 配置与客户端原则

- 项目 contract 与机器 profile 分离。仓库可声明标准命令/Compose 能力，本机 Workspace 只保存 target、端口、连接和 Secret 引用。
- 命令是 argv 数组，不经过 shell 字符串拆分；GUI 应编辑单个参数或由 adapter 生成。
- 中间件 adapter 输出多个命名环境变量。显式环境变量与 adapter 输出同名时预检阻断，不能静默覆盖。
- Host consumer 使用 loopback published port；Compose consumer使用 `host.docker.internal` published port。未发布端口不能猜测。
- Secret API 只允许写、更新、删除和查询 presence；永不回读 value。SQLite 只保存 metadata 和引用。
- 端口必须绑定实际注入策略或 Compose mapping；只做冲突检查的字段不能在 UI 中称为“监听配置”。

## 采用顺序

1. 先闭合 Host target、PostgreSQL/MinIO adapter、Windows Credential Manager、真实双项目运行和取消。
2. 再闭合 Compose target、DeploymentRevision、不可变 image、ownership、verify、rollback/reconcile。
3. 再提供 Redis/Elasticsearch adapter、Compose Watch 与可选 Dagger adapter。
4. Podman 只在 Runtime Protocol 与真实 Windows 验证都成熟后进入支持矩阵。

## 官方资料

- [Docker Compose application model](https://docs.docker.com/compose/intro/compose-application-model/)
- [Docker Compose project name](https://docs.docker.com/compose/how-tos/project-name/)
- [Docker Compose up](https://docs.docker.com/reference/cli/docker/compose/up/)
- [Docker Compose startup order and health](https://docs.docker.com/compose/how-tos/startup-order/)
- [Docker Compose Develop specification](https://docs.docker.com/reference/compose-file/develop/)
- [Docker Compose Watch](https://docs.docker.com/compose/how-tos/file-watch/)
- [Docker BuildKit](https://docs.docker.com/build/buildkit/)
- [Docker build cache optimization](https://docs.docker.com/build/cache/optimize/)
- [GitLab Runner Docker executor](https://docs.gitlab.com/runner/executors/docker/)
- [Dagger overview](https://docs.dagger.io/)
- [Earthly caching](https://docs.earthly.dev/docs/caching)
- [Tilt with Docker Compose](https://docs.tilt.dev/docker_compose.html)
- [Skaffold dev workflow](https://skaffold.dev/docs/workflows/dev/)
- [DevSpace development workflow](https://www.devspace.sh/docs/getting-started/development)
- [DevPod workspace creation](https://devpod.sh/docs/developing-in-workspaces/create-a-workspace)
- [Portainer Git automatic updates](https://docs.portainer.io/faqs/troubleshooting/stacks-deployments-and-updates/how-do-automatic-updates-for-stacks-applications-work)
- [Podman Compose provider model](https://docs.podman.io/en/v5.6.2/markdown/podman-compose.1.html)
- [Tauri external binaries](https://v2.tauri.app/develop/sidecar/)
- [Tauri Windows installer](https://v2.tauri.app/distribute/windows-installer/)
- [Tauri dialog plugin](https://v2.tauri.app/plugin/dialog/)
- [Microsoft CredWriteW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)
- [Microsoft CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)

# Pipedeck

> GitLab 开发者的本地 CI/CD 工作台 · A local CI/CD workbench for GitLab developers

[中文](#中文) · [English](#english)

Pipedeck 是一个只运行在开发者本机的 CI/CD 客户端：导入或克隆仓库、解析你的 `.gitlab-ci.yml`、在本地真实执行 job，并提供多项目工作区、Compose 部署、运行历史与 CLI。就像把你的 GitLab Runner 搬到桌面上，并且**直接跑在你自己写的 `.gitlab-ci.yml` 上**。

Pipedeck is a CI/CD client that runs only on your developer machine. It imports or clones repositories, parses your `.gitlab-ci.yml`, executes jobs locally for real, and provides multi-project workspaces, Compose deployments, run history, and a CLI. Think of it as your GitLab Runner moved to your desktop — running **directly on your own `.gitlab-ci.yml`**.

---

## 中文

### 它是什么

GitLab CI/CD 开发者在本地验证 pipeline 时一直没有好用的入口：官方 runner 只能服务化运行，`gitlab-runner exec` 已在 v17.0 移除，而纯终端工具没有运行历史、没有失败聚合。Pipedeck 把"仓库 → 管道 → 执行 → 部署"整条链路放进一个本机桌面端与 CLI 中。

### 功能特性

- **本地解析并执行 `.gitlab-ci.yml`**：预览完整管道（job / stage / needs / image / 变量展开）；有 image 的 job 在镜像容器内运行，无 image 的 job 在宿主 shell 运行；不支持的语义**显式阻断**，绝不含糊降级。
- **多项目工作区**：保存命令、环境变量、连接 profile 与运行目标，按 rev 版本化；可一键运行预检、启动、构建与质量门禁。
- **本地 Docker 集成**：识别 PostgreSQL / Redis / Elasticsearch / MinIO 中间件；把当前源码构建为不可变镜像，替换明确的 Compose target，验证 readiness，并记录可恢复的 DeploymentRevision。
- **git worktree 多 Environment 并存**：不同分支 / 标签各自独立 worktree、独立 Compose project、独立部署 revision，可回滚。
- **Secret 安全**：Secret 值仅写入 Windows Credential Manager，响应、日志与界面永不回显敏感值。
- **运行历史完整可回放**：按 job 分组日志、取消、重试、单 job 重跑。
- **GUI 是 CLI 的壳**：界面与 `pipedeck` 命令走同一控制 API，每个操作面板展示等价 CLI 命令，支持 `pipedeck run --wait` 的 headless 固化。
- **受保护的清理**：清理只作用于平台拥有（ownership label）的资源，MinIO 与每类中间件最后健康实例永不可删。

### 支持语义基线

`stages`、`script` / `before_script` / `after_script`、`variables`（含本地预定义 `CI_*`）、`rules:if`（子集）、`needs`、`image`、`artifacts`（paths + reports:dotenv）、`include`（local/remote/template 缓存）、`extends` / `!reference`、`workflow` / `default`、`allow_failure`、`when`。

不支持的语义（如 `trigger:project`、`pages`、OIDC/Vault secrets、`id_tokens`、Kubernetes）会被明确阻断并提示原因。

### 系统要求

- Windows 10 / 11（x64）
- [Git](https://git-scm.com/download/win)（必须）
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)（运行容器 job / Compose 部署时需要）

### 安装

从 [GitHub Releases](https://github.com/ShiqinGuo/pipedeck/releases) 下载最新安装包：

- **`Pipedeck_x64-setup.exe`**（NSIS 安装包，推荐）——双击安装，安装器会自动把 `pipedeck` CLI 写入 PATH。
- `Pipedeck_x64_en-US.msi`（MSI 安装包）——适合企业批量部署。

安装完成后，从开始菜单启动 **Pipedeck**，或直接在终端使用 CLI：

```powershell
pipedeck status          # 查看控制面 / 仓库 / 中间件摘要
pipedeck serve           # 启动本地控制服务（GUI 已自动启动时无需）
```

### 快速开始

1. 打开 Pipedeck，在 **仓库（Repositories）** 页导入本机已有 checkout，或从 GitLab 克隆。
2. 仓库自动解析 `.gitlab-ci.yml`，进入 **管道预览** 查看会跑什么。
3. 点击 **运行**，在本地真实执行 pipeline；有 `image` 的 job 跑在容器里。
4. 需要多项目联动时，在 **工作区** 页组合多个仓库，保存命令与连接配置。

### 本地开发

```powershell
uv sync
corepack pnpm install
uv run pipedeck serve       # 本地控制服务（loopback 7421）
corepack pnpm --filter @pipedeck/web dev   # Web 界面（127.0.0.1:5173）
```

桌面端构建：

```powershell
corepack pnpm sidecar:build && corepack pnpm --filter @pipedeck/web build
corepack pnpm desktop:build   # 产物在 apps/desktop/src-tauri/target/release/bundle/
```

质量门禁：

```powershell
uv run ruff format --check . ; uv run ruff check . ; uv run pyright ; uv run pytest
corepack pnpm --filter @pipedeck/web lint
corepack pnpm --filter @pipedeck/web typecheck
corepack pnpm --filter @pipedeck/web test
```

### 国际化

- 界面支持 **简体中文 / English** 切换，在 **设置 → 语言** 中选择，选择后立即生效并持久化。
- 后端 CLI 输出与 API 错误消息为英文（面向国际开发者）；代码注释保留中文。
- 新增文案时，请把界面字符串放入 `apps/web/src/i18n/locales/zh|en/` 分区资源，并调用 `t()`。

### 技术栈

| 层 | 技术 |
|---|---|
| 控制服务 | Python · FastAPI · Pydantic · SQLAlchemy |
| Web 界面 | React 19 · TanStack Router/Query · Tailwind CSS · i18next |
| 桌面壳 | Tauri 2（Rust）· Windows |
| 本地执行 | Git · Docker / Docker Compose · GitLab CI 子集解析器 |
| 密钥 | Windows Credential Manager |

### 文档

- 产品范围与验收：`docs/prd/pipedeck.md`
- 领域模型与事实源：`docs/cognition/local-control-plane.md`
- 交互规范：`docs/design/interaction-model.md`
- 决策记录：`docs/decisions/`
- 目录索引：`docs/README.md`

### 许可

见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## English

### What it is

GitLab CI/CD developers have long lacked a good way to validate pipelines locally: the official runner only runs as a service, `gitlab-runner exec` was removed in v17.0, and terminal-only tools offer no run history or failure aggregation. Pipedeck brings the whole path — repository → pipeline → execution → deployment — into one local desktop client and CLI.

### Features

- **Parses and executes your `.gitlab-ci.yml` locally**: preview the full pipeline (jobs / stages / needs / image / expanded variables); jobs with an `image` run inside their container, jobs without one run in the host shell; unsupported semantics are **explicitly blocked**, never silently degraded.
- **Multi-project workspaces**: persist commands, environment variables, connection profiles, and run targets, versioned by `rev`; run preflight, start, build, and quality gates in one click.
- **Local Docker integration**: detect PostgreSQL / Redis / Elasticsearch / MinIO middleware; build your source into immutable images, replace explicit Compose targets, verify readiness, and record recoverable DeploymentRevisions.
- **Co-existing git worktree environments**: each branch / tag gets an independent worktree, Compose project, and deployment revision, with rollback support.
- **Secret safety**: secret values live only in Windows Credential Manager; responses, logs, and the UI never echo sensitive values.
- **Replayable run history**: per-job grouped logs, cancel, retry, and single-job re-runs.
- **The GUI is a shell over the CLI**: the UI and the `pipedeck` command consume the same control API; every action panel shows the equivalent CLI command, and `pipedeck run --wait` enables headless automation.
- **Protected cleanup**: cleanup only touches platform-owned (ownership-labeled) resources; the last healthy MinIO and per-kind middleware instance can never be deleted.

### Supported semantics baseline

`stages`, `script` / `before_script` / `after_script`, `variables` (including local predefined `CI_*`), `rules:if` (subset), `needs`, `image`, `artifacts` (paths + reports:dotenv), `include` (local/remote/template with cache), `extends` / `!reference`, `workflow` / `default`, `allow_failure`, `when`.

Unsupported semantics (e.g. `trigger:project`, `pages`, OIDC/Vault secrets, `id_tokens`, Kubernetes) are explicitly blocked with a reason.

### Requirements

- Windows 10 / 11 (x64)
- [Git](https://git-scm.com/download/win) (required)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (required for container jobs and Compose deployments)

### Installation

Download the latest installer from [GitHub Releases](https://github.com/ShiqinGuo/pipedeck/releases):

- **`Pipedeck_x64-setup.exe`** (NSIS installer, recommended) — double-click to install; the installer adds the `pipedeck` CLI to your PATH.
- `Pipedeck_x64_en-US.msi` (MSI installer) — suited for enterprise deployment.

After installation, launch **Pipedeck** from the Start menu, or use the CLI directly in a terminal:

```powershell
pipedeck status          # control plane / repositories / middleware summary
pipedeck serve           # start the local control service (not needed if the GUI is running)
```

### Quick start

1. Open Pipedeck and, on the **Repositories** page, import an existing local checkout or clone from GitLab.
2. The repository is parsed automatically — open **Pipeline Preview** to see what will run.
3. Click **Run** to execute the pipeline locally; jobs with an `image` run inside containers.
4. For multi-project workflows, combine repositories on the **Workspaces** page and persist commands and connection configuration.

### Development

```powershell
uv sync
corepack pnpm install
uv run pipedeck serve       # local control service (loopback :7421)
corepack pnpm --filter @pipedeck/web dev   # web UI (127.0.0.1:5173)
```

Desktop build:

```powershell
corepack pnpm sidecar:build && corepack pnpm --filter @pipedeck/web build
corepack pnpm desktop:build   # artifacts in apps/desktop/src-tauri/target/release/bundle/
```

Quality gates:

```powershell
uv run ruff format --check . ; uv run ruff check . ; uv run pyright ; uv run pytest
corepack pnpm --filter @pipedeck/web lint
corepack pnpm --filter @pipedeck/web typecheck
corepack pnpm --filter @pipedeck/web test
```

### Internationalization

- The UI supports **Simplified Chinese / English**, switchable under **Settings → Language**; the choice takes effect immediately and is persisted.
- Backend CLI output and API error messages are in English (for international developers); code comments remain in Chinese.
- When adding copy, put UI strings into the `apps/web/src/i18n/locales/zh|en/` partition resources and call `t()`.

### Tech stack

| Layer | Technology |
|---|---|
| Control service | Python · FastAPI · Pydantic · SQLAlchemy |
| Web UI | React 19 · TanStack Router/Query · Tailwind CSS · i18next |
| Desktop shell | Tauri 2 (Rust) · Windows |
| Local execution | Git · Docker / Docker Compose · GitLab CI subset parser |
| Secrets | Windows Credential Manager |

### Documentation

- Product scope & acceptance: `docs/prd/pipedeck.md`
- Domain model & source of truth: `docs/cognition/local-control-plane.md`
- Interaction spec: `docs/design/interaction-model.md`
- Decision records: `docs/decisions/`
- Index: `docs/README.md`

### License

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

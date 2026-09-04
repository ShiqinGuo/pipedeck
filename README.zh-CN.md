# Pipedeck

**把你的 GitLab pipeline 搬到桌面上跑。**

[English](README.md) · [简体中文](README.zh-CN.md)

[![Release](https://img.shields.io/github/v/release/ShiqinGuo/pipedeck)](https://github.com/ShiqinGuo/pipedeck/releases)
[![License: MIT](https://img.shields.io/github/license/ShiqinGuo/pipedeck)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-blue)](#系统要求)

还在为了验证 `.gitlab-ci.yml` 不停地 push 提交？Pipedeck 导入你的仓库，解析你已经写好的 pipeline，并在你的机器上真实执行——带 `image` 的 job 跑在容器里，其余跑在宿主 shell——同时提供运行历史、多项目工作区，以及与 GUI 驱动同一控制服务的 CLI。

它和 [act](https://github.com/nektos/act)（GitHub Actions 的本地运行器）是同一个思路，只是面向 GitLab CI，并且提供的是桌面工作台而非纯终端体验。

## 为什么需要它

- `gitlab-runner exec` 已在 GitLab 17.0 移除——官方已经没有在本地跑单个 job 的方式。
- 纯终端工具没有运行历史、没有失败聚合，也无法单独重跑某个 job。
- 开发者仍在为变量、镜像、多服务部署手写各种项目专属脚本。

Pipedeck 把「仓库 → 管道 → 执行 → 部署」整条链路放进一个本地客户端。

## 功能特性

- **直接运行你的 `.gitlab-ci.yml`**——预览完整管道（job、stage、`needs`、image、变量展开），然后真实执行：带 image 的 job 跑在容器内，其余跑在宿主 shell。不支持的语义会显式阻断并给出原因，绝不静默降级。
- **可回放的运行历史**——按 job 分组日志、取消、重试、单 job 重跑。
- **多项目工作区**——保存命令、环境变量、连接 profile 与运行目标；按 rev 版本化，一键重跑。
- **可回滚的 Compose 部署**——把源码构建为不可变镜像，替换明确的 Compose target，验证 readiness，记录可恢复的 DeploymentRevision。
- **git worktree 多环境并存**——每个分支 / 标签拥有独立 worktree、独立 Compose project、独立部署 revision。
- **Secret 安全**——Secret 值仅存于 Windows Credential Manager；响应、日志与界面永不回显敏感值。
- **GUI 与 CLI 同一控制 API**——每个操作面板展示等价 CLI 命令，`pipedeck run --wait` 支持脚本与计划任务的 headless 用法。
- **受保护的清理**——清理只作用于 Pipedeck 拥有的资源；每类中间件（PostgreSQL、Redis、Elasticsearch、MinIO）的最后健康实例永不可删。

## 支持的 GitLab CI 语法

`stages`、`script` / `before_script` / `after_script`、`variables`（含本地预定义 `CI_*`）、`rules:if`（子集）、`needs`、`image`、`artifacts`（paths + `reports:dotenv`）、`include`（local / remote / template，带缓存）、`extends` / `!reference`、`workflow` / `default`、`allow_failure`、`when`。

超出该子集的语义——`trigger:project`、`pages`、OIDC/Vault secrets、`id_tokens`、Kubernetes——会被明确阻断并说明原因，而不是跑一半。

`services`、`cache`、`parallel:matrix` 在 roadmap 中。

## 系统要求

- Windows 10 / 11（x64）
- [Git](https://git-scm.com/download/win)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)——运行容器 job 与 Compose 部署时需要

## 安装

从 [GitHub Releases](https://github.com/ShiqinGuo/pipedeck/releases) 下载最新安装包：

| 文件 | 说明 |
| --- | --- |
| `Pipedeck_x.y.z_x64-setup.exe` | NSIS 安装包（推荐）——安装器会把 `pipedeck` CLI 写入 `PATH` |
| `Pipedeck_x.y.z_x64_en-US.msi` | MSI 安装包——适合企业批量部署 |

安装完成后，从开始菜单启动 **Pipedeck**，或直接使用 CLI：

```powershell
pipedeck status   # 查看控制面 / 仓库 / 中间件摘要
```

## 快速开始

1. 打开 Pipedeck，在 **仓库（Repositories）** 页导入本机已有 checkout，或从 GitLab 克隆。
2. Pipedeck 自动解析 `.gitlab-ci.yml`——在管道预览里确认会跑什么。
3. 点击 **运行**。带 `image` 的 job 在容器中执行，看到的正是你熟悉的 GitLab stage 流转。
4. 需要多项目联动时，在 **工作区** 页组合多个仓库。

## 本地开发

```powershell
uv sync
corepack pnpm install

uv run pipedeck serve                      # 控制服务，loopback :7421
corepack pnpm --filter @pipedeck/web dev   # Web 界面，127.0.0.1:5173
```

构建桌面端：

```powershell
corepack pnpm sidecar:build
corepack pnpm --filter @pipedeck/web build
corepack pnpm desktop:build   # 产物在 apps/desktop/src-tauri/target/release/bundle/
```

质量门禁：

```powershell
uv run ruff format --check . ; uv run ruff check . ; uv run pyright ; uv run pytest
corepack pnpm --filter @pipedeck/web lint
corepack pnpm --filter @pipedeck/web typecheck
corepack pnpm --filter @pipedeck/web test
```

界面提供简体中文 / English 切换（**设置 → 语言**）；新增文案请放入 `apps/web/src/i18n/locales/` 下的分区资源。

更多设计文档见 [`docs/`](docs/README.md)——产品范围、领域模型、交互规范与决策记录。

## 参与贡献

欢迎在 [github.com/ShiqinGuo/pipedeck](https://github.com/ShiqinGuo/pipedeck) 提交 Issue 与 Pull Request。提交前请先跑完上述质量门禁。

## 许可

版权所有 (c) 2026 ShiqinGuo

基于 [MIT License](LICENSE) 发布。第三方声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

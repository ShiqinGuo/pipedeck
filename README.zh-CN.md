# Pipedeck

**本地多项目 CI/CD 控制台：在本机构建、集成并测试你的应用。**

[English](README.md) · [简体中文](README.zh-CN.md)

[![Release](https://img.shields.io/github/v/release/ShiqinGuo/pipedeck)](https://github.com/ShiqinGuo/pipedeck/releases)
[![License: MIT](https://img.shields.io/github/license/ShiqinGuo/pipedeck)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-blue)](#系统要求)

Pipedeck 将仓库、质量检查、构建、本地部署与服务就绪检查组织在同一个工作区。把前端、后端和中间件在本机集成起来后，就能直接打开应用、验证真实业务功能。支持宿主进程与 Docker Compose，桌面界面和 CLI 使用同一控制 API。

GitLab CI 执行是其中一种工作流。核心结果是可用的本地测试环境；Pipedeck 不向 staging 或 production 服务器部署。

## 为什么需要它

- 一个功能往往涉及多个仓库、服务和中间件连接。
- 构建成功还不足以判断整套应用是否已经可以测试。
- 切换分支和重新启动服务时，需要知道实际运行的是哪份代码和配置。

Pipedeck 串起「选择仓库 → 检查与构建 → 本地部署 → 确认就绪 → 测试功能」。

## 功能特性

- **本地集成工作区**——组合仓库、命令、连接 profile 与 Host / Compose 目标，配置按版本保存。
- **按依赖顺序启动**——预检发现缺失或循环依赖，前置服务通过就绪检查后再启动下游。
- **当前测试环境**——逐服务查看真实健康状态、运行代码和配置版本；可指定 `/app`、`/docs` 等 HTTP 测试入口，在桌面客户端直接打开系统浏览器。
- **直接运行 `.gitlab-ci.yml`**——预览 job、stage、`needs`、image 与变量，在本地执行支持的语义，不支持的部分会阻断并说明原因。
- **可回放的运行历史**——按 job 分组日志、取消、重试、单 job 重跑。
- **可回滚的 Compose 部署**——把源码构建为不可变镜像，替换明确的 Compose target，验证 readiness，记录可恢复的 DeploymentRevision。
- **按项目切换分支副本**——为指定项目创建 worktree，应用到工作区后再预检。这只替换一个项目的源码；整套环境独立运行仍需单独配置工作区、端口和数据。
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
2. 在 **工作区** 组合功能所需的项目，配置构建 / 启动命令、运行目标、端口、就绪检查、中间件连接和项目间启动依赖。
3. 保存、预检并执行，先处理预检报告的阻断问题。
4. 在当前环境面板确认各服务状态，通过 **打开应用** 进入实际业务页面测试功能。

测试分支时，创建副本需要选择项目，应用到工作区后再预检。可复现的「前端 → 后端 → SQLite」示例见 [`examples/local-integration/`](examples/local-integration/README.md)。

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

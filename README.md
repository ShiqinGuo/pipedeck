# TripGuru Local

TripGuru Local 是一个仅运行在开发者本机的多项目开发控制台。它把仓库发现、工作区配置、中间件选择、质量检查、镜像构建和本地 Docker 集成放在同一个客户端中。

当前产品范围包括：

- 扫描本机 Git 仓库并识别 Python、React/Vite、Vue/Vite、Nuxt 和 Compose 项目。
- 读取 Docker 容器并识别 PostgreSQL、Redis、Elasticsearch 和 MinIO。
- 导入或克隆仓库，保存多项目工作区，并以结构化 argv、Host/Compose target、命名 endpoint 和中间件连接 Profile 完成配置。
- 生成可解释的工作区计划，真实执行本地检查、构建、启动并查看日志、历史、取消和重试。
- 把当前源码构建为不可变本机镜像，替换明确的 Compose target，验证 readiness，并记录可恢复的 DeploymentRevision。
- 将 PostgreSQL/MinIO 连接映射到宿主进程或 Compose 容器；Secret value 仅写入 Windows Credential Manager。
- 预览并清理平台拥有的冗余资源，同时强制中间件保留与 MinIO 保护规则。

完整范围和验收入口见 [docs/README.md](docs/README.md)。

## 本地启动

```powershell
uv sync
corepack pnpm install
uv run tripguru-local-api
corepack pnpm --filter @tripguru/local-web dev
```

打开 `http://127.0.0.1:5173`。API 默认监听 `http://127.0.0.1:7421`。

桌面发行版会为本次 sidecar 生命周期生成随机写 token；浏览器开发模式执行写操作时，API 与 Web 必须显式配置同一个本地开发 token。

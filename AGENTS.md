# Pipedeck 仓库指南

始终使用中文回复。

## 产品边界

Pipedeck 是只面向开发者本机的多项目开发控制台。它负责发现或拉取仓库、配置本地工作区、绑定本地中间件、运行本地质量门禁、构建镜像并协调 Docker Runtime。

- 不管理 staging、production 或任何远程部署环境。
- Docker 实际状态不是配置事实源；工作区配置和运行历史由本地控制服务拥有。
- Secret 不写入仓库、日志、SQLite 明文字段或生成的可提交文件。
- 清理只允许作用于带 `tripguru.local/managed=true` label 的资源。
- PostgreSQL、Redis、Elasticsearch、MinIO 每类至少保留一个健康实例；MinIO 始终受保护。

## 结构

- Python 控制服务：`src/pipedeck/`
- React 客户端：`apps/web/`
- Tauri 桌面壳：`apps/desktop/src-tauri/`
- API 测试：`tests/`
- 浏览器验收：`apps/web/e2e/`
- 长期产品与设计事实：`docs/`

## 命令

```powershell
uv sync
corepack pnpm install
uv run pipedeck serve
corepack pnpm --filter @pipedeck/web dev
```

质量门禁：

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
corepack pnpm --filter @pipedeck/web lint
corepack pnpm --filter @pipedeck/web typecheck
corepack pnpm --filter @pipedeck/web test
corepack pnpm --filter @pipedeck/web build
git diff --check
```

## 实现约束

- API 使用类型化 Pydantic request/response；前端不从展示文本推断状态。
- 只在 trust boundary 解析 Git、Docker、文件和第三方响应。
- Git 更新默认 fast-forward only；不得重置或覆盖 dirty worktree。
- UI 必须实现 loading、empty、error、disabled reason 和 recovery 状态。
- UI 以桌面工作台为主要视口，同时保证窄屏无重叠和文字溢出。

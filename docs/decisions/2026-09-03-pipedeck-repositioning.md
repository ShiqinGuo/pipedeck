# Pipedeck 重定位与本地 CI 引擎决策

日期：2026-09-03
Accountable owner: Pipedeck Engineering
Status: Current

## Context

TripGuru Local 已完成"计划→执行→部署→回滚→清理"纵向链路，但命令来源硬编码（6 种 ProjectKind 枚举 + 按类型写死的命令模板），分支不是一等公民，前端无路由无组件库且类型三份手抄。产品要脱离 TripGuru 品牌、面向所有 GitLab CI 开发者，并以 `.gitlab-ci.yml` 为配置事实源。竞品调研（research/2026-09-03-local-ci-competitor-closes.md）确认市场窗口与交互铁律。

## Decisions

1. **品牌迁移 Pipedeck**：包名 `pipedeck`、CLI `pipedeck`、identifier `com.pipedeck.local`、状态目录 `%LOCALAPPDATA%/Pipedeck`；API 路径 `/api/v1` 与端口 7421 不变以降迁移风险。
2. **`.gitlab-ci.yml` 是唯一项目 contract source**：catalog 的硬编码 ProjectKind/命令模板退化为元数据展示；无 `.gitlab-ci.yml` 的仓库只保留扫描/导入与 Compose 部署能力。以 GitLab 官方 ci.json schema 校验。
3. **job 容器执行器（子集 + 阻断）**：有 image 的 job 用 `docker create + start -a` 在镜像内执行（workspace bind-mount、变量 env 注入、Windows 路径适配），无 image 的 job 走宿主 shell；artifacts:paths 归档到 state 目录、reports:dotenv 注入下游 needs job。不支持语义（trigger/pages/services/secrets/id_tokens/cache/parallel）在计划阶段显式阻断 + WARN，绝不静默跳过——这是 GitLab Runner exec 被移除（"复刻全部语义不可行"）的对冲策略。
4. **Environment = workspace × ref（git worktree 并存）**：`git worktree add` 到 state 目录，每个 Environment 独立 Plan/Run/DeploymentRevision；Compose project identity 派生加入 ref 使多版本并存不冲突；新增 `checkout_ref` 操作与 fast-forward update 并存，dirty worktree 阻断不变量保留。
5. **CLI 与 GUI 同为控制 API 消费者（GUI=CLI 壳）**：`pipedeck` argparse 子命令（serve/status/repos/pipeline/run/env/deploy/logs/secrets/doctor）；serve 首启写 `%LOCALAPPDATA%/Pipedeck/cli-token`，CLI 读之互信（Netlify 模式）；`run --wait` 支持 headless。
6. **前端重建**：React 19 + TanStack Router/Query + Tailwind v4 + shadcn/ui；类型从 FastAPI OpenAPI 生成（openapi-typescript），消灭手写副本；旧 src 与 634 行 spec 的场景清单作为回归基线迁移。
7. **CLI 分发**：CLI 与 sidecar 同为 PyInstaller 单文件随桌面安装包进安装目录；Windows 用 NSIS installerHooks POSTINSTALL 写用户 PATH 并广播 WM_SETTINGCHANGE、PREUNINSTALL 仅移除指向本目录的条目；macOS（Ollama 式首启 symlink）列 roadmap。Tauri 无内建 PATH 能力，必须 DIY。

## Consequences

- rules 表达式求值器自研：首期只支持 ==/!=/=~/!~/&&/||/括号/defined，超集进阻断项，不做半对半错。
- include:remote 默认缓存 + 显式刷新（gitlab-ci-local 策略）。
- Compose identity 派生规则变化需要迁移兼容：旧 workspace ref 默认当前分支。
- Windows docker bind-mount 路径与宿主 job 的 Secret 脱敏沿用现有 redaction 机制。
- 语义子集意味着某些复杂仓库无法本地全量执行——阻断项必须解释缺什么，这是产品诚实的代价。

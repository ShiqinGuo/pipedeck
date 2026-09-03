# Pipedeck — GitLab 开发者的本地 CI/CD 工作台

Canonical record: `docs/prd/pipedeck.md`
Accountable owner: Pipedeck Engineering
Status: Current（取代 prd/tripguru-local.md）

## 问题与用户

使用 GitLab CI/CD 的开发者在本地验证 pipeline 时没有好用的入口：官方 runner 只能服务化（`gitlab-runner exec` 已于 v17.0 移除），gitlab-ci-local 只有终端且无历史、无失败聚合。开发者需要重复手工处理：仓库切换、变量与 Secret 注入、镜像构建、多版本（分支）本地部署与清理。

## 结果与成功标准

开发者能够在一个本机桌面端与 CLI 中：

1. 导入/克隆仓库并自动解析 `.gitlab-ci.yml`，看到完整管道预览（job/stage/needs/image/变量展开）。
2. 本地执行 job：有 image 的 job 在其镜像容器内运行（workspace 挂载 + 变量注入），无 image 的 job 在宿主 shell 运行；语义与线上一致的子集，不支持的语义显式阻断。
3. 变量与 Secret：缺失变量交互式补全，Secret 仅存 Windows Credential Manager，日志永不回显敏感值。
4. 运行历史完整可回放：按 job 分组日志、取消、重试、单 job 重跑、错误聚合。
5. 通过 git worktree 以多个 Environment 并存部署不同分支/标签：每个 Environment 独立构建镜像、独立 Compose project、独立 DeploymentRevision，可回滚。
6. CLI 与 GUI 是同一控制 API 的消费者：`pipedeck run --wait` 支持 headless 固化（脚本/计划任务）；GUI 每个操作展示等价 CLI 命令。
7. 清理只作用于平台拥有（ownership label）的资源，MinIO 与每类中间件最后健康实例永不进入可删集合。
8. 安装桌面客户端后 CLI 即可用（安装器写 PATH / 应用内重装）。

## 范围与非目标

范围：独立桌面客户端、本地控制服务、本机 Git、Git URL、`.gitlab-ci.yml` 解析与本地执行（子集）、job 容器执行器、git worktree 并存、Docker Compose 集成部署、中间件连接、Windows Credential Manager、CLI。

非目标：完整复刻 GitLab CI 全部语义（trigger:project、pages、secrets(OIDC/Vault)、id_tokens、K8s）、staging/production 发布、云环境、远程迁移执行、解析 `.github/workflows`。

支持语义首期基线：stages/script/before_script/after_script、variables（含预定义 CI_* 本地值）、rules:if（子集）、needs、image、artifacts（paths + reports:dotenv）、include（local/remote/template，缓存）、extends/!reference、workflow/default、allow_failure、when。services/cache/parallel:matrix 列入 roadmap。

## 约束与链接

- 所有控制接口只绑定 loopback；写操作需本地 token。
- Docker 资源以 `tripguru.local/managed` 语义的 ownership label 治理（label 名迁移期保留，避免破坏已有部署）。
- Git 更新默认 fast-forward only；dirty worktree 不重置不覆盖；worktree 由平台拥有。
- 未通过计划校验不可执行；Plan 指纹含 yml SHA 与展开结果 hash。
- 行为验收见 [../behavior/workspace-plan.md](../behavior/workspace-plan.md)；交互验收见 [../design/interaction-model.md](../design/interaction-model.md)；竞品依据见 [../research/2026-09-03-local-ci-competitor-closes.md](../research/2026-09-03-local-ci-competitor-closes.md)。

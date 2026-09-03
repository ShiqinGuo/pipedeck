# Pipedeck 交互规范

Canonical record: `docs/design/interaction-model.md`
Accountable owner: Pipedeck Engineering
Status: Current（取代 design/visual-interaction.md 的交互部分）

本文是前端重建与 CLI 的唯一交互事实源。竞品依据见 research/2026-09-03-local-ci-competitor-closes.md。

## 设计原则

1. 零配置起步：无 init/register/登录；导入仓库 → 识别 `.gitlab-ci.yml` → 预览管道，三步内可运行第一个 job。
2. GUI 是 CLI 的壳：每个操作面板在页脚展示等价 `pipedeck` 命令（等宽字体 + 复制按钮），GUI 不拥有第二套逻辑。
3. 先看再跑：任何执行动作前都先呈现计划（会跑哪些 job、什么顺序、注入什么变量、拉什么 image），确认后才执行。
4. 双状态：每个可运行单元同时显示 update status（门禁/构建进行到哪）与 runtime status（服务是否就绪），永不合并成单色点。
5. 失败处理是核心增值：错误聚合面板 + 按 job 分组日志 + 一键重跑 + revision 回滚；每个失败态给出明确 recovery 动作。
6. 静默即缺陷：loading、empty、error、disabled reason、recovery 五态缺一不可；不支持的能力显式阻断并解释，永不静默跳过。
7. 不可逆操作显著提示：删除 Environment、清理资源必须预览作用范围；provider/target 类创建后不可改的选择在创建时强调。

## 信息架构（左侧主导航）

| 区域 | 内容 | 等价 CLI |
| --- | --- | --- |
| Dashboard / | 最近 Run、活跃 Environment、保护资源健康摘要、首跑引导卡 | `pipedeck status` |
| Projects /projects | 仓库列表（扫描/导入/克隆）、每个仓库的 `.gitlab-ci.yml` 识别状态与阻断项、`checkout_ref` 更新 | `pipedeck repos ...` |
| Pipelines /pipelines/$repoId | 解析后的 job 表（name/stage/needs/image/when）、DAG 图、变量预览、运行入口 | `pipedeck pipeline list` |
| Runs /runs 与 /runs/$runId | 历史列表 + 详情（阶段分组、流式日志、取消/重试/单 job 重跑） | `pipedeck run/logs` |
| Workspaces /workspaces/$id | 多项目工作区 + Environments（worktree 并存卡片：ref、最近 Run、部署状态、回滚/删除） | `pipedeck env ...` |
| Resources /resources | Docker 中间件、托管资源、Secrets、清理预览/执行 | `pipedeck secrets/doctor` |
| Settings /settings | 扫描根、token、CLI 安装状态与"重装 CLI 到 PATH"、版本握手 | `pipedeck doctor` |

## 核心闭环八步 → 界面映射

1. **依赖前置**：Dashboard 首跑卡运行 doctor（Docker/Git/磁盘），失败项给安装指引链接；通过后卡片消失。
2. **接入仓库**：Projects 页扫描本机目录 / 导入 / 克隆（可选初始 branch）；每行显示 kind 与 `.gitlab-ci.yml` 状态（无 yml = 仅扫描/部署能力，明示）。
3. **管道预览**（入场动作）：Pipelines 页是"运行前看会跑什么"的一张表——name/stage/needs/allow_failure/image/when + DAG；未 track 文件检测在这里主动警告；include 缓存提供"强制刷新"按钮。
4. **圈定范围**：勾选 job（默认全管道按 stage 顺序；`--needs` 依赖自动带入）；`when: manual` 单独区；`when: never` 折叠隐藏。
5. **变量与 Secret 注入**：运行确认弹层列出将被注入的变量展开表；缺失的变量逐个 secure 输入（Secret 写入 Credential Manager，UI 只显示 presence）；与现有环境变量同名冲突时阻断。
6. **执行与观察**：Runs 详情页按 job 分组流式日志（子集 stage 失败即停可见）、双状态、日志按 Source/Level/关键词过滤、复制/下载。
7. **失败处理**：错误聚合面板（每个失败 job 一条 + 日志锚点跳转）；单 job 重跑；Compose 部署失败走 revision 回滚，回滚失败进入可操作的 degraded 视图。
8. **固化与清理**：重复运行免配置（变量留存于工作区引用）；Resources 页清理预览制，MinIO 与每类中间件最后健康实例永不出现在可删集合。

## 关键交互模式

- **首跑 survey**：首次运行带 image 的管道时，列出将拉取的 image 与预估体积，确认后记住选择。
- **Environment 并存卡片**（Workspaces 详情）：每个 ref 一张卡（branch/tag、worktree 路径、最近 Run、部署 revision/状态、开启/删除）。删除 = `git worktree remove` + Compose 资源清理，需预览清单 + 确认。创建时选择 ref，创建后 ref 不可改（可删重建）。
- **等价命令行**：每个操作面板页脚一行 `pipedeck ...`；复制按钮；命令与实际执行严格同步（GUI 写操作与 CLI 走同一 API）。
- **轮询节奏**：运行中 Run 1-2s，列表 3-4s，静态资源手动刷新；全部可被 TanStack Query 统一管理。

## 状态与响应式约束

- UI 以桌面工作台为主要视口（≥1280），同时保证窄屏（≥390）无重叠与文字溢出；主导航窄屏收起为底部/抽屉。
- 全部写操作展示 token 鉴权失败原因与重试路径；删除/清理类操作二次确认。
- 骨架屏用于首载；空态给"下一步做什么"；错误态给 recovery；disabled 必须给 reason。

## 验收

- 交互验收以本文为准；e2e（Playwright 双视口）按本文场景编写；旧 634 行 workspace.spec 的场景清单作为回归基线迁移。

# Pipedeck 交互规范

Canonical record: `docs/design/interaction-model.md`
Accountable owner: Pipedeck Engineering
Status: Current（取代 design/visual-interaction.md 的交互部分）

本文描述本地集成测试主线的交互约束。当前研究依据见 [本地集成研究](../research/2026-09-07-local-integration.md)与 [0.3.0 迭代及验收](../research/2026-09-08-iteration.md)；旧流水线竞品研究保留为历史资料。

## 设计原则

1. 围绕本地功能测试起步：导入项目 → 组合工作区及依赖 → 预检构建部署 → 打开应用验证业务。首次运行明确显示尚需配置的项目、连接和就绪检查。
2. GUI 是 CLI 的壳：每个操作面板在页脚展示等价 `pipedeck` 命令（等宽字体 + 复制按钮），GUI 不拥有第二套逻辑。
3. 先看再跑：任何执行动作前都先呈现计划（会跑哪些 job、什么顺序、注入什么变量、拉什么 image），确认后才执行。
4. 双状态：每个可运行单元同时显示 update status（门禁/构建进行到哪）与 runtime status（服务是否就绪），永不合并成单色点。
5. 失败处理是核心增值：错误聚合面板 + 按 job 分组日志 + 一键重跑 + revision 回滚；每个失败态给出明确 recovery 动作。
6. 静默即缺陷：loading、empty、error、disabled reason、recovery 五态缺一不可；不支持的能力显式阻断并解释，永不静默跳过。
7. 不可逆操作显著提示：删除 Environment、清理资源必须预览作用范围；provider/target 类创建后不可改的选择在创建时强调。

## 信息架构（左侧主导航）

| 区域 | 内容 | 等价 CLI |
| --- | --- | --- |
| Dashboard / | 集成工作区入口、最近 Run、资源健康摘要、依赖诊断 | `pipedeck status` |
| Projects /projects | 仓库列表（扫描/导入/克隆）、每个仓库的 `.gitlab-ci.yml` 识别状态与阻断项、`checkout_ref` 更新 | `pipedeck repos ...` |
| Pipelines /pipelines/$repoId | 解析后的 job 表（name/stage/needs/image/when）、DAG 图、变量预览、运行入口 | `pipedeck pipeline list` |
| Runs /runs 与 /runs/$runId | 历史列表 + 详情（阶段分组、流式日志、取消/重试/单 job 重跑） | `pipedeck run/logs` |
| Workspaces /workspaces/$id | 整套环境逐服务状态、实际版本、测试入口，以及配置、启动依赖和项目分支检出 | `pipedeck env ...` |
| Resources /resources | Docker 中间件、托管资源、Secrets、清理预览/执行 | `pipedeck secrets/doctor` |
| Settings /settings | 扫描根、token、CLI 安装状态与"重装 CLI 到 PATH"、版本握手 | `pipedeck doctor` |

## 本地集成测试主线

1. **接入项目**：扫描、导入或克隆仓库，选择一项业务功能需要的项目组合。GitLab CI 可作为质量检查和构建的一种入口。
2. **配置工作区**：配置各项目的命令、变量引用、Host / Compose 运行目标、端口及就绪检查，并绑定中间件；需要直接进入业务子路径时配置应用入口。
3. **声明启动依赖**：明确下游依赖哪些项目；预检阻断缺失、循环和自身依赖。Host-only 且无需中间件的工作区不因 Docker 离线而被阻断。
4. **预检执行**：保存配置后显示计划与阻断原因。构建完成后按依赖顺序启动或部署，前置服务通过就绪检查才继续。
5. **查看实际环境**：逐服务展示当前状态、运行记录和来源版本；配置保存后的新 revision 不覆盖旧实例的版本标识。
6. **进入业务测试**：服务就绪后，优先按 `application.endpoint` 与 `application.path` 检查应用地址；未配置时兼容 HTTP 就绪端口的根页面。应用地址自身通过 HTTP 检查后才提供“打开应用”。只配置 TCP 探针时不猜测网页地址，可显式配置应用入口。桌面客户端打开系统浏览器，打开失败保留重试操作；业务是否正确仍需实际操作验证。
7. **切换与恢复**：按项目应用分支 checkout，重新预检；失败时可查运行日志，Compose 部署可按记录恢复。
8. **停止与清理**：停止运行后更新实际状态；资源清理预览作用范围，MinIO 与每类中间件最后健康实例保持受保护。

## 关键交互模式

- **项目分支检出卡片**：创建时明确选择项目和 ref；卡片显示所属源项目，工作区配置已引用该 checkout 时显示“当前已应用”，不把它当作运行版本证明。“应用并预检”先替换对应项目及依赖引用，再按新工作区 revision 预检；活跃运行期间阻断应用。创建 checkout 本身不创建独立 Compose 环境。删除只移除未被工作区或活跃运行引用且干净的 worktree；部署资源通过 Resources 页的受控清理处理。
- **当前环境面板**：每个服务显示独立的就绪状态、恢复动作、运行时源码 HEAD 和配置 revision；配置变化后保留实际运行版本，也保留仍在运行但已移出配置的目标。应用入口使用实际运行计划中的 `application.endpoint/path`，未配置时兼容 HTTP 就绪端口根页面；服务就绪且应用地址检查通过后提供“打开应用”。全部服务检查通过才计为环境就绪，停止后撤下 URL。
- **编辑草稿**：相同 revision 的刷新不覆盖修改；保存请求在途时仍可编辑，响应只替换已提交草稿，保留后续输入并以新 revision 继续保存。其它调用者的新 revision 与草稿冲突时保留输入、禁用保存并提供明确的重新载入动作。分支应用前必须保存或放弃草稿。移除项目时同时清理其它服务指向它的启动依赖。
- **等价命令行**：每个操作面板页脚一行 `pipedeck ...`；复制按钮；命令与实际执行严格同步（GUI 写操作与 CLI 走同一 API）。
- **运行日志与重试**：按 Run 缓存事件游标，只追加新事件；运行终态后补读尾日志并停止轮询，切换 Run 时不混入旧请求结果。重试成功后进入响应返回的新 Run，并清空旧步骤筛选。
- **轮询节奏**：运行中 Run 1-2s，列表 3-4s，静态资源手动刷新；全部可被 TanStack Query 统一管理。

## 状态与响应式约束

- UI 以桌面工作台为主要视口（≥1280），同时保证窄屏（≥390）无重叠与文字溢出；主导航窄屏收起为底部/抽屉。
- 全部写操作展示 token 鉴权失败原因与重试路径；删除/清理类操作二次确认。
- 骨架屏用于首载；空态给"下一步做什么"；错误态给 recovery；disabled 必须给 reason。

## 验收

- 交互验收以本文为准；e2e（Playwright 双视口）按本文场景编写；旧 634 行 workspace.spec 的场景清单作为回归基线迁移。
- 0.3.0 已通过真实打包桌面验收：在隔离状态库中预检并启动两个真实 Git 仓库的依赖服务，显示 2 / 2 就绪；打开系统 Edge 保存中文业务记录，并直接查询 SQLite 确认一致；取消运行后两服务停止且 URL 撤下。验收未 mock 后端或 Tauri，操作方式与验证边界见 [迭代记录](../research/2026-09-08-iteration.md)。本机安装与 GitHub 发布结果单独记录。

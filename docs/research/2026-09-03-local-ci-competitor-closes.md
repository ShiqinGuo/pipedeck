# 本地 CI 竞品闭环交互调研

类型：Research
状态：Current
日期：2026-09-03
Accountable owner: Pipedeck Engineering

## 调研问题

Pipedeck 面向所有使用 GitLab CI 的开发者，在本地桌面端与 CLI 中复用 `.gitlab-ci.yml` 完成质量门禁、镜像构建与多版本并存部署。本调研回答：

1. 同类产品从接入到拿到结果的每一步操作是什么。
2. GUI + CLI 双形态产品的通行架构与交互原则。
3. Pipedeck 的闭环应该长什么样，哪些交互必须做，哪些坑必须规避。

## 结论

- 市场窗口成立：GitLab Runner 17.0 已移除 `gitlab-runner exec`（官方承认复刻全部 CI 语义不可行），gitlab-ci-local 在 CLI 侧接棒但没有任何像样的 GUI；GitHub Local Actions（约 761 星）证明"给本地 CI 一个 GUI"的需求成立。
- 交互铁律：执行引擎只活在 CLI/服务层，GUI 负责"组装参数 + 展示状态"，不重写引擎。
- 闭环八步共性：依赖前置 → 列出可运行单元 → 圈定范围 → 变量与 Secret 注入 → 执行与实时日志 → 失败处理 → 配置固化 → 清理。"列出可运行的东西"永远是入场动作。
- Pipedeck 采用常驻本地控制服务 + 桌面 GUI + CLI 三消费者同 API 架构；GUI 的增值集中在状态可视化、错误聚合、表单化配置与历史对比。

## 竞品闭环

### gitlab-ci-local（firecow/gitlab-ci-local）

定位：零配置本地跑 `.gitlab-ci.yml` 的事实标准 CLI（TypeScript/Bun，单二进制分发）。

闭环：安装（npm/brew/PPA/二进制）→ 进仓库根直接 `gitlab-ci-local <job>` → `--list` 看管道表（name/stage/needs/allow_failure）→ 空格分隔多 job 或 `--needs` 带依赖链 → 变量经 `.gitlab-ci-local-variables.yml`（项目内）与 `$HOME/.gitlab-ci-local/variables.yml`（按 git remote 匹配 project/group/global 三层放 Secret）→ image/services 由 Docker 自动拉取，artifacts 结束后拷回源码目录 → 终端彩色日志 → 改了重跑 → `GCL_*` 环境变量/`.gitlab-ci-local-env` 固化默认值。

亮点：零 onboarding（无 init/register/登录）；`--list` 即"运行前预览管道"；变量按 git remote 分层匹配；配置固化为环境变量/文件后重复使用零成本。

槽点（Pipedeck 必须规避）：只同步 git tracked 文件，未 `git add` 的新文件在 job 里不存在（新手必踩，应主动检测并警告）；外部 include 缓存后需 `--fetch-includes` 强刷（不直觉）；多 job 并行日志混流；无历史、无失败聚合视图。

### act（nektos/act）

定位：本地跑 GitHub Actions 的 Go CLI，与 Pipedeck 同构。

闭环：安装 → `act -l` 列 workflow/job → 首跑 survey 三选一默认 image（Large/Medium/Micro，明示磁盘代价，写入 `~/.actrc` 永不重问）→ `act`/`act -j test`/`act pull_request` → secrets 三通道（`-s NAME` 缺失时交互式 secure prompt、`--secret-file .secrets`、显式赋值并警告进 history）→ `--bind`（worktree 直接挂载，快）vs 默认拷贝（忠实）显式选择 → `--dryrun`/`--graph`/`-l` 构成"先看、再试、再跑"渐进路径。

亮点：首跑 survey 是教科书级 onboarding（一次选择、明示成本、落盘、永不重问）；secrets 的 secure prompt"缺什么问什么"；本地/线上差异用 `if: ${{ !github.event.act }}` 显式声明而非工具硬猜。

槽点：本地绿线上红的差异无任何提示；无运行历史；`.secrets` 文件易误提交且工具不做 gitignore 检查。

### GitLab Runner 官方（本地执行方式已消亡）

`gitlab-runner exec` 2016 年加入，v15.6 弃用，v17.0（2024-05）正式移除。官方原因：随 GitLab CI 演进，"把全部 CI 特性复刻进 exec 已不可行"。当前官方命令无任何本地跑 pipeline 能力（`lint` 只校验语法）。含义：语义忠实度是这类工具的持续维护成本，Pipedeck 必须以"子集 + 不支持即阻断"策略应对，绝不静默跳过；官方真空 + GUI 空白是市场窗口。

### Tilt（tilt-dev/tilt）

定位：本地微服务开发工作台 GUI（K8s 为主），是"本地 pipeline 工作台"的最佳交互范本。

闭环：安装 → 写 Tiltfile（主要学习成本）→ `tilt up` 空格键自动开 Web UI → 资源总览同时显示 update status（构建/部署）与 runtime status（是否 ready）两个独立状态 → 资源详情页日志按 Source/Level/关键词过滤 → 错误聚合面板（"...(more)" 入口一键只看重要事件）→ 改文件自动 watch，per-resource trigger mode（auto/manual）+ 手动触发按钮 + 自定义参数化按钮跑测试/lint → `tilt down`。

可抄：双状态模型（"编译好没好"与"跑起来没跑起来"是两个状态）；错误聚合高亮（所有调研对象里最强的失败处理交互）；trigger mode 开关；参数化自定义按钮；终端空格键跳浏览器（CLI/GUI 无缝互跳）。反面教训：Tiltfile 等于发明新 DSL，学习成本高——印证 Pipedeck 应直接复用 `.gitlab-ci.yml`。

### DevPod（loft-sh/devpod）

定位：devcontainer 标准的 workspace-as-code，Desktop 与 CLI 同源双形态。

闭环：装 Desktop 或 CLI → `devpod provider add docker`（GUI 每个按钮文档明示等价 CLI 命令）→ `devpod up github.com/org/repo@branch`（自动找 devcontainer.json，找不到按语言套模板）→ 自动打开 IDE → `list/stop/start/delete` 生命周期 → `--id` 从同一 repo 派生多 workspace → Recreate（保卷重建）/ Reset（全清）两档清理。

可抄：**"每个 GUI 动作 = 一条 CLI 命令"的透明设计**（GUI 不藏逻辑，随时可脚本化复现）；同一 repo 派生多实例的模型（与 worktree 并存完全同构）；Recreate/Reset 两档语义；"provider 创建后不可改"的教训——不可逆选择要么避免要么显著提示。

### Dagger（1.0-beta）

闭环：安装 → `dagger setup`（登录 + 扫描项目自动装推荐模块，生成 `dagger.toml`）→ `dagger check`（非零退出 = CI 失败同语义）→ `dagger api call` 返回 changeset 预览并询问应用 → step 级"跳进终端"调试。可抄：本地非零退出与 CI 同语义；任何一步可跳进终端应成为失败处理的标准动作。

### GUI wrapper 现状（2024-2026）

- GitHub Local Actions（VS Code 扩展，约 761 星）：唯一成熟案例，本质是"替用户拼 act 命令当 VS Code task 执行"，提供 Components（依赖检测/管理）、Workflows 树 + 6 种运行入口、Settings 表单化配置（Secrets/Variables/Inputs 可 import）、History 视图。
- act-gui、gitlab-ci-local-frontend、gcl-ui：均为 0-1 星玩具。
- 结论：需求被验证，gitlab-ci-local 方向的 GUI 完全空白；两种被验证架构是"编辑器内拼命令"与"常驻 daemon + 本地 Web UI"，Pipedeck 取后者（本地控制服务已是常驻 daemon）。

## 闭环八步 × 竞品矩阵

| 步骤 | gitlab-ci-local | act | Tilt | DevPod | Pipedeck 目标 |
| --- | --- | --- | --- | --- | --- |
| 依赖前置 | Docker 隐含 | Docker 隐含 | Docker+K8s | provider | 首跑 doctor 检测 Docker/Git |
| 列出可运行单元 | `--list` | `-l` | UI 资源列表 | list | Pipeline 预览页（job 表 + DAG） |
| 圈定范围 | job 名/`--needs`/stage | `-j`/event | 资源级 | workspace 级 | 勾选 job 或整管道 |
| 变量/Secret 注入 | variables.yml 三层 | `-s`/secret-file | 无 | provider options | GUI 表单 + Credential Manager + secure prompt |
| 执行 + 日志 | 终端混流 | 按 job 分组 | UI+终端双轨 | 弱展示 | 按 job 分组流式日志 + 历史 |
| 失败处理 | 改了重跑 | 改了重跑 | 错误聚合 | recreate/reset | 错误聚合 + 重跑 + revision 回滚 |
| 配置固化 | GCL_*/env 文件 | `.actrc` | Tiltfile | devcontainer.json | `.gitlab-ci.yml` 即配置（零新 DSL） |
| 清理 | artifacts 目录 | `--rm` | tilt down | delete | ownership label 保护清理 |

## 对 Pipedeck 的直接映射

1. 零配置起步：装完即用，无 init/register；导入仓库 → 解析 `.gitlab-ci.yml` → 预览管道，三步内可跑第一个 job。
2. 首跑 survey：检测 Docker/Git → 列出将拉取的 image 与预估磁盘 → 缺失变量/secrets 交互式补全（Secret 入 Credential Manager）→ 检测未 track 文件并警告（gitlab-ci-local 最大坑的主动防御）→ 选择落盘到工作区配置，永不重问。
3. GUI = CLI 壳：每个操作面板展示等价 `pipedeck` 命令；CLI 与 GUI 是同一控制 API 的两个消费者。
4. 失败处理是 GUI 增值核心：按 job 分组日志 + 错误聚合面板 + 运行历史对比 + 一键重跑单 job；每一步可跳终端调试。
5. worktree 多版本并存套用 DevPod 实例模型：创建→运行→停止→重建→重置→删除，两档清理语义，不可逆操作显著提示。
6. 语义忠实度策略：子集实现 + 不支持语义显式阻断（GitLab Runner exec 移除之教训）。

## 官方资料

- gitlab-ci-local: https://github.com/firecow/gitlab-ci-local
- act: https://github.com/nektos/act 、 https://nektosact.com/usage/index.html
- GitLab Runner 命令与 exec 移除: https://docs.gitlab.com/runner/commands.html 、 https://gitlab.com/gitlab-org/gitlab-runner/-/issues/37492 、 https://gitlab.com/gitlab-org/gitlab/-/issues/385235
- Tilt: https://docs.tilt.dev/
- DevPod: https://www.devpod.sh/
- Dagger: https://docs.dagger.io/
- GitHub Local Actions: https://github.com/SanjulaGanepola/github-local-actions
- act-gui: https://github.com/luckyabsoluter/act-gui

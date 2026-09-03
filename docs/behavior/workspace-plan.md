# 本地仓库、工作区与运行行为

Canonical record: `docs/behavior/workspace-plan.md`
Accountable owner: Pipedeck Engineering
Status: Current

## 场景：发现本机项目

Given 开发者配置了一个可访问的扫描根目录
When 客户端打开项目目录
Then 系统列出含 Git metadata 的仓库并显示识别类型、当前分支、dirty 状态和可用命令
And 无法读取的目录作为稳定错误呈现，不使整个目录不可用

## 场景：导入或克隆仓库

Given 开发者选择一个已有 Git 工作树或提供不含凭据的 Git URL
When 导入或克隆成功
Then 仓库被登记到本地目录并可立即加入工作区
And 私有仓库认证只使用本机 Git credential helper 或 SSH agent

Given 已登记仓库存在未提交修改
When 开发者请求更新
Then 系统阻断任何更新写操作并展示恢复动作
And 系统不得 reset、clean、stash 或覆盖工作树

Given 工作树干净但远端更新不能 fast-forward
When 开发者请求更新
Then `git pull --ff-only` 失败并保留当前分支和文件状态

## 场景：配置多项目工作区

Given 目录中存在可选择项目和本地中间件
When 开发者选择一个或多个项目并选择中间件目标
Then 系统生成包含检查、依赖安装、质量门禁、构建和启动阶段的计划
And 缺少必需中间件或端口冲突时返回明确阻断原因
And 每条命令包含项目归属、工作目录和结构化参数，不生成占位命令
And 中间件目标类型不匹配、停止或不健康时阻断预检
And 任一项目缺少可解析启动契约时阻断预检
And `ready` 只表示当前源码、配置和 Runtime 快照可以创建 Run

## 场景：保存和恢复工作区配置

Given 开发者为多个服务配置命令、执行 target、endpoint、readiness、环境来源和中间件绑定
When 保存工作区并重启本地控制服务
Then 相同定义可被重新打开和编辑
And 敏感变量只保存宿主机环境变量或 Credential Manager metadata 引用，不保存解析后的值
And 修改配置后旧计划明确失效并要求重新预检

## 场景：选择中间件连接

Given Docker 中有两个健康且发布端口的 PostgreSQL 实例
And 服务配置了 PostgreSQL ConnectionProfile 与 Secret 引用
When 开发者把 binding 从实例 A 改为实例 B 并重新预检
Then Plan 只展示脱敏后的连接 host、port、database 和输出变量名
And 宿主进程实际收到 `127.0.0.1` 对应实例 B 的 URL
And Compose target 实际收到 `host.docker.internal` 对应实例 B 的 URL
And 显式 environment 与 Profile 输出同名、资源类型不匹配、Secret 缺失或端口未发布时预检阻断

## 场景：本地 Compose 集成部署

Given 服务明确配置 checkout 内的 Dockerfile 或 Compose service、endpoint 和 readiness
When 集成 Run 执行 deploy 阶段
Then 系统在任何 Docker mutation 前持久化 DeploymentRevision intent
And 构建固定 source/config/revision 的不可变本机 image
And 生成的 Compose 配置使用稳定 project identity 并注入 managed、workspace、target 和 revision labels
And apply 后必须通过显式 HTTP/TCP readiness 才把 revision 标为 active

Given previous revision 处于 active
When 新 revision build 失败
Then previous 容器保持 active 且不执行替换

Given 新 revision apply 或 verify 失败
When previous frozen config 与 image 可用
Then 系统恢复 previous revision 并把新 revision 标为 rolled_back
And 恢复失败时标为 degraded，客户端提供 reconcile 而不声称已回滚

Given sidecar 在 applying、verifying 或 recovering 时中断
When sidecar 重新启动
Then 系统用 SQLite intent、Docker ownership labels 和 readiness 对账到 active、rolled_back 或 degraded

## 场景：运行、日志、取消与重试

Given 工作区预检通过且所有 Secret 引用在执行时可解析
When 开发者启动运行
Then 系统按计划顺序执行有限命令并启动长期服务
And 客户端可按序号增量读取阶段状态、标准输出和标准错误
And 日志不包含任何 Secret 解析值

Given Run 正在执行有限命令或长期服务
When 开发者取消 Run
Then 系统终止该 Run 拥有的整个本机进程树
And Run 进入 `cancelled`，重复取消保持幂等

Given Run 失败、取消或中断
When 开发者重试
Then 系统基于当前源码和配置重新预检并创建新的 Run
And 旧 Run 保持不可变，新 Run 通过 `retry_of` 关联来源

Given 控制服务在 Run 仍为 `running` 时重启
When 本地状态存储初始化
Then 无法继续拥有的旧 Run 被标记为 `interrupted`
And 客户端提供重新预检和重试动作

## 场景：目录为空或控制服务不可用

Given 没有匹配项目或 API 暂时不可用
When 客户端请求目录
Then 客户端分别显示空状态或连接错误
And 提供重新扫描或重试动作，不显示伪造项目数据

## 场景：保护本地中间件

Given Docker 中存在 PostgreSQL、Redis、Elasticsearch 或 MinIO
When 客户端展示资源状态
Then 每个资源显示健康、ownership 和保护状态
And 未带平台 ownership label 的资源不提供删除动作
And 运行中的宿主进程单独显示所属 Run、项目、argv 和启动时间，并可导航到 Run 取消

## 场景：创建托管 PostgreSQL

Given 已保存的 Workspace、未占用的 host port 和可读的 Credential Manager Secret
When 开发者从客户端创建托管 PostgreSQL 18
Then 系统先持久化 planned intent，再以稳定名称和 managed/workspace/kind/resource labels 创建容器
And Secret value 只进入 Docker 子进程环境，不进入 argv、SQLite 或 API response
And 只有容器 identity、image、published port、ownership 与 healthy inspect 全部匹配时记录才进入 active

Given 创建中断、端口冲突、Secret 缺失或容器仍在 starting
When 客户端刷新或执行 reconcile
Then 系统从持久化 intent 幂等推进到 active 或带稳定 failure_code 的 failed/provisioning
And 名称碰撞的外部容器绝不被接管

Given 托管 PostgreSQL 仍引用一个 Secret
When 开发者删除 Secret
Then 系统返回 `SECRET_IN_USE`
And 只有托管资源进入 removed 后 Secret 才可删除

## 场景：受控清理冗余资源

Given Docker 中存在带 `tripguru.local/managed=true` 的冗余资源
When 客户端请求清理预览
Then 只有删除后仍保留同类健康实例的托管资源可被选择
And MinIO、外部资源与同类最后一个健康实例都显示不可删除原因

Given 开发者确认清理预览
When 系统执行删除
Then 系统以最新 Docker 快照逐个重新校验 ownership、类型和保留规则
And 任一资源不再满足条件时跳过该资源并返回稳定错误，不扩大删除范围
And cleanup 委托托管 lifecycle 复核 ownership 并把记录迁为 removed
And PostgreSQL 数据 volume 不随容器 cleanup 自动删除

Contract: `src/pipedeck/api.py`
Evidence: `tests/`, `apps/web/e2e/`

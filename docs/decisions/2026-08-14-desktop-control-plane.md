# 独立桌面客户端与本地控制服务

类型：ARD（Architecture Rationale/Decision）
状态：Accepted
日期：2026-08-14
Accountable owner: TripGuru Engineering
当前契约：`src/tripguru_local/api.py`

## 背景与驱动

产品需要同时管理本机仓库、Git 凭据、Host 命令、Docker 和长期运行状态。界面关闭、刷新或未来增加 CLI 时，调度和配置仍必须只有一个 owner。

## 决策

采用 Tauri 桌面壳、React 客户端和 Python 本地控制服务。所有编排逻辑由控制服务拥有；Tauri 仅管理窗口、托盘和 sidecar 生命周期；React 只消费类型化 API。

## 替代方案

- Docker Desktop Extension：Docker 集成直接，但绑定 Docker Desktop，Host 文件与 Git 操作仍需要高权限 helper。
- Electron：复用 TypeScript 直接，但打包体积和 Node IPC 权限面更大。
- 浏览器控制台：适合开发和测试，但不是最终产品形态。
- Backstage、Portainer、DevPod：分别偏组织门户、容器管理和开发容器，不能独立拥有本产品的组合工作区与本地集成语义。

## 后果与验证

需要维护一个很薄的 Rust 壳，并把 Python 控制服务打包为 sidecar。Web 客户端仍可在浏览器独立开发，API 契约测试和浏览器验收无需依赖桌面打包。

## 复查条件

如果产品转为团队共享服务、取消 Docker Desktop 依赖，或 Python sidecar 的启动与升级成为主要可靠性问题，则新增后续 ARD 重新评估边界。

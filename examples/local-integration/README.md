# 本地集成功能验收示例

这个示例使用 Python 标准库实现两个独立服务，验证完整链路：浏览器页面 → 前端服务 → 后端 API → SQLite。前端启动前会检查后端是否就绪；保存记录后重新查询数据库。

在仓库根目录执行自动验收：

```powershell
uv run pytest tests/test_integration_environment.py -q
```

测试会把 `frontend/` 和 `backend/` 复制为两个临时 Git 仓库，通过真实控制 API 导入并建立工作区。它把前端排在项目列表首位、声明依赖后端，验证构建、依赖排序、就绪检查、运行版本、业务读写及停止后的入口撤除。测试结束会释放进程和临时数据。

如果需要手动体验页面，可在两个终端依次启动：

```powershell
uv run python examples/local-integration/backend/app.py --port 18761 --database local-state/integration-example.db
uv run python examples/local-integration/frontend/app.py --port 18762 --backend http://127.0.0.1:18761
```

打开 `http://127.0.0.1:18762`，输入内容并保存。手动启动仅展示示例业务；上面的自动验收才覆盖 Pipedeck 控制流程。手动运行前需确保 `local-state/` 存在、示例端口空闲，结束后在对应终端按 Ctrl+C。

该示例证明 Host 服务的真实集成和数据读写，不代表已经验证任意业务仓库、完整环境复制或与线上环境完全等价。

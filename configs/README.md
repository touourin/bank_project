# 保留的配置资料

- `bank/schema.json`：最终 mock 的字段类型、事件说明、模拟约定和待确认事项，离线 mock 工具继续使用。
- `mappings/*.json`：保留的原始映射配置，当前没有运行引擎，不由 API 加载。

运行连接配置由 `src/bank_project/settings.py` 与本地 `.env` 管理。MySQL、Neo4j、本体和模型配置保留，当前 API 只提供健康检查。

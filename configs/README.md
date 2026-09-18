# 配置预留目录

后续可分别存放银行 schema、字段映射、本体映射和规则版本。

`bank/schema.json` 为离线 mock 数据工具提供源字段类型、事件属性说明、模拟约定及待银行确认清单。三个 `MOCK_` 事件码仅用于测试，正式码确认后需同步更新生成/校验规则与测试并重新生成。
其中明确区分源表定义、用户确认项与模拟取值约定。当前业务服务没有加载这份配置。
运行配置通过 settings.py 与 .env 管理。Neo4j 为可选依赖；MySQL 只读来源使用 `BANK_MYSQL_SOURCE_ENABLED` 显式启用，并通过 `BANK_MYSQL_SOURCE_TABLES` 限制允许访问的表。

## 通用转换映射

`mappings/*.json` 定义不同业务的对象、事件和关系映射。`bank/schema.json` 为银行示例提供字段依据，通用引擎不依赖固定银行表名。新业务可增加自己的配置，见 `docs/preparation.md`。

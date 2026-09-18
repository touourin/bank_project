# 业务映射配置

此目录中的 JSON 在启动时加载。通用导入/转换代码不写死表名或事件码。

- customer-tags.json：银行客户标签示例。
- customer-journey.json：银行旅程示例。
- 新业务增加独立 JSON，重启后在 GET /api/v1/mappings 查看。

没有匹配配置时，每行保留为独立 record；需要业务语义时定义对象、事件和关系。
配置字段与非银行订单示例见 [数据准备指南](../../docs/preparation.md)。schema_ref 可选，仅引用 configs 内的已有 schema；新业务可以直接配置 columns。

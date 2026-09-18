# 通用数据导入与转换

## 使用流程

1. 在 `/docs` 使用 `POST /api/v1/imports/upload` 选择文件，填写 dataset_id、batch_id、source_system；Excel 可以指定 sheet。
2. 使用 `POST /api/v1/extractions` 提交 dataset_id、batch_id；可指定 mapping。
3. 查看转换摘要、`/extractions/{batch_id}/status` 状态，以及 `/extractions/{batch_id}` 的完整 ExtractionBatch。

导入、转换均为同步调用。导入成功不代表转换成功，也不意味着已建图。大文档转换会等待多个模型调用；调用超时或进程中断后先查询状态，再用相同批次重试。已完成的块会复用缓存，不发布半份结果。

可使用 `POST /api/v1/imports` 从收件目录导入：source_uri 为 `file:相对路径`。不能访问目录外的路径、符号链接、任意 HTTP URL。收件目录由 BANK_IMPORT_ROOT 配置，不是用户可以任意浏览的服务器磁盘。

原始文件完整留存，解析记录包含文件名及 CSV 记录号、JSONL 行号、Excel 页名/行号、PDF 页码、Word 段落号。CSV 一个记录可能跨多个物理文本行，locator 中的 record 指记录序号，包含表头。

Excel 默认读取第一个工作表，回执会提示未导入其他页面；可显式选择 sheet，再用新 batch_id 导入另一页。每个页面独立成为一份来源。公式或错误单元格明确拒绝，不执行公式或使用可能过期的公式缓存。

Excel 数值从 XML 读取原始十进制文本，不再经过 Python float；但无法恢复文件保存前已被 Excel 截断的精度或前导零。标识符应在源表中保存为文本。真实原文件和原始行证据始终保留。

Excel 和 MySQL 的原生日期、时间按 ISO 文本保留，包括原有时分秒、小数秒和已知时区，不擅自补时区；时长使用 ISO duration（如 `PT97212S`）。Excel 日期精度受源文件和读取库约束，完整原文件同时留存。普通文本中的 `YYYYMMDD` 不在导入层修改，按转换配置处理；不要将原生时间戳套用到 `YYYYMMDD` 规则。Excel 原始行列坐标先检查上限、重复和一致性，再展开为单元格数组。

默认上限：单文件 20 MiB、50,000 条记录、500 列、文档正文 200,000 字符、PDF 200 页、Office 解压总量 80 MiB。模型使用 4,000 字符切块、200 字符重叠、最多 64 块。CSV 单字段还受标准库 128 KiB 上限约束。输入超限会拒绝，不静默截断。

## 转换策略

- 没有匹配配置：每行产生一个通用 record 对象，完整保留字段；不会猜测客户、订单或事件。
- 有映射配置：根据主键、字段类型、实体、事件、参与方和关系规则转换。
- 文档：使用模型从原文抽取实体、事件和关系，校验类型、引用和逐字证据；结果标记 review_required。

导入不依赖映射配置。`table` 只是来源表名或逻辑名称，可以任意指定；它不是内置的银行表枚举。
自动选择映射时先精确匹配配置中的 table，再按 match_columns 匹配。多个配置都匹配时必须显式指定 mapping。设置 `mapping: "generic_record"` 可以强制通用记录模式。
配置是运行服务的人维护的本地 JSON，HTTP 请求只能选择已有配置名称，不能上传代码或执行表达式。

## 新增业务配置示例

假设新表的列为 order_id、buyer、seller、amount、day，把以下内容保存到 `configs/mappings/orders.json`，然后重启服务：

```json
{
  "id": "sales_orders",
  "version": "1",
  "table": "orders",
  "match_columns": ["order_id", "buyer", "seller", "amount", "day"],
  "primary_key": ["order_id"],
  "columns": [
    {"name": "amount", "sql_type": "decimal(26,8)"},
    {"name": "day", "sql_type": "varchar(8)", "format": "YYYYMMDD"}
  ],
  "entities": [
    {"alias": "buyer", "entity_type": "company", "identity_scope": "key", "keys": {"company_id": "buyer"}},
    {"alias": "seller", "entity_type": "company", "identity_scope": "key", "keys": {"company_id": "seller"}}
  ],
  "event": {
    "event_type": "purchase",
    "date_field": "day",
    "participants": [
      {"entity": "buyer", "role": "buyer"},
      {"entity": "seller", "role": "seller"}
    ]
  },
  "relations": [
    {"subject": "buyer", "predicate": "buys_from", "object": "seller", "link_event": true}
  ]
}
```

上传 `orders.csv` 后调用转换接口时可明确指定 `mapping: "sales_orders"`。两行相同买卖双方的订单仍然产生两个事件、两条有方向且关联事件的关系。

配置含义：

| 项 | 用途 |
| --- | --- |
| `columns` | 可选字段类型规则；支持 varchar(N)、int、decimal(P,S)、string、text；format 支持 YYYYMMDD、json_object |
| `source_nullable: false` | 明确要求该字段存在且非空；不会把 mock_nullable 自动当成银行要求 |
| `primary_key` | 用于同表重复记录检查和事件标识；没有时按来源快照及行位置隔离 |
| `identity_scope` | record 默认每行独立；key 仅按明确外部标识合并引用，不按名称消歧 |
| `keys` | 外部标识名 → 源字段路径；标识值需为非空文本 |
| `properties` | 输出属性名 → 源字段路径；`copy_row: true` 则复制整行 |
| `when`、`optional` | 按字段值匹配可选实体；缺少可选实体时可跳过相应参与方/关系 |
| `event_type` / `type_field` | 固定事件类型或来源字段，二选一 |
| `date_field` | 日期字段，未提供日期则保持为空，不制造时间 |
| `attributes_field` | 嵌套 JSON 事件属性路径，先使用 json_object 类型解析 |
| `dictionary` | 可选事件码字典，记录名称、分类、属性类型和待确认状态 |
| `relations` | 按实体 alias 指定方向和类型，可关联本行事件 |

字段路径支持 `PROPERTIES.账户号` 这样的对象路径；源列名本身有点号时优先精确匹配列名。当前配置每行至多生成一个事件；一个源记录包含多条事件、数组展开、复杂跨行业务计算时应增加专用转换器并通过接口装配，不能靠猜测拆分。

未定义的额外源字段保留在原始记录和证据中；配置了 copy_row 或事件映射时也会保留在相应属性里。同一明确对象标识出现相互冲突的属性会报错，避免后读入的行覆盖前面的事实。

银行示例通过 schema_ref 引用 `configs/bank/schema.json`；通用层不依赖这个文件，新业务可直接声明 columns。YWJC0017/18 仍为未定义1/2，MOCK_ 临时码及待确认项目继续标记，不自动替换成正式码。

## 模型与数据库配置

文档抽取需配置 BANK_MODEL_ENABLED=true、BANK_MODEL_BASE_URL（例如内部服务的 /v1 基地址）、BANK_MODEL_NAME 和适用的 BANK_MODEL_API_KEY。兼容接口为 POST /chat/completions，要求支持 JSON 对象输出。未配置模型时导入仍可成功，文档转换返回 503，不生成模拟抽取结果。

模型地址只能由服务配置提供，不由上传文件指定。只有明确启用模型才会把文档正文发送到配置的服务；重定向不跟随，错误返回不透传服务密钥或响应正文。可用 BANK_MODEL_PROMPT_FILE 替换提示词和 few-shot，仍需遵守统一输出契约。

模型必须明确返回 entities、events、relations 三个数组，没有事实时返回空数组；缺字段或只返回 `{}` 视为失败。响应限 1 MiB，请求要求 identity 编码，不接受强制压缩的响应，以免先解压后超限。

开启 BANK_MYSQL_SOURCE_ENABLED，并设置 BANK_MYSQL_SOURCE_TABLES 为 JSON 数组（例如 `["orders","customers"]`），再填写连接参数。source_uri 只允许 `mysql:白名单表名`，只读事务读取全表快照；超出行数或字节限制报错，不截断。账号需有查询权限，服务不执行 DDL/DML。Docker 连接宿主机上的数据库时应使用适合环境的地址，而非容器内的 127.0.0.1。

实时流、增量游标、数据库分页任务不在本次实现中；新增来源只需实现 SourceReader，不应把连接代码写进 ingestion 或 extraction。

## 持久化与运行边界

本地数据在 BANK_DATA_DIR，Docker 默认使用 preparation-data 命名卷。包括原文件、导入清单、原子写入的候选批次、转换状态和模型块缓存。目录使用哈希文件名；请求参数不拼接成磁盘路径。

相同 dataset_id/batch_id/来源/内容的导入复用回执；内容变化需要新 batch_id。转换成功后重复调用复用结果；映射配置或程序版本变化时拒绝覆盖旧结果，使用新 batch_id 保存新版本。

修复解析行为后也应以新 batch_id 重新导入原文件；已有导入快照不会偷偷重写。例如旧版本已截去时间的快照不能仅靠重新转换恢复时间。本轮转换版本为 bank-preparation/1.1，历史结果仍可读取。

不同批次之间不擅自按文本去重。固定业务主键的对象/事件标识可保持一致，来源证据分别保留。数据集名称是逻辑命名空间，不是独立租户的授权机制。

同一批次使用操作系统文件锁串行处理；进程退出会释放锁。转换期间失败或进程被杀死不会发布半批候选；状态查询会识别已失去执行进程的 running 状态，并允许重试。原文件摘要不符时停止转换。

HTTP 服务通过 BANK_MAX_CONCURRENT_JOBS 限制每进程同时进行的导入/转换，默认 2。超过上限立即返回 503 和 Retry-After，不读取额外上传内容；健康、状态查询仍可调用。同步任务运行在线程池中；这不是后台任务队列，也不是跨进程并发限制。增加工作进程会增加整体并发和内存需求。

当前默认存储适合本地、单实例容器、K8s 单副本配持久卷。多个副本、分布式任务队列和对象存储需要替换 PreparationStore/RawStore，并由共享数据库或队列负责并发控制，不能把本地文件锁当成分布式锁。

服务默认绑定本机端口，供可信开发团队使用。BANK_API_TOKEN 提供统一 Bearer 鉴权；若对多个团队或业务人员开放，需要进一步接入身份与数据集权限。模板中的密钥为空，真实密钥不提交 Git。

## 是否需要独立前端

当前两位开发者联调：Swagger 已能上传文件、选择批次和映射、触发转换、查看状态与结果，暂不引入单独前端工程。

业务人员使用时，建议增加独立操作台，包含文件/来源选择、批次列表、失败行定位、来源与候选对照、复核确认。前端只调用这些 HTTP API，不包含格式解析、字段映射或模型调用规则。前端可独立部署，后端边界不变。

实际接入业务前需要用明确的真实样本评估模型准确率；本次自动化测试验证的是协议与防错行为，不代表模型语义判断完全正确。所有文档结果都作为候选交接，后续消歧、推理检查及复核仍由对应模块负责。

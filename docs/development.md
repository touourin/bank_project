# 两人共同开发

邓王璘维护通用 ingestion、extraction 和来源/模型适配；晏子怡从 resolution、graph、query 继续实现。业务特定规则放在 configs/mappings，共享 contracts 与 ports 变更需要双方对齐。

## 扩展方式

1. 普通新表先增加映射配置，参考 docs/preparation.md 的非银行订单示例。
2. 新格式、来源、存储或模型先定义/复用 ports，具体 SDK 放 adapters。
3. 业务模块通过构造参数接收接口，不能导入相邻业务模块的实现。
4. bootstrap 负责装配；API 只调用 ApplicationServices 中的接口。
5. 为真实行为补充测试，尤其来源保留、稳定标识、类型和失败边界。

文件导入和转换为独立同步操作；耗时调用在 HTTP 工作线程执行，健康检查保持异步响应。不要把进程内临时任务列表当持久任务队列。

## 检查

- make check：静态检查、测试、OpenAPI 一致性、离线 mock 校验。
- make test：接口、通用映射、两份完整 mock 的 CSV/XLSX、模型协议、来源、持久化及异常处理。
- make schema：接口变更后导出 OpenAPI，不启动服务或读取密钥。
- make build / make run：默认启动独立 API；Neo4j 使用 graph profile。

测试中的模型通过 HTTP MockTransport 验证协议和错误行为，MySQL 使用驱动替身验证只读 SQL、白名单和类型保留。真实供应商模型与银行数据库仍需配置后联调。禁止把模板密钥、原始银行数据、运行结果放入版本控制；data/ 已忽略。

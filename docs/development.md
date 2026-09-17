# 两人共同开发

当前各 service.py 仅保留签名并抛出 FeatureNotImplemented。没有抽取、消歧、建图、查询、推理或编排实现。

邓王璘从 ingestion、extraction 开始；晏子怡从 resolution、graph、query 开始。共享 contracts、ports 与 pipeline 的变更由双方对齐。

## 模块接入方式

1. 先确认该模块的输入输出和业务需求。
2. 在对应目录实现 ports 中的接口，通过构造参数注入存储或模型依赖。
3. 将外部 SDK 封装在 adapters，业务模块不直接导入 SDK。
4. 在 bootstrap 装配具体实现；API 保持依赖接口。
5. 为实际业务行为补充必要测试。

业务模块不直接导入另一个业务模块，由后续 pipeline 通过接口编排。
依赖边界检查自动发现新增业务目录，同时检查 API 和适配器的依赖方向。
目前 bootstrap 仅装配 pipeline、query 占位服务以及可选 Neo4j 连接；其他模块尚未接入执行链路。

HTTP 响应模型位于 api/models.py，模块交接模型位于 contracts/models.py。
基础设施 HealthCheck 使用异步接口：ready 有整体超时，close 在应用退出时等待完成。
当前业务占位接口仍为同步签名，未来是否异步由实际工作负载确定。

## 框架验证

- make check：依次执行静态检查、测试和已保存 OpenAPI 的一致性检查。
- make test：框架启动、HTTP 501 占位行为、配置拒绝、超时取消、生命周期及依赖边界。
- make lint：静态检查和格式检查。
- make schema：导出离线 OpenAPI，不读取环境配置、不连接 Neo4j。
- make build / make run：构建并启动 Docker 服务。

测试通过仅表示框架可用，不代表任何业务链路已实现。原来的转账演示、业务算法、演示命令及相关测试已从工程中撤下。

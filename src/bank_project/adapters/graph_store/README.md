# 图数据库适配器

当前 connection.py 仅管理 Neo4j 驱动生命周期，并通过 RETURN 1 检查连接。

使用异步驱动；asyncio.timeout 覆盖整个就绪检查，超时会取消等待并释放该次会话。
连接与连接池超时只限制获取连接的阶段，不能替代整体检查超时。
参见 [Neo4j 驱动超时说明](https://neo4j.com/docs/api/python-driver/current/api.html#connection-timeout)。

GraphReader、GraphWriter、RunReader 只在 ports.py 中定义，尚无实现。
框架不创建业务 schema、节点或约束，不加载已有演示数据。

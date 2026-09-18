# 模型接口适配

CompatibleModel 实现 DocumentModel，使用可配置的兼容 HTTP 接口。开启 BANK_MODEL_ENABLED 后按需调用；默认 DisabledModel 明确返回依赖未配置。

支持通用抽取提示词、few-shot、自定义提示词文件、JSON 输出、有限重试、超时和响应大小限制。不执行文档内的工具或代码，不跟随 HTTP 重定向，不向调用方透传上游响应正文及密钥。
输出通过共享契约和逐字证据检查，缓存由 extraction 的 DocumentCache 接口管理。此模块不读取原始文件、不连接图数据库。

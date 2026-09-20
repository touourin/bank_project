# 数据工作台前端

第二步按“选择数据 → 核对匹配与生成规则 → 生成并查看结果”组织，普通表直接核对身份字段和属性，高级模板默认折叠。任务来源与新分析选择分别展示，发布状态优先于历史进度文案。

`AlignmentPage` 负责状态编排；`RunOverview` 展示任务来源和状态；`MappingResults` 提供字段搜索、分页及原检索证据；`GenerationRules` 管理唯一一份规则草稿，`AdvancedTemplateEditor` 编辑同一份草稿；`GenerationPanel` 展示已保存规则的生成范围，`GraphPanel` 展示实际发布结果。`workflow.ts` 统一生成范围及任务状态的计算。规则有未保存修改时禁止生成、切换或刷新任务，并提供撤销入口；宽表调整后重新核对。

React + TypeScript + Vite + Ant Design。Docker 由 Caddy 提供静态文件和同源 API 代理。首页包含第一步“数据接入”：文件上传、MySQL 选表、接入记录、分页数据预览、字段结构、单批次删除及错误反馈。

第二步“本体对齐与图谱生成”位于 `src/features/alignment/`，包含跨批次选表、分析任务轮询、字段映射与关联依据、独立图谱生成、已发布版本预览及节点原值详情。两个步骤复用 `ui/` 公共组件及 `api/request.ts` HTTP 边界；不会自动重试生成等写操作。

```bash
npm --prefix frontend run dev
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend run format:check
```

开发代理默认指向 `http://127.0.0.1:8000`，可用 `BANK_API_PROXY` 指定其他后端。主题偏好保存在浏览器本地，API token 和自定义数据库密码仅在页面内存中。刷新后需重新填写访问凭证。

## 公共组件

`frontend/src/ui/` 只负责展示和交互，不依赖 `features/`、数据接入 API 或银行字段。后续步骤可以直接使用这一层。

| 文件 | 职责 |
| --- | --- |
| `UIProvider.tsx` | 中文语言包、主题上下文、组件默认配置；跟随系统主题和减少动画偏好 |
| `theme.ts` | 唯一的颜色配置，同时生成 Ant Design 主题和页面 CSS 变量 |
| `Panel.tsx` | 内容面板、标题、说明及操作区 |
| `DataTable.tsx` | 表格尺寸、横向滚动、表头和可访问区域；不做客户端分页 |
| `PagePagination.tsx` | 后端 offset 与页码的换算、加载禁用、统一数量展示 |
| `Feedback.tsx` | 错误与重试、加载、空状态 |
| `ConfirmDialog.tsx` | 异步确认、阻止重复提交、错误保留与重试，操作中禁止关闭 |
| `ui.css` | 公共组件布局；业务页面布局保留在各自功能目录 |

按钮、输入框、选择框、表单、上传、标签页等直接使用 Ant Design。只在项目有统一行为时封装组件，避免给每个库组件再套一层同名包装。弹窗使用受控组件；如需全局消息，使用 `App.useApp()`，不要使用脱离主题上下文的静态方法。

业务代码位于 `src/features/intake/`。组件负责交互状态，`api.ts` 负责请求，公共组件不发请求。新增步骤在 `features/` 下建立功能目录，复用 `ui/` 与全局主题，不要再单独写一套按钮、表单或分页样式。

数据表格按后端返回的字符串显示，保留前导零和大金额精度，区分 NULL 与空文本。上传控件不会自动发请求；用户点击“解析并暂存”后按文件独立处理，部分失败不会阻止后续文件。表单切换标签页后保留输入，修改 MySQL 连接信息会清除旧选表结果。

## 运行与验证

浏览器测试使用临时批次/分析库，覆盖文件上传、类型与原值、分页、同名文件隔离、多文件部分失败、删除取消及失败重试、密码和凭证不持久化、主题、错误恢复及移动端布局。第二步测试使用替身模型、检索适配器及图存储，验证真实 API 选表分析、结果持久化、取消构图、低分禁止构图、retrieve 候选及原始得分展示、人工选择、节点详情和页面刷新。真实 Neo4j 发布原子性通过独立数据库的 `tests/test_alignment_graph.py` 验证。

组件库在运行时生成样式，Caddy 的 CSP 允许内联样式以支持主题、下拉框和弹窗定位；脚本仍限定同源，不允许内联脚本或 `eval`。修改组件库配置后，应在 Docker 静态构建中检查 CSP 与控制台，不能仅检查 Vite 开发环境。

仅更新前端容器：

```bash
docker compose build frontend
docker compose up -d --no-deps --wait frontend
```

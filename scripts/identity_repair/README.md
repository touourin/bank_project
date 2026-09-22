# 人员身份与企业关系测试数据修订

版本：`person-relations-20260922-v1`。这是对已确认的**合成测试数据**的一次迁移，不是生产客户资料纠错工具，也不从姓名推断真实身份或控制关系。

两套数据独立处理：项目版 `examples/mock` 保留 `MOCK_` 编号；桌面版 `~/Desktop/五张表_修正版` 对应服务器 `shanghai_proj.marketing_*` 源表。客户、账户、交易编号及金额、日期、集团原有成员关系保持不变。旧接入批次、对齐方案和图谱版本不改写。

## 数据设计

- 原始 1,000 个人员身份保留独立编号和证件，使用 980 个姓名，显式保留 20 对同名不同人的反例；其中 18 对同时出现在客户表当前角色中。
- 保留 59 个集团、236 家成员企业。为本测试版本明确设置集团实控人，并在工商股东和征信实际控制人明细中提供一致证据；这是构造的测试场景，不能据此将一般集团成员关系推断为共同控制。其中 53 个集团有多个客户样本，6 个只有一个。
- 非集团企业中，48 家组成 24 对同法人、不同实控人的反例；另外 200 家采用法人和实控人不同的场景。两类场景独立。
- 最终客户表涉及 929 个法人身份、823 个实控人身份；401 家企业的两种角色不同。71 位法人代表多家企业。
- 历史交易经办人、个人征信和个人司法记录仍归属于原证件；修改当前企业角色不会转移历史个人风险记录。资金注入交易的自然人出资账户名称与该场景的控股股东一致。
- 辅助主体和未在当前 1,000 家客户范围内的记录保留原值。没有增加列、合并客户、删除交易或修订既有财务冲突。

## 实现与复核

`policy.py` 是明确的测试场景规则。`state.py` 从不可变备份建立人员与企业映射。`cells.mjs` 使用 Artifact Tool 编写替换值；`workbooks.py` 流式嵌入这些单元格，保留原布局、样式、数值文本、工作表和其他 ZIP 部件。大流水表不整体载入内存。

`workbooks verify` 独立读回每一行、每个单元格和样式，核对只有策略声明的字段发生变化。`audit.py` 另行验证客户表、工商汇总、工商明细、征信实控人与自然人证件是否一致，保留“同名不同人”和“同法人不等于同实控人”反例。`companions.py` 同步项目 CSV、来源包及其校验值，并重新打包来源 ZIP；历史语义审计报告保留原日期，由当前修订报告补充。

`server.py` 先备份四张受影响源表。桌面版与服务器待修改字段连同业务键的全量多重集校验必须相同。发布时使用 SERIALIZABLE 事务重新读取全表，确认备份以来无变化；在同一事务中更新、全量读回并验证所有字段，失败回滚。仅更新对应目录记录中的数据版本和工作簿哈希，其他业务表及图谱历史不修改。凭据来自应用已保存的加密连接，不出现在脚本或证据中。

## 执行顺序

使用仓库根目录，Python 命令需 `PYTHONPATH=scripts`。Excel 操作使用工作区配置的 bundled Python（含 lxml/openpyxl）与 Node（含 `@oai/artifact-tool`），服务器操作使用项目虚拟环境。

1. `python -m identity_repair.workbooks prepare`：备份两套文件并冻结映射。已变更的输入会拒绝再次作为旧版本执行。
2. 按表格技能执行 authoring marker，再运行 `node scripts/identity_repair/cells.mjs data/identity-repair-20260922/values.json data/identity-repair-20260922/authored.xlsx`。
3. 分别执行 `python -m identity_repair.workbooks build project` / `desktop`。
4. 分别执行 `python -m identity_repair.workbooks verify project` / `desktop`，以及 `python -m identity_repair.audit`。
5. `python -m identity_repair.companions`：同步项目 CSV 和来源压缩包；已执行后拒绝重写原始备份。
6. `python -m identity_repair.server backup`：源表完整备份。可安装 `mysql-connector-python` 并设置 `IDENTITY_REPAIR_COMPRESS=1` 使用压缩连接；不设置则用项目 PyMySQL。
7. `python -m identity_repair.server publish`，随后 `python -m identity_repair.server verify`。发布前要求桌面 Excel 完整校验已通过。
8. 分别执行 `python -m identity_repair.workbooks install project` / `desktop`。安装前再次核对原文件和产物哈希。
9. `python -m identity_repair.finalize`：核对安装结果，在两套数据目录写入当前修订清单及测试身份映射。

默认迁移目录 `data/identity-repair-20260922` 保留原始工作簿、CSV、源表 SQL 压缩备份、逐表校验结果和场景清单；最终工作簿另存 `outputs/identity-repair-20260922`。这两个目录不提交 Git。人员规则、脚本、回归测试和小型项目工作簿随代码提交。

重新生成基础数据会回到基础生成器对应的版本；要复现本修订，需从其未修订输出依次执行此迁移。不要对已修订的姓名或重复法人编号重新建立原始身份映射。恢复服务器时以备份 SQL 在独立恢复表中核验后再切换，避免直接往现有表追加重复行。

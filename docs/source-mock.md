# 征信、工商与交易流水 mock

本地提供的三份源字典已整理为 `data/schema-review/normalized-source-schema.json`。新生成器在既有客户标签和旅程之上，输出138张源表、2,407个字段的CSV。生成器与API、数据库和旧mock文件独立。

```bash
.venv/bin/python scripts/generate_source_mock.py
.venv/bin/python scripts/validate_source_mock.py
```

默认输出 `data/mock-sources/`，含征信、工商、交易流水三个ZIP、schema、空值规则、字段覆盖统计、来源关联和校验报告。压缩包中的说明列出正式码表缺失时采用的mock约定。数据目录由Git忽略。新环境需先放入已核对的源字典schema；不会读取开发者桌面上的硬编码路径。

默认复用1,000个客户，核心表覆盖全部客户；拓展表默认使用100个客户做字段/场景覆盖，另有10个明确标记的注销、吊销及事业单位实体。每张表的全部字段都保留并至少有适用非空样例。字段覆盖不代表所有银行正式业务规则已实现。

征信可空字段包含NULL和真实零值。与客户级样例对应的分类指标借鉴36/50的空值比例，其他可空字段的20%缺失是mock假设。主键及明确非空字段不置空；财务金额按块缺失。详见输出 `null-policy.json` 和逐字段实际统计。

工资由原月度旅程拆成逐人交易，约48.5万条工资支付记录，加上结算、借贷、冲正、定期和外币场景。账户余额逐笔滚动，日均按无交易日结转。`expected/customer_tags.csv`保留155列契约；代发和期末存款金额匹配原数据，原先估算的日均以及主动结算指标按源明细重算，差异另列。来源未覆盖的标签仍明确标为继承旧mock，不假称已由三类源推导。

小批量验证：

```bash
.venv/bin/python scripts/generate_source_mock.py --customers 20 --coverage-customers 20 --output data/mock-sources-small
.venv/bin/python scripts/validate_source_mock.py --input data/mock-sources-small
.venv/bin/python -m pytest tests/test_source_mock.py -q
```

生成先写临时目录，读回校验通过后发布。已有输出保留为 `.previous` 备份，备份已存在时拒绝覆盖。不会进行数据库导入、DDL变更或改写原始Excel。

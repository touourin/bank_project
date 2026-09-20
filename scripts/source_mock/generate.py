"""Build the complete source fixture package in a staging directory."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from . import business, credit
from .common import ROOT, CsvSink, aux_fields, write_json
from .facts import Subject
from .schema import load_inputs, null_policy, prepare_schema
from .transactions import generate_customer


def auxiliary_subjects(subjects: list[Subject], seed: int) -> list[Subject]:
    """Extra lifecycle/nonprofit entities avoid cancelling an active baseline firm."""
    extra = []
    for offset in range(10):
        index = 9001 + offset
        tag = copy.deepcopy(subjects[offset % len(subjects)].tag)
        tag.update(
            cust_ind=f"MOCK_AUX_{index}",
            cust_nm=f"模拟{'注销企业' if offset == 0 else '吊销企业' if offset == 1 else '公益事业单位'}{index}",
            ecif_cust_id=f"MOCK_ECIF_AUX_{index}",
            unify_credit_code=f"MOCK{index:014d}",
            legal_rep_nm=f"模拟法人{offset}",
            legal_rep_cust_id=f"MOCK_PERSON_{index}",
            act_ctrl_psn_nm=f"模拟法人{offset}",
            act_ctrl_psn_cust_id=f"MOCK_PERSON_{index}",
            survival_status="注销" if offset == 0 else "吊销" if offset == 1 else "存续",
        )
        for name in (
            "report_crdt_amt",
            "report_crdt_bal",
            "report_crdt_our_bank_amt",
            "report_crdt_our_bank_bal",
        ):
            tag[name] = "0.00000000"
        extra.append(Subject(tag, index, [], seed, "company" if offset < 2 else "nonprofit"))
    return extra


def emit_reference(output: Path, subjects: list[Subject], extra: list[Subject]):
    master = CsvSink(
        output / "reference/customer_identity.csv",
        aux_fields(
            [
                "cust_ind",
                "ecif_cust_id",
                "cust_nm",
                "unify_credit_code",
                "person_cust_id",
                "person_name",
                "person_certificate",
                "algorithm_id",
                "enterprise_report_id",
                "personal_report_id",
                "population",
                "entity_type",
            ]
        ),
    )
    for s in subjects + extra:
        master.add(
            dict(
                cust_ind=s.tag["cust_ind"],
                ecif_cust_id=s.tag["ecif_cust_id"],
                cust_nm=s.tag["cust_nm"],
                unify_credit_code=s.tag["unify_credit_code"],
                person_cust_id=s.person_id,
                person_name=s.person,
                person_certificate=s.certificate,
                algorithm_id=s.algorithm_id,
                enterprise_report_id=s.report,
                personal_report_id=s.personal_report,
                population="baseline" if s in subjects else "supplemental_lifecycle_or_nonprofit",
                entity_type=s.entity_type,
            )
        )
    return master.close()


def build(
    output: Path,
    review_path: Path,
    input_dir: Path,
    customers: int,
    coverage_customers: int,
    seed: int,
) -> dict:
    if not 3 <= customers <= 1000:
        raise ValueError("customers must be between 3 and 1000")
    if not 3 <= coverage_customers <= customers:
        raise ValueError("coverage-customers must be between 3 and customers")
    baseline_contract = json.loads((ROOT / "configs/bank/schema.json").read_text())
    contract = prepare_schema(json.loads(review_path.read_text()), baseline_contract)
    tags, events = load_inputs(input_dir, customers)
    grouped = defaultdict(list)
    for row in events:
        grouped[row["CUST_ID"]].append(
            {**row, "properties": json.loads(row["PROPERTIES"], parse_float=Decimal)}
        )
    subjects = [
        Subject(tag, index, grouped[tag["cust_ind"]], seed) for index, tag in enumerate(tags, 1)
    ]
    extra = auxiliary_subjects(subjects, seed)
    dates = {t["dt"] for t in tags}
    if len(dates) != 1:
        raise ValueError("baseline must contain one as-of date")
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(
        version="1.0",
        synthetic=True,
        seed=seed,
        as_of=next(iter(dates)),
        baseline_customers=len(subjects),
        auxiliary_entities=len(extra),
        coverage_customers=coverage_customers,
        source_table_count=len(contract["tables"]),
        source_field_occurrences=sum(t["column_count"] for t in contract["tables"]),
        baseline_tags_sha256=hashlib.sha256(
            (input_dir / "CCM_C_CUST_FLAG_INFO.csv").read_bytes()
        ).hexdigest(),
        files={},
        limitations=[
            "Code values are explicit mock conventions, not official bank dictionaries.",
            "Core company, report, loan, payroll and balance facts are reconciled; peripheral tables are schema/scene coverage fixtures, not all bank business rules.",
            "Customer feature sample is not a raw credit record sample; unmapped nullable fields use a separately disclosed 20% synthetic missing rate.",
            "Original baseline files are unchanged; source-derived daily averages and settlement metrics are published in expected/customer_tags.csv.",
        ],
    )
    manifest["files"]["reference/customer_identity.csv"] = emit_reference(output, subjects, extra)
    lineage = CsvSink(
        output / "reference/source_row_links.csv",
        aux_fields(
            ["table", "csv_row", "cust_ind", "report_id", "person_cust_id", "fixture_level"]
        ),
    )
    for table in contract["tables"]:
        if table["domain"] == "transactions":
            continue
        filename = table["filename"]
        sink = CsvSink(output / filename, table["columns"], table["mock_primary_key"])
        if table["domain"] == "credit":
            population = subjects + extra if table["sheet"] in credit.CORE else subjects
            # Nonprofit formats are attached to nonprofit reference entities.
            if table["sheet"].startswith(("PBCEC_EG07", "PBCEC_EG08", "PBCEC_EG09", "PBCEC_EG10")):
                population = extra[2:]
            iterator = credit.rows_for(table, population, coverage_customers)
            for row, s, equations in iterator:
                sink.add(row)
                if equations:
                    table["financial_equations"] = equations
                lineage.add(
                    dict(
                        table=table["sheet"],
                        csv_row=str(sink.rows + 1),
                        cust_ind=s.tag["cust_ind"],
                        report_id=s.personal_report
                        if table["sheet"].startswith("PBCPC")
                        else s.report,
                        person_cust_id=s.person_id,
                        fixture_level="core"
                        if table["sheet"] in credit.CORE
                        else "coverage_fixture",
                    )
                )
        else:
            for row, s in business.rows_for(table, subjects + extra, coverage_customers):
                sink.add(row)
                lineage.add(
                    dict(
                        table=table["sheet"],
                        csv_row=str(sink.rows + 1),
                        cust_ind=s.tag["cust_ind"],
                        report_id="",
                        person_cust_id=s.person_id,
                        fixture_level="core"
                        if table["sheet"] in business.CORE
                        else "coverage_fixture",
                    )
                )
        manifest["files"][filename] = sink.close()
    manifest["files"]["reference/source_row_links.csv"] = lineage.close()
    txn_table = next(t for t in contract["tables"] if t["domain"] == "transactions")
    txn = CsvSink(
        output / txn_table["filename"], txn_table["columns"], txn_table["mock_primary_key"]
    )
    accounts = CsvSink(
        output / "reference/accounts.csv",
        aux_fields(
            [
                "cust_ind",
                "accno",
                "ccycd",
                "prod_cd",
                "open_acct_dt",
                "period_start",
                "dt",
                "opening_balance",
                "closing_balance",
            ]
        ),
        ["accno"],
    )
    expected = CsvSink(
        output / "expected/customer_tags.csv",
        [
            dict(
                name=c["name"],
                type=c["sql_type"],
                format=c.get("format"),
                mock_required=not c["mock_nullable"],
            )
            for c in baseline_contract["tables"]["CCM_C_CUST_FLAG_INFO"]["columns"]
        ],
        ["cust_ind"],
    )
    changes = CsvSink(
        output / "expected/changed_tag_values.csv",
        aux_fields(["cust_ind", "field", "baseline_value", "derived_value", "reason"]),
    )
    tag_provenance = {
        c["name"]: "inherited_baseline_not_derived_from_these_sources"
        for c in baseline_contract["tables"]["CCM_C_CUST_FLAG_INFO"]["columns"]
    }
    for position, s in enumerate(subjects, 1):
        metrics = generate_customer(txn_table, s, txn, accounts)
        row = {**s.tag, **metrics}
        for k, v in metrics.items():
            tag_provenance[k] = "derived_from_transaction_ledger_and_account_master"
            if v != s.tag[k]:
                changes.add(
                    dict(
                        cust_ind=s.tag["cust_ind"],
                        field=k,
                        baseline_value=s.tag[k],
                        derived_value=v,
                        reason="源流水实际聚合；月均笔数按ROUND_HALF_UP取整",
                    )
                )
        expected.add(row)
        if position % 100 == 0 or position == len(subjects):
            print(
                f"Generated {position}/{len(subjects)} customers; {txn.rows:,} transaction rows",
                flush=True,
            )
    for name, sink in [
        (txn_table["filename"], txn),
        ("reference/accounts.csv", accounts),
        ("expected/customer_tags.csv", expected),
        ("expected/changed_tag_values.csv", changes),
    ]:
        manifest["files"][name] = sink.close()
    manifest["domain_rows"] = dict(
        Counter(
            {
                domain: sum(
                    v["rows"] for k, v in manifest["files"].items() if k.startswith(domain + "/")
                )
                for domain in ("credit", "business", "transactions")
            }
        )
    )
    write_json(output / "schema.json", contract)
    write_json(output / "null-policy.json", null_policy())
    write_json(output / "tag-provenance.json", tag_provenance)
    write_json(output / "tag-schema.json", baseline_contract["tables"]["CCM_C_CUST_FLAG_INFO"])
    write_json(output / "manifest.json", manifest)
    write_readme(output, manifest)
    return manifest


def write_readme(output, manifest):
    text = f"""# 三类源数据 mock

全部为合成数据。快照 {manifest["as_of"]}，种子 {manifest["seed"]}。复用 {manifest["baseline_customers"]} 个原有企业客户，另有 {manifest["auxiliary_entities"]} 个注销、吊销及事业单位实体用于适用场景覆盖。

| 数据 | 表数 | 行数 |
| --- | ---: | ---: |
| 征信 | 98 | {manifest["domain_rows"]["credit"]:,} |
| 工商 | 39 | {manifest["domain_rows"]["business"]:,} |
| 交易流水 | 1 | {manifest["domain_rows"]["transactions"]:,} |

CSV使用UTF-8 BOM，第一行为规范化英文字段名。读取时按schema指定类型；客户号、账号、证件和YYYYMMDD日期保留文本，金额用Decimal。CSV空单元格表示NULL，字符串NULL、空值与数值0不会混用。

## 空值

征信PK及明确N字段非空。PK/Y冲突按mock主键必填处理，原定义仍保存在schema。样例风险分类指标36/50为空，相关可空指标借鉴72%缺失比例；机构数参考47/50。其他可空字段20%缺失是mock假设。财务表金额按整块缺失，避免一部分合计有值而其组成随机缺失。每表前三个适用主体保留字段覆盖样本，因此小样本实际比例不保证等于目标比例。

原有客户均有征信报告，本版保留报告存在性，只模拟报告内部缺失。样例19000102尚无解释，不作为真实日期生成。`null-policy.json`给出样例位置和规则；`manifest.json`逐字段统计非空、NULL和0。

## 规范化及口径

- 共同字段使用客户标签名称、类型和长度。工商注册/实收资本已由万元转为元；schema保留来源类型、位置和转换动作，生成值是规范化值，不可再次乘10000。
- `EG05BJ01`按明确记录的mock修正规则去除误混入的单元格范围字符串。原名保留在schema。
- 所有短枚举均为mock码。M001表示我行、M002表示另一模拟银行；01作为本版企业统一信用代码/个人合成证件类型，不能拿去解释银行正式码表。
- 个人还款历史表提供一个观测月份；电信缴费记录N表示本版模拟的已缴费，不是正式征信码表定义。
- 授信总额为本版非共享的两家银行协议额度合计，授信余额为对应贷款账户最新余额。查询机构不用于识别授信机构。
- 流水D减少存款余额，C增加余额；txnamt非负。冲正按原流水序号连接，原单和冲正均排除有效结算统计。工资逐人一笔，按模拟员工账户去重。
- 近12月窗口是截至日往前一年后的次日至截至日。月均主动结算笔数除以12并ROUND_HALF_UP取整以符合标签int；不会为凑整数修改交易笔数。
- 每日余额从期初余额和逐笔变动计算，无交易日结转。年日均为本年截至日平均；近12月日均按窗口实际天数。外币折算使用明确固定mock汇率7.1，非市场汇率。
- 定期账户单独建账，外币账户覆盖收付后归零。工资资金补充与期末资金归集是明确的mock业务，不计主动结算。

## 文件

- `credit/`、`business/`、`transactions/`：138张源表CSV，schema内2,407字段全部保留。
- `reference/customer_identity.csv`：客户、企业报告、个人报告和人员证件之间的关系；辅助主体明确标记。
- `reference/source_row_links.csv`：非流水表各行的合成主体/报告归属；用于检查一对多表，不是源schema新增字段。
- `reference/accounts.csv`：账户期初/期末余额、产品、币种和开户时间。
- `expected/customer_tags.csv`：155列预期标签。原有代发和期末存款余额保持一致；原来的估算日均及未生成的主动结算指标按本版流水重算。
- `expected/changed_tag_values.csv`：与原标签相比发生变化的字段和值。
- `tag-provenance.json`：明确哪些标签由本版源数据推导，哪些仍来自旧mock的其他业务来源。不能把继承值说成三类源已覆盖。
- `schema.json`、`null-policy.json`、`manifest.json`、`validation.json`：字段、空值规则、覆盖统计、校验结果。

核心企业身份、资本、授信、贷款、代发和余额进行跨表对账。拓展表提供按字典的类型/字段/场景覆盖；缺失正式码表或业务定义的全部银行规则未被声称已验证。财务表提供显式mock勾稽公式并检查，事业单位格式绑定辅助事业单位；辅助事业单位不生成企业工商、股东出资及实际控制人记录。

## 复现

在项目根目录执行：

```bash
.venv/bin/python scripts/generate_source_mock.py
.venv/bin/python scripts/validate_source_mock.py
```

默认写入`data/mock-sources`（Git忽略）。生成先在临时目录完成并验证，通过后发布；已有目录保留为备份。不会写入数据库或覆盖原有两张mock表。
"""
    (output / "README.md").write_text(text, encoding="utf-8")


def package(output: Path):
    for domain, label in (("credit", "征信"), ("business", "工商"), ("transactions", "交易流水")):
        files = list((output / domain).glob("*.csv")) + [
            output / name
            for name in (
                "README.md",
                "schema.json",
                "null-policy.json",
                "manifest.json",
                "validation.json",
                "tag-provenance.json",
                "tag-schema.json",
            )
        ]
        files += list((output / "reference").glob("*.csv")) + list(
            (output / "expected").glob("*.csv")
        )
        files += [
            output / name
            for name in (
                "realism-report.json",
                "realism-quality.json",
                "semantic-codebooks.json",
                "semantic-audit.json",
            )
            if (output / name).exists()
        ]
        with ZipFile(
            output / f"{label}_mock.zip", "w", compression=ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for path in sorted(files):
                info = ZipInfo(str(path.relative_to(output)), date_time=(2026, 9, 17, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                with archive.open(info, "w", force_zip64=True) as dest, path.open("rb") as src:
                    shutil.copyfileobj(src, dest, 1024 * 1024)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema", type=Path, default=ROOT / "data/schema-review/normalized-source-schema.json"
    )
    parser.add_argument("--baseline", type=Path, default=ROOT / "examples/mock")
    parser.add_argument("--output", type=Path, default=ROOT / "data/mock-sources")
    parser.add_argument("--customers", type=int, default=1000)
    parser.add_argument("--coverage-customers", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260917)
    args = parser.parse_args()
    from .validation import validate

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".source-mock-", dir=args.output.parent) as temp:
        staging = Path(temp) / "result"
        manifest = build(
            staging, args.schema, args.baseline, args.customers, args.coverage_customers, args.seed
        )
        result = validate(staging)
        write_json(staging / "validation.json", result)
        if result["errors"]:
            raise ValueError("Validation failed: " + "; ".join(result["errors"][:20]))
        package(staging)
        if args.output.exists():
            backup = args.output.with_name(args.output.name + ".previous")
            if backup.exists():
                raise FileExistsError(
                    f"Backup already exists: {backup}; choose a new output directory"
                )
            args.output.rename(backup)
        staging.rename(args.output)
    print(
        json.dumps(
            dict(output=str(args.output), rows=manifest["domain_rows"], validation=result),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

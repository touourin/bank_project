"""Validate the mock contract and the explicitly documented synthetic scenarios."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .common import (
    DEFAULT_OUTPUT,
    DEFAULT_SCHEMA,
    JOURNEY_TABLE,
    SCALE,
    TAG_TABLE,
    confirmation_coverage,
    load_schema,
    parse_date,
    read_csv,
)


def _reject_constant(value: str):
    raise ValueError(f"非法 JSON 数值：{value}")


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 字段重复：{key}")
        result[key] = value
    return result


def parse_properties(value: str) -> dict:
    result = json.loads(
        value,
        parse_float=Decimal,
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_object,
    )
    if not isinstance(result, dict):
        raise ValueError("PROPERTIES 必须是 JSON 对象")
    return result


def scalar_error(value: str, field: dict) -> str | None:
    if value == "":
        return None if field["mock_nullable"] else "本版 mock 必须提供值"
    sql_type = field["sql_type"].lower().replace(" ", "")
    varchar = re.fullmatch(r"varchar\((\d+)\)", sql_type)
    decimal = re.fullmatch(r"decimal\((\d+),(\d+)\)", sql_type)
    if varchar and len(value) > int(varchar[1]):
        return f"超出 {sql_type} 长度"
    if sql_type == "int" and not re.fullmatch(r"-?[0-9]+", value):
        return "不是整数"
    if decimal:
        if not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
            return "不是普通十进制数（不接受科学计数法或非有限值）"
        integer, _, fraction = value.lstrip("-").partition(".")
        if len(integer.lstrip("0")) > int(decimal[1]) - int(decimal[2]) or len(fraction) > int(
            decimal[2]
        ):
            return f"超出 {sql_type} 精度"
    if field.get("mock_values") and value not in field["mock_values"]:
        return "不在本版 mock 约定的码值范围内"
    if field.get("format") == "YYYYMMDD":
        try:
            parse_date(value)
        except ValueError:
            return "不是有效的 YYYYMMDD 日期"
    return None


def property_errors(properties: dict, event: dict) -> list[str]:
    errors = []
    fields = event["properties"]
    aliases = {spec["source_name"]: name for name, spec in fields.items() if "source_name" in spec}
    for name, value in properties.items():
        field = fields.get(aliases.get(name, name))
        # The source sample is not a complete bank schema; unknown extra keys are allowed.
        if field is None or value in ("", None):
            continue
        if field["type"] == "string" and not isinstance(value, str):
            errors.append(f"{name} 应为字符串")
        if field["type"] == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, Decimal))
        ):
            errors.append(f"{name} 应为 JSON 数值")
        if field.get("format") == "YYYYMMDD":
            try:
                parse_date(str(value))
            except ValueError:
                errors.append(f"{name} 日期无效")
    return errors


def validate_rows(data: dict[str, list[dict]], schema: dict) -> list[str]:
    errors = []
    for table_name, table in schema["tables"].items():
        rows = data.get(table_name, [])
        if not rows:
            errors.append(f"{table_name}: 没有数据记录")
        fields = table["columns"]
        names = {field["name"] for field in fields}
        keys = set()
        for number, row in enumerate(rows, 2):
            prefix = f"{table_name} 第 {number} 行"
            if set(row) != names or any(not isinstance(v, str) for v in row.values()):
                errors.append(f"{prefix}: 字段数量或字段名不匹配")
                continue
            key = tuple(row[name] for name in table["primary_key"])
            if key in keys:
                errors.append(f"{prefix}: 主键重复")
            keys.add(key)
            for field in fields:
                error = scalar_error(row[field["name"]], field)
                if error:
                    errors.append(f"{prefix}.{field['name']}: {error}")
    if errors:
        return errors

    tags = {row["cust_ind"]: row for row in data[TAG_TABLE]}
    grouped = defaultdict(list)
    for number, row in enumerate(data[JOURNEY_TABLE], 2):
        prefix = f"{JOURNEY_TABLE} 第 {number} 行"
        if row["CUST_ID"] not in tags:
            errors.append(f"{prefix}: CUST_ID 找不到对应 cust_ind")
        elif row["DT"] != tags[row["CUST_ID"]]["dt"]:
            errors.append(f"{prefix}: 与标签快照的数据日期不同")
        if row["OCCUR_DT"] > row["DT"]:
            errors.append(f"{prefix}: 发生日期晚于数据日期")
        if row["KEY_FLAG"] not in {"0", "1"}:
            errors.append(f"{prefix}: KEY_FLAG 只能为 0 或 1")
        event = schema["events"].get(row["EVT_TYPE"])
        if not event or not event["generate"]:
            errors.append(f"{prefix}: 事件码不在本版生成范围内")
            continue
        if row["EVT_CLASS"] != event["event_class"]:
            errors.append(f"{prefix}: 事件大类与事件码不一致")
        try:
            properties = parse_properties(row["PROPERTIES"])
        except (ValueError, TypeError) as error:
            errors.append(f"{prefix}: {error}")
            continue
        errors.extend(
            f"{prefix}.PROPERTIES: {error}" for error in property_errors(properties, event)
        )
        if row["EVT_TYPE"] == "YWJC0001" and properties.get("客户号") != row["CUST_ID"]:
            errors.append(f"{prefix}: 建档客户号与外层 CUST_ID 不一致")
        grouped[row["CUST_ID"]].append((row, properties))
    if errors:
        return errors

    for customer_id, tag in tags.items():
        events = grouped[customer_id]
        if not events:
            errors.append(f"{customer_id}: 没有关联旅程")
            continue
        errors.extend(f"{customer_id}: {error}" for error in _scenario_errors(tag, events))
        errors.extend(f"{customer_id}: {error}" for error in _supplement_errors(tag, events))
        errors.extend(f"{customer_id}: {error}" for error in _timeline_errors(tag, events))
    return errors


def _timeline_errors(tag: dict, events: list[tuple[dict, dict]]) -> list[str]:
    """Check chronological and balance invariants of the generated scenarios."""
    errors = []
    opened, closed = set(), set()
    payroll_signed = set()
    approved = balance = Decimal(0)
    applied = False
    for row, properties in sorted(
        events, key=lambda item: (item[0]["OCCUR_DT"], item[0]["ROWKEY"])
    ):
        code, occurred = row["EVT_TYPE"], row["OCCUR_DT"]
        account = properties.get("账户号")
        if occurred < tag["found_dt"]:
            errors.append("事件早于企业成立日期")
        if code == "YWJC0002":
            opened.add(account)
        elif code == "YWJC0009-1" and account and properties.get("存款产品", "").startswith("单位"):
            # A term-deposit placement opens its own product subaccount; its
            # lifetime is also verified against the source account master.
            opened.add(account)
        elif account and (account not in opened or account in closed):
            errors.append("账户事件必须发生在开户后、销户前")
        if code == "YWJC0008":
            closed.add(account)
        if code == "YWJC0011":
            payroll_account = properties.get("账号")
            if payroll_account not in opened or payroll_account in closed:
                errors.append("代发签约账号尚未开户或已经销户")
            payroll_signed.add(payroll_account)
        if code == "YWJC0012" and account not in payroll_signed:
            errors.append("代发工资早于该账号的代发签约")
        if code in {"YWJC0003-1", "YWJC0003-2"}:
            applied = True
        if code in {"YWJC0004-1", "YWJC0004-2"} and not applied:
            errors.append("授信批复早于申报")
        if code == "YWJC0004-1":
            approved += Decimal(properties.get("批复金额", 0) or 0)
        if code == "YWJC0005":
            balance += Decimal(properties.get("放款金额", 0) or 0)
            if balance > approved:
                errors.append("放款超过已批复额度或早于批复")
        if code in {"YWJC0006-1", "YWJC0006-2"}:
            amount = Decimal(properties.get("还款金额", 0) or 0)
            balance -= amount
            if amount <= 0 or balance < 0:
                errors.append("还款金额非正或超过当时贷款余额")
            if code == "YWJC0006-2" and balance != 0:
                errors.append("结清事件后的贷款余额不为零")
            if code == "YWJC0006-1" and balance <= 0:
                errors.append("未结清事件后的贷款余额应为正")
        for field in ("到期日", "合同到期日", "贷款到期日"):
            if properties.get(field) and properties[field] < occurred:
                errors.append(f"{field}早于对应事件日期")
    if Decimal(tag["org_capt_amt"]) > Decimal(tag["cert_capt_amt"]):
        errors.append("本版实收资本超过注册资本")
    expected_zero = str(int(Decimal(tag["exchg_c_bal"]) == 0))
    if tag["zero_dep_cust_ind"] != expected_zero:
        errors.append("零存款标志与存款余额不一致")
    return errors


def _supplement_errors(tag: dict, events: list[tuple[dict, dict]]) -> list[str]:
    """Validate the five supplemented scenarios using our documented assumptions."""
    errors = []
    by_code = {r["EVT_TYPE"]: (r, p) for r, p in events}
    if tag["ipo_ind"] != str(int("DSJ0004" in by_code)):
        errors.append("ipo_ind 与本版上市事件不一致")
    for row, properties in events:
        code = row["EVT_TYPE"]
        if code == "DSJ0001":
            for name, field in [
                ("注册日期", "found_dt"),
                ("统一社会信用代码", "unify_credit_code"),
            ]:
                if properties.get(name) not in (None, "", tag[field]):
                    errors.append(f"工商注册的{name}与客户标签不一致")
            capital = properties.get("注册资本")
            if capital not in (None, "") and Decimal(capital) != Decimal(tag["cert_capt_amt"]):
                errors.append("工商注册的注册资本与客户标签不一致")
        elif code == "MOCK_CREDIT_SIGN":
            approval = by_code.get("YWJC0004-1")
            disbursement = by_code.get("YWJC0005")
            if approval is None or disbursement is None:
                errors.append("本版合同场景缺少批复通过或放款事件")
                continue
            if not approval[0]["OCCUR_DT"] <= row["OCCUR_DT"] <= disbursement[0]["OCCUR_DT"]:
                errors.append("本版合同签约必须发生在批复通过与放款之间")
            amount = properties.get("合同金额")
            if amount not in (None, "") and Decimal(amount) != Decimal(tag["our_bank_credit_lmt"]):
                errors.append("合同金额与本版授信额度不一致")
            maturity = properties.get("合同到期日")
            if maturity and maturity < row["OCCUR_DT"]:
                errors.append("合同到期日早于签约事件")
    return errors


def _scenario_errors(tag: dict, events: list[tuple[dict, dict]]) -> list[str]:
    """These checks describe our fixtures, not undocumented bank business rules."""
    errors = []

    def equals(field: str, expected) -> None:
        if Decimal(tag[field]) != Decimal(expected).quantize(SCALE, rounding=ROUND_HALF_UP):
            errors.append(f"{field} 与本版 mock 的事件汇总不一致")

    def event_sum(code: str, field: str) -> Decimal:
        return sum(
            (Decimal(p.get(field, 0) or 0) for r, p in events if r["EVT_TYPE"] == code), Decimal(0)
        )

    loan = event_sum("YWJC0005", "放款金额")
    repaid = event_sum("YWJC0006-1", "还款金额") + event_sum("YWJC0006-2", "还款金额")
    equals("exchg_c_loan_bal", loan - repaid)
    equals("cny_c_loan_bal", loan - repaid)
    equals("our_bank_credit_bal", loan - repaid)
    equals("our_bank_usd_lmt", loan - repaid)
    equals("our_bank_credit_lmt", event_sum("YWJC0004-1", "批复金额"))
    equals(
        "our_bank_avl_lmt", Decimal(tag["our_bank_credit_lmt"]) - Decimal(tag["our_bank_usd_lmt"])
    )
    equals("exchg_c_bal", event_sum("YWJC0009-1", "存款金额") + event_sum("YWJC0009-2", "存款金额"))
    equals("agt_amt_12m", event_sum("YWJC0012", "代发金额"))
    count = event_sum("YWJC0012", "代发人数")
    equals("agt_cnt_12m", count)
    equals("NUM_PYRL_AMT_12M_AVG", event_sum("YWJC0012", "代发金额") / count if count else 0)
    payroll = [(r, p) for r, p in events if r["EVT_TYPE"] == "YWJC0012"]
    equals(
        "PYRL_AMT_M",
        sum(
            (
                Decimal(p.get("代发金额", 0) or 0)
                for r, p in payroll
                if r["OCCUR_DT"][:6] == tag["dt"][:6]
            ),
            Decimal(0),
        ),
    )
    equals("PYRL_MON_NUM_12M", len({r["OCCUR_DT"][:6] for r, _ in payroll}))
    if tag["LATEST_PYRL_DT"] != max((r["OCCUR_DT"] for r, _ in payroll), default=""):
        errors.append("LATEST_PYRL_DT 与代发事件日期不一致")
    report_total = Decimal(tag["report_crdt_amt"])
    equals(
        "CFM_CRG_PCT",
        Decimal(tag["report_crdt_our_bank_amt"]) / report_total if report_total else 0,
    )

    codes = {r["EVT_TYPE"] for r, _ in events}
    for field, code in [
        ("pyrl_sign_ind", "YWJC0011"),
        ("corp_ebnkg_sign_ind", "YWJC0013"),
        ("mb_sign_ind", "YWJC0014"),
        ("snd_bl_sign_ind", "YWJC0015"),
        ("rplymt_tax_sign_ind", "YWJC0016"),
    ]:
        if tag[field] != str(int(code in codes)):
            errors.append(f"{field} 与签约事件不一致")
    for field in ("exchg_c_loan_bal", "our_bank_avl_lmt", "exchg_c_bal"):
        if Decimal(tag[field]) < 0:
            errors.append(f"{field} 在本版 mock 场景中不应为负")
    return errors


def validate_directory(directory: Path, schema_path: Path = DEFAULT_SCHEMA) -> list[str]:
    schema = load_schema(schema_path)
    errors, data = [], {}
    for table_name, table in schema["tables"].items():
        path = directory / table["filename"]
        header, rows = read_csv(path)
        if header != [f["name"] for f in table["columns"]]:
            errors.append(f"{path.name}: CSV 表头与字段契约不一致")
        data[table_name] = rows
    errors.extend(validate_rows(data, schema))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_sha256") != hashlib.sha256(schema_path.read_bytes()).hexdigest():
        errors.append("manifest.json: schema 已改变，需要重新生成 mock")
    for table_name, table in schema["tables"].items():
        spec = manifest.get("files", {}).get(table_name, {})
        path = directory / table["filename"]
        if spec.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            errors.append(f"{path.name}: 内容与 manifest 的校验和不一致")
        if spec.get("rows") != len(data[table_name]):
            errors.append(f"{path.name}: 行数与 manifest 不一致")
    actual_counts = dict(Counter(r.get("EVT_TYPE") for r in data[JOURNEY_TABLE]))
    if manifest.get("event_counts") != actual_counts:
        errors.append("manifest.json: 事件统计不一致")
    if manifest.get("pending_bank_confirmation") != confirmation_coverage(
        schema, data[JOURNEY_TABLE]
    ):
        errors.append("manifest.json: 待银行确认清单或实际覆盖数量不一致")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="校验合成 CSV 的字段、编号关联、日期及 mock 场景一致性"
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()
    try:
        errors = validate_directory(args.input_dir, args.schema)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, f"校验失败：{error}\n")
    if errors:
        parser.exit(1, "校验失败：\n" + "\n".join(errors[:30]) + f"\n共 {len(errors)} 项。\n")
    print(f"校验通过：{args.input_dir}")

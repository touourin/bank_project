"""Generate repeatable fictional customers and their related source events."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from calendar import monthrange
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from .common import (
    DEFAULT_OUTPUT,
    DEFAULT_SCHEMA,
    JOURNEY_TABLE,
    SCALE,
    TAG_TABLE,
    compact_date,
    confirmation_coverage,
    json_exact,
    load_schema,
    write_csv,
)
from .profiles import Customer, make_customer, money


def customer_events(customer: Customer, schema: dict) -> list[dict]:
    rows: list[dict] = []
    c = customer
    rng = random.Random(c.seed)
    coverage = c.index <= 20

    def add(code: str, occurred: date, properties: dict, *, key: bool = False) -> None:
        spec = schema["events"][code]
        if not spec["generate"]:
            raise ValueError(f"Event is outside the mock scope: {code}")
        properties = dict(properties)
        for name, field in spec["properties"].items():
            if field.get("format") == "YYYYMMDD" and name not in properties:
                properties[name] = compact_date(occurred)
        rows.append(
            {
                "DT": compact_date(c.as_of),
                "ROWKEY": f"MOCK_EVT_{c.index:04d}_{len(rows) + 1:03d}",
                "EVT_CLASS": spec["event_class"],
                "EVT_TYPE": code,
                "CUST_ID": c.customer_id,
                "OCCUR_DT": compact_date(occurred),
                "KEY_FLAG": "1" if key else "0",
                "PROPERTIES": json_exact(properties),
            }
        )

    opened = c.ago(c.onboard_days - rng.randint(1, 14))
    add("YWJC0001", c.ago(c.onboard_days), {"客户号": c.customer_id})
    account_properties = {
        "账户分类": "一类户",
        "账户号": c.account,
        "账户类型": "",
        "开户机构": c.branch,
        "外币账户": "0",
        "FT账户": "0",
    }
    add("YWJC0002", opened, account_properties)
    if c.deposit:
        add(
            "YWJC0009-2",
            max(opened, c.ago(rng.randint(1, 90))),
            {
                "存款产品": "单位活期存款",
                "账户号": c.account,
                "存款金额": c.deposit,
                "经办机构": c.branch,
            },
        )

    signed = opened + timedelta(days=rng.randint(1, 10))
    if coverage or rng.random() < 0.88:
        add("YWJC0013", signed, {"经办机构": c.branch})
    if coverage or rng.random() < 0.58:
        add("YWJC0014", signed + timedelta(days=2), {"经办机构": c.branch})
    if coverage or rng.random() < 0.55:
        add("YWJC0016", signed + timedelta(days=3), {"经办机构": ""})

    if c.profile in {1, 2, 3}:
        applied = c.ago(rng.randint(160, 310))
        approved = applied + timedelta(days=rng.randint(5, 25))
        contract = approved + timedelta(days=rng.randint(1, 7))
        funded = contract + timedelta(days=rng.randint(1, 10))
        maturity = funded + timedelta(days=365)
        product = rng.choice(["流动资金贷款", "经营周转贷款", "设备购置贷款"])
        code = "YWJC0003-1" if c.index % 2 else "YWJC0003-2"
        properties = {
            "申报金额": c.credit_application,
            "申报模式": "单笔单批" if code.endswith("-1") else "复合授信",
            "授信发生方式": "新发生",
            "申报机构": c.branch,
            "申报编号": f"MOCK_APP_{c.index:04d}",
        }
        if code.endswith("-1"):
            properties["授信品种"] = product
        add(code, applied, properties)
        if c.profile == 3:
            add("YWJC0004-2", approved, {}, key=True)
        else:
            add("YWJC0004-1", approved, {"批复金额": c.limit, "批复期限": "1年"}, key=True)
            add(
                "MOCK_CREDIT_SIGN",
                contract,
                {
                    "授信品种": product,
                    "合同金额": c.limit,
                    "币种": "CNY",
                    "合同到期日": compact_date(maturity),
                    "合同状态": "MOCK_ACTIVE",
                },
            )
            add(
                "YWJC0005",
                funded,
                {
                    "放款金额": c.disbursement,
                    "币种": "CNY",
                    "贷款品种": product,
                    "贷款到期日": compact_date(maturity),
                },
            )
            installments = rng.randint(2, 5)
            repayment_end = c.ago(rng.randint(1, 30))
            paid = Decimal(0)
            for installment in range(1, installments + 1):
                amount = (
                    money(c.repayment / installments)
                    if installment < installments
                    else c.repayment - paid
                )
                paid += amount
                settled = c.profile == 2 and installment == installments
                code = "YWJC0006-2" if settled else "YWJC0006-1"
                occurred = funded + timedelta(
                    days=(repayment_end - funded).days * installment // installments
                )
                add(
                    code,
                    occurred,
                    {
                        "贷款品种" if settled else "授信品种": product,
                        "还款金额": amount,
                    },
                )

    payroll_enabled = (coverage and c.profile in {1, 2}) or (
        c.onboard_days > 365 and rng.random() < 0.52
    )
    if payroll_enabled:
        months = rng.randint(3, 12)
        payday = rng.choice([5, 10, 15, 20, 25])
        dates = []
        for offset in range(12):
            year, month_index = divmod(c.as_of.year * 12 + c.as_of.month - 1 - offset, 12)
            payment_date = date(year, month_index + 1, payday)
            if payment_date <= c.as_of:
                dates.append(payment_date)
            if len(dates) == months:
                break
        add(
            "YWJC0011",
            min(dates) - timedelta(days=rng.randint(7, 20)),
            {
                "账号": c.account,
                "签约机构": c.branch,
            },
        )
        participants = max(1, c.employees * rng.randint(65, 100) // 100)
        for payment_date in sorted(dates):
            # The same synthetic employee group is paid monthly.
            salary = Decimal(c.sector[3] * rng.randint(85, 120)) / 100
            bonus = Decimal("1.5") if payment_date.month == 1 else Decimal(1)
            amount = money(participants * salary * bonus + Decimal(rng.randint(0, 99)) / 100)
            add(
                "YWJC0012",
                payment_date,
                {
                    "代发金额": amount,
                    "代发人数": participants,
                    "账户号": c.account,
                    "代发机构": c.branch,
                },
            )

    if c.deposit and (c.profile == 0 or (c.onboard_days > 120 and rng.random() < 0.28)):
        for _ in range(rng.randint(1, 3)):
            term = rng.choice([3, 6, 12])
            elapsed = rng.randint(1, min(60, term * 25))
            occurred = c.ago(elapsed)
            year, month_index = divmod(occurred.year * 12 + occurred.month - 1 + term, 12)
            maturity = date(
                year, month_index + 1, min(occurred.day, monthrange(year, month_index + 1)[1])
            )
            add(
                "YWJC0009-1",
                occurred,
                {
                    "存款产品": f"单位{term}个月定期存款",
                    "存款期限": f"{term}个月",
                    "账户号": c.account,
                    "存款金额": money(c.deposit * Decimal(rng.randint(20, 100)) / 100),
                    "经办机构": c.branch,
                    "到期日": compact_date(maturity),
                },
            )
        if coverage or rng.random() < 0.65:
            product = rng.choice([("CASH", "现金管理"), ("BOND", "稳健固收"), ("TERM", "定期开放")])
            add(
                "YWJC0010",
                c.ago(rng.randint(1, 35)),
                {
                    "产品代码": f"MOCK_PRODUCT_{product[0]}",
                    "理财产品名称": f"{product[1]}理财（模拟）",
                    "账户号": c.account,
                    "购买金额": money(c.deposit * Decimal(rng.randint(5, 30)) / 100),
                    "经办机构": c.branch,
                },
            )
    if (coverage and c.profile in {0, 4}) or (
        c.sector[0] in {"MFG", "TRADE"} and rng.random() < 0.45
    ):
        add("YWJC0015", signed + timedelta(days=4), {"经办机构": c.branch})
    if c.profile == 4 and (coverage or c.onboard_days > 365):
        add(
            "YWJC0002",
            opened + timedelta(days=15),
            {**account_properties, "账户号": c.secondary_account},
        )
        add(
            "YWJC0008",
            c.ago(rng.randint(1, 20)),
            {"销户经办机构": c.branch, "账户号": c.secondary_account},
        )
    if (coverage and c.profile == 0) or rng.random() < 0.025:
        for code in ("YWJC0017", "YWJC0018"):
            add(code, c.ago(rng.randint(1, 15)), {"经办机构": c.branch})

    add(
        "DSJ0001",
        c.ago(c.founded_days),
        {
            "工商注册机关": f"{c.region[0]}市{c.region[1]}模拟市场监管机构",
            "统一社会信用代码": c.credit_code,
            "注册资本": c.registered_capital,
        },
    )
    if c.listed:
        add(
            "DSJ0004",
            c.ago(rng.randint(30, 180)),
            {
                "股票板块": "MOCK_MAIN_BOARD",
                "股票代码": f"MOCK_STOCK_{c.index:04d}",
            },
            key=True,
        )
    if (coverage and c.profile in {0, 4}) or (
        c.sector[0] in {"MFG", "TRADE", "LOGISTICS"} and rng.random() < 0.3
    ):
        add("MOCK_FX_SIGN", signed + timedelta(days=5), {"经办机构": c.branch})
    if (coverage and c.profile in {1, 2}) or (
        c.sector[0] in {"MFG", "TRADE"} and rng.random() < 0.25
    ):
        add("MOCK_LC_SIGN", signed + timedelta(days=6), {"经办机构": c.branch})

    external_codes = ["DSJ0002", "DSJ0003-1", "DSJ0005-1", "DSJ0005-2", "DSJ0006"]
    selected = (
        [external_codes[c.profile]]
        if coverage
        else rng.sample(external_codes, rng.choices([0, 1, 2], [25, 55, 20])[0])
    )
    for external in selected:
        sentiment = rng.choices(["正面", "中性", "负面"], [35, 55, 10])[0]
        headline = {
            "正面": "新增生产线投入运营",
            "中性": "发布年度经营情况说明",
            "负面": "部分订单交付延期",
        }
        properties = {
            "DSJ0002": {
                "变更事项": "经营地址",
                "变更前内容": f"{c.region[0]}市模拟创业园{c.index % 50 + 1}号",
                "变更后内容": c.address,
            },
            "DSJ0003-1": {
                "轮次": rng.choice(["MOCK_A", "MOCK_B", "MOCK_C"]),
                "投资方": f"模拟产业投资机构{c.index % 12 + 1:02d}",
                "投融资金额": money(c.registered_capital * Decimal(rng.randint(20, 80)) / 100),
            },
            "DSJ0005-1": {
                "招标金额": money(c.registered_capital * Decimal(rng.randint(5, 20)) / 100),
                "标讯标题": f"{c.name}设备采购项目（模拟）",
            },
            "DSJ0005-2": {
                "中标金额": money(c.registered_capital * Decimal(rng.randint(4, 18)) / 100),
                "标讯标题": f"{c.name}服务供应项目中标公示（模拟）",
            },
            "DSJ0006": {
                "舆情标题": f"{c.name}{headline[sentiment]}",
                "媒体名称": "模拟企业资讯平台",
                "舆情等级": "MOCK_LEVEL_2" if sentiment == "负面" else "MOCK_LEVEL_1",
                "舆情属性": sentiment,
            },
        }
        add(
            external,
            c.ago(rng.randint(1, min(330, c.onboard_days - 20))),
            properties[external],
            key=external == "DSJ0006" and sentiment == "负面",
        )
    return rows


def customer_tags(c: Customer, events: list[dict], schema: dict) -> dict[str, str]:
    rng = random.Random(c.seed + 1)
    columns = schema["tables"][TAG_TABLE]["columns"]
    values = {
        field["name"]: (
            Decimal(0)
            if field["sql_type"].lower().startswith("decimal")
            else 0
            if field["sql_type"].lower() == "int"
            else "0"
            if field["sql_type"].lower() == "varchar(1)"
            else ""
        )
        for field in columns
    }
    codes = {e["EVT_TYPE"] for e in events}
    payroll = [
        json.loads(e["PROPERTIES"], parse_float=Decimal)
        for e in events
        if e["EVT_TYPE"] == "YWJC0012"
    ]
    payroll_amount = sum((p["代发金额"] for p in payroll), Decimal(0))
    payroll_count = sum(p["代发人数"] for p in payroll)
    monthly_payroll = sum(
        (p["代发金额"] for p in payroll if p["代发日期"][:6] == compact_date(c.as_of)[:6]),
        Decimal(0),
    )
    parsed = [(e, json.loads(e["PROPERTIES"], parse_float=Decimal)) for e in events]
    total_deposit = sum(
        (p["存款金额"] for e, p in parsed if e["EVT_TYPE"] in {"YWJC0009-1", "YWJC0009-2"}),
        Decimal(0),
    )
    average_factor = Decimal(rng.randint(55, 115)) / 100
    tech = c.sector[0] in {"TECH", "MEDICAL"}
    other_limit = money(c.registered_capital * Decimal(rng.randint(0, 80)) / 100)
    other_balance = money(other_limit * Decimal(rng.randint(10, 75)) / 100)
    person = "赵钱孙李周吴郑王"[c.index % 8] + "明远思宁文昕瑞安"[c.index // 8 % 8] + "（模拟）"
    organization = f"MOCK_ORG_{c.region[2]}_{c.region[3]}"
    values.update(
        {
            "rowkey": f"MOCK_TAG_{c.index:04d}",
            "cust_ind": c.customer_id,
            "ecif_cust_id": f"MOCK_ECIF_{c.index:04d}",
            "cust_nm": c.name,
            "unify_credit_code": c.credit_code,
            "found_dt": compact_date(c.ago(c.founded_days)),
            "survival_status": "存续",
            "opscope": c.sector[2],
            "cert_capt_amt": c.registered_capital,
            "cert_capt_ccy": "CNY",
            "org_capt_amt": money(c.registered_capital * Decimal(rng.randint(30, 100)) / 100),
            "org_capt_ccy": "CNY",
            "hold_type": "MOCK_HOLD_1",
            "corp_econ_type": "MOCK_ECON_1",
            "four_commis_corp_scale": f"MOCK_{c.size}",
            "cert_province": f"MOCK_{c.region[2]}",
            "cert_city": f"MOCK_{c.region[2]}_{c.region[3]}",
            "cert_district": f"MOCK_{c.region[3]}",
            "busin_addr": c.address,
            "mec_num": c.employees,
            "industry_cd": f"MOCK_{c.sector[0]}",
            "industry_big_cd": f"MOCK_{c.sector[0]}_2",
            "industry_mid_cd": f"MOCK_{c.sector[0]}_3",
            "industry_sml_cd": f"MOCK_{c.sector[0]}_4",
            "oprt_cust_ind": "1",
            "scient_corp_ind": str(int(tech)),
            "poe_ind": "1",
            "ipo_ind": str(int("DSJ0004" in codes)),
            "legal_rep_nm": person,
            "legal_rep_cust_id": f"MOCK_PERSON_{c.index:04d}",
            "act_ctrl_psn_nm": person,
            "act_ctrl_psn_cust_id": f"MOCK_PERSON_{c.index:04d}",
            "vol_manager_id": f"{organization}_{c.index % 8 + 1:02d}",
            "vol_manager_org_id": organization,
            "manu_manager_id": f"{organization}_{c.index % 8 + 1:02d}",
            "manu_manager_org_id": organization,
            "pyrl_sign_ind": str(int("YWJC0011" in codes)),
            "rplymt_tax_sign_ind": str(int("YWJC0016" in codes)),
            "corp_ebnkg_sign_ind": str(int("YWJC0013" in codes)),
            "mb_sign_ind": str(int("YWJC0014" in codes)),
            "snd_bl_sign_ind": str(int("YWJC0015" in codes)),
            "agt_cnt_12m": payroll_count,
            "agt_amt_12m": payroll_amount,
            "agt_num_12m": payroll[0]["代发人数"] if payroll else 0,
            "agt_vld_ind_12m": str(int(bool(payroll))),
            "open_acct_dt": min(e["OCCUR_DT"] for e in events if e["EVT_TYPE"] == "YWJC0002"),
            "lvl1_cust_ind": "1",
            "bsc_dep_acct_ind": "1",
            "zero_dep_cust_ind": str(int(total_deposit == 0)),
            "exchg_c_bal": total_deposit,
            "exchg_c_bal_yavg": money(total_deposit * average_factor),
            "c_exchg_cny_12m_yaug": money(total_deposit * average_factor),
            "exchg_cny_dmd_12m_yaug": money(c.deposit * average_factor),
            "genal_cust_ind": "1",
            "crg_cst_ind": str(int(c.limit > 0)),
            "manufacture_indu_loan_cust_ind": str(int(c.sector[0] == "MFG" and c.loan_balance > 0)),
            "mediun_cust_ind": str(int(c.size == "MEDIUM")),
            "our_bank_credit_lmt": c.limit,
            "our_bank_credit_bal": c.loan_balance,
            "our_bank_usd_lmt": c.loan_balance,
            "our_bank_avl_lmt": c.limit - c.loan_balance,
            "exchg_c_loan_bal": c.loan_balance,
            "cny_c_loan_bal": c.loan_balance,
            "exchg_c_loan_yavg": (c.disbursement + c.loan_balance) / 2,
            "corp_report_dt": compact_date(c.as_of),
            "report_crdt_amt": c.limit + other_limit,
            "report_crdt_bal": c.loan_balance + other_balance,
            "report_crdt_our_bank_amt": c.limit,
            "report_crdt_our_bank_bal": c.loan_balance,
            "hhnw_tech_entp_ind": str(int(tech and rng.random() < 0.6)),
            "st_tp_sme_ind": str(int(tech and c.size != "LARGE")),
            "SH_SCI_RNG": f"MOCK_RANGE_{rng.randint(1, 4)}" if tech else "",
            "CST_MGRP_ID": f"MOCK_GROUP_{c.group:02d}" if c.group else "",
            "CST_MGRP_NM": f"模拟产业集团{c.group:02d}" if c.group else "",
            "PYRL_MON_NUM_12M": len({p["代发日期"][:6] for p in payroll}),
            "PYRL_AMT_M": monthly_payroll,
            "CFM_CRG_PCT": c.limit / (c.limit + other_limit)
            if c.limit + other_limit
            else Decimal(0),
            "NUM_PYRL_AMT_12M_AVG": payroll_amount / payroll_count if payroll_count else Decimal(0),
            "LATEST_PYRL_DT": max((p["代发日期"] for p in payroll), default=""),
            "UNION_MEC_NUM": c.employees,
            "CUST_STATUS": "MOCK_ACTIVE",
            "dt": compact_date(c.as_of),
        }
    )
    for code, field in [
        ("YWJC0013", "corp_ebnkg_sign_dt"),
        ("YWJC0014", "mb_sign_dt"),
        ("YWJC0015", "snd_bl_sign_dt"),
    ]:
        values[field] = max((e["OCCUR_DT"] for e in events if e["EVT_TYPE"] == code), default="")
    financing = [p for e, p in parsed if e["EVT_TYPE"] == "DSJ0003-1"]
    if financing:
        latest = max(financing, key=lambda p: p["日期"])
        values.update(
            latest_fnc_round=latest["轮次"],
            latest_round_fnc_amt=latest["投融资金额"],
            latest_round_fnc_cny="CNY",
            latest_round_fnc_inv=latest["投资方"],
        )
    values["risk_cust_ind"] = str(
        int(any(e["EVT_TYPE"] == "DSJ0006" and p["舆情属性"] == "负面" for e, p in parsed))
    )
    # Keep the source's ambiguous field names; do not guess their intended meanings.
    values["snd_bl_cust_sign_ind"] = ""
    values["pyrl_sign_dt"] = ""
    result = {}
    for field in columns:
        value = values[field["name"]]
        if isinstance(value, Decimal):
            value = format(value.quantize(SCALE, rounding=ROUND_HALF_UP), "f")
        result[field["name"]] = str(value)
    return result


def build_dataset(schema: dict, *, customers: int, as_of: date, seed: int) -> dict[str, list[dict]]:
    if customers < 1 or customers > 10000:
        raise ValueError("customers must be between 1 and 10000")
    if as_of.year < 2000:
        raise ValueError("as_of must be on or after 2000-01-01")
    rng = random.Random(seed)
    tags, journeys = [], []
    for index in range(1, customers + 1):
        customer = make_customer(index, as_of, rng)
        events = customer_events(customer, schema)
        tags.append(customer_tags(customer, events, schema))
        journeys.extend(events)
    journeys.sort(key=lambda row: (row["CUST_ID"], row["OCCUR_DT"], row["ROWKEY"]))
    return {TAG_TABLE: tags, JOURNEY_TABLE: journeys}


def generate(
    output: Path,
    *,
    schema_path: Path = DEFAULT_SCHEMA,
    customers: int = 1000,
    as_of: date = date(2026, 9, 17),
    seed: int = 20260917,
) -> dict:
    from .validation import validate_rows

    schema = load_schema(schema_path)
    data = build_dataset(schema, customers=customers, as_of=as_of, seed=seed)
    errors = validate_rows(data, schema)
    if errors:
        raise ValueError("Generated data failed validation:\n" + "\n".join(errors[:20]))
    output.mkdir(parents=True, exist_ok=True)
    # Prepare all outputs before replacing files so failed validation never publishes data.
    with TemporaryDirectory(prefix=".mock-", dir=output) as temporary:
        staging = Path(temporary)
        files = {}
        for name, rows in data.items():
            table = schema["tables"][name]
            path = staging / table["filename"]
            write_csv(path, [f["name"] for f in table["columns"]], rows)
            files[name] = dict(
                filename=path.name,
                rows=len(rows),
                columns=len(table["columns"]),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        manifest = dict(
            schema_version=schema["version"],
            generator_version="2.0",
            schema_sha256=hashlib.sha256(schema_path.read_bytes()).hexdigest(),
            synthetic=True,
            seed=seed,
            as_of=as_of.isoformat(),
            files=files,
            event_counts=dict(sorted(Counter(r["EVT_TYPE"] for r in data[JOURNEY_TABLE]).items())),
            excluded_event_codes=[
                code for code, event in schema["events"].items() if not event["generate"]
            ],
            pending_bank_confirmation=confirmation_coverage(schema, data[JOURNEY_TABLE]),
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for path in staging.iterdir():
            path.replace(output / path.name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="生成两张纯合成银行 CSV，不启动服务或写数据库")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--customers", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 17))
    args = parser.parse_args()
    try:
        result = generate(
            args.output_dir,
            schema_path=args.schema,
            customers=args.customers,
            seed=args.seed,
            as_of=args.as_of,
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"生成失败：{error}\n")
    print(
        f"已生成 {result['files'][TAG_TABLE]['rows']} 个客户、{result['files'][JOURNEY_TABLE]['rows']} 条旅程：{args.output_dir}"
    )

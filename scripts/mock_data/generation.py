"""Generate repeatable fictional customers and their related source events."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Customer:
    index: int
    as_of: date
    deposit: Decimal
    credit_application: Decimal

    @property
    def customer_id(self) -> str:
        return f"MOCK_CUST_{self.index:04d}"

    @property
    def profile(self) -> int:
        return (self.index - 1) % 5

    @property
    def account(self) -> str:
        return f"{self.index:020d}"

    @property
    def secondary_account(self) -> str:
        return f"{100000 + self.index:020d}"

    @property
    def branch(self) -> str:
        return f"模拟支行{self.index % 4 + 1:02d}"

    @property
    def employees(self) -> int:
        return 30 + self.index * 5

    @property
    def credit_code(self) -> str:
        return f"MOCK{self.index:014d}"

    @property
    def registered_capital(self) -> Decimal:
        return Decimal(5000000 + self.index * 100000)

    @property
    def limit(self) -> Decimal:
        return self.credit_application if self.profile in {1, 2} else Decimal(0)

    @property
    def disbursement(self) -> Decimal:
        return self.limit / 2

    @property
    def repayment(self) -> Decimal:
        return self.disbursement if self.profile == 2 else self.disbursement / 4

    @property
    def loan_balance(self) -> Decimal:
        return self.disbursement - self.repayment

    def ago(self, days: int) -> date:
        return self.as_of - timedelta(days=days)


def customer_events(customer: Customer, schema: dict) -> list[dict]:
    rows: list[dict] = []
    c = customer

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

    add("YWJC0001", c.ago(300), {"客户号": c.customer_id})
    account_properties = {
        "账户分类": "一类户",
        "账户号": c.account,
        "账户类型": "",
        "开户机构": c.branch,
        "外币账户": "0",
        "FT账户": "0",
    }
    add("YWJC0002", c.ago(290), account_properties)
    add(
        "YWJC0009-2",
        c.ago(200),
        {
            "存款产品": "模拟活期存款",
            "账户号": c.account,
            "存款金额": c.deposit,
            "经办机构": c.branch,
        },
    )

    if c.profile != 3:
        for code, days in [("YWJC0013", 250), ("YWJC0014", 245), ("YWJC0016", 240)]:
            add(code, c.ago(days), {"经办机构": "" if code == "YWJC0016" else c.branch})

    if c.profile in {1, 2, 3}:
        code = "YWJC0003-1" if c.index % 2 else "YWJC0003-2"
        properties = {
            "申报金额": c.credit_application,
            "申报模式": "模拟单笔单批" if code.endswith("-1") else "模拟复合授信",
            "授信发生方式": "新发生",
            "申报机构": c.branch,
            "申报编号": f"MOCK_APP_{c.index:04d}",
        }
        if code.endswith("-1"):
            properties["授信品种"] = "模拟流动资金贷款"
        add(code, c.ago(180), properties)
        if c.profile == 3:
            add("YWJC0004-2", c.ago(170), {}, key=True)
        else:
            add("YWJC0004-1", c.ago(170), {"批复金额": c.limit, "批复期限": "1年"}, key=True)
            add(
                "MOCK_CREDIT_SIGN",
                c.ago(165),
                {
                    "授信品种": "模拟流动资金贷款",
                    "合同金额": c.limit,
                    "币种": "CNY",
                    "合同到期日": compact_date(c.as_of + timedelta(days=205)),
                    "合同状态": "MOCK_ACTIVE",
                },
            )
            add(
                "YWJC0005",
                c.ago(160),
                {
                    "放款金额": c.disbursement,
                    "币种": "CNY",
                    "贷款品种": "模拟流动资金贷款",
                    "贷款到期日": compact_date(c.as_of + timedelta(days=205)),
                },
            )
            repayment_code = "YWJC0006-1" if c.profile == 1 else "YWJC0006-2"
            product_key = "授信品种" if c.profile == 1 else "贷款品种"
            add(
                repayment_code,
                c.ago(30),
                {
                    product_key: "模拟流动资金贷款",
                    "还款金额": c.repayment,
                },
            )
            add("YWJC0011", c.ago(220), {"账号": c.account, "签约机构": c.branch})
            current_payday = c.as_of.replace(day=min(c.as_of.day, 10))
            for payment_date, amount in [
                (
                    c.as_of.replace(day=1) - timedelta(days=5),
                    Decimal(c.employees * 8000) + Decimal("0.25"),
                ),
                (current_payday, Decimal(c.employees * 8100) + Decimal("0.75")),
            ]:
                add(
                    "YWJC0012",
                    payment_date,
                    {
                        "代发金额": amount,
                        "代发人数": c.employees,
                        "账户号": c.account,
                        "代发机构": c.branch,
                    },
                )

    if c.profile in {0, 4}:
        add(
            "YWJC0009-1",
            c.ago(60),
            {
                "存款产品": "模拟定期存款",
                "存款期限": "6个月",
                "账户号": c.account,
                "存款金额": c.deposit * 2,
                "经办机构": c.branch,
                "到期日": compact_date(c.as_of + timedelta(days=120)),
            },
        )
        add(
            "YWJC0010",
            c.ago(40),
            {
                "产品代码": f"MOCK_PRODUCT_{c.index:04d}",
                "理财产品名称": "模拟理财产品",
                "账户号": c.account,
                "购买金额": Decimal(100000),
                "经办机构": c.branch,
            },
        )
        add("YWJC0015", c.ago(180), {"经办机构": c.branch})
    if c.profile == 4:
        add("YWJC0002", c.ago(280), {**account_properties, "账户号": c.secondary_account})
        add("YWJC0008", c.ago(5), {"销户经办机构": c.branch, "账户号": c.secondary_account})
    if c.profile == 0:
        for code in ("YWJC0017", "YWJC0018"):
            add(code, c.ago(15), {"经办机构": c.branch})

    # Supplement missing examples using requirement fields and explicit mock codes.
    add(
        "DSJ0001",
        c.ago(1500),
        {
            "工商注册机关": "模拟市场监督管理局",
            "统一社会信用代码": c.credit_code,
            "注册资本": c.registered_capital,
        },
    )
    if c.profile == 0:
        add(
            "DSJ0004",
            c.ago(120),
            {"股票板块": "MOCK_MAIN_BOARD", "股票代码": f"MOCK_STOCK_{c.index:04d}"},
            key=True,
        )
    if c.profile in {0, 4}:
        add("MOCK_FX_SIGN", c.ago(230), {"经办机构": c.branch})
    if c.profile in {1, 2}:
        add("MOCK_LC_SIGN", c.ago(225), {"经办机构": c.branch})

    external = ["DSJ0002", "DSJ0003-1", "DSJ0005-1", "DSJ0005-2", "DSJ0006"][c.profile]
    external_properties = {
        "DSJ0002": {"变更事项": "经营地址", "变更前内容": "模拟旧地址", "变更后内容": "模拟新地址"},
        "DSJ0003-1": {"轮次": "MOCK_A", "投资方": "模拟投资机构", "投融资金额": Decimal(2000000)},
        "DSJ0005-1": {"招标金额": Decimal(500000), "标讯标题": "模拟采购项目招标"},
        "DSJ0005-2": {"中标金额": Decimal(480000), "标讯标题": "模拟采购项目中标"},
        "DSJ0006": {
            "舆情标题": "模拟企业经营动态",
            "媒体名称": "模拟信息来源",
            "舆情等级": "MOCK_LEVEL_1",
            "舆情属性": "中性",
        },
    }
    add(external, c.ago(90), external_properties[external])
    return rows


def customer_tags(c: Customer, events: list[dict], schema: dict) -> dict[str, str]:
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
    total_deposit = c.deposit * (3 if c.profile in {0, 4} else 1)
    values.update(
        {
            "rowkey": f"MOCK_TAG_{c.index:04d}",
            "cust_ind": c.customer_id,
            "ecif_cust_id": f"MOCK_ECIF_{c.index:04d}",
            "cust_nm": f"模拟客户{c.index:04d}科技有限公司",
            "unify_credit_code": c.credit_code,
            "found_dt": compact_date(c.ago(1500)),
            "survival_status": "存续",
            "opscope": "模拟软件开发与技术服务",
            "cert_capt_amt": c.registered_capital,
            "cert_capt_ccy": "CNY",
            "org_capt_amt": Decimal(2500000 + c.index * 50000),
            "org_capt_ccy": "CNY",
            "hold_type": "MOCK_HOLD_1",
            "corp_econ_type": "MOCK_ECON_1",
            "four_commis_corp_scale": "MOCK_SCALE_1",
            "cert_province": "MOCK_PROVINCE",
            "cert_city": "MOCK_CITY",
            "cert_district": "MOCK_DISTRICT",
            "busin_addr": "模拟新地址" if c.profile == 0 else f"模拟园区{c.index:04d}号",
            "mec_num": c.employees,
            "industry_cd": "MOCK_L1",
            "industry_big_cd": "MOCK_L2",
            "industry_mid_cd": "MOCK_L3",
            "industry_sml_cd": "MOCK_L4",
            "oprt_cust_ind": "1",
            "scient_corp_ind": "1",
            "poe_ind": "1",
            "ipo_ind": str(int("DSJ0004" in codes)),
            "legal_rep_nm": f"模拟法人{c.index:04d}",
            "legal_rep_cust_id": f"MOCK_PERSON_{c.index:04d}",
            "act_ctrl_psn_nm": f"模拟法人{c.index:04d}",
            "act_ctrl_psn_cust_id": f"MOCK_PERSON_{c.index:04d}",
            "vol_manager_id": f"MOCK_MANAGER_{c.index % 4 + 1:02d}",
            "vol_manager_org_id": f"MOCK_ORG_{c.index % 4 + 1:02d}",
            "manu_manager_id": f"MOCK_MANAGER_{c.index % 4 + 1:02d}",
            "manu_manager_org_id": f"MOCK_ORG_{c.index % 4 + 1:02d}",
            "pyrl_sign_ind": str(int("YWJC0011" in codes)),
            "rplymt_tax_sign_ind": str(int("YWJC0016" in codes)),
            "corp_ebnkg_sign_ind": str(int("YWJC0013" in codes)),
            "mb_sign_ind": str(int("YWJC0014" in codes)),
            "snd_bl_sign_ind": str(int("YWJC0015" in codes)),
            "agt_cnt_12m": payroll_count,
            "agt_amt_12m": payroll_amount,
            "agt_num_12m": c.employees if payroll else 0,
            "agt_vld_ind_12m": str(int(bool(payroll))),
            "open_acct_dt": compact_date(c.ago(290)),
            "lvl1_cust_ind": "1",
            "exchg_c_bal": total_deposit,
            "exchg_c_bal_yavg": total_deposit * Decimal("0.8"),
            "c_exchg_cny_12m_yaug": total_deposit * Decimal("0.8"),
            "exchg_cny_dmd_12m_yaug": c.deposit * Decimal("0.8"),
            "genal_cust_ind": "1",
            "crg_cst_ind": str(int(c.limit > 0)),
            "our_bank_credit_lmt": c.limit,
            "our_bank_credit_bal": c.loan_balance,
            "our_bank_usd_lmt": c.loan_balance,
            "our_bank_avl_lmt": c.limit - c.loan_balance,
            "exchg_c_loan_bal": c.loan_balance,
            "cny_c_loan_bal": c.loan_balance,
            "exchg_c_loan_yavg": (c.disbursement + c.loan_balance) / 2,
            "corp_report_dt": compact_date(c.ago(10)),
            "report_crdt_amt": c.limit * 2,
            "report_crdt_bal": c.loan_balance * 2,
            "report_crdt_our_bank_amt": c.limit,
            "report_crdt_our_bank_bal": c.loan_balance,
            "hhnw_tech_entp_ind": str(int(c.profile in {1, 2})),
            "st_tp_sme_ind": "1",
            "SH_SCI_RNG": "MOCK_RANGE_1",
            "CST_MGRP_ID": f"MOCK_GROUP_{(c.index - 1) // 4 + 1:02d}",
            "CST_MGRP_NM": f"模拟集团{(c.index - 1) // 4 + 1:02d}",
            "PYRL_MON_NUM_12M": len({p["代发日期"][:6] for p in payroll}),
            "PYRL_AMT_M": monthly_payroll,
            "CFM_CRG_PCT": Decimal("0.5") if c.limit else Decimal(0),
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
    if c.profile == 1:
        values.update(
            latest_fnc_round="MOCK_A",
            latest_round_fnc_amt=Decimal(2000000),
            latest_round_fnc_cny="CNY",
            latest_round_fnc_inv="模拟投资机构",
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
        customer = Customer(
            index,
            as_of,
            Decimal(rng.randrange(100000, 900000)),
            Decimal(rng.randrange(50, 100) * 100000),
        )
        events = customer_events(customer, schema)
        tags.append(customer_tags(customer, events, schema))
        journeys.extend(events)
    journeys.sort(key=lambda row: (row["CUST_ID"], row["OCCUR_DT"], row["ROWKEY"]))
    return {TAG_TABLE: tags, JOURNEY_TABLE: journeys}


def generate(
    output: Path,
    *,
    schema_path: Path = DEFAULT_SCHEMA,
    customers: int = 20,
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
    parser.add_argument("--customers", type=int, default=20)
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

"""Shared fictional identities and dictionary-driven peripheral fixtures."""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

from .common import amount, compact, day, dec, stable


class Subject:
    def __init__(
        self, tag: dict, index: int, events: list[dict], seed: int, entity_type: str = "company"
    ):
        self.tag, self.index, self.events, self.seed = tag, index, events, seed
        self.entity_type = entity_type
        self.as_of, self.founded = day(tag["dt"]), day(tag["found_dt"])
        self.opened = day(tag["open_acct_dt"])
        self.person = tag["legal_rep_nm"]
        self.person_id = tag["legal_rep_cust_id"]
        self.algorithm_id = f"MOCK_ALG_{index:04d}"
        self.certificate = f"MOCKP{index:013d}"
        # One source table declares EA01AI01 as VARCHAR(4); a four-digit
        # synthetic report ID fits every supplied table without changing it.
        self.report = f"{index:04d}"
        self.personal_report = f"MPC{index:08d}{tag['dt']}"
        self.account = f"{index:020d}"
        self.branch = next(
            (e["properties"].get("开户机构") for e in events if e["EVT_TYPE"] == "YWJC0002"),
            "模拟支行",
        )
        self.regno = f"MR{index:013d}"
        self.orgcode = f"M{index:08d}"

    def loan_id(self, bank: int = 1) -> str:
        return f"{self.index:04d}{bank:02d}"

    def limit(self, bank: int = 1) -> Decimal:
        if bank == 1:
            return dec(self.tag["report_crdt_our_bank_amt"])
        return dec(self.tag["report_crdt_amt"]) - self.limit(1)

    def balance(self, bank: int = 1) -> Decimal:
        if bank == 1:
            return dec(self.tag["report_crdt_our_bank_bal"])
        return dec(self.tag["report_crdt_bal"]) - self.balance(1)

    def principal(self, bank: int = 1) -> Decimal:
        if bank == 1:
            return sum(
                (
                    dec(e["properties"]["放款金额"])
                    for e in self.events
                    if e["EVT_TYPE"] == "YWJC0005"
                ),
                Decimal(0),
            )
        return min(self.limit(2), self.balance(2) * Decimal("1.2")).quantize(Decimal("0.01"))

    def loan_history(self, bank: int = 1) -> dict:
        start = max(self.opened, self.as_of - timedelta(days=300))
        result = dict(
            signed=start,
            funded=start + timedelta(days=1),
            maturity=start + timedelta(days=366),
            repaid=self.as_of - timedelta(days=20),
            last_amount=self.principal(bank) - self.balance(bank),
        )
        if bank == 1:
            for e in self.events:
                if e["EVT_TYPE"] == "MOCK_CREDIT_SIGN":
                    result["signed"] = day(e["OCCUR_DT"])
                elif e["EVT_TYPE"] == "YWJC0005":
                    result["funded"] = day(e["OCCUR_DT"])
                    result["maturity"] = day(e["properties"]["贷款到期日"])
                elif e["EVT_TYPE"] in {"YWJC0006-1", "YWJC0006-2"}:
                    result["repaid"] = day(e["OCCUR_DT"])
                    result["last_amount"] = dec(e["properties"]["还款金额"])
        return result


def date_value(field: dict, value) -> str:
    dtype = field["type"].upper()
    if field.get("format") == "YYYYMMDD":
        return compact(value)
    if dtype == "DATE":
        return value.isoformat()
    if dtype == "TIMESTAMP":
        return value.isoformat() + " 12:00:00"
    length = int(re.search(r"\((\d+)\)", dtype)[1]) if "(" in dtype else 20
    if length == 4:
        return str(value.year)
    if length == 6:
        return value.strftime("%Y%m")
    if length == 7:
        return value.strftime("%Y-%m")
    if length <= 9:
        return compact(value)
    if "报告" in field["source_description"] and length >= 19:
        return value.isoformat() + " 12:00:00"
    return value.isoformat()


def semantic_value(field: dict, s: Subject, table: str, ordinal: int = 1) -> str:
    """Fallback is declared per field in the exported contract; never untyped random data."""
    name, raw, desc = field["name"], field["source_key"], field["source_description"]
    dtype = field["type"].upper()
    if name in s.tag:
        return s.tag[name]
    if raw in {"EA01AI01", "PA01AI01"}:
        return s.report if raw.startswith("E") else s.personal_report
    if raw in {"PA01BI01", "PA01CI01"}:
        return s.certificate
    if raw in {"PA01BQ01", "EC030Q01", "RYNAME", "INAME", "INAMECLEAN", "NAME"}:
        return s.person
    if raw in {"PA01BD01", "PA01CD01", "EA01CD01", "EC030D01", "EC050D02"}:
        return "01"
    if raw == "EA01CI01":
        return s.tag["unify_credit_code"]
    if raw in {"REGNO", "ORIREGNO", "RQREGNO"}:
        return s.regno
    if raw in {"ORGCODES", "JGDM", "RQORGCODE"}:
        return s.orgcode
    if raw == "RQCREDITCODE":
        return s.tag["unify_credit_code"]
    if raw in {"RQNAME", "JGMC"}:
        return s.tag["cust_nm"]
    if raw == "PALGORITHMID":
        return s.algorithm_id
    if raw == "CERTIFICATE_CODE":
        return s.certificate
    if raw == "PB020Q01":
        return f"模拟配偶{s.index:04d}"
    if raw == "PB020I01":
        return f"MOCKS{s.index:013d}"
    if raw in {
        "ED01AI01",
        "ED04AI01",
        "PD01AI01",
        "ED060I01",
        "PD02AI01",
        "PD01AI04",
        "ED01AI03",
        "ED04AI03",
    }:
        return f"{s.index:05d}" if raw.startswith("PD") else s.loan_id()
    if raw in {"ED060I02", "ED01AI02", "ED04AI02"}:
        return "M001"
    if dtype == "INT" or any(
        x in desc
        for x in [
            "个数",
            "账户数",
            "条数",
            "记录数",
            "笔数",
            "月数",
            "期数",
            "机构数",
            "月份数",
            "人数",
            "总数量",
            "条目数量",
        ]
    ):
        return str(12 if any(x in desc for x in ["月数", "期数", "月份数"]) else 1)
    if table.startswith(("PBCPC_", "PBCEC_")) and re.search(r"D\d{2}$", raw):
        length = int(re.search(r"\((\d+)\)", dtype)[1]) if "(" in dtype else 50
        return "1" if length == 1 else "01" if length == 2 else "M01"
    if "币种" in desc or "国籍" in desc:
        return "CNY" if "币种" in desc else "CHN"
    if "电子邮箱" in desc or "邮箱" in desc:
        return f"mock{s.index}@example.invalid"
    if "电话" in desc or "手机号码" in desc:
        return f"000{s.index:08d}"
    if "证件号码" in desc or "证照号码" in desc or "身份标识号码" in desc:
        return s.certificate
    if "信用代码" in desc:
        return f"MOCKR{s.index:013d}"
    if "姓名" in desc or desc in {"法人代表姓名", "出资人名称", "实际控制人名称"}:
        return s.person
    if "单位" in desc and ("工作" in desc or "缴费" in desc):
        return s.tag["cust_nm"]
    if any(x in desc for x in ["住所", "地址", "所在地", "参缴地"]):
        length = int(re.search(r"\((\d+)\)", dtype)[1]) if "(" in dtype else 400
        if length <= 10:
            return ("M" + f"{s.index:09d}")[-length:]
        return s.tag["busin_addr"]
    if dtype in {"DATE", "TIMESTAMP"} or any(
        x in desc
        for x in [
            "日期",
            "年月",
            "月份",
            "年份",
            "年度",
            "报告时间",
            "报告生成时间",
            "期限自",
            "期限至",
        ]
    ):
        value = s.as_of - timedelta(days=30)
        if "出生" in desc:
            value = s.as_of - timedelta(days=365 * (35 + s.index % 20))
        elif "成立" in desc:
            value = s.founded
        elif any(
            x in desc
            for x in ["开立", "开户", "生效", "开始", "起始", "参缴", "申请", "立案", "期限自"]
        ):
            value = max(s.opened, s.as_of - timedelta(days=360))
        elif any(x in desc for x in ["到期", "截止", "结束", "终止", "期限至"]):
            value = s.as_of + timedelta(days=365)
        elif "报表年份" in desc:
            value = s.as_of.replace(year=s.as_of.year - 1)
        elif "报告时间" in desc or "报告生成时间" in desc:
            value = s.as_of
        return date_value(field, value)
    if "比例" in desc or "比率" in desc or "汇率" in desc or "每股收益" in desc:
        scale = int(dtype.split(",")[1].rstrip(")")) if dtype.startswith("DECIMAL") else 2
        return amount(Decimal("7.1") if "汇率" in desc else Decimal("0.5"), scale)
    if (
        dtype.startswith("DECIMAL")
        or re.search(r"[A-Z]\d{2}[A-Z]?J\d{2}$", raw)
        or any(
            x in desc
            for x in [
                "金额",
                "余额",
                "额度",
                "数额",
                "资本",
                "收入",
                "费用",
                "支出",
                "利润",
                "资产",
                "债务",
                "成本",
                "税金",
                "货币资金",
            ]
        )
    ):
        number = dec(s.tag["cert_capt_amt"]) / (100 + stable(table, raw) % 100)
        if "逾期" in desc or "不良" in desc or "欠息" in desc:
            number = Decimal(0) if s.index % 7 else min(number, s.balance() / 20)
        if "余额" in desc:
            number = s.balance() / 10
        if "额度" in desc:
            number = s.limit() / 10
        scale = int(dtype.split(",")[1].rstrip(")")) if dtype.startswith("DECIMAL") else 2
        return amount(number, scale)
    if "编号" in desc or "号码" in desc or raw.endswith(("ID", "NO", "NUM")):
        length = int(re.search(r"\((\d+)\)", dtype)[1]) if "(" in dtype else 50
        text = f"M{s.index:06d}{ordinal:02d}{stable(raw) % 1000:03d}"
        return text if len(text) <= length else f"{s.index:04d}{ordinal:02d}"[-length:]
    if "标志" in desc or "是否" in desc:
        return "0" if s.index % 3 else "1"
    if any(
        x in desc
        for x in [
            "类型",
            "种类",
            "分类",
            "方式",
            "状态",
            "代码",
            "等级",
            "性别",
            "学历",
            "学位",
            "国籍",
            "职位",
            "频率",
            "性质",
            "行业",
            "原因代码",
        ]
    ):
        length = int(re.search(r"\((\d+)\)", dtype)[1]) if "(" in dtype else 50
        return "1" if length == 1 else "01" if length == 2 else "M01"
    if "法院" in desc:
        return "模拟区人民法院"
    if "机构" in desc or "机关" in desc:
        return s.branch
    if "企业" in desc or "公司" in desc:
        return f"模拟关联企业{s.index:04d}有限公司"
    if "名称" in desc:
        return "模拟" + desc.replace("名称", "")
    length = int(re.search(r"\((\d+)\)", dtype)[1]) if "(" in dtype else 50
    if length <= 5:
        return "1"
    return f"模拟{desc}记录"[:length]


def serialize_value(value, field: dict) -> str:
    if value is None:
        return ""
    dtype = field["type"].upper()
    if dtype.startswith("DECIMAL"):
        return amount(value, int(dtype.split(",")[1].rstrip(")")))
    return str(value)


def make_row(table: dict, s: Subject, overrides: dict | None = None, ordinal: int = 1) -> dict:
    overrides = overrides or {}
    result = {}
    for field in table["columns"]:
        raw, name = field["source_key"], field["name"]
        value = overrides.get(raw, overrides.get(name))
        if raw not in overrides and name not in overrides:
            value = semantic_value(field, s, table["sheet"], ordinal)
        result[name] = serialize_value(value, field)
    return result


def nullify_credit(row: dict, table: dict, s: Subject, protected: set[str], position: int) -> None:
    if position < 3:
        return
    for f in table["columns"]:
        if f["mock_required"] or f["source_key"] in protected or f["name"] in protected:
            continue
        desc, raw = f["source_description"], f["source_key"]
        if table["sheet"].startswith("PBCEC_EG"):
            should_null = (
                "J" in raw and stable(s.seed, s.index, table["sheet"], "financial-block") % 100 < 20
            )
        elif "关注" in desc or "不良" in desc:
            should_null = stable(s.seed, s.index, "credit-classification") % 100 < 72
        elif "机构数" in desc and "当前" in desc:
            should_null = stable(s.seed, s.index, "institution-count") % 100 < 94
        else:
            should_null = stable(s.seed, s.index, table["sheet"], raw) % 100 < 20
        if should_null:
            row[f["name"]] = ""

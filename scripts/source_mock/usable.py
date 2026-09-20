"""Semantic repair of the five linked fixtures, with field-level test codebooks.

Run after source_mock.realistic. It reads one immutable package and writes a new
package plus the two final baseline CSVs. It never connects to a database.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from .common import ROOT, CsvSink, amount, aux_fields, day, dec, read_csv, stable, write_json
from .realistic import ID_WEIGHTS, credit_code, person_name


def identity(area, index):
    area = "320506" if area == "320571" else area
    body = f"{area}{1970 + index % 27:04d}{1 + index % 12:02d}{1 + index % 28:02d}{100 + index % 800:03d}"
    return body + "10X98765432"[sum(int(c) * w for c, w in zip(body, ID_WEIGHTS, strict=True)) % 11]


def m(value):
    return amount(value, 2)


# These dictionaries are explicitly local fixture conventions, not claims about
# the bank's unpublished or PBOC production code sets.
CODE_MEANINGS = {
    "性别": {"1": "男", "2": "女"},
    "学历": {"10": "研究生", "20": "本科", "30": "大专", "40": "高中"},
    "学位": {"0": "无学位", "1": "学士", "2": "硕士"},
    "就业状况": {"01": "在职"},
    "婚姻状况": {"20": "已婚"},
    "居住状况": {"01": "自有住房", "02": "租赁住房"},
    "单位性质": {"03": "民营企业"},
    "职业": {"1": "企业负责人"},
    "职务": {"1": "负责人"},
    "职位": {"1": "执行董事或经理"},
    "职称": {"0": "无职称", "2": "中级"},
    "分数说明": {"01": "按时还款", "02": "信用历史较短", "03": "近期查询较多"},
    "业务大类": {"1": "贷款", "2": "信用卡"},
    "借款人身份类别": {"1": "自然人", "2": "企业"},
    "主借款人身份类别": {"1": "自然人", "2": "企业"},
    "相关还款责任类型": {"1": "保证人"},
    "相关还款责任人类型": {"1": "保证人"},
    "责任类型": {"1": "保证人"},
    "后付费业务类型": {"1": "电信"},
    "后付费账户类型": {"01": "电信后付费"},
    "公共信息类型": {"1": "欠税"},
    "授信额度用途": {"01": "个人消费"},
    "授信协议状态": {"1": "有效", "2": "终止"},
    "标注及声明类型": {"1": "异议处理说明"},
    "账户类型": {
        "01": "非循环贷款",
        "02": "循环额度下分账户",
        "03": "循环贷款",
        "04": "贷记卡",
        "05": "准贷记卡",
    },
    "借贷账户类型": {"01": "流动资金贷款"},
    "业务种类": {"01": "一般贷款", "02": "个人消费贷款"},
    "业务种类细分": {"01": "流动资金贷款"},
    "还款方式": {"01": "按月等额本金"},
    "还款频率": {"01": "月"},
    "担保方式": {"1": "保证"},
    "贷款发放形式": {"1": "正常发放"},
    "发放形式": {"1": "正常发放"},
    "共同借款标志": {"0": "否", "1": "是"},
    "共同借款标识": {"0": "否", "1": "是"},
    "共同债务标识": {"0": "否", "1": "是"},
    "债权转移时的还款状态": {"N": "正常"},
    "账户状态": {"1": "正常", "3": "结清"},
    "账户活动状态": {"1": "未结清", "2": "已结清"},
    "五级分类": {"1": "正常", "2": "关注", "3": "次级", "4": "可疑", "5": "损失"},
    "资产质量分类": {"1": "正常", "2": "关注", "3": "不良"},
    "还款状态": {"N": "正常", "1": "逾期1期", "C": "结清"},
    "特殊交易类型": {"01": "提前还款"},
    "特殊事件类型": {"01": "联系方式变更"},
    "交易类型": {"01": "提前还款"},
    "当前缴费状态": {"1": "正常", "2": "欠费"},
    "缴费状态": {"1": "正常", "2": "欠费"},
    "结案方式": {"1": "判决", "2": "调解", "3": "执行完毕", "0": "尚未结案"},
    "人员类别": {"1": "历史救助对象"},
    "等级": {"2": "中级"},
    "对象类型": {"1": "报告基本信息"},
    "是否包含授信限额": {"0": "否", "1": "是"},
    "经济类型": {"173": "私营有限责任公司", "174": "私营股份有限公司"},
    "组织机构类型": {"01": "企业", "02": "事业单位"},
    "企业规模": {"1": "大型", "2": "中型", "3": "小型", "4": "微型"},
    "存续状态": {"1": "存续", "2": "注销", "3": "吊销"},
    "出资人类型": {"01": "自然人"},
    "出资人身份类别": {"1": "自然人"},
    "上级机构类型": {"1": "企业集团"},
    "实际控制人身份类型": {"1": "自然人"},
    "借款期限": {"01": "一年及以内", "02": "一年以上"},
    "借贷业务种类大类": {"01": "贷款"},
    "借贷业务种类细分": {"01": "流动资金贷款"},
    "其他还款保证方式": {"0": "无"},
    "最近一次还款形式": {"01": "正常还款", "02": "提前还款"},
    "欠息类型": {"1": "表内欠息"},
    "担保账户类型": {"01": "融资性担保"},
    "担保交易业务种类细分": {"1": "贷款保证", "01": "贷款保证"},
    "反担保方式": {"1": "保证"},
    "代偿（垫款）标志": {"0": "否"},
    "垫款标志": {"0": "否"},
    "授信额度类型": {"01": "非循环", "02": "循环"},
    "额度循环标志": {"0": "非循环", "1": "循环"},
    "诉讼地位": {"1": "被告"},
    "审判程序": {"1": "一审"},
    "监管级别": {"1": "一般监管"},
    "所属名录": {"1": "融资企业名录"},
    "融资控制类型": {"1": "正常融资"},
    "报表类型": {"01": "年度报表"},
    "报表类型细分": {"1": "企业本部"},
    "评级结果": {"AA": "AA级", "A+": "A级上调一级", "A": "A级", "BB": "BB级"},
}
TXN_KINDS = {
    "SETTLE_IN": ("TRF_IN", "TRANSFER", "销售货款收款"),
    "SETTLE_OUT": ("TRF_OUT", "TRANSFER", "采购货款支付"),
    "PAYROLL": ("PAYROLL", "PAYROLL", "代发工资"),
    "FUNDING": ("FUND_IN", "TRANSFER", "股东往来款划入"),
    "LOAN_DRAW": ("LN_DRAW", "LOAN", "贷款发放"),
    "LOAN_REPAY": ("LN_REPAY", "LOAN", "贷款还款"),
    "REV_ORIGINAL": ("TRF_OUT", "TRANSFER", "转账支出"),
    "REVERSAL": ("REVERSAL", "CORRECT", "转账冲正"),
    "CASH_SWEEP": ("SWEEP", "TRANSFER", "账户资金归集"),
    "OPEN_DEPOSIT": ("OPEN_DEP", "DEPOSIT", "开户存入"),
    "TERM_DEPOSIT": ("TERM_IN", "DEPOSIT", "定期存款存入"),
    "FX_IN": ("FX_IN", "TRANSFER", "外币货款收款"),
    "FX_OUT": ("FX_OUT", "TRANSFER", "外币货款支付"),
}


def codebook(field, table):
    raw, desc = field["source_key"], field["source_description"]
    size = int(re.search(r"\d+", field["type"])[0]) if "(" in field["type"] else 999
    if raw == "PC02AD01":
        return CODE_MEANINGS["账户类型"]
    if "币种" in desc:
        return {"CNY": "人民币", "USD": "美元"}
    if desc == "国籍":
        return {"CHN": "中国"}
    if "机构类型" in desc and desc not in {"组织机构类型", "上级机构类型"}:
        return {"01": "商业银行"}
    if desc == "业务管理机构" and size == 2:
        return {"01": "商业银行"}
    if desc in {"查询机构代码", "上一次查询机构代码", "业务管理机构代码"}:
        return {"0001": "浦江银行（本测试我行）", "0002": "江海银行（本测试他行）"}
    if "证件类型" in desc or "身份标识类型" in desc:
        return {"01": "居民身份证", "10": "统一社会信用代码"}
    if desc in {"查询原因", "查询原因代码", "上一次查询原因"}:
        return {"01": "贷款审批", "02": "信用卡审批", "03": "本人查询", "04": "贷后管理"}
    if desc == "业务类型":
        return {"1": "贷款", "01": "贷款", "02": "电信", "03": "供水"}
    if re.search(r"D\d{2}$", raw) and desc not in {"行业", "所属行业", "机构所在地", "对象标识"}:
        if desc not in CODE_MEANINGS:
            raise ValueError(f"No semantic codebook: {table}.{raw}: {desc}")
        return {k: v for k, v in CODE_MEANINGS[desc].items() if len(k) <= size}
    return None


def put(row, overrides, preserve_missing=True):
    for key, value in overrides.items():
        if key in row and (not preserve_missing or row[key] != ""):
            row[key] = str(value)


def personal_facts(tag, i):
    principal = Decimal(50000 + i % 40 * 10000)
    closed = i % 10 == 0
    balance = Decimal(0) if closed else (principal * Decimal(".6"))
    overdue = not closed and i % 7 == 0
    return dict(
        principal=principal,
        limit=principal * Decimal("1.2"),
        balance=balance,
        used=balance,
        paid=principal / 20,
        overdue=overdue,
        arrears=principal / 20 if overdue else Decimal(0),
        closed=closed,
        product=f"{1 + i % 5:02d}",
        account=f"{i:05d}",
        stage_limit=principal / 2,
        stage_used=Decimal(0) if closed else principal / 5,
    )


def fix_credit(table, row, tag, person, idx):
    name = table["sheet"]
    asof = day(tag["dt"])
    pf = personal_facts(tag, idx)
    area = tag["cert_district"]
    for f in table["columns"]:
        key, raw, desc = f["name"], f["source_key"], f["source_description"]
        if row[key] == "":
            continue
        codes = codebook(f, name)
        if codes:
            if "币种" in desc:
                value = "CNY"
            elif desc == "国籍":
                value = "CHN"
            elif desc in {"查询机构代码", "上一次查询机构代码", "业务管理机构代码"}:
                value = "0002" if row[key] == "M002" else "0001"
            elif "身份标识类型" in desc or "证件类型" in desc:
                value = "10" if raw in {"EA01CD01", "EC040D02"} else "01"
            elif desc == "经济类型":
                value = "174" if tag.get("ipo_ind") == "1" else "173"
            elif desc == "组织机构类型":
                value = "02" if person["entity_type"] == "nonprofit" else "01"
            elif desc == "企业规模":
                value = {"大型": "1", "中型": "2", "小型": "3", "微型": "4"}[
                    tag["four_commis_corp_scale"]
                ]
            elif desc == "存续状态":
                value = {"存续": "1", "注销": "2", "吊销": "3"}.get(tag["survival_status"], "1")
            elif desc == "性别":
                value = "1" if int(person["person_certificate"][-2]) % 2 else "2"
            elif desc == "学历":
                value = ["10", "20", "30", "40"][idx % 4]
            elif desc == "学位":
                value = ["2", "1", "0", "0"][idx % 4]
            elif desc == "居住状况":
                value = "01" if idx % 3 else "02"
            elif desc == "分数说明":
                value = ["01", "02", "03"][idx % 3]
            elif desc == "评级结果":
                value = ["AA", "A+", "A", "BB"][idx % 4]
            elif desc == "账户类型":
                value = pf["product"] if name.startswith("PBCPC_PD01") else "01"
            elif desc in {"共同借款标志", "共同借款标识", "共同债务标识"}:
                value = "0"
            elif desc == "授信额度类型":
                value = "02" if row.get("ED060D03") == "1" else "01"
            elif desc in {"额度循环标志", "是否包含授信限额"}:
                value = row[key] if row[key] in codes else next(iter(codes))
            elif desc == "业务类型":
                value = (
                    "03"
                    if name == "PBCEC_EE01AB_UTILITIESFEES"
                    else "1"
                    if "1" in codes and "VARCHAR(1)" == f["type"].upper()
                    else "01"
                )
            else:
                value = next(iter(codes))
            row[key] = value
        elif desc in {"行业", "所属行业"}:
            row[key] = tag["industry_cd"] if desc == "行业" else tag["industry_mid_cd"]
        elif desc in {"机构所在地", "参缴地", "所在地"} and f["type"].upper() == "VARCHAR(6)":
            row[key] = area
        elif re.search(r"S\d{2}$", raw) and not re.fullmatch(r"\d+", row[key]):
            row[key] = "1"
        elif raw == "PG010D02":
            row[key] = "001"
        elif raw == "EI010I01":
            row[key] = "000001"
        elif raw == "PB020I01":
            row[key] = identity(area, idx + 3001)
        elif raw == "EC040I01":
            row[key] = credit_code(area, idx + 20000)
        elif raw == "PA01DQ02":
            row[key] = f"139{10000000 + idx:08d}"
        elif raw == "PC010Q01":
            row[key] = str(550 + idx % 300)
        elif raw == "PC010Q02":
            row[key] = str(30 + idx % 70)
        elif raw == "PB040R01":
            row[key] = tag["found_dt"][:4]
        elif desc == "许可类型":
            row[key] = "经营许可"
        elif desc == "认证类型":
            row[key] = "质量管理体系认证"
        elif desc == "资质类型":
            row[key] = "科技型中小企业认定"
        elif desc == "专利有效期":
            row[key] = "10"
        elif raw == "PB01AQ01":
            row[key] = f"contact{idx}@example.com"
        elif desc == "案件状态":
            row[key] = "执行中"
        elif row[key] == "M01":
            raise ValueError(f"Unresolved {name}.{key}")
    # Scalar coherent scenarios; retain sampled missing values unless the field
    # becomes structurally inapplicable (closure dates on an open account etc.).
    p, b, limit, paid = pf["principal"], pf["balance"], pf["limit"], pf["paid"]
    normal = "C" if pf["closed"] else "1" if pf["overdue"] else "N"
    grade = "2" if pf["overdue"] else "1"
    if name == "PBCPC_PD02AZ_FACILITYAGTSUM":
        put(
            row,
            {
                "PD02AJ01": m(limit),
                "PD02AJ03": m(limit),
                "PD02AJ04": m(b),
                "PD02AD04": "2" if pf["closed"] else "1",
            },
        )
    if name == "PBCPC_PD01ABC_PERFORMANC":
        put(
            row,
            {
                "PD01AJ01": m(p),
                "PD01AJ02": m(limit),
                "PD01AJ03": m(limit),
                "PD01BJ01": m(b),
                "PD01BJ02": m(paid),
                "PD01BD01": "3" if pf["closed"] else "1",
                "PD01BD03": grade,
                "PD01CD02": grade,
                "PD01BD04": normal,
                "PD01CD01": "3" if pf["closed"] else "1",
                "PD01CJ01": m(b),
                "PD01CJ02": m(b),
                "PD01CJ03": m(pf["stage_used"]),
                "PD01CJ04": m(paid),
                "PD01CJ05": m(0 if pf["overdue"] else paid),
                "PD01CS02": int(pf["overdue"]),
                "PD01CJ06": m(pf["arrears"]),
                "PD01CJ07": m(pf["arrears"]),
                "PD01CJ08": "0.00",
                "PD01CJ09": "0.00",
                "PD01CJ10": "0.00",
                "PD01CJ11": "0.00",
                "PD01CJ12": m(b),
                "PD01CJ13": m(b),
                "PD01CJ14": m(p),
                "PD01CJ15": m(p),
                "PD01AS01": "24",
                "PD01CS01": "0" if pf["closed"] else "12",
            },
        )
        if not pf["closed"]:
            row["PD01BR01"] = ""
        if idx % 17:
            row["PD01BR04"] = ""
    if name in {"PBCPC_PD01D_LATEST24MONTH", "PBCPC_PD01E_LATEST5YEAR"}:
        put(row, {"PD01DD01": normal, "PD01ED01": normal, "PD01EJ01": m(pf["arrears"])})
    if name == "PBCPC_PD01H_LARGESTAGE":
        put(row, {"PD01HJ01": m(pf["stage_limit"]), "PD01HJ02": m(pf["stage_used"])})
    if name == "PBCPC_PD01F_SPECIALTRADE":
        put(row, {"PD01FJ01": m(paid), "PD01FS02": "0"})
    if name == "PBCPC_PC02A_CREDITSUMMARYCUE":
        put(
            row,
            {
                "PC02AS01": "1",
                "PC02AS02": "1",
                "PC02AD01": pf["product"],
                "PC02AD02": "2" if pf["product"] in {"04", "05"} else "1",
                "PC02AS03": "1",
                "PC02AR01": (asof - timedelta(days=360)).strftime("%Y-%m"),
            },
        )
    for letter, product in zip("EFGHI", ["01", "02", "03", "04", "05"], strict=True):
        if name.startswith("PBCPC_PC02" + letter + "_"):
            pre = "PC02" + letter
            applies = pf["product"] == product
            values = {
                pre + "S01": int(applies),
                pre + "S02": int(applies),
                pre + "J01": m(limit if applies else 0),
            }
            if letter in "EFG":
                values.update(
                    {pre + "J02": m(b if applies else 0), pre + "J03": m(paid if applies else 0)}
                )
            else:
                values.update(
                    {
                        pre + "J02": m(limit if applies else 0),
                        pre + "J03": m(limit if applies else 0),
                        pre + "J04": m(b if applies else 0),
                        pre + "J05": m(b if applies else 0),
                    }
                )
            put(row, values)
    if name == "PBCPC_PC02D_OVERDUESUM":
        put(
            row,
            {
                "PC02DS01": int(pf["overdue"]),
                "PC02DS02": int(pf["overdue"]),
                "PC02DS03": int(pf["overdue"]),
                "PC02DS04": int(pf["overdue"]),
                "PC02DJ01": m(pf["arrears"]),
            },
        )
    if name in {"PBCPC_PC02B_ASSUREERREPAYSUM", "PBCPC_PC02C_FELLBACKDEBTSUM"}:
        for k in row:
            if re.match(r"PC02[BC][JS]", k) and row[k] != "":
                row[k] = "0.00" if "J" in k else "0"
    if name in {"PBCPC_PD03AZ_GUARANTEE", "PBCPC_PC02K_GUARANTEESUM"}:
        put(
            row,
            {
                "PD03AJ01": m(limit),
                "PD03AJ02": m(b),
                "PD03AS01": "0",
                "PD03AD07": "N",
                "PC02KJ01": m(limit),
                "PC02KJ02": m(b),
            },
        )
    tel_arrears = Decimal(99 + idx % 200) if idx % 9 == 0 else Decimal(0)
    if name == "PBCPC_PE01AZ_TELPAYMENT":
        put(
            row,
            {
                "PE01AJ01": m(tel_arrears),
                "PE01AD03": "2" if tel_arrears else "1",
                "PE01AR01": (asof - timedelta(days=800)).isoformat(),
                "PE01AQ02": "N" * 23 + ("1" if tel_arrears else "N"),
            },
        )
    if name == "PBCPC_PC03_TELPAYMENTSUM":
        put(row, {"PC030S01": "1", "PC030S02": int(bool(tel_arrears)), "PC030J01": m(tel_arrears)})
    if name == "PBCPC_PC04_PUBLICINFOSUM":
        put(
            row,
            {
                "PC040S01": "1",
                "PC040D01": "1",
                "PC040S02": "1",
                "PC040J01": m(1500 + idx * 10 if idx % 7 == 0 else 0),
            },
        )
    if name == "PBCPC_PC05AB_QUERYRECOREDSUM":
        put(
            row,
            {
                k: ("1" if k in {"PC05BS01", "PC05BS03"} else "0")
                for k in row
                if k.startswith("PC05BS")
            },
        )
    if name == "PBCPC_PF05AZ_ACCFUND":
        put(
            row,
            {
                "PF05AQ02": "0.07",
                "PF05AQ03": "0.07",
                "PF05AJ01": m((6000 + idx % 25 * 500) * Decimal(".14")),
            },
        )
    if name == "PBCPC_PF06AZ_SALVATION":
        # Historical relief, before the subject's later business activity.
        historical = day(tag["found_dt"]) - timedelta(days=730)
        put(
            row,
            {
                "PF06AQ03": m(900 + idx % 10 * 50),
                "PF06AQ02": "无固定工作单位",
                "PF06AR01": historical.isoformat(),
                "PF06AR02": (historical + timedelta(days=30)).isoformat(),
            },
        )
    if name == "PBCPC_PF07AZ_COMPETENCE":
        put(row, {"PF07AR01": (asof - timedelta(days=1100)).strftime("%Y-%m")})
        if idx % 10:
            row["PF07AR03"] = ""
    if name == "PBCEC_EF10_PATENTINFO":
        put(row, {"EF100R01": f"2024-{1 + idx % 12:02d}-15"})
    if name in {"PBCEC_EE01AB_UTILITIESFEES", "PBCEC_EF05AB_ACCFUND"}:
        pre = "EE01" if "EE01" in name else "EF05"
        payable = (
            Decimal(300 + idx * 11)
            if pre == "EE01"
            else Decimal(tag["mec_num"]) * (6000 + idx % 10 * 500) * Decimal(".14")
        )
        due = payable / 2 if idx % 13 == 0 else Decimal(0)
        put(
            row,
            {
                pre + "AJ01": m(
                    due if pre == "EE01" else Decimal(tag["mec_num"]) * (6000 + idx % 10 * 500)
                ),
                pre + "AJ02": m(due),
                pre + "BJ01": m(payable),
                pre + "BJ02": m(payable - due),
                pre + "BJ03": m(due),
                pre + "AD01": ("03" if pre == "EE01" else "2" if due else "1"),
                pre + "AD02": "2" if due else "1",
                pre + "BD01": "2" if due else "1",
                pre + "AS01": tag["mec_num"],
            },
        )
    if name == "PBCEC_ED01B_REPAYMENT":
        arrears = min(dec(row.get("ED01BJ04")), dec(row.get("ED01BJ01")))
        put(
            row,
            {
                "ED01BJ04": m(arrears),
                "ED01BJ05": m(arrears),
                "ED01BS02": int(bool(arrears)),
                "ED01BD01": "2" if arrears else "1",
            },
        )
    if name == "PBCEC_EC02_CONTRIBUTIVE" and tag.get("ipo_ind") == "1":
        put(row, {"EC020Q02": "0.55"})
    if name == "PBCEC_ED01C_SPECIALTRADE":
        put(row, {"ED01CS02": "0"})
    if name == "PBCEC_ED03_DEBITINTEREST":
        put(row, {"ED030J01": m(500 + idx * 3 if idx % 7 == 0 else 0)})
    if name == "PBCEC_ED04AB_GUARANTEEDETAIL":
        face = Decimal(50000 + idx * 1000)
        bal = Decimal(0) if idx % 10 == 0 else face * Decimal(".6")
        put(
            row,
            {
                "ED04AJ01": m(face),
                "ED04BJ01": m(bal),
                "ED04AQ01": "0.20",
                "ED04BJ02": m(bal * Decimal(".8")),
                "ED04BD01": "2" if not bal else "1",
            },
        )
        if bal:
            row["ED04BR02"] = ""
    if name == "PBCEC_ED05_GUARANTEESUBORG":
        bal = dec(row.get("ED050J01"))
        put(
            row,
            {
                "ED050S01": int(bal > 0),
                "ED050J02": "0.00",
                "ED050J03": "0.00",
                "ED050J04": "0.00",
                "ED050J05": m(bal),
            },
        )
    if name == "PBCEC_ED07_REPAYMENTLOANACCT":
        principal = Decimal(50000 + idx * 1000)
        balance = principal * Decimal(".5")
        arrears = Decimal(1500 + idx * 10) if idx % 7 == 0 else Decimal(0)
        put(
            row,
            {
                "ED070J01": m(principal),
                "ED070J02": m(balance),
                "ED070J03": m(arrears),
                "ED070J04": m(arrears),
                "ED070J05": m(principal),
                "ED070J06": m(principal),
                "ED070S01": int(bool(arrears)),
                "ED070D08": "2" if arrears else "1",
                "ED070D09": "1" if arrears else "N",
            },
        )
    if name in {"PBCEC_ED08_REPAYMENTDISCOUNT", "PBCEC_ED09_GUARANTEE"}:
        pre = "ED080" if "ED08" in name else "ED090"
        face = Decimal(80000 + idx * 500)
        bal = face * Decimal(".4")
        put(
            row,
            {
                pre + "J01": m(face),
                pre + "J02": m(bal),
                pre + "J03": "0.00",
                pre + "J04": "0.00",
                pre + "J05": m(face),
                "ED100J03": m(face),
            },
        )
    # Monetary text and numeric amount must describe the same case or penalty.
    target = Decimal(20000 + stable(idx, "execution") % 780000)
    ratio = [Decimal(0), Decimal(".4"), Decimal(1)][idx % 3]
    for k in ("PF02AJ01", "PF03AJ01", "EF020J01", "EF030J01"):
        put(row, {k: m(target)})
    put(
        row,
        {
            "PF03AJ02": m(target * ratio),
            "EF030J02": m(target * ratio),
            "PF03AQ03": "执行完毕" if ratio == 1 else "执行中",
            "EF030Q04": "执行完毕" if ratio == 1 else "执行中",
            "PF03AD01": "3" if ratio == 1 else "0",
            "EF030D01": "3" if ratio == 1 else "0",
        },
    )
    if "PF03AR02" in row and ratio != 1:
        row["PF03AR02"] = ""
    for f in table["columns"]:
        k, desc = f["name"], f["source_description"]
        if row[k] == "":
            continue
        if desc in {"诉讼标的", "申请执行标的"}:
            row[k] = f"货款人民币{m(target)}元"
        elif desc == "已执行标的":
            row[k] = f"已收回人民币{m(target * ratio)}元"
        elif desc == "判决/调解结果":
            row[k] = f"被告支付货款{m(target)}元及相应利息。"
        elif desc == "处罚金额":
            row[k] = m(1000 + 500 * (idx % 25))
        elif desc == "立案日期":
            row[k] = (
                asof - timedelta(days=210 + idx % 55 + (60 if "CIVIL" in name else 0))
            ).isoformat()
        elif desc == "判决/调解生效日期":
            row[k] = (asof - timedelta(days=240 + idx % 55)).isoformat()
        elif desc == "案号" and "CIVIL" in name:
            row[k] = row[k].replace("执", "民初")
    return row


def refresh(source: Path, baseline: Path, output: Path, baseline_output: Path):
    assert source.resolve() != output.resolve() and baseline.resolve() != baseline_output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline_output.mkdir(parents=True, exist_ok=True)
    schema = json.loads((source / "schema.json").read_text())
    manifest = json.loads((source / "manifest.json").read_text())
    contract = json.loads((ROOT / "configs/bank/schema.json").read_text())
    people = {r["cust_ind"]: r for r in read_csv(source / "reference/customer_identity.csv")}
    original_tags = read_csv(baseline / "CCM_C_CUST_FLAG_INFO.csv")
    tags = {r["cust_ind"]: r for r in read_csv(source / "expected/customer_tags.csv")}
    old_tags = {r["cust_ind"]: r for r in original_tags}
    events = read_csv(baseline / "E_CRM_C_CUST_TOUR_EVT_SUM.csv")
    links = read_csv(source / "reference/source_row_links.csv")
    owner = {(r["table"], int(r["csv_row"]) - 2): r["cust_ind"] for r in links}
    data = {
        t["sheet"]: read_csv(source / t["filename"])
        for t in schema["tables"]
        if t["domain"] != "transactions"
    }
    # Auxiliary entities only exercise discontinued-company/nonprofit formats.
    alltags = dict(tags)
    for n, p in enumerate(people.values()):
        if p["cust_ind"] not in alltags:
            proto = dict(original_tags[(n - len(tags)) % len(tags)])
            proto.update(p)
            proto.update(legal_rep_nm=p["person_name"], act_ctrl_psn_nm=p["person_name"])
            basic = next(
                (
                    r
                    for r in data["T_SAIC_BASIC"]
                    if r["unify_credit_code"] == p["unify_credit_code"]
                ),
                {},
            )
            proto.update({k: v for k, v in basic.items() if k in proto})
            index = int(p["cust_ind"].split("_")[-1])
            if index in {9001, 9002}:
                proto["survival_status"] = "注销" if index == 9001 else "吊销"
            alltags[p["cust_ind"]] = proto
    names = {
        t["cust_nm"]: t["cust_nm"].replace("有限公司", "股份有限公司")
        for t in tags.values()
        if t["ipo_ind"] == "1"
    }
    name_pattern = re.compile("|".join(map(re.escape, names))) if names else None

    def rename(v):
        return name_pattern.sub(lambda match: names[match[0]], v) if name_pattern else v

    for person in people.values():
        person["cust_nm"] = rename(person["cust_nm"])
    for tag in alltags.values():
        tag["cust_nm"] = rename(tag["cust_nm"])
        tag["hold_type"] = "私人控股"
        tag["corp_econ_type"] = "私营股份有限公司" if tag["ipo_ind"] == "1" else "私营有限责任公司"
        tag["four_commis_corp_scale"] = {
            "MOCK_LARGE": "大型",
            "MOCK_MEDIUM": "中型",
            "MOCK_SMALL": "小型",
            "MOCK_MICRO": "微型",
        }.get(tag["four_commis_corp_scale"], tag["four_commis_corp_scale"])
        tag["latest_fnc_round"] = tag["latest_fnc_round"].removeprefix("MOCK_")
        if tag["SH_SCI_RNG"].startswith("MOCK_RANGE_"):
            tag["SH_SCI_RNG"] = {
                "1": "0–19",
                "2": "20–39",
                "3": "40–59",
                "4": "60–79",
                "5": "80–100",
            }[tag["SH_SCI_RNG"][-1]]
        tag["CUST_STATUS"] = "正常"
        tag["mediun_cust_ind"] = str(int(tag["four_commis_corp_scale"] == "中型"))
    grouped_events = defaultdict(list)
    accounts = read_csv(source / "reference/accounts.csv")
    by_customer_accounts = defaultdict(list)
    for a in accounts:
        by_customer_accounts[a["cust_ind"]].append(a)
    # Preserve exact JSON numbers while replacing only JSON string literals.
    for e in events:
        tag = tags[e["CUST_ID"]]
        i = int(e["CUST_ID"].split("_")[-1])

        def edit_string(match, i=i):
            v = rename(json.loads(match[0]))
            repl = {
                "MOCK_ACTIVE": "生效",
                "MOCK_MAIN_BOARD": "主板",
                "MOCK_LEVEL_1": "一般",
                "MOCK_LEVEL_2": "关注",
                "MOCK_LEVEL_3": "重要",
                "MOCK_A": "A",
                "MOCK_B": "B",
                "MOCK_C": "C",
                "MOCK_PRODUCT_BOND": "WM0001",
                "MOCK_PRODUCT_FUND": "WM0002",
                "MOCK_PRODUCT_MIX": "WM0003",
            }
            if v.startswith("MOCK_STOCK_"):
                v = f"{600000 + i:06d}"
            else:
                v = repl.get(v, v)
            return json.dumps(v, ensure_ascii=False)

        e["PROPERTIES"] = re.sub(r'"(?:\\.|[^"\\])*"', edit_string, e["PROPERTIES"])
        if e["EVT_TYPE"] == "YWJC0009-1":
            p = json.loads(e["PROPERTIES"])
            aa = [
                a
                for a in by_customer_accounts[e["CUST_ID"]]
                if a["prod_cd"] == "TERM" and a["open_acct_dt"] == e["OCCUR_DT"]
            ]
            if aa:
                e["PROPERTIES"] = e["PROPERTIES"].replace(
                    json.dumps(p["账户号"]), json.dumps(aa[0]["accno"])
                )
        if e["EVT_TYPE"] == "YWJC0002":
            e["PROPERTIES"] = e["PROPERTIES"].replace('"账户类型":""', '"账户类型":"基本存款账户"')
        grouped_events[e["CUST_ID"]].append(e)
    for cid, tag in tags.items():
        evs = grouped_events[cid]
        for code, key in [("YWJC0011", "snd_bl_cust_sign_ind"), ("YWJC0016", "pyrl_sign_dt")]:
            tag[key] = next((e["OCCUR_DT"] for e in evs if e["EVT_TYPE"] == code), "")
        tag["SETL_ACTV_ACCT_IND"] = str(int(int(tag["MANU_SETTLE_AMT_MON_NUM"]) >= 6))
        tag["swap_corp_cust_ind"] = str(
            int(any(a["ccycd"] != "CNY" for a in by_customer_accounts[cid]))
        )
        for k in [
            "corp_ebnkg_trans_y_ind",
            "corp_mb_trans_y_ind",
            "CORP_EBNKG_M_IND",
            "CORP_MB_M_IND",
        ]:
            tag[k] = "0"
        # Actual loan daily averages from disbursement/repayment events.
        start = date(day(tag["dt"]).year, 1, 1)
        end = day(tag["dt"])
        changes = defaultdict(Decimal)
        opening = Decimal(0)
        for e in evs:
            if e["EVT_TYPE"] not in {"YWJC0005", "YWJC0006-1", "YWJC0006-2"}:
                continue
            p = json.loads(e["PROPERTIES"], parse_float=Decimal)
            d = day(e["OCCUR_DT"])
            delta = dec(p.get("放款金额", 0)) - dec(p.get("还款金额", 0))
            if d < start:
                opening += delta
            else:
                changes[d] += delta
        daily = opening
        total = Decimal(0)
        for n in range((end - start).days + 1):
            daily += changes[start + timedelta(days=n)]
            total += daily
        tag["exchg_c_loan_yavg"] = amount(dec(amount(total / ((end - start).days + 1), 2)))
        for field in ["exchg_c_bal_yavg", "c_exchg_cny_12m_yaug", "exchg_cny_dmd_12m_yaug"]:
            tag[field] = amount(dec(amount(tag[field], 2)))
    # Per-source business corrections and explicit types/code dictionaries.
    for table in schema["tables"]:
        if table["domain"] == "transactions":
            continue
        name = table["sheet"]
        # The supplied dictionary marks some nullable closing dates as part of
        # a PK. Preserve that raw metadata but do not fabricate a closing date
        # for an open account to satisfy our generated uniqueness constraint.
        for f in table["columns"]:
            if (
                f["source_description"] in {"关闭日期", "结案日期"}
                and f.get("source_nullable") == "Y"
                and f["name"] in table["mock_primary_key"]
            ):
                table["mock_primary_key"].remove(f["name"])
                f.update(
                    mock_required=False,
                    condition="源字典同时标PK与可空；测试键不含此日期，未关闭/未结案时留空。",
                )
        if name == "T_SAIC_SHAREHOLDER" and "SHANAME" not in table["mock_primary_key"]:
            table["mock_primary_key"].append("SHANAME")
            for f in table["columns"]:
                if f["name"] == "SHANAME":
                    f.update(
                        mock_required=True,
                        condition="源字典主键仅定位企业；为支持同企业多股东，测试唯一键增加股东名称。",
                    )
        for n, row in enumerate(data[name]):
            cid = owner[name, n]
            tag = alltags[cid]
            p = people[cid]
            i = int(cid.split("_")[-1])
            asof = day(tag["dt"])
            row.update({k: rename(v) for k, v in row.items()})
            if table["domain"] == "credit":
                fix_credit(table, row, tag, p, i)
            else:
                if name.startswith("VW_GSGR_RYPOS"):
                    ratio = Decimal(".55") if tag.get("ipo_ind") == "1" else Decimal(1)
                    put(
                        row,
                        {
                            "REGCAP": amount(dec(tag["cert_capt_amt"]) / 10000),
                            "FUNDEDRATIO": m(ratio),
                            "SUBCONAM": amount(dec(tag["cert_capt_amt"]) / 10000 * ratio),
                        },
                    )
                if name == "VW_GSGR_PERSONCASEINFO":
                    put(
                        row,
                        {
                            "CASEREASON": "不实商业宣传",
                            "CASERESULT": "已履行处罚决定",
                            "CASEVAL": m(1000 + 500 * (i % 25)),
                        },
                    )
                if name in {"T_SAIC_FRPOSITION", "T_SAIC_FRINV", "T_SAIC_ENTINV"}:
                    status = "注销" if i % 10 == 0 else "吊销" if i % 10 == 1 else "存续"
                    row["ENTSTATUS"] = status
                    row["CANDATE"] = (
                        (asof - timedelta(days=30)).isoformat() if status == "注销" else ""
                    )
                    row["REVDATE"] = (
                        (asof - timedelta(days=60)).isoformat() if status == "吊销" else ""
                    )
                if name == "T_SAIC_STOCKPAWN":
                    put(
                        row,
                        {
                            "STK_PAWN_STATUS": "已注销",
                            "STK_PAWN_REGDATE": (asof - timedelta(days=210)).isoformat(),
                        },
                    )
                if name in {"T_SAIC_MORTGAGEREG", "T_SAIC_MORTGAGEBASIC"}:
                    put(row, {"STATUS": "已注销", "MAB_STATUS": "已注销"})
                if name == "T_SAIC_MORTGAGEALT":
                    put(row, {"MAB_ALT_DATE": (asof - timedelta(days=60)).isoformat()})
                if name == "T_SAIC_SHARESFROST" and i % 3 != 2:
                    for k in ["THAWAUTH", "THAWCOMMENT", "THAWDATE", "THAWDOCNO"]:
                        row[k] = ""
                if name == "T_SAIC_JUDICIALAIDALTER":
                    put(row, {"ASSIGNEE_LICENCE": identity(tag["cert_district"], i + 201)})
                if name == "T_SAIC_JUDICIALAID":
                    row["STATUS"] = "已解冻" if i % 3 == 2 else "冻结"
                if name == "T_SAIC_JUDICIALAIDDETAIL":
                    closed = i % 3 == 2
                    renewed = i % 5 == 0
                    row["FREEZE_FLAG"] = "已解冻" if closed else "冻结"
                    row["FREEZE_DATE"] = (asof - timedelta(days=16)).isoformat() if closed else ""
                    if renewed:
                        row.update(
                            FROFROM=(asof - timedelta(days=180)).isoformat(),
                            FROTO=(asof - timedelta(days=90)).isoformat(),
                            FPERIOD="三个月",
                            CFPERIOD="一年",
                            CFROFROM=(asof - timedelta(days=90)).isoformat(),
                            CFROTO=(asof + timedelta(days=275)).isoformat(),
                        )
                    else:
                        for k in ["CFPERIOD", "CFROFROM", "CFROTO"]:
                            row[k] = ""
                    row["EXPIRATION_DATE"] = row["FREEZE_DATE"] if closed else ""
                    row["EXPIRATION_REASON"] = "债务履行完毕，解除冻结" if closed else ""
                if name == "T_SAIC_LISTEDSHAREHOLDER":
                    # The subject owns shares in the related listed company.
                    put(
                        row,
                        {
                            "SHHOLDERCREDITCODE": tag["unify_credit_code"],
                            "SHHOLDERNAME": tag["cust_nm"],
                            "SHHOLDERREGNO": row["REGNO"],
                        },
                    )
                if name == "T_SAIC_BASIC":
                    put(
                        row,
                        {
                            "ENTTYPE": "股份有限公司" if tag["ipo_ind"] == "1" else "有限责任公司",
                            "ENTTYPECODE": "1222" if tag["ipo_ind"] == "1" else "1130",
                        },
                    )
            for k in set(row) & set(tag):
                if k in {
                    "cust_ind",
                    "cust_nm",
                    "unify_credit_code",
                    "cert_capt_amt",
                    "org_capt_amt",
                    "mec_num",
                    "found_dt",
                    "legal_rep_nm",
                    "act_ctrl_psn_nm",
                    "survival_status",
                    "busin_addr",
                    "opscope",
                    "dt",
                }:
                    row[k] = tag[k]
        for f in table["columns"]:
            if table["domain"] == "credit":
                codes = codebook(f, name)
                if codes:
                    f["test_codebook"] = codes
            if name == "T_SAIC_BASIC" and f["name"] in {"cert_capt_amt", "org_capt_amt"}:
                f["source_description_original"] = f["source_description"]
                f["source_description"] = (
                    f["source_description"]
                    .replace("(万元)", "（元；源万元已换算）")
                    .replace("（万元）", "（元；源万元已换算）")
                )
            f["test_rule"] = "依据字段含义及关联客户生成；空白为缺失或不适用。"
    # Correct liquidation ownership: completed liquidation belongs to auxiliary
    # discontinued entities, never to the 1,000 active bank customers.
    name = "T_SAIC_LIQUIDATION"
    prototype = data[name][0]
    newrows = []
    for cid in [c for c in people if c.startswith("MOCK_AUX_")][:2]:
        tag = alltags[cid]
        r = dict(prototype)
        i = int(cid.split("_")[-1])
        p = people[cid]
        for k in r:
            if k in tag:
                r[k] = tag[k]
        r.update(
            ADDR=tag["busin_addr"],
            LIGENTITY=tag["cust_nm"],
            LIGPRINCIPAL=p["person_name"],
            LIQMEN=p["person_name"] + "、" + person_name(i + 1),
            REGNO=tag["cert_district"] + f"{i:09d}",
            ORGCODES=tag["unify_credit_code"][8:17],
        )
        newrows.append(r)
    data[name] = newrows
    oldlinks = [r for r in links if r["table"] == name]
    links = [r for r in links if r["table"] != name]
    for n, cid in enumerate([c for c in people if c.startswith("MOCK_AUX_")][:2]):
        links.append(
            {
                **oldlinks[0],
                "csv_row": str(n + 2),
                "cust_ind": cid,
                "person_cust_id": people[cid]["person_cust_id"],
            }
        )
    # Shareholders of listed subjects: founder plus a separate institutional
    # holder, with contribution sums equal to registered capital.
    name = "T_SAIC_SHAREHOLDER"
    addition = []
    for n, row in enumerate(data[name]):
        cid = owner[name, n]
        tag = alltags[cid]
        if tag.get("ipo_ind") != "1":
            continue
        total = dec(tag["cert_capt_amt"]) / 10000
        row.update(FUNDEDRATIO="0.55", SUBCONAM=amount(total * Decimal(".55")))
        other = dict(row)
        i = int(cid.split("_")[-1])
        other.update(
            SHANAME=f"嘉禾产业投资{i:04d}有限公司",
            PALGORITHMID=f"ORG_INV_{i:04d}",
            INVTYPE="企业法人股东",
            FUNDEDRATIO="0.45",
            SUBCONAM=amount(total * Decimal(".45")),
        )
        row["INVAMOUNT"] = other["INVAMOUNT"] = "2"
        addition.append((cid, other))
    for cid, row in addition:
        data[name].append(row)
        links.append(
            dict(
                table=name,
                csv_row=str(len(data[name]) + 1),
                cust_ind=cid,
                report_id="",
                person_cust_id=people[cid]["person_cust_id"],
                fixture_level="core",
            )
        )
    # Reconcile summaries to actual enterprise detail rows, including true zeros.
    reconcile_credit(data, owner, alltags, people)
    for table in schema["tables"]:
        if table["domain"] == "transactions":
            continue
        sink = CsvSink(output / table["filename"], table["columns"], table["mock_primary_key"])
        from .semantic_validation import row_errors

        for row in data[table["sheet"]]:
            errors = row_errors(table, row)
            if errors:
                raise ValueError(f"{table['sheet']}: {errors}")
            sink.add(row)
        manifest["files"][table["filename"]] = sink.close()
    transaction_table = next(t for t in schema["tables"] if t["domain"] == "transactions")
    refine_transactions(source, output, transaction_table, tags, manifest)
    # Save canonical tags used by validation and delivered in Excel identically.
    for table, _label, rows in [
        ("CCM_C_CUST_FLAG_INFO", "客户标签", list(tags.values())),
        ("E_CRM_C_CUST_TOUR_EVT_SUM", "客户旅程", events),
    ]:
        c = contract["tables"][table]
        fields = [
            dict(
                name=f["name"],
                type=f["sql_type"],
                format=f.get("format"),
                mock_required=not f["mock_nullable"],
            )
            for f in c["columns"]
        ]
        sink = CsvSink(baseline_output / c["filename"], fields, c["primary_key"])
        for row in rows:
            sink.add(row)
        info = sink.close()
        if table == "CCM_C_CUST_FLAG_INFO":
            (output / "expected").mkdir(exist_ok=True)
            shutil.copy2(baseline_output / c["filename"], output / "expected/customer_tags.csv")
            manifest["files"]["expected/customer_tags.csv"] = info
    for filename in ["reference/accounts.csv"]:
        (output / filename).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / filename, output / filename)
    sink = CsvSink(
        output / "reference/customer_identity.csv", aux_fields(list(next(iter(people.values()))))
    )
    for r in people.values():
        sink.add(r)
    manifest["files"]["reference/customer_identity.csv"] = sink.close()
    sink = CsvSink(output / "reference/source_row_links.csv", aux_fields(list(links[0])))
    for r in links:
        sink.add(r)
    manifest["files"]["reference/source_row_links.csv"] = sink.close()
    changes = []
    for cid, t in tags.items():
        for k, v in t.items():
            if v != old_tags[cid][k]:
                changes.append(
                    dict(
                        cust_ind=cid,
                        field=k,
                        baseline_value=old_tags[cid][k],
                        derived_value=v,
                        reason="字段语义或关联流水、旅程事实修正",
                    )
                )
    sink = CsvSink(
        output / "expected/changed_tag_values.csv",
        aux_fields(["cust_ind", "field", "baseline_value", "derived_value", "reason"]),
    )
    for r in changes:
        sink.add(r)
    manifest["files"]["expected/changed_tag_values.csv"] = sink.close()
    for f in [
        "null-policy.json",
        "tag-provenance.json",
        "tag-schema.json",
        "realism-profiles.json",
    ]:
        if (source / f).exists():
            shutil.copy2(source / f, output / f)
    manifest.update(
        version="2.0",
        semantic_revision="2026-09-19",
        currency_average_scale=2,
        baseline_tags_sha256=manifest["files"]["expected/customer_tags.csv"]["sha256"],
    )
    manifest["domain_rows"] = {
        d: sum(v["rows"] for k, v in manifest["files"].items() if k.startswith(d + "/"))
        for d in ["credit", "business", "transactions"]
    }
    manifest["limitations"] = [
        "全部为虚构测试数据。银行专有枚举采用随字段提供的测试码表，不声称等同银行正式接口码表。",
        "覆盖源字典全部字段；征信保留随机缺失，并按业务条件增加不适用空值。财务报表不同制度为独立覆盖场景。",
        "客户标签采用本包约定的测试计算口径；规模、客群等无完整生产规则的标签为明确场景值。",
        "交易流水覆盖近12个月账户现金流；旅程包括更早事件、产品余额快照与外部资讯，不要求每条旅程对应一笔现金交易。",
    ]
    write_json(output / "schema.json", schema)
    write_json(output / "manifest.json", manifest)
    bm = json.loads((baseline / "manifest.json").read_text())
    for table, c in contract["tables"].items():
        path = baseline_output / c["filename"]
        bm["files"][table]["rows"] = len(tags) if table == "CCM_C_CUST_FLAG_INFO" else len(events)
        bm["files"][table]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    bm["semantic_revision"] = "2026-09-19"
    write_json(baseline_output / "manifest.json", bm)
    write_json(
        output / "semantic-codebooks.json",
        {
            t["sheet"]: {
                f["name"]: f["test_codebook"] for f in t["columns"] if "test_codebook" in f
            }
            for t in schema["tables"]
        },
    )
    (output / "README.md").write_text(
        "# 五表测试数据\n\n"
        + "\n".join(manifest["limitations"])
        + "\n\n客户标签与 expected/customer_tags.csv 完全一致。代码值定义见 schema.json、semantic-codebooks.json 及 Excel 说明页。\nSETL_ACTV_ACCT_IND：近12月主动结算达到6个月；年/月电子渠道标志：对应期间存在已签约渠道的成功、未冲正交易。\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "tag_changes": len(changes),
                "source_tables": len(schema["tables"]),
                "status": "staged",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def reconcile_credit(data, owner, tags, people):
    """Derive report summaries from the fixture's details, without making up debt."""
    by_report = defaultdict(lambda: defaultdict(list))
    report_customers = {p["enterprise_report_id"]: cid for cid, p in people.items()}
    for name, rows in data.items():
        if not name.startswith("PBCEC_"):
            continue
        for r in rows:
            by_report[r.get("EA01AI01", "")][name].append(r)
    for rid, tables in by_report.items():
        loans = tables.get("PBCEC_ED01A_BASICLOANACCT", [])
        repays = tables.get("PBCEC_ED01B_REPAYMENT", [])
        rp = {r["ED01AI01"]: r for r in repays}
        for loan in loans:
            r = rp[loan["ED01AI01"]]
            bal = dec(r["ED01BJ01"])
            active = bal > 0
            put(loan, {"ED01AD01": "1" if active else "2"})
            if active:
                loan["ED01AR03"] = ""
            elif not loan["ED01AR03"]:
                loan["ED01AR03"] = r["ED01BR02"]
            maturity = date.fromisoformat(loan["ED01AR02"])
            asof = day(loan["dt"])
            months = (
                max(
                    0,
                    (maturity.year - asof.year) * 12
                    + maturity.month
                    - asof.month
                    + (maturity.day > asof.day),
                )
                if active
                else 0
            )
            put(r, {"ED01BS03": months})
        total = sum((dec(r["ED01BJ01"]) for r in repays), Decimal(0))
        over = sum((dec(r["ED01BJ04"]) for r in repays), Decimal(0))
        tag = tags.get(report_customers.get(rid))
        if tag:
            tag["loan_overdue_cust_ind"] = str(
                int(
                    any(
                        loan["ED01AI02"] == "0001" and dec(rp[loan["ED01AI01"]]["ED01BJ04"]) > 0
                        for loan in loans
                    )
                )
            )
            if tag["loan_overdue_cust_ind"] == "1":
                tag["risk_cust_ind"] = "1"
        active = [loan for loan in loans if dec(rp[loan["ED01AI01"]]["ED01BJ01"]) > 0]
        closed = [loan for loan in loans if loan not in active]
        details = {
            "PBCEC_EB01A_CREDITCUE": {
                "EB01AS01": len({loan["ED01AI02"] for loan in loans}),
                "EB01AS02": len({loan["ED01AI02"] for loan in active}),
                "EB01AR01": min((loan["ED01AR01"][:4] for loan in loans), default=""),
                "EB01AJ01": m(total),
                "EB01AJ02": "0.00",
                "EB01AJ03": m(
                    sum((dec(r["ED01BJ01"]) for r in repays if r["ED01BD01"] == "2"), Decimal(0))
                ),
                "EB01AJ04": "0.00",
            },
            "PBCEC_EB01B_NONCREDITCUE": {
                f"EB01BS0{n}": len(tables.get(t, []))
                for n, t in enumerate(
                    [
                        "PBCEC_EE01AB_UTILITIESFEES",
                        "PBCEC_EF01_TAXINFO",
                        "PBCEC_EF02_CIVILJUDGEMENT",
                        "PBCEC_EF03_FORCEEXECUTION",
                        "PBCEC_EF04_ADMINPUNISHMENT",
                    ],
                    1,
                )
            },
            "PBCEC_EB02A_SUMUNSETTLED": {
                "EB02AS01": "0",
                "EB02AJ01": "0.00",
                "EB02AS02": "0",
                "EB02AJ02": "0.00",
                "EB02AJ03": m(over),
                "EB02AJ04": m(over),
                "EB02AJ05": "0.00",
                "EB02AS03": int(bool(active)),
                "EB02AS04": len(active),
                "EB02AJ06": m(total),
            },
            "PBCEC_EB02B_SUMSETTLED": {
                "EB02BS01": "0",
                "EB02BJ01": "0.00",
                "EB02BS02": "0",
                "EB02BJ02": "0.00",
                "EB02BS03": int(bool(closed)),
                "EB02BS04": len(closed),
            },
            "PBCEC_EB02C_DEBTHISTORY": {
                "EB02CS01": "1",
                "EB02CR01": next(iter(loans), {"dt": "20260917"})["dt"][:4]
                + "-"
                + next(iter(loans), {"dt": "20260917"})["dt"][4:6],
                "EB02CS02": len(active),
                "EB02CJ01": m(total),
                "EB02CS03": sum(r["ED01BD01"] == "2" for r in repays),
                "EB02CJ02": m(
                    sum((dec(r["ED01BJ01"]) for r in repays if r["ED01BD01"] == "2"), Decimal(0))
                ),
                "EB02CS04": "0",
                "EB02CJ03": "0.00",
                "EB02CS05": sum(dec(r["ED01BJ04"]) > 0 for r in repays),
                "EB02CJ04": m(over),
                "EB02CS06": sum(dec(r["ED01BJ05"]) > 0 for r in repays),
                "EB02CJ05": m(over),
            },
        }
        guar = tables.get("PBCEC_ED04AB_GUARANTEEDETAIL", [])
        gb = sum((dec(r["ED04BJ01"]) for r in guar), Decimal(0))
        details["PBCEC_EB01A_CREDITCUE"].update(EB01AJ05=m(gb), EB01AJ06="0.00", EB01AJ07="0.00")
        details["PBCEC_EB03A_SUMUNSETTLED"] = {
            "EB03AS01": int(gb > 0),
            "EB03AS02": sum(dec(r["ED04BJ01"]) > 0 for r in guar),
            "EB03AJ01": m(gb),
        }
        details["PBCEC_EB03B_SUMSETTLED"] = {
            "EB03BS01": int(any(dec(r["ED04BJ01"]) == 0 for r in guar)),
            "EB03BS02": sum(dec(r["ED04BJ01"]) == 0 for r in guar),
        }
        rr = tables.get("PBCEC_ED07_REPAYMENTLOANACCT", []) + tables.get(
            "PBCEC_ED08_REPAYMENTDISCOUNT", []
        )
        details["PBCEC_EB05A_CREDITSUM"] = {
            "EB05AS01": int(bool(rr)),
            "EB05AJ01": "0.00",
            "EB05AS02": "0",
            "EB05AJ02": "0.00",
            "EB05AJ03": m(sum((dec(r.get("ED070J01", r.get("ED080J01"))) for r in rr), Decimal(0))),
            "EB05AS03": len(rr),
            "EB05AJ04": m(sum((dec(r.get("ED070J02", r.get("ED080J02"))) for r in rr), Decimal(0))),
            "EB05AJ05": "0.00",
            "EB05AJ06": "0.00",
        }
        for name, values in details.items():
            for r in tables.get(name, []):
                put(r, values)


def refine_transactions(source, output, table, tags, manifest):
    sink = CsvSink(output / table["filename"], table["columns"], table["mock_primary_key"])
    people = {p["cust_ind"]: p for p in read_csv(source / "reference/customer_identity.csv")}
    with (source / table["filename"]).open(encoding="utf-8-sig", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), 1):
            t = tags[row["cust_ind"]]
            i = int(t["cust_ind"].split("_")[-1])
            kind = row["ev_ecd"].removeprefix("MOCK_")
            short, category, label = TXN_KINDS[kind]
            seq = int(row["acc_dtl_sn"])
            dt = row["txn_dt"].replace("-", "")
            branch = t["cert_district"] + "001"
            # An electronic transaction requires an effective channel contract.
            channels = [
                ch
                for ch, key in [("WEB", "corp_ebnkg_sign_dt"), ("MOBILE", "mb_sign_dt")]
                if t[key] and t[key] <= dt
            ]
            channel = channels[seq % len(channels)] if channels else "COUNTER"
            row.update(
                cust_nm=t["cust_nm"],
                acct_nm=t["cust_nm"],
                txn_cgycd=category,
                bill_ctcd="EVOUCHER",
                txn_medm_tpcd="ACCOUNT",
                sys_tx_type=category,
                sys_tx_code=short,
                chnl_tpcd=channel,
                txn_py_stlmd_cd="TRANSFER",
                txn_trgr_way="BATCH" if kind == "PAYROLL" else "MANUAL",
                exgsetl_stat_cd="NONE",
                invfrcty_icmepd_cd=("TRADE_IN" if kind == "FX_IN" else "TRADE_OUT")
                if kind.startswith("FX_")
                else "",
                acc_dpbkinno=branch,
                txn_insid=branch,
                txn_empid="E" + branch + "01",
                fst_ahn_empid="E" + branch + "02",
                snd_ahn_empid="E" + branch + "03" if dec(row["txnamt"]) >= 1000000 else "",
                txn_medm_id=row["accno"],
                txn_cardno="",
                src_sys="CORE",
                src_table="ACCOUNT_TXN",
                etl_job="LOAD_ACCOUNT_TXN",
                dtljrnlentr_ntl_sbjid="2001",
                cntrprt_txn_py_brno="0002",
                txn_pstcrpt=label,
                txn_use="工资薪金"
                if kind == "PAYROLL"
                else "货款结算"
                if kind.startswith(("SETTLE", "FX_"))
                else "资金调拨",
            )
            # Preserve one meaningful card/counter scene rather than a card
            # number on every web transfer. Optional credential fields follow it.
            if channel == "COUNTER" and kind in {"SETTLE_IN", "SETTLE_OUT"} and seq % 5 == 0:
                row.update(
                    txn_medm_tpcd="CARD",
                    txn_medm_id=f"{6214000000000000000 + i:019d}",
                    txn_cardno=f"{6214000000000000000 + i:019d}",
                )
            if channel != "COUNTER":
                for k in [
                    "agnc_psn_nm",
                    "agnc_psn_crdt_tpcd",
                    "agnc_psn_crdt_no",
                    "agnc_psn_ctc_tel",
                    "agnc_psn_nat",
                ]:
                    row[k] = ""
            elif not row["agnc_psn_nm"]:
                row.update(
                    agnc_psn_nm=t["legal_rep_nm"],
                    agnc_psn_crdt_tpcd="01",
                    agnc_psn_crdt_no=people[t["cust_ind"]]["person_certificate"],
                    agnc_psn_ctc_tel=f"139{10000000 + i:08d}",
                    agnc_psn_nat="CHN",
                )
            if row["mrch_cgy_cd"]:
                row["mrch_cgy_cd"] = "WHOLESALE"
            if kind in {"LOAN_DRAW", "LOAN_REPAY"}:
                row.update(
                    cntrprt_txn_accno_nm="浦江银行贷款清算专户",
                    cntrprtbookentracnonm="浦江银行贷款清算专户",
                    cntrprt_trdbrh_nm=row["txn_insid_nm"],
                    cntrprt_txn_py_brno="0001",
                    cntrprt_wthr_ccb_cst="1",
                )
                counter = f"{9000000000 + i:020d}"
            elif kind == "FUNDING":
                counter = f"{9100000000 + i:020d}"
            elif kind in {"CASH_SWEEP", "TERM_DEPOSIT", "OPEN_DEPOSIT"}:
                counter = f"{9200000000 + i:020d}"
            else:
                counter = row["cntrprt_txn_accno"]
            row.update(
                cntrprt_txn_accno=counter,
                cntrprtbookentr_accno=counter,
                cntpr_dep_accno=counter,
                cntpr_cst_id="CP_" + counter[-12:],
            )
            if kind == "FUNDING":
                row.update(
                    cntrprt_txn_accno_nm=t["legal_rep_nm"], cntrprtbookentracnonm=t["legal_rep_nm"]
                )
            if kind in {"CASH_SWEEP", "TERM_DEPOSIT", "OPEN_DEPOSIT"}:
                row.update(cntrprt_txn_accno_nm=t["cust_nm"], cntrprtbookentracnonm=t["cust_nm"])
            if dt[:4] == t["dt"][:4] and row["be_rvrs_ind"] == "0" and row["rvrs_cd"] == "0":
                if channel == "WEB":
                    t["corp_ebnkg_trans_y_ind"] = "1"
                if channel == "MOBILE":
                    t["corp_mb_trans_y_ind"] = "1"
                if dt[:6] == t["dt"][:6]:
                    if channel == "WEB":
                        t["CORP_EBNKG_M_IND"] = "1"
                    if channel == "MOBILE":
                        t["CORP_MB_M_IND"] = "1"
            sink.add(row)
            if n % 100000 == 0:
                print(f"Corrected transactions: {n:,}", flush=True)
    manifest["files"][table["filename"]] = sink.close()
    book = {
        "txn_cgycd": {
            v[1]: {
                "TRANSFER": "转账",
                "PAYROLL": "代发工资",
                "LOAN": "贷款",
                "CORRECT": "冲正",
                "DEPOSIT": "存款",
            }[v[1]]
            for v in TXN_KINDS.values()
        },
        "sys_tx_code": {v[0]: v[2] for v in TXN_KINDS.values()},
        "bill_ctcd": {"EVOUCHER": "电子记账凭证"},
        "txn_medm_tpcd": {"ACCOUNT": "账号", "CARD": "结算卡"},
        "chnl_tpcd": {"WEB": "企业网银", "MOBILE": "企业手机银行", "COUNTER": "柜面"},
        "exgsetl_stat_cd": {"NONE": "未发生结售汇；外币原币收付不等于结售汇"},
        "invfrcty_icmepd_cd": {"TRADE_IN": "货物贸易收入", "TRADE_OUT": "货物贸易支出"},
        "txn_py_stlmd_cd": {"TRANSFER": "转账结算"},
        "txn_trgr_way": {"BATCH": "批量代发", "MANUAL": "客户发起"},
        "mrch_cgy_cd": {"WHOLESALE": "批发商户"},
        "dtljrnlentr_ntl_sbjid": {"2001": "企业存款记账科目"},
        "ccycd": {"CNY": "人民币", "USD": "美元"},
        "dbtcrdrccd": {"C": "贷记入账", "D": "借记支出"},
        "prod_cd": {"DEMAND": "活期存款", "TERM": "定期存款", "FX": "外币活期存款"},
        "acc_tpcd": {"01": "单位账户"},
        "cst_acc_tpcd": {"01": "对公客户账户"},
        "cshex_cd": {"1": "现汇账户记账"},
        "cash_tfr_ind": {"T": "转账"},
        "iwrd_out_indcd": {"I": "收入", "O": "支出"},
        "xbrdr_txn_ind": {"0": "境内", "1": "跨境"},
        "cnsmr_sys_ind": {"MOCK": "本地测试消费系统"},
        "rvrs_cd": {"0": "非冲正", "1": "冲正"},
        "be_rvrs_ind": {"0": "未被冲正", "1": "已被冲正"},
    }
    book["sys_tx_type"] = book["txn_cgycd"]
    for key in ["ev_ecd", "smy_cd", "lcl_txn_tpcd", "trdpt_txn_cd", "inr_txn_cd"]:
        book[key] = {"MOCK_" + k: v[2] for k, v in TXN_KINDS.items()}
    for f in table["columns"]:
        if f["name"] in book:
            f["test_codebook"] = book[f["name"]]


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=ROOT / "data/mock-sources")
    p.add_argument("--baseline", type=Path, default=ROOT / "examples/mock")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--baseline-output", type=Path, required=True)
    args = p.parse_args()
    refresh(args.source, args.baseline, args.output, args.baseline_output)

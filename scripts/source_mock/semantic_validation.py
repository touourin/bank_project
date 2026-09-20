"""Independent semantic contracts, beyond SQL type and generated checksums."""

from __future__ import annotations

import re
from decimal import Decimal

from .common import dec


def row_errors(table, row):
    errors = []
    name = table["sheet"]
    for f in table["columns"]:
        value = row[f["name"]]
        desc = f["source_description"]
        raw = f["source_key"]
        if not value:
            continue
        if value == "M01" or "（模拟）" in value or "(模拟)" in value or value.startswith("模拟"):
            errors.append(f"{f['name']}: unresolved placeholder")
        if f.get("test_codebook") and value not in f["test_codebook"]:
            errors.append(f"{f['name']}: value outside documented test codebook")
        if table["domain"] == "credit":
            if re.search(r"S\d{2}$", raw) and not value.isdigit():
                errors.append(f"{raw}: count is not a nonnegative integer")
            if re.search(r"J\d{2}$", raw) and not re.fullmatch(r"-?\d+(\.\d+)?", value):
                errors.append(f"{raw}: amount is not decimal")
        if "比例" in desc and not (Decimal(0) <= dec(value) <= Decimal(1)):
            errors.append(f"{raw}: ratio outside [0,1]")

    def le(small, big):
        if row.get(small) and row.get(big) and dec(row[small]) > dec(row[big]):
            errors.append(f"{small} exceeds {big}")

    def ordered(start, end):
        if row.get(start) and row.get(end) and row[start] > row[end]:
            errors.append(f"{end} precedes {start}")

    def zero_count(money, count):
        if row.get(money) and row.get(count) and (dec(row[money]) == 0) != (int(row[count]) == 0):
            errors.append(f"{money}/{count}: zero amount/count disagree")

    pairs = {
        "PBCPC_PD02AZ_FACILITYAGTSUM": [("PD02AJ04", "PD02AJ01"), ("PD02AJ01", "PD02AJ03")],
        "PBCPC_PD01ABC_PERFORMANC": [
            ("PD01BJ01", "PD01AJ01"),
            ("PD01AJ01", "PD01AJ02"),
            ("PD01CJ06", "PD01CJ01"),
            ("PD01CJ12", "PD01CJ14"),
        ],
        "PBCPC_PD01H_LARGESTAGE": [("PD01HJ02", "PD01HJ01")],
        "PBCPC_PC02H_LOANCARDSUM": [
            ("PC02HJ03", "PC02HJ02"),
            ("PC02HJ02", "PC02HJ01"),
            ("PC02HJ04", "PC02HJ01"),
        ],
        "PBCPC_PC02I_STANDARDSUM": [
            ("PC02IJ03", "PC02IJ02"),
            ("PC02IJ02", "PC02IJ01"),
            ("PC02IJ04", "PC02IJ01"),
        ],
        "PBCEC_ED04AB_GUARANTEEDETAIL": [("ED04BJ01", "ED04AJ01"), ("ED04BJ02", "ED04BJ01")],
        "PBCEC_ED07_REPAYMENTLOANACCT": [
            ("ED070J02", "ED070J05"),
            ("ED070J05", "ED070J06"),
            ("ED070J04", "ED070J03"),
            ("ED070J03", "ED070J02"),
        ],
        "PBCEC_ED08_REPAYMENTDISCOUNT": [("ED080J02", "ED080J05")],
        "PBCEC_ED09_GUARANTEE": [("ED090J02", "ED100J03")],
        "PBCPC_PF03AZ_FORCEEXECUTION": [("PF03AJ02", "PF03AJ01")],
        "PBCEC_EF03_FORCEEXECUTION": [("EF030J02", "EF030J01")],
    }
    for small, big in pairs.get(name, []):
        le(small, big)
    for start, end in [
        ("PD02AR01", "PD02AR02"),
        ("PD01AR01", "PD01AR02"),
        ("PD01AR01", "PD01BR01"),
        ("ED01AR01", "ED01AR02"),
        ("ED01AR01", "ED01AR03"),
        ("ED060R01", "ED060R02"),
        ("ED04AR01", "ED04AR02"),
        ("ED04AR01", "ED04BR02"),
        ("PF02AR01", "PF02AR02"),
        ("EF020R01", "EF020R02"),
        ("PF03AR01", "PF03AR02"),
        ("EF100R01", "EF100R02"),
        ("INDATE", "OUTDATE"),
        ("APPDATE", "CHECKDATE"),
        ("CHECKDATE", "REGDATE"),
        ("BEGINDATE", "ENDDATE"),
        ("FROFROM", "THAWDATE"),
        ("FROFROM", "FROTO"),
        ("CFROFROM", "CFROTO"),
    ]:
        ordered(start, end)
    for money, count in [
        ("PD01CJ06", "PD01CS02"),
        ("ED01BJ04", "ED01BS02"),
        ("ED070J03", "ED070S01"),
        ("PC02DJ01", "PC02DS04"),
        ("PC030J01", "PC030S02"),
    ]:
        zero_count(money, count)
    if row.get("ENTSTATUS") == "存续" and (row.get("CANDATE") or row.get("REVDATE")):
        errors.append("active company has cancellation/revocation date")
    if row.get("FREEZE_FLAG") == "冻结" and row.get("FREEZE_DATE"):
        errors.append("active freeze has release date")
    if name in {"PBCPC_PD01ABC_PERFORMANC", "PBCEC_ED04AB_GUARANTEEDETAIL"}:
        state, closing = (
            ("PD01BD01", "PD01BR01") if name.startswith("PBCPC") else ("ED04BD01", "ED04BR02")
        )
        if row.get(state) == "1" and row.get(closing):
            errors.append("active account has closing date")
    for text, money in [
        ("PF02AQ04", "PF02AJ01"),
        ("PF03AQ04", "PF03AJ01"),
        ("PF03AQ05", "PF03AJ02"),
        ("EF020Q03", "EF020J01"),
        ("EF030Q03", "EF030J01"),
        ("EF030Q05", "EF030J02"),
        ("PF04AQ02", "PF04AJ01"),
        ("EF040Q03", "EF040J01"),
    ]:
        if row.get(text) and row.get(money):
            match = re.search(r"(\d+(?:\.\d+)?)元", row[text])
            if match and dec(match[1]) != dec(row[money]):
                errors.append(f"{text}/{money}: narrative amount disagrees")
    return errors


def ledger_errors(row, tag):
    errors = []
    if "M01" in row.values():
        errors.append("unresolved ledger M01")
    if row["ccycd"] not in {"CNY", "USD"}:
        errors.append("invalid currency")
    if row["sys_tx_code"] == "PAYROLL" and row["dbtcrdrccd"] != "D":
        errors.append("payroll must be debit")
    channel = row["chnl_tpcd"]
    dt = row["txn_dt"].replace("-", "")
    if channel in {"WEB", "MOBILE"}:
        signed = tag["corp_ebnkg_sign_dt" if channel == "WEB" else "mb_sign_dt"]
        if not signed or signed > dt:
            errors.append("transaction precedes channel signing")
    if row["ccycd"] == "CNY" and row["invfrcty_icmepd_cd"]:
        errors.append("domestic payment has cross-border declaration")
    if row["txn_medm_tpcd"] == "CARD" and row["txn_medm_id"] != row["txn_cardno"]:
        errors.append("card medium mismatch")
    if row["txn_medm_tpcd"] == "ACCOUNT" and row["txn_cardno"]:
        errors.append("account transfer has card number")
    return errors

"""Cross-source identity invariants, independent of cell-patch comparisons."""

from collections import Counter, defaultdict

from marketing_refresh.summary_common import Sources

from .state import OUTPUT, STATE, policy, save


def run(version):
    directory = OUTPUT / version
    p = policy(version)
    tags = Sources(directory / "客户标签.xlsx").rows("数据")
    by_code = {r["unify_credit_code"]: r for r in tags}
    by_report = {key: by_code[p.companies[cid]["credit_code"]] for key, cid in p.by_report.items()}
    checks = Counter()

    def equal(actual, expected, check):
        assert actual == expected, check
        checks[check] += 1

    for r in Sources(directory / "工商.xlsx").rows("数据"):
        if r["unify_credit_code"] in by_code:
            equal(
                r["legal_rep_nm"],
                by_code[r["unify_credit_code"]]["legal_rep_nm"],
                "business_summary_legal",
            )
    business = Sources(directory / "来源明细/工商明细.xlsx")
    for table, field, role in [
        ("T_SAIC_BASIC", "legal_rep_nm", "legal_rep"),
        ("T_SAIC_ORGDETAIL", "legal_rep_nm", "legal_rep"),
        ("T_SAIC_PERSON", "PERNAME", "legal_rep"),
        ("T_SAIC_SHAREHOLDER", "SHANAME", "act_ctrl_psn"),
    ]:
        for r in business.rows(table):
            tag = by_code.get(r["unify_credit_code"])
            if tag is None or (table == "T_SAIC_SHAREHOLDER" and r["INVTYPE"] != "自然人股东"):
                continue
            equal(r[field], tag[role + "_nm"], table)
    credit = Sources(directory / "来源明细/征信明细.xlsx")
    for table, name, certificate, role in [
        ("PBCEC_EC02_CONTRIBUTIVE", "EC020Q01", "EC020I01", "act_ctrl_psn"),
        ("PBCEC_EC03_SENIOREXECUTIVE", "EC030Q01", "EC030I01", "legal_rep"),
        ("PBCEC_EC05_ACTUALCONTROLLER", "act_ctrl_psn_nm", "EC050I01", "act_ctrl_psn"),
    ]:
        for r in credit.rows(table):
            tag = by_report.get(r["EA01AI01"])
            if tag is None:
                continue
            equal(r[name], tag[role + "_nm"], table + "/name")
            equal(
                r[certificate], p.people[tag[role + "_cust_id"]].certificate, table + "/certificate"
            )
    for r in credit.rows("PBCPC_PA01AB_REPORTHEADER"):
        if r["PA01BI01"] in p.by_certificate:
            equal(r["PA01BQ01"], p.by_certificate[r["PA01BI01"]].name, "personal_report_name")
    for table, name, cert in [
        ("T_SAIC_PUNISHED", "INAMECLEAN", "CARDNUMCLEAN"),
        ("T_SAIC_PUNISHBREAK", "INAMECLEAN", "CARDNUM"),
        ("T_SAIC_JUDICIALAIDALTER", "INAME", "INAME_LICENCE"),
        ("T_SAIC_JUDICIALAIDDETAIL", "INAME", "LICENCE_NO"),
    ]:
        for r in business.rows(table):
            if r[cert] in p.by_certificate:
                equal(r[name], p.by_certificate[r[cert]].name, table + "/original_person")
    groups, representatives, owners = defaultdict(set), defaultdict(set), defaultdict(set)
    names = defaultdict(set)
    for r in tags:
        for role in ("legal_rep", "act_ctrl_psn"):
            equal(r[role + "_nm"], p.people[r[role + "_cust_id"]].name, "person_id_to_name")
            names[r[role + "_nm"]].add(r[role + "_cust_id"])
        if r["CST_MGRP_ID"]:
            groups[r["CST_MGRP_ID"]].add(r["act_ctrl_psn_cust_id"])
        representatives[r["legal_rep_cust_id"]].add(r["cust_ind"])
        owners[r["act_ctrl_psn_cust_id"]].add(r["cust_ind"])
    assert len(groups) == 59 and all(len(ids) == 1 for ids in groups.values())
    for c in p.companies.values():
        if c["scenario"] == "shared_representative_separate_controllers":
            represented = representatives[c["legal"]]
            assert len(represented) == 2
            assert len({p.companies[cid]["controller"] for cid in represented}) == 2
    report = dict(
        coverage=p.export()["coverage"],
        checks=dict(checks),
        legal_identities=len(representatives),
        controlling_identities=len(owners),
        homonym_names_present_in_tags=sum(len(ids) > 1 for ids in names.values()),
    )
    save(STATE / version / "cross-source-audit.json", report)
    print(version, report, flush=True)


if __name__ == "__main__":
    for version in ("project", "desktop"):
        run(version)

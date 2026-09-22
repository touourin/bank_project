"""Regression coverage for the explicit synthetic identity-data migration."""

from collections import Counter, defaultdict

import pytest

from scripts.identity_repair.policy import Policy


@pytest.fixture
def source():
    tags, people = [], []
    for i in range(1000):
        tags.append(
            dict(
                cust_ind=f"C{i:04}",
                cust_nm=f"Company {i}",
                unify_credit_code=f"U{i:04}",
                legal_rep_cust_id=f"P{i:04}",
                legal_rep_nm=f"Old {i % 64}",
                act_ctrl_psn_cust_id=f"P{i:04}",
                act_ctrl_psn_nm=f"Old {i % 64}",
                CST_MGRP_ID=f"G{i // 4:02}" if i < 236 else None,
                CST_MGRP_NM=f"Group {i // 4}" if i < 236 else None,
            )
        )
        people.append(
            dict(
                unify_credit_code=f"U{i:04}",
                person_certificate=f"31011419700101{i:04}",
                enterprise_report_id=f"R{i:04}",
            )
        )
    return tags, people


def test_scenarios_keep_group_membership_and_identity_authority(source):
    tags, people = source
    p = Policy(tags, people)
    result = [p.transform("客户标签", row) for row in tags]
    assert len({person.certificate for person in p.people.values()}) == 1000
    assert p.export()["coverage"]["homonym_pairs"] == 20
    assert sum(row["legal_rep_cust_id"] != row["act_ctrl_psn_cust_id"] for row in result) == 401
    assert Counter(row["CST_MGRP_ID"] for row in tags) == Counter(
        row["CST_MGRP_ID"] for row in result
    )
    controllers = defaultdict(set)
    names = defaultdict(set)
    for row, old in zip(result, tags, strict=True):
        assert (row["cust_ind"], row["cust_nm"], row["unify_credit_code"]) == (
            old["cust_ind"],
            old["cust_nm"],
            old["unify_credit_code"],
        )
        if row["CST_MGRP_ID"]:
            controllers[row["CST_MGRP_ID"]].add(row["act_ctrl_psn_cust_id"])
        for role in ("legal_rep", "act_ctrl_psn"):
            assert p.people[row[role + "_cust_id"]].name == row[role + "_nm"]
    assert all(len(ids) == 1 for ids in controllers.values())
    for person in p.people.values():
        names[person.name].add(person.certificate)
    assert sum(len(certs) == 2 for certs in names.values()) == 20


def test_shared_representative_does_not_imply_same_controller(source):
    p = Policy(*source)
    a, b = p.companies["C0236"], p.companies["C0237"]
    assert a["legal"] == b["legal"]
    assert a["controller"] != b["controller"]
    assert a["group_id"] is None and b["group_id"] is None


def test_cross_source_roles_and_historical_person_are_distinct(source):
    p = Policy(*source)
    company = p.companies["C0003"]
    legal, controller, base = (p.people[company[key]] for key in ("legal", "controller", "base"))
    row = dict(
        unify_credit_code=company["credit_code"],
        EC020Q01=base.previous_name,
        EC020I01=base.certificate,
        EC020J01="123456789.12345678",
    )
    changed = p.transform("PBCEC_EC02_CONTRIBUTIVE", row)
    assert (changed["EC020Q01"], changed["EC020I01"]) == (controller.name, controller.certificate)
    assert changed["EC020J01"] == row["EC020J01"]
    assert row["EC020Q01"] == base.previous_name
    risk = dict(
        row,
        TYPE="自然人",
        CARDNUM=base.certificate,
        INAMECLEAN=base.previous_name,
        BUSINESSENTITY=base.previous_name,
    )
    changed = p.transform("T_SAIC_PUNISHBREAK", risk)
    assert changed["INAMECLEAN"] == changed["BUSINESSENTITY"] == base.name
    assert changed["CARDNUM"] == base.certificate != legal.certificate


def test_ledger_preserves_money_accounts_and_original_agent(source):
    p = Policy(*source)
    c = p.companies["C0003"]
    base, controller = p.people[c["base"]], p.people[c["controller"]]
    row = dict(
        cust_ind=c["customer_id"],
        ev_ecd="FUNDING",
        agnc_psn_nm=base.previous_name,
        agnc_psn_crdt_no=base.certificate,
        cntrprt_txn_accno_nm=base.previous_name,
        txnamt="9007199254740993.01",
        accno="000001234",
        txn_dt="20260917",
    )
    changed = p.transform("交易流水", row)
    assert changed["agnc_psn_nm"] == base.name
    assert changed["cntrprt_txn_accno_nm"] == controller.name
    assert {
        k: v for k, v in changed.items() if k not in {"agnc_psn_nm", "cntrprt_txn_accno_nm"}
    } == {k: v for k, v in row.items() if k not in {"agnc_psn_nm", "cntrprt_txn_accno_nm"}}
    assert p.transform("交易流水", changed) == changed


def test_personal_report_uses_certificate_even_without_company(source):
    p = Policy(*source)
    person = p.people["P0003"]
    row = {"id_number": person.certificate, "subject_name": person.previous_name}
    assert p.transform("征信", row)["subject_name"] == person.name


def test_auxiliary_company_outside_reviewed_scope_is_unchanged(source):
    p = Policy(*source)
    row = dict(cust_ind="AUX", unify_credit_code="AUX_CODE", legal_rep_nm="Unrelated")
    assert p.transform("工商", row) == row


def test_ambiguous_company_names_fail_before_mutation(source):
    source[0][1]["cust_nm"] = source[0][0]["cust_nm"]
    with pytest.raises(ValueError, match="Ambiguous company"):
        Policy(*source)


def test_rejects_rebuilding_plan_from_already_repaired_or_ambiguous_inputs(source):
    p = Policy(*source)
    changed = [p.transform("客户标签", row) for row in source[0]]
    with pytest.raises(ValueError, match="duplicate source person"):
        Policy(changed, source[1])
    source[1][1]["person_certificate"] = source[1][0]["person_certificate"]
    with pytest.raises(ValueError, match="share a certificate"):
        Policy(*source)

"""Regression tests for defects found in the delivered five-workbook fixtures."""

from scripts.source_mock.semantic_validation import ledger_errors, row_errors
from scripts.source_mock.usable import codebook


def table(name, fields=()):
    return {"sheet": name, "domain": "credit", "columns": list(fields)}


def test_counts_cannot_contain_a_generic_code_even_when_sql_type_is_varchar():
    field = {
        "name": "PC02AS02",
        "source_key": "PC02AS02",
        "source_description": "业务类型数量",
        "type": "VARCHAR(3)",
    }
    assert row_errors(table("PBCPC_PC02A_CREDITSUMMARYCUE", [field]), {"PC02AS02": "M01"})
    assert row_errors(table("PBCPC_PC02A_CREDITSUMMARYCUE", [field]), {"PC02AS02": "1"}) == []
    assert row_errors(table("PBCPC_PC02A_CREDITSUMMARYCUE", [field]), {"PC02AS02": ""}) == []


def test_currency_rule_takes_precedence_over_credit_d_code_suffix():
    field = {
        "name": "PD01AD04",
        "source_key": "PD01AD04",
        "source_description": "币种",
        "type": "VARCHAR(3)",
    }
    assert codebook(field, "PBCPC_PD01ABC_PERFORMANC") == {"CNY": "人民币", "USD": "美元"}


def test_zero_arrears_requires_zero_overdue_periods():
    assert row_errors(table("PBCPC_PD01ABC_PERFORMANC"), {"PD01CJ06": "0.00", "PD01CS02": "12"})
    assert (
        row_errors(table("PBCPC_PD01ABC_PERFORMANC"), {"PD01CJ06": "0.00", "PD01CS02": "0"}) == []
    )


def test_balance_and_case_narratives_are_checked_independently():
    assert row_errors(table("PBCPC_PD02AZ_FACILITYAGTSUM"), {"PD02AJ04": "120", "PD02AJ01": "100"})
    assert row_errors(
        table("PBCEC_EF03_FORCEEXECUTION"),
        {"EF030Q05": "已收回人民币100.00元", "EF030J02": "200.00"},
    )


def test_channel_activity_requires_a_signed_contract_and_correct_applicability():
    row = {
        "ccycd": "CNY",
        "sys_tx_code": "TRF_IN",
        "dbtcrdrccd": "C",
        "chnl_tpcd": "WEB",
        "txn_dt": "2026-01-01",
        "invfrcty_icmepd_cd": "TRADE_IN",
        "txn_medm_tpcd": "ACCOUNT",
        "txn_cardno": "",
    }
    errors = ledger_errors(row, {"corp_ebnkg_sign_dt": "20260201"})
    assert "transaction precedes channel signing" in errors
    assert "domestic payment has cross-border declaration" in errors


def test_open_company_and_frozen_shares_cannot_have_terminal_dates():
    assert row_errors(table("T_SAIC_ENTINV"), {"ENTSTATUS": "存续", "CANDATE": "2026-08-01"})
    assert row_errors(
        table("T_SAIC_JUDICIALAIDDETAIL"), {"FREEZE_FLAG": "冻结", "FREEZE_DATE": "2026-08-01"}
    )

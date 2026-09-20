"""Report/identity/credit/loan facts plus complete dictionary coverage fixtures."""

from .common import amount
from .facts import make_row, nullify_credit
from .financial import statement_values

CORE = {
    "PBCEC_EA01AB_REPORTHEADER",
    "PBCEC_EA01C_IDENTITYINFO",
    "PBCEC_EA01DE_DISSENTANDRATE",
    "PBCEC_EB01A_CREDITCUE",
    "PBCEC_EB04_FACILITYAGTSUM",
    "PBCEC_EC01_BASICIDENTITY",
    "PBCEC_EC02_CONTRIBUTIVE",
    "PBCEC_EC03_SENIOREXECUTIVE",
    "PBCEC_EC05_ACTUALCONTROLLER",
    "PBCEC_ED06_FACILITYAGREEMENT",
    "PBCEC_ED01A_BASICLOANACCT",
    "PBCEC_ED01B_REPAYMENT",
}


def rows_for(table: dict, subjects: list, coverage_customers: int = 100):
    name = table["sheet"]
    population = subjects if name in CORE else subjects[:coverage_customers]
    if name in {"PBCEC_EC02_CONTRIBUTIVE", "PBCEC_EC05_ACTUALCONTROLLER"}:
        population = [s for s in population if s.entity_type == "company"]
    if name in {"PBCEC_ED01C_SPECIALTRADE", "PBCEC_ED04AB_GUARANTEEDETAIL"}:
        population = [s for s in population if s.limit() > 0]
    equations = []
    for pos, s in enumerate(population):
        overrides = {}
        protected = {"EA01AR01", "PA01AR01"}
        banks = [1]
        if name in {
            "PBCEC_ED06_FACILITYAGREEMENT",
            "PBCEC_ED01A_BASICLOANACCT",
            "PBCEC_ED01B_REPAYMENT",
        }:
            banks = [b for b in (1, 2) if s.limit(b) > 0]
        for bank in banks:
            overrides = {}
            history = s.loan_history(bank)
            if name in {"PBCPC_PD01D_LATEST24MONTH", "PBCPC_PD01E_LATEST5YEAR"}:
                # One observed repayment month, not a fabricated complete history.
                prefix = "PD01D" if "24MONTH" in name else "PD01E"
                month = s.as_of.strftime("%Y-%m")
                overrides = {prefix + f"R{i:02d}": month for i in (1, 2, 3)}
                if prefix == "PD01E":
                    overrides.update(PD01ES01="1", PD01EJ01="0.00")
            elif name == "PBCPC_PE01AZ_TELPAYMENT":
                # N denotes paid in this synthetic convention only.
                overrides = dict(PE01AQ02="N" * 24, PE01AJ01="0.00")
            elif name == "PBCEC_EA01C_IDENTITYINFO":
                overrides = {
                    "EA01CS01": "1",
                    "EA01CD01": "01",
                    "EA01CI01": s.tag["unify_credit_code"],
                }
            elif name == "PBCEC_EB04_FACILITYAGTSUM":
                overrides = dict(
                    EB040J01=amount(s.limit(), 2),
                    EB040J02=amount(s.balance(), 2),
                    EB040J03=amount(s.limit() - s.balance(), 2),
                    EB040J04=amount(s.limit(2), 2),
                    EB040J05=amount(s.balance(2), 2),
                    EB040J06=amount(s.limit(2) - s.balance(2), 2),
                    EB040D01="0",
                )
            elif name == "PBCEC_EB01A_CREDITCUE":
                overrides = dict(
                    EB01AJ01=amount(s.balance() + s.balance(2), 2),
                    EB01AJ02="0.00",
                    EB01AJ03="0.00",
                    EB01AJ04="0.00",
                    EB01AJ05="0.00",
                    EB01AJ06="0.00",
                    EB01AJ07="0.00",
                    EB01AS01=str(sum(s.limit(b) > 0 for b in (1, 2))),
                    EB01AS02=str(sum(s.balance(b) > 0 for b in (1, 2))),
                )
            elif name == "PBCEC_EC01_BASICIDENTITY":
                overrides = dict(
                    EC010Q01=s.tag["busin_addr"],
                    EC010Q02=s.tag["busin_addr"],
                    EC010R01=s.tag["found_dt"][:4],
                    EC010D05="1" if s.tag["survival_status"] == "存续" else "0",
                )
            elif name == "PBCEC_EC02_CONTRIBUTIVE":
                overrides = dict(
                    EC020J01=amount(s.tag["cert_capt_amt"], 2),
                    EC020S01="1",
                    EC020Q01=s.person,
                    EC020I01=s.certificate,
                    EC020Q02="1.00",
                )
            elif name == "PBCEC_EC03_SENIOREXECUTIVE":
                overrides = dict(EC030S01="1", EC030Q01=s.person, EC030I01=s.certificate)
            elif name == "PBCEC_EC05_ACTUALCONTROLLER":
                overrides = dict(
                    EC050S01="1",
                    EC050D01="1",
                    EC050Q01=s.tag["act_ctrl_psn_nm"],
                    EC050I01=s.certificate,
                )
            elif name == "PBCEC_ED06_FACILITYAGREEMENT":
                overrides = dict(
                    ED060I01=s.loan_id(bank),
                    ED060I02=f"M00{bank}",
                    ED060D03=str(bank - 1),
                    ED060D04="CNY",
                    ED060J01=amount(s.limit(bank), 2),
                    ED060J04=amount(s.balance(bank), 2),
                    ED060J03=amount(s.limit(bank), 2),
                    ED060I03=s.loan_id(bank),
                    ED060R01=history["signed"].isoformat(),
                    ED060R02=history["maturity"].isoformat(),
                    ED060R03=s.as_of.isoformat(),
                )
            elif name == "PBCEC_ED01A_BASICLOANACCT":
                overrides = dict(
                    ED01AI01=s.loan_id(bank),
                    ED01AI03=s.loan_id(bank),
                    ED01AI02=f"M00{bank}",
                    ED01AD07="CNY",
                    ED01AJ01=amount(s.principal(bank), 2),
                    ED01AJ02=amount(s.limit(bank), 2),
                    ED01AD01="1" if s.balance(bank) > 0 else "0",
                    ED01AR01=history["funded"].isoformat(),
                    ED01AR02=history["maturity"].isoformat(),
                    ED01AR03=history["repaid"].isoformat(),
                    ED01AR04=s.as_of.isoformat(),
                )
                if s.balance(bank) > 0:
                    overrides["ED01AR03"] = None
            elif name == "PBCEC_ED01B_REPAYMENT":
                overrides = dict(
                    ED01AI01=s.loan_id(bank),
                    ED01BS01="1",
                    ED01BJ01=amount(s.balance(bank), 2),
                    ED01BJ02=amount(history["last_amount"], 2),
                    ED01BJ03=amount(history["last_amount"], 2),
                    ED01BJ04="0.00",
                    ED01BJ05="0.00",
                    ED01BS02="0",
                    ED01BD01="1",
                    ED01BR01=s.as_of.isoformat(),
                    ED01BR02=history["repaid"].isoformat(),
                    ED01BR04=history["repaid"].isoformat(),
                    ED01BR05=history["repaid"].isoformat(),
                )
            if name.startswith("PBCEC_EG"):
                values, equations = statement_values(table, s)
                overrides.update(values)
            # Values required for baseline reconciliation cannot be erased by
            # generic missingness. Other nullable credit values remain sampled.
            if name in CORE:
                protected.update(overrides)
                if name == "PBCEC_EB01A_CREDITCUE":
                    protected.difference_update(
                        {"EB01AJ03", "EB01AJ04", "EB01AJ06", "EB01AJ07", "EB01AS02"}
                    )
            row = make_row(table, s, overrides, bank)
            nullify_credit(row, table, s, protected, pos)
            yield row, s, equations

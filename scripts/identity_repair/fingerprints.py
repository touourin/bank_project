"""Order-independent fingerprints including every row and NULL position."""

import hashlib
import json

PROJECTIONS = {
    "客户标签": (
        "cust_ind",
        "legal_rep_nm",
        "legal_rep_cust_id",
        "act_ctrl_psn_nm",
        "act_ctrl_psn_cust_id",
        "CST_MGRP_ID",
        "CST_MGRP_NM",
    ),
    "工商": ("unify_credit_code", "dt", "legal_rep_nm"),
    "征信": ("subject_type", "report_id", "id_number", "subject_name"),
    "交易流水": (
        "cust_ind",
        "accno",
        "acc_dtl_sn",
        "ev_ecd",
        "agnc_psn_crdt_no",
        "agnc_psn_nm",
        "cntrprt_txn_accno_nm",
        "cntrprtbookentracnonm",
    ),
}


class Fingerprint:
    def __init__(self, columns):
        self.columns = columns
        self.rows = self.total = self.xor = 0

    def add(self, record):
        values = [None if record.get(k) is None else str(record[k]) for k in self.columns]
        encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()
        value = int.from_bytes(hashlib.sha256(encoded).digest(), "big")
        self.rows += 1
        self.total = (self.total + value) % (1 << 256)
        self.xor ^= value

    def result(self):
        return dict(rows=self.rows, sum_sha256=f"{self.total:064x}", xor_sha256=f"{self.xor:064x}")

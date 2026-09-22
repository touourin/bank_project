"""Back up and transactionally repair the explicitly authorized source tables.

Source data only: immutable intake/graph runs and unrelated database tables are
outside this migration. Credentials reuse the saved application connection.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
from datetime import datetime

import pymysql
from marketing_refresh.database import ident
from marketing_refresh.summary_database import connect as plain_connection
from marketing_refresh.summary_database import credentials

from .fingerprints import PROJECTIONS, Fingerprint
from .policy import REVISION
from .state import STATE, policy, save, sha

TABLES = {
    "客户标签": "marketing_customer_tags",
    "工商": "marketing_business",
    "征信": "marketing_credit",
    "交易流水": "marketing_transactions",
}
OUT = STATE / "server"


def connect():
    if os.environ.get("IDENTITY_REPAIR_COMPRESS") != "1":
        return plain_connection()
    import mysql.connector

    raw = mysql.connector.connect(
        **credentials(),
        charset="utf8mb4",
        autocommit=False,
        compress=True,
        use_pure=False,
        connection_timeout=10,
        read_timeout=180,
        write_timeout=180,
        allow_local_infile=False,
    )

    class CompressedConnection:
        def cursor(self, kind=None):
            return raw.cursor(buffered=kind is None)

        def escape(self, value):
            return pymysql.converters.escape_item(value, "utf8mb4")

        def begin(self):
            return raw.start_transaction()

        def commit(self):
            return raw.commit()

        def rollback(self):
            return raw.rollback()

        def close(self):
            return raw.close()

    return CompressedConnection()


def scan(connection, label, *, backup=False):
    table = TABLES[label]
    with connection.cursor() as cursor:
        cursor.execute("SHOW CREATE TABLE " + ident(table))
        ddl = cursor.fetchone()[1]
        cursor.execute("SHOW COLUMNS FROM " + ident(table))
        columns = [r[0] for r in cursor.fetchall()]
    full, expected = Fingerprint(columns), Fingerprint(columns)
    projected, projected_expected = Fingerprint(PROJECTIONS[label]), Fingerprint(PROJECTIONS[label])
    p = policy("desktop") if backup else None
    path = OUT / (table + ".before.sql.gz")
    stream = (
        gzip.open(path.with_suffix(".writing.gz"), "wt", encoding="utf-8", compresslevel=1)
        if backup
        else None
    )
    changed_rows = 0
    try:
        if stream:
            stream.write("SET NAMES utf8mb4;\n" + ddl + ";\n")
        with connection.cursor(pymysql.cursors.SSCursor) as cursor:
            cursor.execute("SELECT * FROM " + ident(table))
            prefix = (
                "INSERT INTO " + ident(table) + " (" + ",".join(map(ident, columns)) + ") VALUES\n"
            )
            while batch := cursor.fetchmany(250):
                sql = []
                for values in batch:
                    row = dict(zip(columns, values, strict=True))
                    full.add(row)
                    projected.add(row)
                    if backup:
                        wanted = p.transform(label, row)
                        changed_rows += wanted != row
                        expected.add(wanted)
                        projected_expected.add(wanted)
                        sql.append("(" + ",".join(connection.escape(v) for v in values) + ")")
                if stream:
                    stream.write(prefix + ",\n".join(sql) + ";\n")
                if full.rows % 100000 == 0:
                    print(
                        f"{'Backup' if backup else 'Readback'} {table}: {full.rows:,}", flush=True
                    )
    finally:
        if stream:
            stream.close()
    result = dict(
        columns=columns, fingerprint=full.result(), projection=projected.result(), ddl=ddl
    )
    if backup:
        path.with_suffix(".writing.gz").replace(path)
        result.update(
            backup_sha256=sha(path),
            expected=expected.result(),
            expected_projection=projected_expected.result(),
            changed_rows=changed_rows,
        )
    print(f"Completed {'backup' if backup else 'readback'} {table}: {full.rows:,}", flush=True)
    return result


def backup():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "before.json").exists():
        raise ValueError("Backup already exists; never overwrite migration evidence")
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        result = {label: scan(connection, label, backup=True) for label in TABLES}
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_key,source_metadata FROM marketing_table_catalog WHERE table_key IN (%s,%s,%s,%s)",
                tuple(TABLES.values()),
            )
            catalog = dict(cursor.fetchall())
        save(OUT / "catalog.before.json", catalog)
        save(OUT / "before.json", result)
    finally:
        connection.rollback()
        connection.close()


def load_mappings(connection, p):
    """Session-local tables permit indexed joins without changing source schema."""
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMPORARY TABLE repair_companies (cust_ind VARCHAR(128) PRIMARY KEY, credit_code VARCHAR(128), old_name VARCHAR(128), base_name VARCHAR(128), legal_name VARCHAR(128), legal_id VARCHAR(128), controller_name VARCHAR(128), controller_id VARCHAR(128), INDEX(credit_code)) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
        )
        cursor.execute(
            "CREATE TEMPORARY TABLE repair_people (certificate VARCHAR(128) PRIMARY KEY, name VARCHAR(128)) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
        )
        cursor.executemany(
            "INSERT INTO repair_people VALUES (%s,%s)",
            [(x.certificate, x.name) for x in p.people.values()],
        )
        rows = []
        for c in p.companies.values():
            base, legal, ctrl = (p.people[c[k]] for k in ("base", "legal", "controller"))
            rows.append(
                (
                    c["customer_id"],
                    c["credit_code"],
                    base.previous_name,
                    base.name,
                    legal.name,
                    legal.id,
                    ctrl.name,
                    ctrl.id,
                )
            )
        cursor.executemany("INSERT INTO repair_companies VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)


def repair(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE marketing_customer_tags t JOIN repair_companies p ON t.cust_ind=p.cust_ind SET t.legal_rep_nm=p.legal_name,t.legal_rep_cust_id=p.legal_id,t.act_ctrl_psn_nm=p.controller_name,t.act_ctrl_psn_cust_id=p.controller_id"
        )
        cursor.execute(
            "UPDATE marketing_business t JOIN repair_companies p ON t.unify_credit_code=p.credit_code SET t.legal_rep_nm=p.legal_name WHERE t.legal_rep_nm IS NOT NULL AND t.legal_rep_nm<>''"
        )
        cursor.execute(
            "UPDATE marketing_credit t JOIN repair_people p ON t.id_number=p.certificate SET t.subject_name=p.name WHERE t.subject_name IS NOT NULL AND t.subject_name<>''"
        )
        cursor.execute(
            "UPDATE marketing_transactions t JOIN repair_people p ON t.agnc_psn_crdt_no=p.certificate JOIN repair_companies c ON t.cust_ind=c.cust_ind SET t.agnc_psn_nm=p.name WHERE t.agnc_psn_nm IS NOT NULL AND t.agnc_psn_nm<>''"
        )
        for field in ("cntrprt_txn_accno_nm", "cntrprtbookentracnonm"):
            cursor.execute(
                f"UPDATE marketing_transactions t JOIN repair_companies p ON t.cust_ind=p.cust_ind SET t.{field}=CASE WHEN t.ev_ecd='FUNDING' THEN p.controller_name ELSE p.base_name END WHERE BINARY t.{field}=BINARY p.old_name"
            )


def publish():
    before = json.loads((OUT / "before.json").read_text())
    verified = json.loads((STATE / "desktop/verified.json").read_text())
    if (OUT / "published.json").exists():
        raise ValueError("Already published; use verify to read back")
    for label, table in TABLES.items():
        original = before[label]
        workbook = verified[label + ".xlsx"]
        assert sha(OUT / (table + ".before.sql.gz")) == original["backup_sha256"], "Backup changed"
        assert workbook["projection_before"] == original["projection"], (
            f"Desktop/server baseline differs: {label}"
        )
        assert workbook["projection_after"] == original["expected_projection"], (
            f"Desktop/server repair differs: {label}"
        )
    connection = connect()
    try:
        load_mappings(connection, policy("desktop"))
        connection.commit()
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        connection.begin()
        # Full locked read prevents overwriting concurrent changes after backup.
        for label in TABLES:
            current = scan(connection, label)
            assert current["fingerprint"] == before[label]["fingerprint"], (
                f"Source changed since backup: {label}"
            )
            assert current["ddl"] == before[label]["ddl"], f"Schema changed: {label}"
        repair(connection)
        after = {}
        for label in TABLES:
            current = scan(connection, label)
            assert current["fingerprint"] == before[label]["expected"], (
                f"Unexpected source changes: {label}"
            )
            assert current["projection"] == verified[label + ".xlsx"]["projection_after"], label
            after[label] = current
        with connection.cursor() as cursor:
            for label, table in TABLES.items():
                cursor.execute(
                    "SELECT source_metadata FROM marketing_table_catalog WHERE table_key=%s FOR UPDATE",
                    (table,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("Missing source catalog entry")
                metadata = json.loads(row[0])
                metadata.update(
                    data_revision=REVISION,
                    xlsx_sha256=verified[label + ".xlsx"]["sha256"],
                    identity_repaired_at=datetime.now().astimezone().isoformat(),
                )
                metadata["source_comment"] = (
                    "synthetic-person-relations-v1;xlsx-sha256=" + metadata["xlsx_sha256"]
                )
                cursor.execute(
                    "UPDATE marketing_table_catalog SET source_metadata=%s WHERE table_key=%s",
                    (json.dumps(metadata, ensure_ascii=False), table),
                )
        connection.commit()
        save(OUT / "published.json", {"revision": REVISION, "tables": after})
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def verify():
    published = json.loads((OUT / "published.json").read_text())
    connection = connect()
    try:
        result = {label: scan(connection, label) for label in TABLES}
        for label, actual in result.items():
            assert actual["fingerprint"] == published["tables"][label]["fingerprint"], label
        save(OUT / "verified.json", result)
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backup", "publish", "verify"))
    globals()[parser.parse_args().action]()

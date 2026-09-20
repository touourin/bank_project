"""Publish the verified desktop subject summaries with backups and an atomic swap.

Credentials are reused from the application's encrypted source connection or
MARKETING_REFRESH_PASSWORD. They are never written to the migration evidence.
Unrelated tables and immutable application import snapshots are not modified.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import openpyxl
import pymysql
from cryptography.fernet import Fernet

from bank_project.settings import Settings
from bank_project.staging.database import StagingDatabase

from .common import DESKTOP, OUTPUT, ROOT, TABLES, dump, load
from .database import Fingerprint, database_fingerprint, ident

HOST, PORT, DATABASE, USER = "192.168.130.250", 30306, "shanghai_proj", "shanghai_app"
CHANGED = {k: TABLES[k] for k in ("征信", "工商")}
OUT = ROOT / "data/subject-summary-20260920"
BASES = {"desktop": DESKTOP}
STATE = OUT / "database"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def credentials():
    password = os.environ.get("MARKETING_REFRESH_PASSWORD")
    if not password:
        settings = Settings()
        key_path = settings.data_dir / "staging/credentials.key"
        if key_path.exists():
            key = key_path.read_bytes()
        else:
            key = subprocess.check_output(
                [
                    "docker",
                    "exec",
                    "bank-project-api-1",
                    "python",
                    "-c",
                    "from bank_project.settings import Settings; import sys; "
                    "sys.stdout.buffer.write((Settings().data_dir/'staging/credentials.key').read_bytes())",
                ],
                stderr=subprocess.DEVNULL,
            )
        with StagingDatabase(settings).connect() as cursor:
            cursor.execute(
                "SELECT options FROM intake_jobs WHERE JSON_CONTAINS_PATH(options,'one','$.mysql') ORDER BY created_at DESC"
            )
            saved = cursor.fetchall()
        for (options,) in saved:
            payload = json.loads(Fernet(key).decrypt(json.loads(options)["mysql"].encode()))
            source = payload["connection"]
            if (source["host"], source["port"], source["database"], source["user"]) == (
                HOST,
                PORT,
                DATABASE,
                USER,
            ):
                password = source["password"]
                break
    if not password:
        raise RuntimeError("No saved credential matches the intended source database")
    return dict(host=HOST, port=PORT, database=DATABASE, user=USER, password=password)


def connect():
    return pymysql.connect(
        **credentials(),
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=10,
        read_timeout=180,
        write_timeout=180,
        local_infile=False,
    )


def inspect():
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME,REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
                (DATABASE,),
            )
            print("foreign_keys", cursor.fetchall())
            cursor.execute("SHOW COLUMNS FROM marketing_table_catalog")
            print("catalog_columns", [r[:2] for r in cursor.fetchall()])
            cursor.execute("SELECT table_key,physical_table FROM marketing_table_catalog")
            print("catalog_entries", cursor.fetchall())
    finally:
        connection.close()


def canonical(value, field):
    if value is None:
        return None
    if field["kind"] == "text":
        if not isinstance(value, str):
            raise ValueError("Identifier/text lost its source type")
        return value
    number = Decimal(str(value))
    fixed = number.quantize(Decimal(".00000001"))
    if abs(number - fixed) > Decimal(".00000001"):
        raise ValueError("Number exceeds reviewed summary precision")
    return format(fixed.normalize(), "f")


def inputs(label):
    directory = OUT / "desktop" / label
    specification = load(directory / "input.json")
    verified = load(directory / "verified.json")
    path = BASES["desktop"] / (label + ".xlsx")
    if digest(path) != verified["output_sha256"]:
        raise ValueError("Desktop workbook changed since verification")
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        iterator = workbook["数据"].iter_rows(values_only=True)
        columns = [f["key"] for f in specification["fields"]]
        if list(next(iterator)) != columns:
            raise ValueError("Summary header mismatch")
        records = []
        for row in iterator:
            records.append(
                [canonical(v, f) for v, f in zip(row, specification["fields"], strict=True)]
            )
        wanted = [
            [canonical(v, f) for v, f in zip(row, specification["fields"], strict=True)]
            for row in specification["rows"]
        ]
        if records != wanted:
            raise ValueError("Actual Excel values differ from verified preparation")
    finally:
        workbook.close()
    return specification, records, verified["output_sha256"]


def summary_fingerprint(connection, table, fields):
    columns = [f["key"] for f in fields]
    result = Fingerprint(columns)
    with connection.cursor(pymysql.cursors.SSCursor) as cursor:
        cursor.execute("SELECT " + ",".join(map(ident, columns)) + " FROM " + ident(table))
        while batch := cursor.fetchmany(500):
            for row in batch:
                result.add([canonical(v, f) for v, f in zip(row, fields, strict=True)])
    return result.result()


def verify_customer_links(connection, table, specification, records):
    columns = [field["key"] for field in specification["fields"]]
    expected = sum(row[columns.index("cust_ind")] is not None for row in records)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM " + ident(table) + " s "
            "JOIN marketing_customer_tags t ON s.cust_ind=t.cust_ind AND s.dt=t.dt"
        )
        if cursor.fetchone()[0] != expected:
            raise RuntimeError("Summary/customer snapshot association differs from source")
        cursor.execute(
            "SELECT COUNT(*) FROM " + ident(table) + " s "
            "LEFT JOIN marketing_customer_tags t ON s.cust_ind=t.cust_ind AND s.dt=t.dt "
            "WHERE s.cust_ind IS NOT NULL AND t.cust_ind IS NULL"
        )
        if cursor.fetchone()[0]:
            raise RuntimeError("Nonempty customer identifier has no matching snapshot")


def backup_table(connection, table):
    path = STATE / (table + ".before.sql.gz")
    with connection.cursor() as cursor:
        cursor.execute("SHOW CREATE TABLE " + ident(table))
        ddl = cursor.fetchone()[1]
        cursor.execute("SHOW COLUMNS FROM " + ident(table))
        columns = [r[0] for r in cursor.fetchall()]
    fp = Fingerprint(columns)
    temporary = path.with_suffix(".writing.gz")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=1) as target:
        target.write("SET NAMES utf8mb4;\n" + ddl + ";\n")
        prefix = "INSERT INTO " + ident(table) + " (" + ",".join(map(ident, columns)) + ") VALUES\n"
        with connection.cursor(pymysql.cursors.SSCursor) as cursor:
            cursor.execute("SELECT * FROM " + ident(table))
            while batch := cursor.fetchmany(150):
                values = []
                for row in batch:
                    fp.add(row)
                    values.append("(" + ",".join(connection.escape(v) for v in row) + ")")
                target.write(prefix + ",\n".join(values) + ";\n")
                if fp.count % 3000 == 0:
                    print(f"Backed up {table}: {fp.count:,}", flush=True)
    temporary.replace(path)
    result = dict(
        file=str(path), sha256=digest(path), ddl=ddl, columns=columns, fingerprint=fp.result()
    )
    print(f"Backup complete {table}: {fp.count:,}", flush=True)
    return result


def create_stage(connection, table, specification, records, xlsx_sha):
    fields = specification["fields"]
    keys = (
        ("subject_type", "report_id")
        if specification["label"] == "征信"
        else ("unify_credit_code", "dt")
    )
    definitions, comments = [], []
    for field in fields:
        key = field["key"]
        if field["kind"] == "text":
            sql_type = "VARCHAR(128)" if key in {*keys, "cust_ind"} else "TEXT"
        else:
            sql_type = "DECIMAL(30,8)"
        nullability = "NOT NULL" if key in keys else "NULL"
        definitions.append(f"{ident(key)} {sql_type} {nullability} COMMENT %s")
        comments.append(field["name"] + ("；" + field["unit"] if field["unit"] else ""))
    definitions.extend(
        [
            "PRIMARY KEY (" + ",".join(map(ident, keys)) + ")",
            "INDEX idx_summary_customer (cust_ind)",
        ]
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE "
            + ident(table)
            + " ("
            + ",".join(definitions)
            + ") ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT=%s",
            [*comments, "synthetic-subject-summary-v1;xlsx-sha256=" + xlsx_sha],
        )
        sql = (
            "INSERT INTO "
            + ident(table)
            + " ("
            + ",".join(ident(f["key"]) for f in fields)
            + ") VALUES ("
            + ",".join(["%s"] * len(fields))
            + ")"
        )
        for start in range(0, len(records), 250):
            cursor.executemany(sql, records[start : start + 250])
    connection.commit()
    expected = Fingerprint([f["key"] for f in fields])
    for row in records:
        expected.add(row)
    actual = summary_fingerprint(connection, table, fields)
    if actual != expected.result():
        raise ValueError("Full stage readback differs from Excel")
    print(f"Staged and verified {table}: {actual['rows']:,}", flush=True)
    return actual


def refresh_catalog(connection, prepared):
    """Rebuild only the two affected derived catalog entries under the swap lock."""
    table = "marketing_table_catalog"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM marketing_table_catalog WHERE table_key IN (%s,%s)",
            tuple(CHANGED.values()),
        )
        columns = [d[0] for d in cursor.description]
        records = cursor.fetchall()
    if len(records) != 2:
        raise RuntimeError("Expected two existing catalog entries")
    before = [dict(zip(columns, row, strict=True)) for row in records]
    dump(STATE / "catalog.before.json", before)
    with gzip.open(STATE / "catalog.restore.sql.gz", "wt", encoding="utf-8") as backup:
        backup.write("SET NAMES utf8mb4;\n")
        for row in before:
            assignments = [
                ident(key) + "=" + connection.escape(value)
                for key, value in row.items()
                if key != "id"
            ]
            backup.write(
                "UPDATE "
                + ident(table)
                + " SET "
                + ",".join(assignments)
                + " WHERE id="
                + connection.escape(row["id"])
                + ";\n"
            )
    for label, physical in CHANGED.items():
        spec, _, sha = prepared[label]
        current = next(row for row in before if row["table_key"] == physical)
        if current["admin_managed"] or current["admin_deleted"]:
            raise RuntimeError("Catalog entry has a manual management override")
        fields = spec["fields"]
        descriptions = {
            f["key"]: f["name"] + ("；单位：" + f["unit"] if f["unit"] else "") + "；" + f["rule"]
            for f in fields
        }
        with connection.cursor() as cursor:
            cursor.execute("SHOW FULL COLUMNS FROM " + ident(physical))
            schema = [
                dict(
                    name=r[0],
                    type=r[1],
                    nullable=r[3] == "YES",
                    key=r[4],
                    description=descriptions[r[0]],
                )
                for r in cursor.fetchall()
            ]
        keys = ["subject_type", "report_id"] if label == "征信" else ["unify_credit_code", "dt"]
        dimensions = (
            ["subject_type", "id_number", "report_id"] if label == "征信" else ["unify_credit_code"]
        )
        brief = "\n".join(f"{f['key']}: {descriptions[f['key']]}" for f in fields)
        purpose = (
            spec["grain"]
            + "。已从来源明细汇总；空值表示缺失、不适用或冲突，不当成0。完整明细在本地来源明细文件中。"
        )
        metadata = json.loads(current["source_metadata"])
        metadata.update(
            indexed_at=datetime.now().astimezone().isoformat(),
            column_count=len(fields),
            row_count=len(spec["rows"]),
            data_origin="user_supplied_synthetic_xlsx_subject_summary",
            source_comment="synthetic-subject-summary-v1;xlsx-sha256=" + sha,
            xlsx_sha256=sha,
            source_detail=spec["detail"],
        )

        def serialized(value):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE marketing_table_catalog SET
                symbol_col='cust_ind',time_col='dt',time_dtype='text_ymd',
                key_fields=%s,secondary_dims=%s,purpose=%s,columns_brief=%s,
                table_description=%s,column_descriptions=%s,
                column_descriptions_schema_fingerprint='',embedding=NULL,description_embedding=NULL,
                columns_schema=%s,source_metadata=%s
                WHERE id=%s AND table_key=%s""",
                (
                    serialized(keys),
                    serialized(dimensions),
                    purpose,
                    brief,
                    label + " " + physical + "\n" + purpose + "\n" + brief,
                    serialized(descriptions),
                    serialized(schema),
                    serialized(metadata),
                    current["id"],
                    physical,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Catalog row changed during publication")
    connection.commit()
    print("Updated both field catalogs; invalidated embeddings of obsolete schemas.", flush=True)


def sync():
    if (STATE / "published.json").exists():
        raise RuntimeError(
            "Already published; inspect stored evidence instead of repeating the migration"
        )
    if (STATE / "renamed.json").exists():
        raise RuntimeError("A prior swap needs inspection; refusing to publish again")
    STATE.mkdir(parents=True, exist_ok=True)
    prepared = {label: inputs(label) for label in CHANGED}
    connection = connect()
    locked = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK('bank_project_subject_summary_20260920',0)")
            if cursor.fetchone()[0] != 1:
                raise RuntimeError("Another summary migration is active")
            cursor.execute(
                "SELECT TABLE_NAME,REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
                (DATABASE,),
            )
            if any(a in CHANGED.values() or b in CHANGED.values() for a, b in cursor.fetchall()):
                raise RuntimeError("Changed table has foreign-key dependencies")
            cursor.execute(
                "SELECT EVENT_OBJECT_TABLE FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s",
                (DATABASE,),
            )
            if any(r[0] in CHANGED.values() for r in cursor.fetchall()):
                raise RuntimeError("Changed table has triggers")
            cursor.execute("SHOW FULL TABLES")
            existing = dict(cursor.fetchall())
            if any(existing.get(table) != "BASE TABLE" for table in CHANGED.values()):
                raise RuntimeError("Expected source tables missing")
        backup_file = STATE / "backup.json"
        if backup_file.exists():
            backups = load(backup_file)
            if any(digest(Path(v["file"])) != v["sha256"] for v in backups.values()):
                raise ValueError("Backup bytes changed")
        else:
            with connection.cursor() as cursor:
                cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            backups = {table: backup_table(connection, table) for table in CHANGED.values()}
            connection.rollback()
            dump(backup_file, backups)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stages = {
            table: "__summary_" + stamp + "_" + table.removeprefix("marketing_")
            for table in CHANGED.values()
        }
        previous = {
            table: "__prior_" + stamp + "_" + table.removeprefix("marketing_")
            for table in CHANGED.values()
        }
        staged = {}
        for label, table in CHANGED.items():
            spec, records, sha = prepared[label]
            staged[table] = create_stage(connection, stages[table], spec, records, sha)
        dump(
            STATE / "staged.json",
            dict(stage_tables=stages, previous_tables=previous, fingerprints=staged),
        )
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION lock_wait_timeout=20")
            cursor.execute(
                "LOCK TABLES "
                + ",".join(
                    ident(t) + " WRITE"
                    for t in [*CHANGED.values(), *stages.values(), "marketing_table_catalog"]
                )
            )
            locked = True
        for table, info in backups.items():
            if database_fingerprint(connection, table, info["columns"]) != info["fingerprint"]:
                raise RuntimeError("Source data changed after backup; publication stopped")
        renames = []
        for table in CHANGED.values():
            renames.extend(
                [
                    ident(table) + " TO " + ident(previous[table]),
                    ident(stages[table]) + " TO " + ident(table),
                ]
            )
        with connection.cursor() as cursor:
            cursor.execute("RENAME TABLE " + ",".join(renames))
        dump(STATE / "renamed.json", dict(previous_tables=previous, at=datetime.now().isoformat()))
        refresh_catalog(connection, prepared)
        with connection.cursor() as cursor:
            cursor.execute("UNLOCK TABLES")
            locked = False
        # A separate connection verifies the visible names after the swap.
        fresh = connect()
        try:
            for label, table in CHANGED.items():
                actual = summary_fingerprint(fresh, table, prepared[label][0]["fields"])
                if actual != staged[table]:
                    raise RuntimeError("Published content mismatch; previous tables retained")
                verify_customer_links(fresh, table, prepared[label][0], prepared[label][1])
                with fresh.cursor() as cursor:
                    cursor.execute(
                        "SELECT columns_schema,column_descriptions FROM marketing_table_catalog WHERE table_key=%s",
                        (table,),
                    )
                    schema, descriptions = cursor.fetchone()
                    expected_keys = [f["key"] for f in prepared[label][0]["fields"]]
                    if [f["name"] for f in json.loads(schema)] != expected_keys or set(
                        json.loads(descriptions)
                    ) != set(expected_keys):
                        raise RuntimeError("Published catalog still references obsolete fields")
        finally:
            fresh.close()
        # Only our two replaced tables are removed, after durable SQL backups and readback.
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE " + ",".join(ident(t) for t in previous.values()))
        result = dict(
            database=DATABASE,
            host=HOST,
            source_dataset="desktop",
            at=datetime.now().isoformat(),
            tables={
                table: dict(
                    rows=len(prepared[label][1]),
                    columns=len(prepared[label][0]["fields"]),
                    xlsx_sha256=prepared[label][2],
                    fingerprint=staged[table],
                )
                for label, table in CHANGED.items()
            },
            backup=str(backup_file),
            fresh_connection_readback=True,
            catalog_updated=True,
        )
        dump(STATE / "published.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        if locked:
            with connection.cursor() as cursor:
                cursor.execute("UNLOCK TABLES")
        connection.close()


def verify_unchanged():
    # Existing byte-for-byte Excel readback and publication fingerprints provide
    # the reference; current database values are streamed again in full.
    os.environ["MARKETING_REFRESH_PASSWORD"] = credentials()["password"]
    from .database import connection as compressed_connection

    connection = compressed_connection(compressed=True)
    baseline = load(OUTPUT / "database_staged.json")
    result = {}
    try:
        for label, table in TABLES.items():
            if label in CHANGED:
                continue
            source = load(OUTPUT / "import_rows" / (table + ".verified.json"))
            if digest(BASES["desktop"] / (label + ".xlsx")) != source["xlsx_sha256"]:
                raise ValueError("Unchanged workbook no longer matches baseline")
            actual = database_fingerprint(connection, table, source["columns"])
            if actual != baseline[table]["fingerprint"]:
                raise ValueError(
                    "Existing database content differs from current desktop data: " + table
                )
            result[table] = dict(xlsx_sha256=source["xlsx_sha256"], fingerprint=actual)
            print("Unchanged table fully verified:", table, actual["rows"], flush=True)
    finally:
        connection.close()
    dump(STATE / "unchanged_verified.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "sync", "verify-unchanged"])
    action = parser.parse_args().action
    {"inspect": inspect, "sync": sync, "verify-unchanged": verify_unchanged}[action]()

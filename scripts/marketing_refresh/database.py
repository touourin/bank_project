"""Backup and explicitly publish the verified desktop workbook dataset.

Credentials come from environment variables or a terminal prompt, never files.
The backup includes every existing table, including the five application tables
the user explicitly requested to remove.
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path

import pymysql

from .common import DESKTOP, OUTPUT, TABLES, dump, load, rows


def connection(compressed=False):
    password = os.environ.get("MARKETING_REFRESH_PASSWORD")
    if not password:
        import getpass

        password = getpass.getpass("MySQL password: ")
    if compressed:
        import sys

        sys.path.insert(0, str(OUTPUT / "mysql_client"))
        import mysql.connector

        raw = mysql.connector.connect(
            host="192.168.130.250",
            port=30306,
            user="shanghai_app",
            password=password,
            database="shanghai_proj",
            charset="utf8mb4",
            connection_timeout=10,
            read_timeout=300,
            write_timeout=300,
            autocommit=False,
            compress=True,
            allow_local_infile=False,
        )

        class Adapter:
            def cursor(self, kind=None):
                return raw.cursor(buffered=kind is None)

            def commit(self):
                return raw.commit()

            def close(self):
                return raw.close()

        return Adapter()
    return pymysql.connect(
        host="192.168.130.250",
        port=30306,
        user="shanghai_app",
        password=password,
        database="shanghai_proj",
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=300,
        write_timeout=300,
        autocommit=False,
    )


def ident(name):
    if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
        raise ValueError("Unsafe identifier")
    return "`" + name + "`"


def backup():
    marker = OUTPUT / "backup.json"
    if marker.exists():
        print("Existing completed backup:", load(marker)["directory"], flush=True)
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path.home() / "Desktop" / ("五张表_更新前备份_" + stamp)
    dest.mkdir()
    shutil.copytree(DESKTOP, dest / "五张表_修正版")
    c = connection()
    meta = {"directory": str(dest), "tables": {}, "created_at": stamp}
    try:
        with c.cursor() as q:
            q.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
            names = [r[0] for r in q.fetchall()]
            q.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        for name in names:
            with c.cursor() as q:
                q.execute("SHOW CREATE TABLE " + ident(name))
                ddl = q.fetchone()[1]
                q.execute("SELECT COUNT(*) FROM " + ident(name))
                expected = q.fetchone()[0]
            path = dest / (name + ".sql.gz")
            count = 0
            with gzip.open(path, "wt", encoding="utf-8", compresslevel=1) as f:
                f.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n" + ddl + ";\n")
                with c.cursor(pymysql.cursors.SSCursor) as q:
                    q.execute("SELECT * FROM " + ident(name))
                    columns = [d[0] for d in q.description]
                    prefix = (
                        "INSERT INTO "
                        + ident(name)
                        + " ("
                        + ",".join(ident(k) for k in columns)
                        + ") VALUES\n"
                    )
                    while batch := q.fetchmany(300):
                        values = []
                        for row in batch:
                            values.append(
                                "("
                                + ",".join(
                                    "X'" + v.hex() + "'" if isinstance(v, bytes) else c.escape(v)
                                    for v in row
                                )
                                + ")"
                            )
                        f.write(prefix + ",\n".join(values) + ";\n")
                        count += len(batch)
                        if count % 30000 == 0:
                            print(f"Backup {name}: {count:,}/{expected:,}", flush=True)
                f.write("SET FOREIGN_KEY_CHECKS=1;\n")
            if count != expected:
                raise ValueError(f"Backup count mismatch: {name}")
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            meta["tables"][name] = {
                "rows": count,
                "ddl": ddl,
                "columns": columns,
                "file": str(path),
                "sha256": digest,
            }
            print(f"Backed up {name}: {count:,}", flush=True)
        c.rollback()
    finally:
        c.close()
    dump(marker, meta)
    dump(dest / "backup_manifest.json", meta)
    (dest / "恢复说明.txt").write_text(
        "这里保存更新前五份原始 Excel 和 shanghai_proj 全部表的 SQL 压缩备份。\n恢复到空库时解压并按 SQL 执行；文件没有数据库密码。\n如需原位恢复，先确认当前表已另行备份，避免覆盖后续写入。\n",
        encoding="utf-8",
    )
    print("Backup complete:", dest, flush=True)


class Fingerprint:
    """Order-independent SHA-256 multiset digest, including NULL positions."""

    def __init__(self, columns):
        self.columns = columns
        self.count = 0
        self.total = 0
        self.xor = 0

    def add(self, values):
        encoded = json.dumps(
            [[i, v] for i, v in enumerate(values) if v is not None],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        digest = int.from_bytes(hashlib.sha256(encoded).digest(), "big")
        self.total = (self.total + digest) % (1 << 256)
        self.xor ^= digest
        self.count += 1

    def result(self):
        return {
            "rows": self.count,
            "sum_sha256": f"{self.total:064x}",
            "xor_sha256": f"{self.xor:064x}",
        }


def table_names(c):
    with c.cursor() as q:
        q.execute("SHOW FULL TABLES")
        return {name: kind for name, kind in q.fetchall()}


def source_name(table):
    return table.replace("marketing_", "marketing_mock_", 1)


def stage_name(table):
    return "__refresh_20260920_" + table.removeprefix("marketing_")


def before_name(table):
    return "__before_20260920_" + table.removeprefix("marketing_")


def verify_prerequisites():
    if load(OUTPUT / "validation.json")["error_count"]:
        raise ValueError("Offline reconciliation has not passed")
    installed = load(OUTPUT / "desktop_installed.json")
    for label, table in TABLES.items():
        verified = load(OUTPUT / "import_rows" / (table + ".verified.json"))
        with (DESKTOP / (label + ".xlsx")).open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()
        if actual != installed[label]["sha256"] or actual != verified["xlsx_sha256"]:
            raise ValueError("Desktop workbook differs from import: " + label)
    return installed


def check_original_tables(c):
    meta = load(OUTPUT / "backup.json")
    names = table_names(c)
    for name, info in meta["tables"].items():
        if names.get(name) != "BASE TABLE":
            raise ValueError("Original table changed: " + name)
        if name in {source_name(t) for t in TABLES.values()}:
            with c.cursor() as q:
                q.execute("SELECT COUNT(*) FROM " + ident(name))
                if q.fetchone()[0] != info["rows"]:
                    raise ValueError("Business data changed since backup: " + name)
    expected = set(meta["tables"]) | {stage_name(t) for t in TABLES.values()}
    if set(names) - expected:
        raise ValueError("Unexpected tables appeared: " + repr(set(names) - expected))


def refresh_auxiliary_backup(c, names):
    """Caller holds WRITE locks through backup, rename, and deletion."""
    dest = Path(load(OUTPUT / "backup.json")["directory"])
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {}
    for name in sorted(names):
        with c.cursor() as q:
            q.execute("SHOW CREATE TABLE " + ident(name))
            ddl = q.fetchone()[1]
            q.execute("SELECT * FROM " + ident(name))
            columns = [d[0] for d in q.description]
            records = q.fetchall()
        path = dest / (stamp + "_删除前最新_" + name + ".sql.gz")
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n" + ddl + ";\n")
            prefix = (
                "INSERT INTO "
                + ident(name)
                + " ("
                + ",".join(ident(k) for k in columns)
                + ") VALUES "
            )
            for record in records:
                values = ",".join(
                    "X'" + v.hex() + "'" if isinstance(v, bytes) else c.escape(v) for v in record
                )
                f.write(prefix + "(" + values + ");\n")
            f.write("SET FOREIGN_KEY_CHECKS=1;\n")
        with path.open("rb") as f:
            sha = hashlib.file_digest(f, "sha256").hexdigest()
        result[name] = {"rows": len(records), "file": str(path), "sha256": sha}
    dump(dest / (stamp + "_删除前最新辅助表清单.json"), result)
    dump(OUTPUT / "latest_auxiliary_backup.json", result)
    (dest / "恢复说明.txt").write_text(
        "本目录包含两个时间点的备份，请勿把全部 SQL 文件不加区分地重复执行。\n"
        "1. 原始完整快照：原文件名 marketing_*.sql.gz，对应 backup_manifest.json。\n"
        "2. 删除前最新辅助表：带时间戳和“删除前最新”的 SQL 文件，对应最新的“删除前最新辅助表清单.json”。\n"
        "3. 恢复删除前状态时，五张业务表选原始 marketing_mock_*.sql.gz；辅助表选最新清单中的文件。\n"
        "4. 五张表_修正版/ 及 五张表_修正版.zip 是更新前的原始 Excel。\n"
        "SQL 应恢复至空库，或先确认目标表已有独立备份；不要覆盖后续写入。文件不包含数据库密码。\n",
        encoding="utf-8",
    )
    print(
        "Latest auxiliary data backed up while writes are locked:",
        {k: v["rows"] for k, v in result.items()},
        flush=True,
    )


def database_fingerprint(c, name, columns):
    result = Fingerprint(columns)
    with c.cursor(pymysql.cursors.SSCursor) as q:
        q.execute("SELECT " + ",".join(ident(k) for k in columns) + " FROM " + ident(name))
        while batch := q.fetchmany(1000):
            for row in batch:
                result.add(row)
            if result.count % 100000 == 0:
                print(f"Database readback {name}: {result.count:,}", flush=True)
    return result.result()


def stage():
    verify_prerequisites()
    manifest = load(OUTPUT / "manifest.json")
    marker = OUTPUT / "database_staged.json"
    completed = load(marker) if marker.exists() else {}
    c = connection(compressed=True)
    try:
        check_original_tables(c)
        for label, table in TABLES.items():
            target = stage_name(table)
            columns = manifest["tables"][label]["columns"]
            verified = load(OUTPUT / "import_rows" / (table + ".verified.json"))
            if (
                target in table_names(c)
                and completed.get(table, {}).get("xlsx_sha256") == verified["xlsx_sha256"]
            ):
                actual = database_fingerprint(c, target, columns)
                if actual == completed[table]["fingerprint"]:
                    print("Verified completed staging:", table, flush=True)
                    continue
            with c.cursor() as q:
                q.execute("DROP TABLE IF EXISTS " + ident(target))
                q.execute("CREATE TABLE " + ident(target) + " LIKE " + ident(source_name(table)))
                q.execute(
                    "ALTER TABLE " + ident(target) + " COMMENT=%s",
                    (
                        "synthetic-validation-v2:"
                        + verified["xlsx_sha256"]
                        + ";"
                        + label
                        + ".xlsx;asof=20260917",
                    ),
                )
                q.execute("SHOW COLUMNS FROM " + ident(target))
                if [r[0] for r in q.fetchall()] != columns:
                    raise ValueError("Schema mismatch " + label)
            expected = Fingerprint(columns)
            content_digest = hashlib.sha256()
            grouped = defaultdict(list)
            batch_size = 0

            def flush(grouped=grouped, columns=columns, target=target):
                nonlocal batch_size
                with c.cursor() as q:
                    q.max_stmt_length = 4 * 1024 * 1024
                    for records in grouped.values():
                        present = set().union(*(row.keys() for row in records))
                        keys = tuple(k for k in columns if k in present)
                        values = [tuple(row.get(k) for k in keys) for row in records]
                        sql = (
                            "INSERT INTO "
                            + ident(target)
                            + " ("
                            + ",".join(ident(k) for k in keys)
                            + ") VALUES ("
                            + ",".join(["%s"] * len(keys))
                            + ")"
                        )
                        q.executemany(sql, values)
                c.commit()
                grouped.clear()
                batch_size = 0

            for row in rows(OUTPUT / "import_rows" / (table + ".jsonl.gz")):
                content_digest.update(
                    (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
                )
                values = [row.get(k) for k in columns]
                expected.add(values)
                # One bulk statement per source table and batch, including
                # explicit NULLs for optional cells. Grouping by each nullable
                # shape caused thousands of one-row network round trips.
                grouped[row.get("source_table", table)].append(row)
                batch_size += 1
                if batch_size >= 1000:
                    flush()
                interval = 25000 if verified["rows"] > 25000 else 5000
                if expected.count % interval == 0:
                    print(f"Imported {table}: {expected.count:,}/{verified['rows']:,}", flush=True)
            if batch_size:
                flush()
            if expected.count != verified["rows"]:
                raise ValueError("Import file count mismatch")
            if content_digest.hexdigest() != verified["content_sha256"]:
                raise ValueError("Import file changed after Excel readback")
            actual = database_fingerprint(c, target, columns)
            if actual != expected.result():
                raise ValueError("Database full-content readback mismatch: " + table)
            completed[table] = {
                "label": label,
                "stage": target,
                "xlsx_sha256": verified["xlsx_sha256"],
                "fingerprint": actual,
            }
            dump(marker, completed)
            print(f"Staged and fully verified {table}: {actual['rows']:,}", flush=True)
    finally:
        c.close()


def publish():
    verify_prerequisites()
    staged = load(OUTPUT / "database_staged.json")
    if set(staged) != set(TABLES.values()):
        raise ValueError("All five tables must be staged")
    c = connection()
    try:
        names = table_names(c)
        final = set(TABLES.values())
        backup_names = {before_name(t) for t in final}
        auxiliary = set(load(OUTPUT / "backup.json")["tables"]) - {source_name(t) for t in final}
        allowed = (
            final
            | backup_names
            | auxiliary
            | {source_name(t) for t in final}
            | {stage_name(t) for t in final}
        )
        if set(names) - allowed or any(kind != "BASE TABLE" for kind in names.values()):
            raise ValueError("Unexpected object before publication")
        # Briefly prevent the active application from writing more auxiliary
        # rows after its final backup. All source/staging tables are included so
        # RENAME TABLE and foreign-key-safe deletion occur under WRITE locks.
        with c.cursor() as q:
            q.execute("SET SESSION lock_wait_timeout=20")
            q.execute("LOCK TABLES " + ",".join(ident(n) + " WRITE" for n in sorted(names)))
        refresh_auxiliary_backup(c, auxiliary & set(names))
        if not final.issubset(names):
            check_original_tables(c)
            parts = []
            for table in TABLES.values():
                with c.cursor() as q:
                    q.execute("SELECT COUNT(*) FROM " + ident(stage_name(table)))
                    if q.fetchone()[0] != staged[table]["fingerprint"]["rows"]:
                        raise ValueError("Staged count changed")
                parts.extend(
                    [
                        ident(source_name(table)) + " TO " + ident(before_name(table)),
                        ident(stage_name(table)) + " TO " + ident(table),
                    ]
                )
            with c.cursor() as q:
                q.execute("RENAME TABLE " + ",".join(parts))
            dump(
                OUTPUT / "database_renamed.json",
                {"tables": list(final), "at": datetime.datetime.now().isoformat()},
            )
        names = table_names(c)
        if set(names) - final - backup_names - auxiliary:
            raise ValueError("Unexpected table after rename")
        # The explicit user request is to keep exactly five business tables.
        # Child tables are dropped first so foreign-key checks remain enabled.
        drop_order = [
            "marketing_memory_chunks",
            "marketing_session_memory",
            "marketing_question_scores",
            "marketing_table_catalog",
            "marketing_memory_sessions",
        ] + sorted(backup_names)
        deleted = []
        with c.cursor() as q:
            for name in drop_order:
                if name in names:
                    q.execute("DROP TABLE " + ident(name))
                    deleted.append(name)
        if set(table_names(c)) != final:
            raise ValueError("Database does not contain exactly five tables")
        counts = {}
        with c.cursor() as q:
            for table in TABLES.values():
                q.execute("SELECT COUNT(*) FROM " + ident(table))
                counts[table] = q.fetchone()[0]
                if counts[table] != staged[table]["fingerprint"]["rows"]:
                    raise ValueError("Published row count mismatch")
        result = {
            "database": "shanghai_proj",
            "tables": counts,
            "deleted": deleted,
            "published_at": datetime.datetime.now().isoformat(),
            "full_content_verified_before_rename": True,
        }
        dump(OUTPUT / "database_published.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        try:
            with c.cursor() as q:
                q.execute("UNLOCK TABLES")
        finally:
            c.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "stage", "publish"])
    args = parser.parse_args()
    {"backup": backup, "stage": stage, "publish": publish}[args.action]()

"""Exact, disk-backed composite keys; hashes narrow searches, bytes decide equality."""

import hashlib
import json

from bank_project.alignment.models import AlignmentError


def encoded_key(values):
    if any(value is None or value == "" for value in values):
        return None
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()


class KeyIndex:
    def __init__(self, store):
        self.store, self.database = store, store.database
        with self.database.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS staging_key_sets (
                id CHAR(64) CHARACTER SET ascii PRIMARY KEY, ready BOOLEAN NOT NULL DEFAULT FALSE)
                ENGINE=InnoDB""")
            db.execute("""CREATE TABLE IF NOT EXISTS staging_keys (
                key_set CHAR(64) CHARACTER SET ascii NOT NULL, position BIGINT NOT NULL,
                digest BINARY(32) NOT NULL, value MEDIUMBLOB NOT NULL,
                PRIMARY KEY(key_set,position), INDEX key_lookup(key_set,digest),
                FOREIGN KEY(key_set) REFERENCES staging_key_sets(id) ON DELETE CASCADE) ENGINE=InnoDB""")

    def ensure(self, source, columns):
        names = [c.name for c in source.table.columns]
        if not columns or len(set(columns)) != len(columns) or not set(columns) <= set(names):
            raise AlignmentError("关联字段缺失或重复")
        positions = [names.index(name) for name in columns]
        key_set = hashlib.sha256(encoded_key([source.table.id, *columns])).hexdigest()
        with self.database.connect() as db:
            db.execute("INSERT IGNORE INTO staging_key_sets(id) VALUES(%s)", (key_set,))
            db.execute("SELECT ready FROM staging_key_sets WHERE id=%s", (key_set,))
            if db.fetchone()[0]:
                return key_set
        buffer, size = [], 0

        def flush():
            nonlocal buffer, size
            if buffer:
                with self.database.connect() as db:
                    db.executemany(
                        "INSERT INTO staging_keys VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE digest=VALUES(digest),value=VALUES(value)",
                        buffer,
                    )
                buffer, size = [], 0

        for position, row in self.store.iter_rows(source.table.id):
            value = encoded_key([row.values[i] for i in positions])
            if value is None:
                continue
            if buffer and (len(buffer) >= 500 or size + len(value) > 1024 * 1024):
                flush()
            buffer.append((key_set, position, hashlib.sha256(value).digest(), value))
            size += len(value)
        flush()
        with self.database.connect() as db:
            db.execute("UPDATE staging_key_sets SET ready=TRUE WHERE id=%s", (key_set,))
        return key_set

    def stats(self, source, target, left, right):
        a, b = self.ensure(source, left), self.ensure(target, right)
        with self.database.connect() as db:
            # Equality is binary, including case, spaces, empty-vs-null and leading zeroes.
            db.execute(
                """SELECT 1 FROM staging_keys a JOIN staging_keys b
                ON b.key_set=a.key_set AND b.digest=a.digest AND b.value=a.value AND b.position>a.position
                WHERE a.key_set=%s LIMIT 1""",
                (b,),
            )
            if db.fetchone():
                return True, 0, 0
            db.execute(
                """SELECT COUNT(*),COALESCE(SUM(b.position IS NOT NULL),0)
                FROM staging_keys a LEFT JOIN staging_keys b
                ON b.key_set=%s AND b.digest=a.digest AND b.value=a.value WHERE a.key_set=%s""",
                (b, a),
            )
            total, matched = db.fetchone()
        return False, int(matched), int(total - matched)

    def pairs(self, source, target, left, right):
        a, b = self.ensure(source, left), self.ensure(target, right)
        position = -1
        while True:
            with self.database.connect() as db:
                db.execute(
                    """SELECT a.position,b.position FROM staging_keys a JOIN staging_keys b
                    ON b.key_set=%s AND b.digest=a.digest AND b.value=a.value
                    WHERE a.key_set=%s AND a.position>%s ORDER BY a.position LIMIT 1000""",
                    (b, a, position),
                )
                rows = db.fetchall()
            if not rows:
                return
            for position, target_position in rows:
                yield position, target_position

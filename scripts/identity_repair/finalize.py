"""Record verified revision metadata after workbook and source-table installation."""

import json
import shutil

from .policy import REVISION
from .state import BASES, ROOT, STATE, policy, save, sha


def run():
    server = json.loads((STATE / "server/verified.json").read_text())
    for version, base in BASES.items():
        files = json.loads((STATE / version / "verified.json").read_text())
        for name, metadata in files.items():
            assert sha(base / name) == metadata["sha256"], "Installed workbook differs"
        plan = policy(version).export()
        revision = dict(
            revision=REVISION,
            synthetic=True,
            coverage=plan["coverage"],
            files=files,
            historical_import_batches_modified=False,
            source_database={
                "host": "192.168.130.250",
                "database": "shanghai_proj",
                "dataset": "desktop",
                "verified_rows": {
                    label: table["fingerprint"]["rows"] for label, table in server.items()
                },
            },
        )
        save(base / "identity-relations.json", plan)
        if version == "project":
            path = base / "manifest.json"
            backup = STATE / version / "manifest.before.json"
            if not backup.exists():
                shutil.copy2(path, backup)
            manifest = json.loads(path.read_text())
            manifest["identity_relation_revision"] = revision
            manifest["files"]["CCM_C_CUST_FLAG_INFO"]["sha256"] = sha(
                base / "CCM_C_CUST_FLAG_INFO.csv"
            )
            save(path, manifest)
        else:
            save(base / "identity-repair-manifest.json", revision)
            shutil.copy2(ROOT / "scripts/identity_repair/README.md", base / "人员关系修订说明.md")
        save(STATE / version / "installed.json", revision)
        print("Recorded installed revision", version, flush=True)


if __name__ == "__main__":
    run()

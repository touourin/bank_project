"""Provision only the application staging schema/account; keep source credentials separate."""

import secrets
import subprocess
from pathlib import Path

import pymysql
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
env_path = root / ".env"
values = dotenv_values(env_path)
user = "bank_staging"
password = values.get("BANK_STAGING_MYSQL_PASSWORD") or secrets.token_urlsafe(36)
# Generated credential uses URL-safe characters; user-supplied credentials go through SQL quoting.
quoted = pymysql.converters.escape_string(password)
sql = f"""CREATE DATABASE IF NOT EXISTS bank_staging CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{quoted}';
ALTER USER '{user}'@'%' IDENTIFIED BY '{quoted}';
GRANT ALL PRIVILEGES ON bank_staging.* TO '{user}'@'%';
"""
result = subprocess.run(
    [
        "docker",
        "compose",
        "exec",
        "-T",
        "mysql",
        "sh",
        "-c",
        'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql -uroot',
    ],
    input=sql,
    text=True,
    capture_output=True,
    cwd=root,
)
if result.returncode:
    raise SystemExit("暂存账号初始化失败，请检查本地 MySQL；未输出任何凭据。")
updates = {
    "BANK_STAGING_BACKEND": "mysql",
    "BANK_STAGING_MYSQL_HOST": "127.0.0.1",
    "BANK_STAGING_MYSQL_PORT": "3306",
    "BANK_STAGING_MYSQL_DATABASE": "bank_staging",
    "BANK_STAGING_MYSQL_USER": user,
    "BANK_STAGING_MYSQL_PASSWORD": password,
    "BANK_INTAKE_MAX_UPLOAD_MB": "512",
    "BANK_INTAKE_MAX_EXPANDED_MB": "8192",
    "BANK_INTAKE_MAX_ROWS": "2000000",
    "BANK_INTAKE_MAX_CELLS": "200000000",
}
if values.get("BANK_STAGING_MYSQL_PASSWORD"):
    for key in updates:
        if key.startswith("BANK_INTAKE_") and values.get(key):
            updates[key] = values[key]
lines = env_path.read_text().splitlines() if env_path.exists() else []
lines = [line for line in lines if line.split("=", 1)[0] not in updates]
lines += ["", "# Internal staging database (separate from source MySQL credentials)."]
lines += [f"{key}={value}" for key, value in updates.items()]
env_path.write_text("\n".join(lines) + "\n")
env_path.chmod(0o600)
print("Dedicated staging schema and account configured. No source data changed.")

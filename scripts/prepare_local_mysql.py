"""Create credentials for the project's local development database without overwriting them."""

import os
import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def prepare(root: Path) -> None:
    env = root / ".env"
    values = {**dotenv_values(env), **os.environ}
    if values.get("BANK_MYSQL_HOST", "127.0.0.1") not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("当前配置指向外部 MySQL；请直接运行 load_mock_mysql.py，不要启动本地库")
    if values.get("BANK_MYSQL_USER", "bank_app") == "root":
        raise ValueError("本地应用数据库账号不能使用 root")
    directory = root / "data/mysql"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    password_file = directory / "root-password"
    try:
        fd = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if password_file.is_symlink() or not password_file.read_text().strip():
            raise ValueError("root 密码文件无效，拒绝覆盖") from None
    else:
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(36))
    defaults = {
        "BANK_MYSQL_HOST": "127.0.0.1",
        "BANK_MYSQL_PORT": "3306",
        "BANK_MYSQL_DATABASE": "bank_project",
        "BANK_MYSQL_USER": "bank_app",
        "BANK_MYSQL_PASSWORD": secrets.token_urlsafe(36),
        "BANK_MYSQL_DOCKER_HOST": "mysql",
        "BANK_MYSQL_DOCKER_PORT": "3306",
    }
    if env.is_symlink():
        raise ValueError(".env 不能是符号链接")
    for key, value in defaults.items():
        if not values.get(key):
            set_key(env, key, value)
    env.chmod(0o600)


if __name__ == "__main__":
    prepare(ROOT)
    print("本地 MySQL 配置已准备好；密码保存在 .env 和 data/mysql/root-password，不输出到终端。")

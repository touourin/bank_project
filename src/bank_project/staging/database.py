"""Short-lived connections to the dedicated internal MySQL schema."""

from contextlib import contextmanager

import pymysql


class StagingDatabase:
    def __init__(self, settings):
        self.settings = settings

    @contextmanager
    def connect(self):
        s = self.settings
        connection = pymysql.connect(
            host=s.staging_mysql_host,
            port=s.staging_mysql_port,
            user=s.staging_mysql_user,
            password=s.staging_mysql_password.get_secret_value(),
            database=s.staging_mysql_database,
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=10,
            read_timeout=120,
            write_timeout=120,
        )
        try:
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

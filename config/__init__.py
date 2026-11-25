# MySQL via PyMySQL: must run before Django loads its MySQL backend. Harmless if PyMySQL isn't installed (SQLite).
try:
    import pymysql

    pymysql.version_info = (1, 4, 6)   # satisfies Django's minimum mysqlclient version check
    pymysql.install_as_MySQLdb()
except ImportError:
    pass

from .celery import app as celery_app

__all__ = ("celery_app",)

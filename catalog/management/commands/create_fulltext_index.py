from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "MySQL only: add the FULLTEXT index on catalog_work.search_text (idempotent)."

    def handle(self, *args, **opts):
        if connection.vendor != "mysql":
            self.stdout.write("Not MySQL — nothing to do (the 'basic' search backend works everywhere).")
            return
        with connection.cursor() as c:
            c.execute("SHOW INDEX FROM catalog_work WHERE Key_name = 'work_search_ft'")
            if c.fetchone():
                self.stdout.write("Index already exists.")
                return
            c.execute("ALTER TABLE catalog_work ADD FULLTEXT INDEX work_search_ft (search_text)")
        self.stdout.write(self.style.SUCCESS("FULLTEXT index created. Set SEARCH_BACKEND=mysql_fulltext."))

from django.core.management.base import BaseCommand

from circulation import services


class Command(BaseCommand):
    help = "Run the scheduled jobs once (for cron on hosts without Celery beat)."

    def handle(self, *args, **opts):
        self.stdout.write(f"fines assessed: {services.assess_all_fines()}")
        self.stdout.write(f"holds expired: {services.expire_holds()}")
        self.stdout.write(f"reminders sent: {services.send_due_reminders()}")
        self.stdout.write(f"loans anonymised: {services.anonymize_history()}")

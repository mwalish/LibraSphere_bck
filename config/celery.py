import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("librasphere")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "assess-fines": {"task": "circulation.tasks.assess_fines", "schedule": crontab(hour=0, minute=5)},
    "expire-holds": {"task": "circulation.tasks.expire_holds", "schedule": crontab(minute="*/30")},
    "due-reminders": {"task": "circulation.tasks.send_due_reminders", "schedule": crontab(hour=8, minute=0)},
    "anonymize-history": {"task": "circulation.tasks.anonymize_history", "schedule": crontab(hour=2, minute=0)},
}

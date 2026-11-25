from celery import shared_task

from . import services


@shared_task
def assess_fines():
    return services.assess_all_fines()


@shared_task
def expire_holds():
    return services.expire_holds()


@shared_task
def send_due_reminders():
    return services.send_due_reminders()


@shared_task
def anonymize_history():
    return services.anonymize_history()

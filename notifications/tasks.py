from celery import shared_task
from django.core.mail import send_mail
from django.utils import timezone

from .models import Notification
from .services import TEMPLATES


@shared_task
def send_notification_email(notification_id):
    n = Notification.objects.select_related("user").filter(pk=notification_id, emailed_at__isnull=True).first()
    tpl = TEMPLATES.get(n.type) if n else None
    if not n or not tpl:
        return
    send_mail(tpl[0].format(**n.payload), tpl[1].format(**n.payload), None, [n.user.email], fail_silently=True)
    n.emailed_at = timezone.now()
    n.save(update_fields=["emailed_at"])

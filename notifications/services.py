import logging

from django.conf import settings
from django.db import transaction

from .models import Notification

log = logging.getLogger(__name__)

# type -> (email subject, email body). Phase 2 moves these into editable, translated templates.
TEMPLATES = {
    "hold_ready": ("Your hold is ready: {title}",
                   "Good news! \"{title}\" is waiting for you at {branch}. Please collect it by {expires}."),
    "due_soon": ("Due soon: {title}", "\"{title}\" is due on {due}. You can renew it in your account."),
    "membership_approved": ("Welcome to LibraSphere",
                            "Your membership is active. Your member number is {member_no}."),
}


def _deliver(notification_id):
    """Send the email. A failure here (no Redis, SMTP down, ...) is logged and swallowed: the in-app
    notification already exists and the library transaction it belongs to must never fail because of it."""
    from .tasks import send_notification_email
    try:
        if settings.CELERY_TASK_ALWAYS_EAGER:
            send_notification_email(notification_id)        # plain call: no broker involved at all
        else:
            send_notification_email.delay(notification_id)  # queued for a Celery worker
    except Exception:
        log.exception("Could not deliver notification %s", notification_id)


def notify(user, type, **payload):
    """Create the in-app notification now; deliver the email after the surrounding transaction commits."""
    payload = {k: str(v) for k, v in payload.items()}
    n = Notification.objects.create(user=user, type=type, payload=payload)
    transaction.on_commit(lambda: _deliver(n.id), robust=True)
    return n

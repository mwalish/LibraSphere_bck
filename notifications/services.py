from django.db import transaction

from .models import Notification

# type -> (email subject, email body). Phase 2 moves these into editable, translated templates.
TEMPLATES = {
    "hold_ready": ("Your hold is ready: {title}",
                   "Good news! \"{title}\" is waiting for you at {branch}. Please collect it by {expires}."),
    "due_soon": ("Due soon: {title}", "\"{title}\" is due on {due}. You can renew it in your account."),
    "membership_approved": ("Welcome to LibraSphere",
                            "Your membership is active. Your member number is {member_no}."),
}


def notify(user, type, **payload):
    """Create the in-app notification now; deliver the email after the surrounding transaction commits."""
    payload = {k: str(v) for k, v in payload.items()}
    n = Notification.objects.create(user=user, type=type, payload=payload)
    from .tasks import send_notification_email
    transaction.on_commit(lambda: send_notification_email.delay(n.id))
    return n

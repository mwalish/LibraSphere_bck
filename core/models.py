from django.conf import settings
from django.db import models


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    branch_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    meta = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]


def audit(actor, action, target=None, branch_id=None, **meta):
    """Record a staff action. Cheap and append-only."""
    AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None, action=action,
        target_type=type(target).__name__ if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        branch_id=branch_id, meta={k: str(v) for k, v in meta.items()},
    )

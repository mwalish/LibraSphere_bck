from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStamped


class Policy(models.Model):
    """Circulation rules as data. null branch / tier / resource_type = 'applies to all'.
    The most specific matching row wins (see services.resolve_policy)."""
    branch = models.ForeignKey("branches.Branch", null=True, blank=True, on_delete=models.CASCADE)
    tier = models.ForeignKey("accounts.MembershipTier", null=True, blank=True, on_delete=models.CASCADE)
    resource_type = models.ForeignKey("catalog.ResourceType", null=True, blank=True, on_delete=models.CASCADE)
    loan_days = models.PositiveSmallIntegerField(default=14)
    max_renewals = models.PositiveSmallIntegerField(default=2)
    renew_days = models.PositiveSmallIntegerField(default=14)
    fine_per_day = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("10.00"))
    fine_cap = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("500.00"))
    grace_days = models.PositiveSmallIntegerField(default=0)
    hold_pickup_days = models.PositiveSmallIntegerField(default=3)

    class Meta:
        verbose_name_plural = "policies"
        constraints = [models.UniqueConstraint(fields=["branch", "tier", "resource_type"], name="unique_policy_scope")]


class Loan(models.Model):
    item = models.ForeignKey("catalog.Item", on_delete=models.PROTECT, related_name="loans")
    # null after history anonymisation (members who did not opt in to keep history)
    member = models.ForeignKey("accounts.Member", null=True, on_delete=models.SET_NULL, related_name="loans")
    branch = models.ForeignKey("branches.Branch", on_delete=models.PROTECT, related_name="loans")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    issued_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField()
    returned_at = models.DateTimeField(null=True, blank=True)
    returned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    renewals_count = models.PositiveSmallIntegerField(default=0)
    # == item id while the loan is open, NULL once returned. UNIQUE => one open loan per copy, enforced by
    # the database even on MySQL (which has no partial unique indexes; NULLs don't collide).
    active_key = models.BigIntegerField(null=True, blank=True, unique=True)

    class Meta:
        ordering = ["-issued_at"]
        indexes = [models.Index(fields=["member", "returned_at"]), models.Index(fields=["due_at"])]

    @property
    def is_overdue(self):
        return self.returned_at is None and self.due_at < timezone.now()


class LoanEvent(models.Model):
    class Type(models.TextChoices):
        ISSUE = "issue"
        RENEW = "renew"
        RETURN = "return"
        LOST = "lost"
        REMINDER = "reminder"

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="events")
    type = models.CharField(max_length=10, choices=Type.choices)
    at = models.DateTimeField(default=timezone.now)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["at", "id"]


class Hold(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        READY = "ready"
        FULFILLED = "fulfilled"
        EXPIRED = "expired"
        CANCELLED = "cancelled"

    member = models.ForeignKey("accounts.Member", on_delete=models.CASCADE, related_name="holds")
    work = models.ForeignKey("catalog.Work", on_delete=models.CASCADE, related_name="holds")
    pickup_branch = models.ForeignKey("branches.Branch", on_delete=models.PROTECT, related_name="holds")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    placed_at = models.DateTimeField(default=timezone.now)
    ready_item = models.ForeignKey("catalog.Item", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    # "<member>:<work>" while queued/ready, NULL afterwards: one live hold per member per title.
    active_key = models.CharField(max_length=40, null=True, blank=True, unique=True)

    class Meta:
        ordering = ["placed_at", "id"]
        indexes = [models.Index(fields=["work", "status", "placed_at"])]

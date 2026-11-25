from django.conf import settings
from django.db import models


class Fine(models.Model):
    class Reason(models.TextChoices):
        OVERDUE = "overdue"
        LOST = "lost"
        DAMAGED = "damaged"
        OTHER = "other"

    class Status(models.TextChoices):
        OPEN = "open"
        PAID = "paid"
        WAIVED = "waived"

    member = models.ForeignKey("accounts.Member", on_delete=models.CASCADE, related_name="fines")
    loan = models.ForeignKey("circulation.Loan", null=True, blank=True, on_delete=models.SET_NULL, related_name="fines")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reason = models.CharField(max_length=10, choices=Reason.choices)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN, db_index=True)
    assessed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["assessed_at"]
        constraints = [models.UniqueConstraint(fields=["loan", "reason"], name="one_fine_per_loan_reason")]

    @property
    def balance(self):
        return self.amount - self.amount_paid


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash"
        MPESA = "mpesa"
        CARD = "card"

    member = models.ForeignKey("accounts.Member", on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=6, choices=Method.choices, default=Method.CASH)
    reference = models.CharField(max_length=60, blank=True)
    taken_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class FineAllocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    fine = models.ForeignKey(Fine, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=10, decimal_places=2)


class Waiver(models.Model):
    fine = models.OneToOneField(Fine, on_delete=models.CASCADE, related_name="waiver")
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.CharField(max_length=200)
    at = models.DateTimeField(auto_now_add=True)

from django.db import models

from core.models import TimeStamped


class Library(models.Model):
    """The consortium. Branches of one Library can lend to each other."""
    name = models.CharField(max_length=150)
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name_plural = "libraries"

    def __str__(self):
        return self.name


class Branch(TimeStamped):
    library = models.ForeignKey(Library, on_delete=models.PROTECT, related_name="branches")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=12, unique=True)
    address = models.CharField(max_length=255, blank=True)
    timezone = models.CharField(max_length=64, default="Africa/Nairobi")
    opening_hours = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class BranchClosure(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="closures")
    date = models.DateField()
    reason = models.CharField(max_length=150, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["branch", "date"], name="one_closure_per_day")]
        ordering = ["date"]

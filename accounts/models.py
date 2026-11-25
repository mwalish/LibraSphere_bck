import secrets

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from core.models import TimeStamped


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _make(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._make(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self._make(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    locale = models.CharField(max_length=8, default="en")
    accessibility_prefs = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # Django admin site access only
    date_joined = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    def __str__(self):
        return self.email


class BranchRole(models.Model):
    class Role(models.TextChoices):
        MANAGER = "branch_manager", "Branch manager"
        LIBRARIAN = "librarian", "Librarian"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="branch_roles")
    branch = models.ForeignKey("branches.Branch", on_delete=models.CASCADE, related_name="staff_roles")
    role = models.CharField(max_length=20, choices=Role.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "branch"], name="one_role_per_user_branch")]


class MembershipTier(models.Model):
    name = models.CharField(max_length=50, unique=True)
    loan_limit = models.PositiveSmallIntegerField(default=5)
    hold_limit = models.PositiveSmallIntegerField(default=3)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    period_days = models.PositiveIntegerField(default=365)

    def __str__(self):
        return self.name


def _new_member_no():
    while True:
        code = f"LS-{secrets.token_hex(3).upper()}"
        if not Member.objects.filter(member_no=code).exists():
            return code


class Member(TimeStamped):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        EXPIRED = "expired", "Expired"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="member")
    member_no = models.CharField(max_length=20, unique=True, blank=True)
    barcode = models.CharField(max_length=40, unique=True, blank=True)
    qr_token = models.CharField(max_length=40, unique=True, blank=True)
    home_branch = models.ForeignKey("branches.Branch", on_delete=models.PROTECT, related_name="members")
    tier = models.ForeignKey(MembershipTier, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    date_of_birth = models.DateField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    guardian = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="dependants")
    keep_history = models.BooleanField(default=False)  # opt-in reading history archive

    def save(self, *args, **kwargs):
        if not self.member_no:
            self.member_no = _new_member_no()
        if not self.barcode:
            self.barcode = self.member_no
        if not self.qr_token:
            self.qr_token = secrets.token_urlsafe(16)
        super().save(*args, **kwargs)

    @property
    def is_borrowing_allowed(self):
        return self.status == self.Status.ACTIVE and (self.expires_at is None or self.expires_at > timezone.now())

    def __str__(self):
        return f"{self.member_no} {self.user.full_name}"

import secrets

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from branches.models import Branch
from .models import Member, MembershipTier, User


class MemberSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    tier_name = serializers.CharField(source="tier.name", read_only=True, default=None)
    home_branch_name = serializers.CharField(source="home_branch.name", read_only=True)

    class Meta:
        model = Member
        fields = ["id", "member_no", "barcode", "email", "full_name", "phone", "home_branch", "home_branch_name",
                  "tier", "tier_name", "status", "expires_at", "date_of_birth", "keep_history", "created_at"]
        read_only_fields = ["id", "member_no", "barcode", "status", "created_at"]


class MemberLookupSerializer(serializers.ModelSerializer):
    """Minimal data returned to any staff member who scans a card (member may be from another branch)."""
    full_name = serializers.CharField(source="user.full_name")
    tier_name = serializers.CharField(source="tier.name", default=None)

    class Meta:
        model = Member
        fields = ["id", "member_no", "full_name", "status", "tier_name", "expires_at"]


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    full_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    home_branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects.filter(is_active=True))
    tier = serializers.PrimaryKeyRelatedField(queryset=MembershipTier.objects.all(), required=False, allow_null=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists")
        return value

    def validate(self, attrs):
        if attrs.get("password"):
            validate_password(attrs["password"])
        return attrs

    @transaction.atomic
    def create(self, data):
        temp = None
        password = data.get("password") or None
        if not password:
            password = temp = secrets.token_urlsafe(9)
        user = User.objects.create_user(email=data["email"], password=password,
                                        full_name=data["full_name"], phone=data.get("phone", ""))
        member = Member.objects.create(user=user, home_branch=data["home_branch"], tier=data.get("tier"),
                                       date_of_birth=data.get("date_of_birth"))
        member.temporary_password = temp
        return member


class MeSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    member = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "phone", "locale", "accessibility_prefs", "is_superuser", "roles", "member"]
        read_only_fields = ["id", "email", "is_superuser"]

    def get_roles(self, user):
        return [{"branch": r.branch_id, "branch_name": r.branch.name, "role": r.role}
                for r in user.branch_roles.select_related("branch")]

    def get_member(self, user):
        m = getattr(user, "member", None)
        if not m:
            return None
        return {"id": m.id, "member_no": m.member_no, "status": m.status, "tier": m.tier.name if m.tier else None,
                "home_branch": m.home_branch_id, "home_branch_name": m.home_branch.name,
                "qr_token": m.qr_token, "expires_at": m.expires_at, "keep_history": m.keep_history}

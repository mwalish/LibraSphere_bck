from rest_framework import serializers

from .models import Hold, Loan, Policy


class LoanSerializer(serializers.ModelSerializer):
    item_barcode = serializers.CharField(source="item.barcode", read_only=True)
    title = serializers.CharField(source="item.edition.work.title", read_only=True)
    work = serializers.IntegerField(source="item.edition.work_id", read_only=True)
    member_no = serializers.CharField(source="member.member_no", read_only=True, default=None)
    member_name = serializers.CharField(source="member.user.full_name", read_only=True, default=None)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = Loan
        fields = ["id", "item_barcode", "title", "work", "member_no", "member_name", "branch", "branch_name",
                  "issued_at", "due_at", "returned_at", "renewals_count", "status"]

    def get_status(self, loan):
        if loan.returned_at:
            return "returned"
        return "overdue" if loan.is_overdue else "active"


class HoldSerializer(serializers.ModelSerializer):
    title = serializers.CharField(source="work.title", read_only=True)
    pickup_branch_name = serializers.CharField(source="pickup_branch.name", read_only=True)
    member_no = serializers.CharField(source="member.member_no", read_only=True)
    member_name = serializers.CharField(source="member.user.full_name", read_only=True)
    position = serializers.SerializerMethodField()

    class Meta:
        model = Hold
        fields = ["id", "work", "title", "pickup_branch", "pickup_branch_name", "member_no", "member_name",
                  "status", "position", "placed_at", "ready_at", "expires_at"]

    def get_position(self, hold):
        if hold.status != Hold.Status.QUEUED:
            return None
        return Hold.objects.filter(work_id=hold.work_id, pickup_branch_id=hold.pickup_branch_id,
                                   status=Hold.Status.QUEUED, placed_at__lte=hold.placed_at).count()


class PolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = Policy
        fields = ["id", "branch", "tier", "resource_type", "loan_days", "max_renewals", "renew_days",
                  "fine_per_day", "fine_cap", "grace_days", "hold_pickup_days"]


class CheckoutSerializer(serializers.Serializer):
    member = serializers.CharField(help_text="Member number or card barcode")
    item = serializers.CharField(help_text="Item barcode")


class CheckinSerializer(serializers.Serializer):
    item = serializers.CharField()


class HoldCreateSerializer(serializers.Serializer):
    work = serializers.IntegerField()
    pickup_branch = serializers.IntegerField()
    member = serializers.CharField(required=False, allow_blank=True)

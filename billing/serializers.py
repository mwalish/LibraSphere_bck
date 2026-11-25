from rest_framework import serializers

from .models import Fine, Payment


class FineSerializer(serializers.ModelSerializer):
    balance = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    member_no = serializers.CharField(source="member.member_no", read_only=True)
    member_name = serializers.CharField(source="member.user.full_name", read_only=True)
    title = serializers.CharField(source="loan.item.edition.work.title", read_only=True, default=None)

    class Meta:
        model = Fine
        fields = ["id", "member_no", "member_name", "title", "reason", "amount", "amount_paid", "balance",
                  "status", "assessed_at"]


class PaymentCreateSerializer(serializers.Serializer):
    member = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    method = serializers.ChoiceField(choices=Payment.Method.choices, default="cash")
    reference = serializers.CharField(required=False, allow_blank=True, default="")


class PaymentSerializer(serializers.ModelSerializer):
    member_no = serializers.CharField(source="member.member_no", read_only=True)

    class Meta:
        model = Payment
        fields = ["id", "member_no", "amount", "method", "reference", "created_at"]

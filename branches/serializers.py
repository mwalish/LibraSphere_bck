from rest_framework import serializers

from .models import Branch, BranchClosure


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "library", "name", "code", "address", "timezone", "opening_hours", "is_active"]


class ClosureSerializer(serializers.ModelSerializer):
    class Meta:
        model = BranchClosure
        fields = ["id", "branch", "date", "reason"]
        read_only_fields = ["branch"]

import re
from datetime import date

from django.db import transaction
from rest_framework import serializers

from branches.models import Branch
from core.permissions import can_staff
from .models import Creator, Edition, Item, ResourceType, Subject, Work, WorkCreator


def clean_isbn(value):
    if value in (None, ""):
        return None
    digits = re.sub(r"[\s\-]", "", str(value))
    if not re.fullmatch(r"\d{13}|\d{9}[\dXx]", digits):
        raise serializers.ValidationError("ISBN must be 10 or 13 digits")
    return digits.upper()


class ResourceTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceType
        fields = ["id", "code", "name", "metadata_template"]


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ["id", "name", "parent"]


class ItemSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    title = serializers.CharField(source="edition.work.title", read_only=True)

    class Meta:
        model = Item
        fields = ["id", "edition", "title", "branch", "branch_name", "barcode", "call_number", "shelf_location",
                  "status", "acquired_at", "price"]
        read_only_fields = ["status"]
        extra_kwargs = {"barcode": {"required": False, "allow_blank": True}}

    def validate_branch(self, branch):
        if not can_staff(self.context["request"].user, branch.id):
            raise serializers.ValidationError("You don't work at this branch")
        return branch

    def validate(self, attrs):
        # the barcode and edition of an existing copy never change
        if self.instance:
            attrs.pop("edition", None)
            attrs.pop("barcode", None)
        return attrs


class CopiesSerializer(serializers.Serializer):
    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects.filter(is_active=True))
    count = serializers.IntegerField(min_value=1, max_value=100, default=1)
    call_number = serializers.CharField(required=False, allow_blank=True, default="")
    shelf_location = serializers.CharField(required=False, allow_blank=True, default="")
    price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)


class EditionWriteSerializer(serializers.ModelSerializer):
    resource_type = serializers.SlugRelatedField(slug_field="code", queryset=ResourceType.objects.all())
    isbn13 = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    isbn10 = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    copies = CopiesSerializer(many=True, required=False, write_only=True)

    class Meta:
        model = Edition
        fields = ["id", "work", "resource_type", "isbn13", "isbn10", "publisher", "year", "language", "pages",
                  "cover_url", "metadata", "copies"]
        extra_kwargs = {"work": {"required": False}}

    def validate_isbn13(self, value):
        isbn = clean_isbn(value)
        if isbn:
            if len(isbn) != 13:
                raise serializers.ValidationError("ISBN-13 must have 13 digits")
            dup = Edition.objects.filter(isbn13=isbn).exclude(pk=getattr(self.instance, "pk", None)).first()
            if dup:  # duplicate detection
                raise serializers.ValidationError(f"Already in the catalog (work #{dup.work_id}: {dup.work.title})")
        return isbn

    def validate_isbn10(self, value):
        return clean_isbn(value)

    def validate(self, attrs):
        rt = attrs.get("resource_type") or getattr(self.instance, "resource_type", None)
        required = (rt.metadata_template or {}).get("required", []) if rt else []
        meta = attrs.get("metadata", getattr(self.instance, "metadata", {})) or {}
        missing = [k for k in required if not meta.get(k)]
        if missing:
            raise serializers.ValidationError({"metadata": f"{rt.name} needs: {', '.join(missing)}"})
        for c in attrs.get("copies", []):
            if not can_staff(self.context["request"].user, c["branch"].id):
                raise serializers.ValidationError({"copies": f"You don't work at {c['branch'].name}"})
        return attrs

    @transaction.atomic
    def create(self, data):
        copies = data.pop("copies", [])
        edition = Edition.objects.create(**data)
        add_copies(edition, copies)
        edition.work.refresh_search_text()
        return edition

    def update(self, instance, data):
        data.pop("copies", None)
        data.pop("work", None)
        instance = super().update(instance, data)
        instance.work.refresh_search_text()
        return instance


def add_copies(edition, copies):
    for c in copies:
        for _ in range(c["count"]):
            Item.objects.create(edition=edition, branch=c["branch"], call_number=c.get("call_number", ""),
                                shelf_location=c.get("shelf_location", ""), price=c.get("price"),
                                acquired_at=date.today())


# ------------------------------------------------------------------ works
class WorkListSerializer(serializers.ModelSerializer):
    creators = serializers.SerializerMethodField()
    resource_types = serializers.SerializerMethodField()
    available_copies = serializers.IntegerField(read_only=True)
    total_copies = serializers.IntegerField(read_only=True)

    class Meta:
        model = Work
        fields = ["id", "title", "subtitle", "creators", "cover_url", "language", "resource_types",
                  "available_copies", "total_copies", "created_at"]

    def get_creators(self, w):
        return [c.name for c in w.creators.all()]

    def get_resource_types(self, w):
        return sorted({e.resource_type.code for e in w.editions.all()})


class EditionReadSerializer(serializers.ModelSerializer):
    resource_type = serializers.CharField(source="resource_type.code")
    resource_type_name = serializers.CharField(source="resource_type.name")

    class Meta:
        model = Edition
        fields = ["id", "resource_type", "resource_type_name", "isbn13", "isbn10", "publisher", "year",
                  "language", "pages", "cover_url", "metadata"]


class WorkDetailSerializer(WorkListSerializer):
    subjects = SubjectSerializer(many=True, read_only=True)
    editions = EditionReadSerializer(many=True, read_only=True)

    class Meta(WorkListSerializer.Meta):
        fields = WorkListSerializer.Meta.fields + ["description", "first_published", "subjects", "editions"]


class WorkWriteSerializer(serializers.ModelSerializer):
    creators = serializers.ListField(child=serializers.CharField(max_length=200), required=False)
    subjects = serializers.ListField(child=serializers.CharField(max_length=100), required=False)
    editions = EditionWriteSerializer(many=True, required=False)

    class Meta:
        model = Work
        fields = ["id", "title", "subtitle", "description", "language", "first_published", "cover_url",
                  "creators", "subjects", "editions"]

    def to_representation(self, instance):
        # responses use the read serializer (the write fields above are lists of plain strings)
        return WorkDetailSerializer(instance, context=self.context).data

    def _relations(self, work, creators, subjects):
        if creators is not None:
            WorkCreator.objects.filter(work=work).delete()
            for i, name in enumerate(dict.fromkeys(n.strip() for n in creators if n.strip())):
                creator, _ = Creator.objects.get_or_create(name=name, defaults={"sort_name": name})
                WorkCreator.objects.create(work=work, creator=creator)
        if subjects is not None:
            work.subjects.set([Subject.objects.get_or_create(name=n.strip())[0] for n in subjects if n.strip()])

    @transaction.atomic
    def create(self, data):
        creators, subjects = data.pop("creators", []), data.pop("subjects", [])
        editions = self.initial_data.get("editions", [])
        data.pop("editions", None)
        work = Work.objects.create(**data)
        self._relations(work, creators, subjects)
        for e in editions:
            s = EditionWriteSerializer(data={**e, "work": work.pk}, context=self.context)
            s.is_valid(raise_exception=True)
            s.save()
        work.refresh_search_text()
        return work

    @transaction.atomic
    def update(self, instance, data):
        creators, subjects = data.pop("creators", None), data.pop("subjects", None)
        data.pop("editions", None)
        instance = super().update(instance, data)
        self._relations(instance, creators, subjects)
        instance.refresh_search_text()
        return instance

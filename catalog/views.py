from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, ProtectedError, Q
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.permissions import BranchScopedQuerysetMixin, IsStaff, StaffWriteOrReadOnly, can_staff
from core.exceptions import CirculationError
from .models import Edition, Item, ResourceType, Subject, Work
from .search import apply_search
from .serializers import (EditionWriteSerializer, ItemSerializer, ResourceTypeSerializer, SubjectSerializer,
                          WorkDetailSerializer, WorkListSerializer, WorkWriteSerializer)

LENDABLE = [Item.Status.AVAILABLE, Item.Status.ON_LOAN, Item.Status.HOLD_SHELF, Item.Status.IN_TRANSIT]


class WorkViewSet(viewsets.ModelViewSet):
    """Public catalog (read) + staff catalog management (write)."""
    permission_classes = [StaffWriteOrReadOnly]
    ordering_fields = ["title", "created_at"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action in ("create", "partial_update", "update"):
            return WorkWriteSerializer
        return WorkDetailSerializer if self.action == "retrieve" else WorkListSerializer

    def get_queryset(self):
        p = self.request.query_params
        avail = Q(editions__items__status=Item.Status.AVAILABLE)
        total = Q(editions__items__status__in=LENDABLE)
        if p.get("branch"):
            avail &= Q(editions__items__branch_id=p["branch"])
            total &= Q(editions__items__branch_id=p["branch"])
        qs = (Work.objects
              .annotate(available_copies=Count("editions__items", filter=avail, distinct=True),
                        total_copies=Count("editions__items", filter=total, distinct=True))
              .prefetch_related("creators", "subjects", "editions__resource_type"))
        qs = apply_search(qs, p.get("q"))
        if p.get("resource_type"):
            qs = qs.filter(editions__resource_type__code=p["resource_type"])
        if p.get("language"):
            qs = qs.filter(language=p["language"])
        if p.get("subject"):
            qs = qs.filter(subjects__id=p["subject"])
        if p.get("available") in ("1", "true"):
            qs = qs.filter(available_copies__gt=0)
        return qs

    def destroy(self, request, *args, **kwargs):
        work = self.get_object()
        try:
            work.delete()
        except ProtectedError:
            raise CirculationError("has_history", "This title has copies or loan history. Withdraw the copies instead.")
        return Response(status=204)

    @action(detail=True, methods=["get"])
    def availability(self, request, pk=None):
        """Per-branch copy counts for a title."""
        rows = (Item.objects.filter(edition__work_id=pk).exclude(status__in=["lost", "withdrawn"])
                .values("branch_id", "branch__name", "status").annotate(n=Count("id")))
        out = {}
        for r in rows:
            b = out.setdefault(r["branch_id"], {"branch": r["branch_id"], "branch_name": r["branch__name"],
                                                "total": 0, "available": 0})
            b["total"] += r["n"]
            if r["status"] == Item.Status.AVAILABLE:
                b["available"] += r["n"]
        return Response(list(out.values()))

    @action(detail=False, methods=["get"])
    def popular(self, request):
        def compute():
            since = timezone.now() - timedelta(days=90)
            ids = (Work.objects.annotate(n=Count("editions__items__loans",
                                                 filter=Q(editions__items__loans__issued_at__gte=since)))
                   .filter(n__gt=0).order_by("-n").values_list("id", flat=True)[:10])
            return list(ids)
        ids = cache.get_or_set("popular_works", compute, 300)
        works = {w.id: w for w in self.get_queryset().filter(id__in=ids)}
        return Response(WorkListSerializer([works[i] for i in ids if i in works], many=True).data)


class EditionViewSet(mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    queryset = Edition.objects.select_related("work", "resource_type")
    serializer_class = EditionWriteSerializer
    permission_classes = [IsStaff]
    http_method_names = ["post", "patch", "head", "options"]


class ItemViewSet(BranchScopedQuerysetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                  mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = ItemSerializer
    permission_classes = [IsStaff]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = self.scope_queryset(Item.objects.select_related("edition__work", "branch"))
        p = self.request.query_params
        for key, field in (("barcode", "barcode"), ("status", "status"), ("work", "edition__work_id"),
                           ("branch", "branch_id")):
            if p.get(key):
                qs = qs.filter(**{field: p[key]})
        return qs

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        item = self.get_object()
        if item.status in (Item.Status.ON_LOAN, Item.Status.HOLD_SHELF):
            raise CirculationError("item_busy", "Check the item in (or release its hold) before withdrawing it")
        item.status = Item.Status.WITHDRAWN
        item.save(update_fields=["status", "updated_at"])
        return Response(ItemSerializer(item).data)


class ResourceTypeViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = ResourceType.objects.all()
    serializer_class = ResourceTypeSerializer
    permission_classes = [AllowAny]
    pagination_class = None


class SubjectViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = Subject.objects.all()
    serializer_class = SubjectSerializer
    permission_classes = [AllowAny]
    pagination_class = None

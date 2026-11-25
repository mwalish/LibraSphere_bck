from decimal import Decimal

from django.db.models import F, Sum
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import BranchScopedQuerysetMixin, IsManager, IsStaff
from . import services
from .models import Fine, Payment
from .serializers import FineSerializer, PaymentCreateSerializer, PaymentSerializer

FINE_SELECT = ("member__user", "loan__item__edition__work")


class FineViewSet(BranchScopedQuerysetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = FineSerializer
    permission_classes = [IsStaff]
    branch_field = "member__home_branch"

    def get_queryset(self):
        qs = self.scope_queryset(Fine.objects.select_related(*FINE_SELECT))
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("member"):
            qs = qs.filter(member__member_no__iexact=p["member"])
        return qs.order_by("-assessed_at")

    @action(detail=True, methods=["post"], permission_classes=[IsManager])
    def waive(self, request, pk=None):
        fine = services.waive_fine(fine_id=pk, reason=request.data.get("reason", ""), staff=request.user)
        return Response(FineSerializer(Fine.objects.select_related(*FINE_SELECT).get(pk=fine.pk)).data)


class PaymentViewSet(BranchScopedQuerysetMixin, mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [IsStaff]
    branch_field = "member__home_branch"

    def get_queryset(self):
        return self.scope_queryset(Payment.objects.select_related("member")).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        s = PaymentCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        payment = services.record_payment(member_code=d["member"], amount=d["amount"], method=d["method"],
                                          reference=d["reference"], staff=request.user)
        return Response(PaymentSerializer(payment).data, status=201)


class MyFinesView(APIView):
    def get(self, request):
        member = getattr(request.user, "member", None)
        if not member:
            raise PermissionDenied("This account has no membership")
        fines = Fine.objects.filter(member=member).select_related(*FINE_SELECT).order_by("-assessed_at")[:50]
        owed = (Fine.objects.filter(member=member, status=Fine.Status.OPEN)
                .aggregate(t=Sum(F("amount") - F("amount_paid")))["t"] or Decimal("0"))
        return Response({"total_owed": str(owed), "fines": FineSerializer(fines, many=True).data})

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from accounts.models import Member
from branches.models import Branch
from catalog.models import Work
from core.permissions import BranchScopedQuerysetMixin, IsManager, IsStaff, can_staff
from . import services
from .models import Hold, Loan, Policy
from .serializers import (CheckinSerializer, CheckoutSerializer, HoldCreateSerializer, HoldSerializer,
                          LoanSerializer, PolicySerializer)

LOAN_SELECT = ("item__edition__work", "member__user", "branch")


class LoanViewSet(BranchScopedQuerysetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = LoanSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = self.scope_queryset(Loan.objects.select_related(*LOAN_SELECT))
        p = self.request.query_params
        if p.get("member"):
            qs = qs.filter(member__member_no__iexact=p["member"])
        status = p.get("status")
        if status == "active":
            qs = qs.filter(returned_at__isnull=True)
        elif status == "overdue":
            qs = qs.filter(returned_at__isnull=True, due_at__lt=timezone.now())
        elif status == "returned":
            qs = qs.filter(returned_at__isnull=False)
        return qs.order_by("-issued_at")

    @action(detail=False, methods=["post"])
    def checkout(self, request):
        s = CheckoutSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        loan = services.checkout(member_code=s.validated_data["member"], item_barcode=s.validated_data["item"],
                                 staff=request.user)
        loan = Loan.objects.select_related(*LOAN_SELECT).get(pk=loan.pk)
        return Response(LoanSerializer(loan).data, status=201)

    @action(detail=False, methods=["post"])
    def checkin(self, request):
        s = CheckinSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        loan, outcome = services.checkin(item_barcode=s.validated_data["item"], staff=request.user)
        loan = Loan.objects.select_related(*LOAN_SELECT).get(pk=loan.pk)
        hold, fine = outcome["hold"], outcome["fine"]
        return Response({
            "loan": LoanSerializer(loan).data,
            "action": outcome["action"],  # "hold" => put on the hold shelf; "shelve" => back to its shelf
            "shelve_at": loan.item.shelf_location or loan.item.call_number,
            "hold": HoldSerializer(hold).data if hold else None,
            "fine": str(fine.balance) if fine else None,
        })

    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        loan = services.renew(loan_id=pk, actor=request.user)
        return Response(LoanSerializer(Loan.objects.select_related(*LOAN_SELECT).get(pk=loan.pk)).data)

    @action(detail=True, methods=["post"], url_path="mark-lost")
    def mark_lost(self, request, pk=None):
        loan = services.mark_lost(loan_id=pk, staff=request.user)
        return Response(LoanSerializer(Loan.objects.select_related(*LOAN_SELECT).get(pk=loan.pk)).data)


class HoldViewSet(BranchScopedQuerysetMixin, viewsets.GenericViewSet):
    serializer_class = HoldSerializer
    branch_field = "pickup_branch"

    def get_permissions(self):
        return [IsStaff()] if self.action == "list" else [IsAuthenticated()]

    def get_queryset(self):
        qs = self.scope_queryset(Hold.objects.select_related("work", "pickup_branch", "member__user"))
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("member"):
            qs = qs.filter(member__member_no__iexact=p["member"])
        return qs

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(HoldSerializer(page, many=True).data)

    def create(self, request):
        s = HoldCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            work, branch = Work.objects.get(pk=d["work"]), Branch.objects.get(pk=d["pickup_branch"], is_active=True)
        except (Work.DoesNotExist, Branch.DoesNotExist):
            raise NotFound("Unknown title or branch")
        if d.get("member"):  # a librarian placing a hold on behalf of a member
            if not can_staff(request.user, branch.id):
                raise PermissionDenied()
            member = Member.objects.filter(member_no__iexact=d["member"]).first()
            if not member:
                raise NotFound("No member with that card")
        else:
            member = getattr(request.user, "member", None)
            if not member:
                raise PermissionDenied("Only members can place holds")
        hold = services.place_hold(member=member, work=work, pickup_branch=branch, actor=request.user)
        hold = Hold.objects.select_related("work", "pickup_branch", "member__user").get(pk=hold.pk)
        return Response(HoldSerializer(hold).data, status=201)

    def destroy(self, request, pk=None):
        hold = services.cancel_hold(hold_id=pk, actor=request.user)
        return Response(HoldSerializer(hold).data)


class PolicyViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin,
                    mixins.DestroyModelMixin, viewsets.GenericViewSet):
    serializer_class = PolicySerializer
    permission_classes = [IsManager]
    queryset = Policy.objects.all()
    pagination_class = None


# ---------------------------------------------------------------- "me" endpoints (members)
def _member(user):
    member = getattr(user, "member", None)
    if not member:
        raise PermissionDenied("This account has no membership")
    return member


class MyLoansView(APIView):
    def get(self, request):
        qs = Loan.objects.filter(member=_member(request.user)).select_related(*LOAN_SELECT)
        if request.query_params.get("history") in ("1", "true"):
            qs = qs.filter(returned_at__isnull=False)[:100]
        else:
            qs = qs.filter(returned_at__isnull=True).order_by("due_at")
        return Response(LoanSerializer(qs, many=True).data)


class MyRenewView(APIView):
    def post(self, request, pk):
        loan = services.renew(loan_id=pk, actor=request.user)
        return Response(LoanSerializer(Loan.objects.select_related(*LOAN_SELECT).get(pk=loan.pk)).data)


class MyHoldsView(APIView):
    def get(self, request):
        qs = (Hold.objects.filter(member=_member(request.user), status__in=[Hold.Status.QUEUED, Hold.Status.READY])
              .select_related("work", "pickup_branch", "member__user"))
        return Response(HoldSerializer(qs, many=True).data)

from datetime import timedelta

from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core.models import audit
from core.permissions import BranchScopedQuerysetMixin, IsManager, IsStaff, can_staff
from notifications.services import notify
from .models import Member, MembershipTier
from .serializers import (MemberLookupSerializer, MemberSerializer, MeSerializer, RegisterSerializer)


class LoginView(TokenObtainPairView):
    throttle_scope = "login"


class RefreshView(TokenRefreshView):
    throttle_scope = "login"


class LogoutView(APIView):
    def post(self, request):
        try:
            RefreshToken(request.data.get("refresh", "")).blacklist()
        except TokenError:
            pass
        return Response(status=204)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "register"

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.validated_data.pop("tier", None)  # self-registration never picks a tier
        member = s.save()
        return Response({"detail": "Registration received. A librarian will approve your membership.",
                         "member_no": member.member_no}, status=201)


class MeView(APIView):
    def get(self, request):
        return Response(MeSerializer(request.user).data)

    def patch(self, request):
        s = MeSerializer(request.user, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        keep = request.data.get("keep_history")
        if keep is not None and hasattr(request.user, "member"):
            request.user.member.keep_history = bool(keep)
            request.user.member.save(update_fields=["keep_history", "updated_at"])
        return Response(MeSerializer(request.user).data)


class MemberViewSet(BranchScopedQuerysetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                    mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = MemberSerializer
    permission_classes = [IsStaff]
    branch_field = "home_branch"
    http_method_names = ["get", "post", "patch", "head", "options"]
    ordering_fields = ["created_at", "member_no"]

    def get_queryset(self):
        qs = self.scope_queryset(Member.objects.select_related("user", "tier", "home_branch"))
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("search"):
            s = p["search"]
            qs = qs.filter(member_no__icontains=s) | qs.filter(user__full_name__icontains=s) | qs.filter(user__email__icontains=s)
        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        """Walk-in registration by staff: active immediately; returns a one-time temporary password."""
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not can_staff(request.user, s.validated_data["home_branch"].id):
            raise PermissionDenied("You can only register members at your own branch")
        member = s.save()
        self._activate(member, request.user)
        data = MemberSerializer(member).data
        data["temporary_password"] = member.temporary_password
        return Response(data, status=201)

    def _activate(self, member, staff):
        member.status = Member.Status.ACTIVE
        period = member.tier.period_days if member.tier else 365
        member.expires_at = timezone.now() + timedelta(days=period)
        member.save(update_fields=["status", "expires_at", "updated_at"])
        audit(staff, "member.activate", member, branch_id=member.home_branch_id)

    @action(detail=True, methods=["post"], permission_classes=[IsManager])
    def approve(self, request, pk=None):
        member = self.get_object()
        if not can_staff(request.user, member.home_branch_id, "branch_manager"):
            raise PermissionDenied()
        if not member.tier:
            member.tier = MembershipTier.objects.order_by("price").first()
        self._activate(member, request.user)
        notify(member.user, "membership_approved", member_no=member.member_no)
        return Response(MemberSerializer(member).data)

    @action(detail=True, methods=["post"], permission_classes=[IsManager])
    def suspend(self, request, pk=None):
        member = self.get_object()
        member.status = Member.Status.SUSPENDED
        member.save(update_fields=["status", "updated_at"])
        audit(request.user, "member.suspend", member, branch_id=member.home_branch_id)
        return Response(MemberSerializer(member).data)

    @action(detail=False, methods=["get"])
    def lookup(self, request):
        """Scan a card: exact member number / barcode, any branch."""
        code = request.query_params.get("code", "").strip()
        m = Member.objects.select_related("user", "tier").filter(member_no__iexact=code).first() if code else None
        if not m:
            raise NotFound("No member with that card")
        return Response(MemberLookupSerializer(m).data)

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, SAFE_METHODS, BasePermission
from rest_framework.response import Response

from core.permissions import can_staff, staff_branches
from .models import Branch, BranchClosure
from .serializers import BranchSerializer, ClosureSerializer


class BranchPermission(BasePermission):
    """Read: anyone. Create: super admin. Update / closures: manager of that branch."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        if view.action == "create":
            return request.user.is_authenticated and request.user.is_superuser
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return request.method in SAFE_METHODS or can_staff(request.user, obj.id, "branch_manager")


class BranchViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                    mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = BranchSerializer
    permission_classes = [BranchPermission]
    pagination_class = None
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = Branch.objects.all()
        if self.request.method == "GET" and not (self.request.user.is_authenticated and staff_branches(self.request.user) != set()):
            qs = qs.filter(is_active=True)
        return qs

    @action(detail=True, methods=["get", "post"], url_path="closures")
    def closures(self, request, pk=None):
        branch = self.get_object()
        if request.method == "GET":
            return Response(ClosureSerializer(branch.closures.all(), many=True).data)
        if not can_staff(request.user, branch.id, "branch_manager"):
            raise PermissionDenied()
        s = ClosureSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(branch=branch)
        return Response(s.data, status=201)

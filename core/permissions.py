"""Branch-scoped role checks. Super admin == Django superuser; other staff hold BranchRole rows."""
from rest_framework.permissions import SAFE_METHODS, BasePermission

RANK = {"librarian": 1, "branch_manager": 2}


def staff_branches(user, minimum="librarian"):
    """None => every branch (super admin). Otherwise the set of branch ids where the user holds
    at least `minimum`. Empty set => not staff."""
    if not getattr(user, "is_authenticated", False):
        return set()
    if user.is_superuser:
        return None
    cache = user.__dict__.setdefault("_role_cache", {})
    if minimum not in cache:
        need = RANK[minimum]
        cache[minimum] = {r.branch_id for r in user.branch_roles.all() if RANK.get(r.role, 0) >= need}
    return cache[minimum]


def can_staff(user, branch_id, minimum="librarian"):
    ids = staff_branches(user, minimum)
    return ids is None or branch_id in ids


class IsStaff(BasePermission):
    minimum = "librarian"

    def has_permission(self, request, view):
        ids = staff_branches(request.user, self.minimum)
        return ids is None or bool(ids)


class IsManager(IsStaff):
    minimum = "branch_manager"


class StaffWriteOrReadOnly(BasePermission):
    """Anyone may read (public catalog); only staff may write."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        ids = staff_branches(request.user)
        return ids is None or bool(ids)


class BranchScopedQuerysetMixin:
    """Restrict a staff queryset to the branches the user works at."""
    branch_field = "branch"
    scope_minimum = "librarian"

    def scope_queryset(self, qs):
        ids = staff_branches(self.request.user, self.scope_minimum)
        return qs if ids is None else qs.filter(**{f"{self.branch_field}__in": ids})

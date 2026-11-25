from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Member
from catalog.models import Item
from circulation.models import Hold, Loan, LoanEvent
from core.permissions import IsStaff, staff_branches


def _scope(qs, user, field):
    ids = staff_branches(user)
    return qs if ids is None else qs.filter(**{f"{field}__in": ids})


class ReportView(APIView):
    """GET /reports/<kind>/ ?branch= &from=YYYY-MM-DD &to=YYYY-MM-DD   (staff, scoped to their branches)"""
    permission_classes = [IsStaff]

    def get(self, request, kind):
        handler = getattr(self, f"report_{kind.replace('-', '_')}", None)
        if not handler:
            raise NotFound("Unknown report")
        return Response(handler(request))

    def _loans(self, request):
        qs = _scope(Loan.objects.all(), request.user, "branch")
        p = request.query_params
        if p.get("branch"):
            qs = qs.filter(branch_id=p["branch"])
        return qs

    def report_today(self, request):
        now = timezone.now()
        start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
        loans = self._loans(request)
        active = loans.filter(returned_at__isnull=True)
        events = LoanEvent.objects.filter(loan__in=loans, at__gte=start)
        return {
            "active_loans": active.count(),
            "overdue": active.filter(due_at__lt=now).count(),
            "due_today": active.filter(due_at__gte=now, due_at__lt=start + timedelta(days=1)).count(),
            "checkouts_today": events.filter(type="issue").count(),
            "returns_today": events.filter(type="return").count(),
            "holds_ready": _scope(Hold.objects.filter(status="ready"), request.user, "pickup_branch").count(),
            "members_pending": _scope(Member.objects.filter(status="pending"), request.user, "home_branch").count(),
        }

    def report_most_borrowed(self, request):
        p = request.query_params
        qs = self._loans(request)
        since = p.get("from") or (timezone.now() - timedelta(days=90)).date().isoformat()
        qs = qs.filter(issued_at__date__gte=since)
        if p.get("to"):
            qs = qs.filter(issued_at__date__lte=p["to"])
        rows = (qs.values("item__edition__work_id", "item__edition__work__title")
                .annotate(loans=Count("id")).order_by("-loans")[:20])
        return [{"work": r["item__edition__work_id"], "title": r["item__edition__work__title"], "loans": r["loans"]}
                for r in rows]

    def report_new_arrivals(self, request):
        since = timezone.now() - timedelta(days=int(request.query_params.get("days", 30)))
        qs = _scope(Item.objects.filter(created_at__gte=since), request.user, "branch")
        rows = (qs.values("edition__work_id", "edition__work__title").annotate(copies=Count("id"))
                .order_by("-copies")[:50])
        return [{"work": r["edition__work_id"], "title": r["edition__work__title"], "copies": r["copies"]}
                for r in rows]

    def report_overdue(self, request):
        now = timezone.now()
        qs = (self._loans(request).filter(returned_at__isnull=True, due_at__lt=now, member__isnull=False)
              .select_related("member__user", "item__edition__work", "branch").order_by("due_at")[:200])
        return [{"loan": l.id, "title": l.item.edition.work.title, "member_no": l.member.member_no,
                 "member_name": l.member.user.full_name, "email": l.member.user.email, "branch": l.branch.name,
                 "due_at": l.due_at, "days_overdue": (now - l.due_at).days} for l in qs]

    def report_membership_growth(self, request):
        qs = _scope(Member.objects.all(), request.user, "home_branch")
        rows = qs.annotate(m=TruncMonth("created_at")).values("m").annotate(n=Count("id")).order_by("m")
        return [{"month": r["m"].date().isoformat(), "new_members": r["n"]} for r in rows]

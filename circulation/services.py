"""The circulation engine. Every rule about lending lives here (views stay thin), and every function
that touches several rows runs inside one transaction with row locks so two desks can't collide."""
import math
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from accounts.models import Member
from billing.models import Fine
from branches.models import BranchClosure
from catalog.models import Item
from core.exceptions import CirculationError
from core.models import audit
from core.permissions import can_staff
from notifications.services import notify
from .models import Hold, Loan, LoanEvent, Policy

CFG = lambda: settings.CIRCULATION  # noqa: E731
DEFAULT_POLICY = dict(loan_days=14, max_renewals=2, renew_days=14, fine_per_day=Decimal("10.00"),
                      fine_cap=Decimal("500.00"), grace_days=0, hold_pickup_days=3)


# ------------------------------------------------------------------ policy & dates
def resolve_policy(branch, tier, resource_type):
    """Most specific Policy row wins: branch (4) > tier (2) > resource type (1). Falls back to defaults."""
    candidates = Policy.objects.filter(
        Q(branch=branch) | Q(branch__isnull=True),
        Q(tier=tier) | Q(tier__isnull=True),
        Q(resource_type=resource_type) | Q(resource_type__isnull=True))
    best, best_score = None, -1
    for p in candidates:
        score = (p.branch_id is not None) * 4 + (p.tier_id is not None) * 2 + (p.resource_type_id is not None)
        if score > best_score:
            best, best_score = p, score
    return best or Policy(**DEFAULT_POLICY)


def compute_due(branch, days, start=None):
    """Due at 23:59 local time, pushed past any branch closure days."""
    due = timezone.localtime((start or timezone.now()) + timedelta(days=days)).replace(
        hour=23, minute=59, second=0, microsecond=0)
    for _ in range(31):
        if not BranchClosure.objects.filter(branch=branch, date=due.date()).exists():
            break
        due += timedelta(days=1)
    return due


# ------------------------------------------------------------------ helpers
def outstanding_fines(member):
    total = Fine.objects.filter(member=member, status=Fine.Status.OPEN).aggregate(
        t=Sum(F("amount") - F("amount_paid")))["t"]
    return total or Decimal("0")


def check_member_can_borrow(member):
    if not member.is_borrowing_allowed:
        raise CirculationError("member_inactive", f"Membership is {member.get_status_display().lower()} or expired")
    owed = outstanding_fines(member)
    if owed >= CFG()["FINE_BLOCK_THRESHOLD"]:
        raise CirculationError("fines_block", f"Outstanding fines of {owed} must be paid before borrowing")


def _lock_member(code):
    try:
        return Member.objects.select_for_update().get(Q(member_no__iexact=code) | Q(barcode__iexact=code))
    except Member.DoesNotExist:
        raise NotFound("No member with that card")


def _lock_item(barcode):
    try:
        return Item.objects.select_for_update().get(barcode=barcode)
    except Item.DoesNotExist:
        raise NotFound("No item with that barcode")


def _tier_limit(member, field, default_key):
    return getattr(member.tier, field) if member.tier else CFG()[default_key]


# ------------------------------------------------------------------ check-out / check-in
@transaction.atomic
def checkout(*, member_code, item_barcode, staff):
    item = _lock_item(item_barcode)
    if not can_staff(staff, item.branch_id):
        raise PermissionDenied("This item belongs to a branch you don't work at")
    member = _lock_member(member_code)
    check_member_can_borrow(member)

    limit = _tier_limit(member, "loan_limit", "DEFAULT_LOAN_LIMIT")
    if Loan.objects.filter(member=member, returned_at__isnull=True).count() >= limit:
        raise CirculationError("loan_limit", f"Loan limit reached ({limit} items)")

    hold = None
    if item.status == Item.Status.HOLD_SHELF:
        hold = Hold.objects.select_for_update().filter(ready_item=item, status=Hold.Status.READY).first()
        if not hold or hold.member_id != member.id:
            raise CirculationError("reserved_for_other", "This copy is reserved for another member")
    elif item.status != Item.Status.AVAILABLE:
        raise CirculationError("not_available", f"This item is {item.get_status_display().lower()}")

    policy = resolve_policy(item.branch, member.tier, item.edition.resource_type)
    now = timezone.now()
    try:
        loan = Loan.objects.create(item=item, member=member, branch=item.branch, issued_by=staff, issued_at=now,
                                   due_at=compute_due(item.branch, policy.loan_days, now), active_key=item.id)
    except IntegrityError:
        raise CirculationError("already_on_loan", "This item is already on loan")
    LoanEvent.objects.create(loan=loan, type=LoanEvent.Type.ISSUE, by=staff)
    item.status = Item.Status.ON_LOAN
    item.save(update_fields=["status", "updated_at"])
    if hold:
        hold.status, hold.active_key = Hold.Status.FULFILLED, None
        hold.save(update_fields=["status", "active_key"])
    audit(staff, "loan.checkout", loan, branch_id=item.branch_id, member=member.member_no)
    return loan


@transaction.atomic
def checkin(*, item_barcode, staff):
    item = _lock_item(item_barcode)
    if not can_staff(staff, item.branch_id):
        raise PermissionDenied("This item belongs to a branch you don't work at")
    loan = Loan.objects.select_for_update().filter(item=item, returned_at__isnull=True).first()
    if not loan:
        raise CirculationError("not_on_loan", "This item is not currently on loan")
    now = timezone.now()
    loan.returned_at, loan.returned_by, loan.active_key = now, staff, None
    loan.save(update_fields=["returned_at", "returned_by", "active_key"])
    LoanEvent.objects.create(loan=loan, type=LoanEvent.Type.RETURN, by=staff)
    fine = assess_loan_fine(loan, now)
    hold = _release_item(item)
    audit(staff, "loan.checkin", loan, branch_id=item.branch_id)
    return loan, {"action": "hold" if hold else "shelve", "hold": hold, "fine": fine}


@transaction.atomic
def renew(*, loan_id, actor):
    try:
        loan = Loan.objects.select_for_update().get(pk=loan_id)
    except Loan.DoesNotExist:
        raise NotFound("Loan not found")
    owner = loan.member and loan.member.user_id == actor.id
    if not (owner or can_staff(actor, loan.branch_id)):
        raise PermissionDenied()
    if loan.returned_at:
        raise CirculationError("already_returned", "This loan is already closed")
    member = loan.member
    check_member_can_borrow(member)
    policy = resolve_policy(loan.branch, member.tier, loan.item.edition.resource_type)
    if loan.renewals_count >= policy.max_renewals:
        raise CirculationError("max_renewals", f"This item can be renewed {policy.max_renewals} times at most")
    if Hold.objects.filter(work_id=loan.item.edition.work_id, status=Hold.Status.QUEUED).exists():
        raise CirculationError("held_by_others", "Another member is waiting for this title")
    now = timezone.now()
    loan.due_at = compute_due(loan.branch, policy.renew_days, max(now, loan.due_at))
    loan.renewals_count += 1
    loan.save(update_fields=["due_at", "renewals_count"])
    LoanEvent.objects.create(loan=loan, type=LoanEvent.Type.RENEW, by=actor)
    return loan


@transaction.atomic
def mark_lost(*, loan_id, staff):
    try:
        loan = Loan.objects.select_for_update().get(pk=loan_id)
    except Loan.DoesNotExist:
        raise NotFound("Loan not found")
    if not can_staff(staff, loan.branch_id):
        raise PermissionDenied()
    if loan.returned_at:
        raise CirculationError("already_returned", "This loan is already closed")
    now = timezone.now()
    loan.returned_at, loan.returned_by, loan.active_key = now, staff, None
    loan.save(update_fields=["returned_at", "returned_by", "active_key"])
    item = Item.objects.select_for_update().get(pk=loan.item_id)
    item.status = Item.Status.LOST
    item.save(update_fields=["status", "updated_at"])
    LoanEvent.objects.create(loan=loan, type=LoanEvent.Type.LOST, by=staff)
    assess_loan_fine(loan, now)
    if loan.member:
        Fine.objects.get_or_create(loan=loan, reason=Fine.Reason.LOST,
                                   defaults={"member": loan.member, "amount": item.price or CFG()["LOST_FEE"]})
    audit(staff, "loan.lost", loan, branch_id=loan.branch_id)
    return loan


# ------------------------------------------------------------------ holds
def _mark_ready(hold, item):
    policy = resolve_policy(item.branch, hold.member.tier, item.edition.resource_type)
    now = timezone.now()
    hold.status, hold.ready_item, hold.ready_at = Hold.Status.READY, item, now
    hold.expires_at = now + timedelta(days=policy.hold_pickup_days)
    hold.save(update_fields=["status", "ready_item", "ready_at", "expires_at"])
    item.status = Item.Status.HOLD_SHELF
    item.save(update_fields=["status", "updated_at"])
    notify(hold.member.user, "hold_ready", title=item.edition.work.title, branch=item.branch.name,
           expires=timezone.localtime(hold.expires_at).strftime("%d %b %Y"))


def _release_item(item):
    """A copy just became free: hand it to the next queued hold at its branch, else back on the shelf."""
    nxt = (Hold.objects.select_for_update()
           .filter(work_id=item.edition.work_id, pickup_branch_id=item.branch_id, status=Hold.Status.QUEUED)
           .order_by("placed_at", "id").first())
    if nxt:
        _mark_ready(nxt, item)
        return nxt
    item.status = Item.Status.AVAILABLE
    item.save(update_fields=["status", "updated_at"])
    return None


@transaction.atomic
def place_hold(*, member, work, pickup_branch, actor):
    member = Member.objects.select_for_update().get(pk=member.pk)
    if not (member.user_id == actor.id or can_staff(actor, pickup_branch.id)):
        raise PermissionDenied()
    check_member_can_borrow(member)
    limit = _tier_limit(member, "hold_limit", "DEFAULT_HOLD_LIMIT")
    live = [Hold.Status.QUEUED, Hold.Status.READY]
    if Hold.objects.filter(member=member, status__in=live).count() >= limit:
        raise CirculationError("hold_limit", f"Hold limit reached ({limit})")
    if Loan.objects.filter(member=member, returned_at__isnull=True, item__edition__work=work).exists():
        raise CirculationError("already_borrowed", "You already have this title on loan")
    items = list(Item.objects.select_for_update().filter(edition__work=work, branch=pickup_branch)
                 .exclude(status__in=[Item.Status.LOST, Item.Status.WITHDRAWN]))
    if not items:
        raise CirculationError("no_copies_at_branch", f"{pickup_branch.name} has no copies of this title")
    try:
        hold = Hold.objects.create(member=member, work=work, pickup_branch=pickup_branch,
                                   active_key=f"{member.pk}:{work.pk}")
    except IntegrityError:
        raise CirculationError("duplicate_hold", "You already have a hold on this title")
    free = next((i for i in items if i.status == Item.Status.AVAILABLE), None)
    if free:  # nobody ahead of you and a copy is on the shelf: ready immediately
        _mark_ready(hold, free)
    return hold


@transaction.atomic
def cancel_hold(*, hold_id, actor):
    try:
        hold = Hold.objects.select_for_update().get(pk=hold_id)
    except Hold.DoesNotExist:
        raise NotFound("Hold not found")
    if not (hold.member.user_id == actor.id or can_staff(actor, hold.pickup_branch_id)):
        raise PermissionDenied()
    if hold.status not in (Hold.Status.QUEUED, Hold.Status.READY):
        raise CirculationError("hold_closed", "This hold is already closed")
    was_ready, item = hold.status == Hold.Status.READY, hold.ready_item
    hold.status, hold.active_key = Hold.Status.CANCELLED, None
    hold.save(update_fields=["status", "active_key"])
    if was_ready and item:
        _release_item(Item.objects.select_for_update().get(pk=item.pk))
    return hold


def expire_holds():
    """Ready holds not collected in time go back to the queue/shelf. Run by Celery every 30 minutes."""
    count = 0
    for hold_id in list(Hold.objects.filter(status=Hold.Status.READY, expires_at__lt=timezone.now())
                        .values_list("id", flat=True)):
        with transaction.atomic():
            hold = Hold.objects.select_for_update().get(pk=hold_id)
            if hold.status != Hold.Status.READY:
                continue
            item = Item.objects.select_for_update().get(pk=hold.ready_item_id)
            hold.status, hold.active_key = Hold.Status.EXPIRED, None
            hold.save(update_fields=["status", "active_key"])
            _release_item(item)
            count += 1
    return count


# ------------------------------------------------------------------ fines & housekeeping
def assess_loan_fine(loan, now=None):
    """Create/refresh the overdue fine for a loan (idempotent). Late days are counted per started day."""
    if not loan.member_id:
        return None
    end = loan.returned_at or now or timezone.now()
    if end <= loan.due_at:
        return None
    policy = resolve_policy(loan.branch, loan.member.tier, loan.item.edition.resource_type)
    late_days = math.ceil((end - loan.due_at).total_seconds() / 86400) - policy.grace_days
    if late_days <= 0:
        return None
    amount = min(policy.fine_cap, policy.fine_per_day * late_days)
    fine, created = Fine.objects.get_or_create(loan=loan, reason=Fine.Reason.OVERDUE,
                                               defaults={"member": loan.member, "amount": amount})
    if not created and amount > fine.amount and fine.status != Fine.Status.WAIVED:
        fine.amount, fine.status = amount, Fine.Status.OPEN
        fine.save(update_fields=["amount", "status", "updated_at"])
    return fine


def assess_all_fines():
    loans = (Loan.objects.filter(returned_at__isnull=True, due_at__lt=timezone.now(), member__isnull=False)
             .select_related("member__tier", "item__edition__resource_type", "branch"))
    count = 0
    for loan in loans:
        if assess_loan_fine(loan):
            count += 1
    return count


def send_due_reminders():
    now = timezone.now()
    loans = (Loan.objects.filter(returned_at__isnull=True, due_at__gt=now, member__isnull=False,
                                 due_at__lte=now + timedelta(days=CFG()["DUE_REMINDER_DAYS"]))
             .exclude(events__type=LoanEvent.Type.REMINDER)
             .select_related("member__user", "item__edition__work"))
    count = 0
    for loan in loans:
        with transaction.atomic():
            LoanEvent.objects.create(loan=loan, type=LoanEvent.Type.REMINDER)
            notify(loan.member.user, "due_soon", title=loan.item.edition.work.title,
                   due=timezone.localtime(loan.due_at).strftime("%d %b %Y"))
            count += 1
    return count


def anonymize_history():
    """Privacy: detach returned loans from members who didn't opt in to a reading-history archive."""
    cutoff = timezone.now() - timedelta(days=CFG()["HISTORY_RETENTION_DAYS"])
    return (Loan.objects.filter(returned_at__lt=cutoff, member__isnull=False, member__keep_history=False)
            .exclude(fines__status=Fine.Status.OPEN).update(member=None))

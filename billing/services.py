from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from rest_framework.exceptions import NotFound, PermissionDenied

from accounts.models import Member
from core.exceptions import CirculationError
from core.models import audit
from core.permissions import can_staff
from .models import Fine, FineAllocation, Payment, Waiver


@transaction.atomic
def record_payment(*, member_code, amount, method, reference, staff):
    """Apply a payment to the member's oldest open fines first."""
    try:
        member = Member.objects.select_for_update().get(Q(member_no__iexact=member_code) | Q(barcode__iexact=member_code))
    except Member.DoesNotExist:
        raise NotFound("No member with that card")
    amount = Decimal(amount)
    if amount <= 0:
        raise CirculationError("bad_amount", "Amount must be greater than zero")
    fines = list(Fine.objects.select_for_update().filter(member=member, status=Fine.Status.OPEN).order_by("assessed_at", "id"))
    owed = sum((f.balance for f in fines), Decimal("0"))
    if amount > owed:
        raise CirculationError("overpayment", f"Member only owes {owed}")
    payment = Payment.objects.create(member=member, amount=amount, method=method, reference=reference, taken_by=staff)
    remaining = amount
    for fine in fines:
        if remaining <= 0:
            break
        part = min(fine.balance, remaining)
        FineAllocation.objects.create(payment=payment, fine=fine, amount=part)
        fine.amount_paid += part
        if fine.balance == 0:
            fine.status = Fine.Status.PAID
        fine.save(update_fields=["amount_paid", "status", "updated_at"])
        remaining -= part
    audit(staff, "payment.record", payment, branch_id=member.home_branch_id, amount=amount, method=method)
    return payment


@transaction.atomic
def waive_fine(*, fine_id, reason, staff):
    try:
        fine = Fine.objects.select_for_update().select_related("member").get(pk=fine_id)
    except Fine.DoesNotExist:
        raise NotFound("Fine not found")
    if not can_staff(staff, fine.member.home_branch_id, "branch_manager"):
        raise PermissionDenied()
    if fine.status != Fine.Status.OPEN:
        raise CirculationError("fine_closed", "This fine is already settled")
    if not reason.strip():
        raise CirculationError("reason_required", "Give a reason for the waiver")
    fine.status = Fine.Status.WAIVED
    fine.save(update_fields=["status", "updated_at"])
    Waiver.objects.create(fine=fine, by=staff, reason=reason.strip())
    audit(staff, "fine.waive", fine, branch_id=fine.member.home_branch_id, reason=reason)
    return fine

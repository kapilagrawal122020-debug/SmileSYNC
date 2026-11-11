"""Advance payments: money a patient paid before it was billed.

An advance stays as credit on the patient until it is applied to an invoice (or refunded).
Applying it creates a normal Payment with method "advance", so invoice totals, dues and the
dashboard keep working without special cases.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from .models import Advance, InvoiceItem, Payment

ZERO = Decimal("0")


def open_advances(patient):
    """Advances that still have money left, oldest first (that is the order they are used in)."""
    advances = patient.advances.prefetch_related("payments", "refunds").order_by("received_at", "id")
    return [a for a in advances if a.balance > 0]


def advance_balance(patient):
    return sum((a.balance for a in open_advances(patient)), ZERO)


@transaction.atomic
def apply_advance(invoice, amount=None, user=None):
    """Pay (part of) an invoice from the patient's advances. Returns the Payments created.

    amount=None means "as much as possible": the invoice due or the advance left, whichever is smaller.
    """
    advances = open_advances(invoice.patient)
    available = sum((a.balance for a in advances), ZERO)
    due = invoice.due

    if amount is None:
        amount = min(due, available)
        if amount <= 0:
            return []  # nothing to apply, not an error
    elif amount <= 0:
        raise ValidationError({"amount": "Amount must be more than zero."})
    elif amount > due:
        raise ValidationError({"amount": f"The invoice only has {due} due."})
    elif amount > available:
        raise ValidationError({"amount": f"Only {available} advance is available."})

    payments, left = [], amount
    for advance in advances:
        part = min(advance.balance, left)
        if part <= 0:
            continue
        payments.append(Payment.objects.create(
            invoice=invoice, amount=part, method=Payment.ADVANCE, advance=advance,
            reference=f"Advance #{advance.id}", received_by=user,
        ))
        left -= part
        if left <= 0:
            break
    return payments


def summary(patient):
    """Everything the Billing tab needs in one call."""
    billed = InvoiceItem.objects.filter(invoice__patient=patient).aggregate(t=Sum("amount"))["t"] or ZERO
    paid = Payment.objects.filter(invoice__patient=patient).aggregate(t=Sum("amount"))["t"] or ZERO
    advances = list(patient.advances.prefetch_related("payments", "refunds"))
    received = sum((a.amount for a in advances), ZERO)
    used = sum((a.used for a in advances), ZERO)
    refunded = sum((a.refunded for a in advances), ZERO)
    return {
        "billed": billed,
        "paid": paid,  # includes money paid from advances
        "due": billed - paid,
        "advance_received": received,
        "advance_used": used,
        "advance_refunded": refunded,
        "advance_balance": received - used - refunded,
    }

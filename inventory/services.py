from datetime import date, timedelta

from django.db import transaction

from .models import StockItem, StockMovement, UsageRule


@transaction.atomic
def change_stock(item, change, reason, user=None, treatment=None, note=""):
    """The only place stock quantity changes. Logs a StockMovement every time."""
    item = StockItem.objects.select_for_update().get(pk=item.pk)
    item.quantity += change
    item.save(update_fields=["quantity"])
    StockMovement.objects.create(
        item=item, change=change, reason=reason, user=user, treatment=treatment, note=note
    )
    return item


@transaction.atomic
def deduct_for_treatment(treatment, user=None):
    """Treatment done -> deduct material using the clinic's usage rules. Runs once per treatment.

    Example: "Composite Class 1" x 3 with a rule of 0.2 g takes 0.6 g off the A2 tube.
    Returns [{"item", "used", "left"}] for the app to show.
    """
    if treatment.stock_deducted:
        return []
    rules = UsageRule.objects.filter(
        clinic=treatment.patient.clinic, procedure__iexact=treatment.procedure
    ).select_related("item")
    used = []
    for rule in rules:
        amount = rule.quantity * treatment.units
        item = change_stock(rule.item, -amount, StockMovement.TREATMENT, user, treatment)
        used.append({"item": item.name, "used": amount, "left": item.quantity})
    treatment.stock_deducted = True
    treatment.save(update_fields=["stock_deducted"])
    return used


def stock_alerts(clinic, expiry_days=30):
    """Items at or below reorder level, and items expiring soon."""
    items = StockItem.objects.filter(clinic=clinic)
    soon = date.today() + timedelta(days=expiry_days)
    return {
        "low_stock": [i for i in items if i.is_low],
        "expiring": list(items.filter(expiry_date__isnull=False, expiry_date__lte=soon)),
    }

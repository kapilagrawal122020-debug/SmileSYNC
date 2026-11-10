from django.conf import settings
from django.db import models

from clinic.models import Clinic


class StockItem(models.Model):
    """Item master. `quantity` is kept in the base unit (grams, ml or pieces)."""

    UNITS = [("g", "grams"), ("ml", "millilitres"), ("pcs", "pieces")]

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="stock_items")
    name = models.CharField(max_length=100)  # e.g. "Composite A2"
    unit = models.CharField(max_length=5, choices=UNITS, default="pcs")
    pack_size = models.DecimalField(max_digits=8, decimal_places=2, default=1)  # 3 (g tube), 500 (g alginate), 100 (gloves/box)
    shade_or_size = models.CharField(max_length=30, blank=True)
    batch = models.CharField(max_length=50, blank=True)  # latest delivery's batch and expiry
    expiry_date = models.DateField(null=True, blank=True)
    reorder_level = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["name"]

    @property
    def is_low(self):
        return self.quantity <= self.reorder_level

    def __str__(self):
        return self.name


class UsageRule(models.Model):
    """Average material use: treatment -> item -> quantity per unit of the treatment."""

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="usage_rules")
    procedure = models.CharField(max_length=100)  # must match Treatment.procedure (case-insensitive)
    item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="rules")
    quantity = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ["procedure"]


class StockMovement(models.Model):
    """Every change to stock is logged here (audit trail and monthly variance)."""

    STOCK_IN, TREATMENT, MANUAL, COUNT = "stock_in", "treatment", "manual", "count"
    REASONS = [(STOCK_IN, "Stock in"), (TREATMENT, "Treatment"), (MANUAL, "Manual"), (COUNT, "Monthly count")]

    item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="movements")
    change = models.DecimalField(max_digits=10, decimal_places=2)  # negative = used
    reason = models.CharField(max_length=15, choices=REASONS)
    treatment = models.ForeignKey("patients.Treatment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

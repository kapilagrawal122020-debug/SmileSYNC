from django.db import models
from django.db.models import Q


class ListDefinition(models.Model):
    """One editable dropdown list, for example "visit-statuses". Managed by the platform admin."""

    LOCKED, BOUND, CLINIC = "locked", "bound", "clinic"
    KIND_CHOICES = [
        (LOCKED, "Locked reference list"),   # platform admin edits only; clinics never do
        (BOUND, "Behaviour-bound list"),     # some codes drive code paths; those codes cannot be turned off
        (CLINIC, "Clinic-owned list"),       # a clinic may add its own values later
    ]

    key = models.SlugField(max_length=60, unique=True)
    title = models.CharField(max_length=100)
    group = models.CharField(max_length=80, blank=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=CLINIC)
    used_in = models.CharField(max_length=200, blank=True)        # where the product uses it
    stored_today = models.CharField(max_length=200, blank=True)   # how it was stored before this list existed
    scope = models.CharField(max_length=200, blank=True)          # who should edit it
    about = models.TextField(blank=True)                          # the concept, in plain language
    admin_note = models.TextField(blank=True)                     # how the admin should treat it
    sort_order = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "title"]

    def __str__(self):
        return self.title


class ListValue(models.Model):
    """One value of a list. The code never changes once created; the label can."""

    list = models.ForeignKey(ListDefinition, on_delete=models.CASCADE, related_name="values")
    clinic = models.ForeignKey("clinic.Clinic", null=True, blank=True, on_delete=models.CASCADE, related_name="+")  # empty = global
    code = models.CharField(max_length=60)
    label = models.CharField(max_length=200)
    labels = models.JSONField(default=dict, blank=True)  # {"hi": "...", "mr": "..."}
    description = models.CharField(max_length=500, blank=True)
    group = models.CharField(max_length=80, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)  # code depends on it: it cannot be turned off
    metadata = models.JSONField(default=dict, blank=True)  # colour, default minutes, fee, tax flag...
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["list", "clinic", "code"], name="unique_code_per_list_and_clinic"),
            models.UniqueConstraint(fields=["list", "code"], condition=Q(clinic__isnull=True), name="unique_global_code_per_list"),
        ]

    def __str__(self):
        return f"{self.list_id}:{self.code}"

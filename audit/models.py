from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Append-only record of who did what and when. Rows are never edited or deleted by the app."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user_label = models.CharField(max_length=120, blank=True)  # name or phone at the time, kept if the user is later removed
    role = models.CharField(max_length=20, blank=True)
    action = models.CharField(max_length=60, db_index=True)    # e.g. "login", "list.value.update"
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    clinic = models.ForeignKey("clinic.Clinic", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    detail = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

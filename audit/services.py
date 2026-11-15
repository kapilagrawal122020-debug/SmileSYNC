from .models import AuditEvent


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR")) or None


def log(action, request=None, user=None, obj=None, clinic=None, label="", **detail):
    """Write one audit event. Never raises: a failed log must not break the action being logged."""
    try:
        user = user or (request.user if request is not None and getattr(request.user, "is_authenticated", False) else None)
        AuditEvent.objects.create(
            user=user, user_label=(label or (user.name or user.phone if user else ""))[:120], role=(user.role if user else "")[:20],
            action=action[:60], object_type=(obj.__class__.__name__ if obj is not None else "")[:40],
            object_id=str(obj.pk) if obj is not None else "",
            clinic=clinic or (user.clinic if user is not None else None), detail=detail, ip=client_ip(request),
        )
    except Exception:  # noqa: BLE001
        pass

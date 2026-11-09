import ipaddress

from django.conf import settings

from .models import AuditLog


def client_ip(request):
    if request is None:
        return None
    raw = request.META.get("REMOTE_ADDR")
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            raw = forwarded.split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(raw)) if raw else None
    except ValueError:
        return None


def log(request, action, obj=None, changes=None, user=None, username=""):
    """Write one audit entry. Never raises into the caller's request."""
    if user is None and request is not None and request.user.is_authenticated:
        user = request.user
    AuditLog.objects.create(
        user=user,
        username=username or (user.get_username() if user else ""),
        action=action,
        model=obj._meta.model_name if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        object_repr=str(obj)[:255] if obj is not None else "",
        changes=list(changes or []),
        ip=client_ip(request),
    )
